#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Normalize structurally valid OpenAPI specifications.

Malformed references and operations fail closed before output is written.

IMPORTANT: This script reads from docs/specifications/api and writes in-place.
The original specs (specs/original/) are NEVER modified.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml
from openapi_spec_validator import validate
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from scripts.package_config import load_packaged_yaml
from scripts.utils.json_writer import write_json_file
from scripts.utils.source_graph_validator import (
    select_source_specs,
    source_spec_files,
    validate_source_graph,
)
from scripts.utils.spec_batch import publish_spec_batch

console = Console()

# Public for processing APIs and tests; its bytes come only from the packaged YAML.
DEFAULT_CONFIG = load_packaged_yaml("normalization.yaml")

_CONFIG_SCHEMA = {
    "paths": frozenset({"enriched", "normalized", "reports"}),
    "normalization": frozenset({"type_standardization"}),
    "processing": frozenset({"parallel_workers"}),
    "output": frozenset({"json_indent", "sort_keys"}),
}


@dataclass
class NormalizationStats:
    """Statistics for normalization processing."""

    files_processed: int = 0
    files_succeeded: int = 0
    files_failed: int = 0
    types_normalized: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class NormalizationResult:
    """Result of normalizing a single specification file."""

    filename: str
    success: bool
    changes: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def load_config(config_path: Path | None = None) -> dict:
    """Load packaged normalization configuration with an optional validated overlay."""
    canonical = load_packaged_yaml("normalization.yaml")
    overlay: dict[str, Any] = {}
    if config_path is not None:
        if not config_path.is_file():
            raise FileNotFoundError(f"normalization configuration not found: {config_path}")
        with config_path.open() as f:
            document = yaml.safe_load(f)
        if document is not None:
            if not isinstance(document, dict):
                raise TypeError("normalization configuration must be an object")
            overlay = document
        _validate_config(overlay)
    config = _deep_merge(canonical, overlay)
    _validate_config(config)
    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_config(config: dict[str, Any]) -> None:
    """Reject obsolete or misspelled controls instead of silently ignoring them."""
    if not isinstance(config, dict):
        raise TypeError("normalization configuration must be an object")
    unknown_sections = sorted(set(config) - set(_CONFIG_SCHEMA))
    if unknown_sections:
        raise ValueError(f"unsupported normalization configuration sections: {unknown_sections}")
    for section, allowed_keys in _CONFIG_SCHEMA.items():
        if section not in config:
            continue
        section_config = config[section]
        if not isinstance(section_config, dict):
            raise TypeError(f"normalization configuration {section!r} must be an object")
        unknown_keys = sorted(set(section_config) - allowed_keys)
        if unknown_keys:
            raise ValueError(
                f"unsupported normalization configuration keys in {section!r}: {unknown_keys}"
            )


def load_spec(spec_path: Path) -> dict[str, Any]:
    """Load an OpenAPI specification from JSON file."""
    with spec_path.open() as f:
        return json.load(f)


def save_spec(
    spec: dict[str, Any],
    output_path: Path,
    indent: int = 2,
    sort_keys: bool = False,
) -> None:
    """Save an OpenAPI specification to JSON file.

    Delegates to `write_json_file`, which applies Biome formatting so
    the output satisfies Super-Linter's BIOME_FORMAT check at commit time.
    """
    write_json_file(
        spec,
        output_path,
        indent=indent,
        sort_keys=sort_keys,
        ensure_ascii=False,
    )


def _validation_error(spec_file: Path) -> str | None:
    try:
        spec = load_spec(spec_file)
        validate_source_graph(spec)
        validate(spec)
    except Exception as error:
        return f"{spec_file.name}: {error}"
    return None


def normalize_types(spec: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Standardize type values to lowercase.

    Returns (modified_spec, count_of_normalizations).
    """
    normalized_count = 0
    valid_types = {"string", "number", "integer", "boolean", "array", "object", "null"}

    def normalize_recursive(obj: Any) -> Any:
        nonlocal normalized_count

        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                if key == "type" and isinstance(value, str):
                    lower_value = value.lower()
                    if lower_value in valid_types and value != lower_value:
                        result[key] = lower_value
                        normalized_count += 1
                    else:
                        result[key] = value
                else:
                    result[key] = normalize_recursive(value)
            return result
        if isinstance(obj, list):
            return [normalize_recursive(item) for item in obj]
        return obj

    return normalize_recursive(spec), normalized_count


def normalize_spec_file(
    spec_path: Path,
    output_path: Path,
    config: dict,
) -> NormalizationResult:
    """Normalize a single specification file.

    Args:
        spec_path: Path to the enriched specification file.
        output_path: Path to save the normalized specification.
        config: Normalization configuration.

    Returns:
        NormalizationResult with processing details.
    """
    filename = spec_path.name
    changes = defaultdict(int)

    try:
        _validate_config(config)
        spec = load_spec(spec_path)
        validate_source_graph(spec)
        validate(spec)
        norm_config = config["normalization"]

        if norm_config["type_standardization"]:
            spec, count = normalize_types(spec)
            changes["types_normalized"] = count

        validate_source_graph(spec)
        validate(spec)
        output_config = config["output"]
        save_spec(
            spec,
            output_path,
            indent=output_config["json_indent"],
            sort_keys=output_config["sort_keys"],
        )

        return NormalizationResult(
            filename=filename,
            success=True,
            changes=dict(changes),
        )

    except Exception as e:
        return NormalizationResult(
            filename=filename,
            success=False,
            error=str(e),
        )


def process_spec_wrapper(args: tuple) -> NormalizationResult:
    """Wrapper for multiprocessing."""
    spec_path, output_path, config = args
    return normalize_spec_file(spec_path, output_path, config)


def normalize_all_specs(
    input_dir: Path,
    output_dir: Path,
    config: dict,
    parallel: bool = True,
) -> NormalizationStats:
    """Normalize all specification files in a directory.

    Args:
        input_dir: Directory containing enriched specifications.
        output_dir: Directory to save normalized specifications.
        config: Normalization configuration.
        parallel: Enable parallel processing.

    Returns:
        NormalizationStats with processing summary.
    """
    stats = NormalizationStats()

    _validate_config(config)
    selection = select_source_specs(input_dir)
    spec_files = list(selection.files)
    if not spec_files:
        console.print(f"[yellow]No specification files found in {input_dir}[/yellow]")
        return stats

    console.print(f"[blue]Found {len(spec_files)} specification files to normalize[/blue]")

    output_dir.parent.mkdir(parents=True, exist_ok=True)

    workers = config["processing"]["parallel_workers"] if parallel else 1

    with TemporaryDirectory(
        prefix=f".{output_dir.name}-staging-", dir=output_dir.parent
    ) as staging:
        staging_dir = Path(staging)
        process_args = [
            (spec_file, staging_dir / spec_file.name, config) for spec_file in spec_files
        ]

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Normalizing specifications...", total=len(spec_files))

            if parallel and workers > 1:
                with ProcessPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(process_spec_wrapper, args): args[0].name
                        for args in process_args
                    }

                    for future in as_completed(futures):
                        filename = futures[future]
                        try:
                            result = future.result()
                            _update_stats(stats, result)
                        except Exception as error:
                            stats.files_failed += 1
                            stats.errors.append({"file": filename, "error": str(error)})

                        stats.files_processed += 1
                        progress.update(task, advance=1)
            else:
                for args in process_args:
                    try:
                        result = process_spec_wrapper(args)
                        _update_stats(stats, result)
                    except Exception as error:
                        stats.files_failed += 1
                        stats.errors.append({"file": args[0].name, "error": str(error)})

                    stats.files_processed += 1
                    progress.update(task, advance=1)

        if stats.files_failed or stats.errors or stats.files_succeeded != stats.files_processed:
            _abort_normalization_batch(stats, "one or more specifications failed validation")
            return stats

        try:
            publish_spec_batch(staging_dir, output_dir, selection)
        except Exception as error:
            stats.errors.append({"file": "<publication>", "error": str(error)})
            _abort_normalization_batch(stats, "transactional publication failed")

    return stats


def _abort_normalization_batch(stats: NormalizationStats, reason: str) -> None:
    """Report a transaction abort without claiming unpublished partial successes."""
    stats.errors.append(
        {"file": "<batch>", "error": f"normalization transaction aborted: {reason}"}
    )
    stats.files_failed = stats.files_processed
    stats.files_succeeded = 0
    stats.types_normalized = 0


def _update_stats(stats: NormalizationStats, result: NormalizationResult) -> None:
    """Update statistics from a normalization result."""
    if result.success:
        stats.files_succeeded += 1
        stats.types_normalized += result.changes.get("types_normalized", 0)
    else:
        stats.files_failed += 1
        if result.error:
            stats.errors.append({"file": result.filename, "error": result.error})


def generate_report(stats: NormalizationStats, output_path: Path) -> None:
    """Generate normalization report."""
    report = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "summary": {
            "files_processed": stats.files_processed,
            "files_succeeded": stats.files_succeeded,
            "files_failed": stats.files_failed,
            "types_normalized": stats.types_normalized,
        },
        "errors": stats.errors,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    console.print(f"[green]Report saved to {output_path}[/green]")


def print_summary(stats: NormalizationStats) -> None:
    """Print normalization summary to console."""
    table = Table(title="Normalization Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Files Processed", str(stats.files_processed))
    table.add_row("Files Succeeded", str(stats.files_succeeded))
    table.add_row("Files Failed", str(stats.files_failed))
    table.add_row("Types Normalized", str(stats.types_normalized))

    console.print(table)

    if stats.errors:
        console.print(f"\n[red]Errors ({len(stats.errors)}):[/red]")
        for error in stats.errors[:10]:
            console.print(f"  - {error['file']}: {error['error'][:100]}...")
        if len(stats.errors) > 10:
            console.print(f"  ... and {len(stats.errors) - 10} more errors")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Normalize F5 XC API specifications for UI compatibility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to configuration overlay (default: packaged config/normalization.yaml)",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="Override input directory for enriched specs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override output directory for normalized specs",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        help="Override directory for reports",
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="Disable parallel processing",
    )
    parser.add_argument(
        "--workers",
        type=int,
        help="Number of parallel workers",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze specs without writing output",
    )

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    # Determine directories
    input_dir = args.input_dir or Path(config["paths"]["enriched"])
    output_dir = args.output_dir or Path(config["paths"]["normalized"])
    report_dir = args.report_dir or Path(config["paths"]["reports"])

    # Override workers if specified
    if args.workers:
        config["processing"]["parallel_workers"] = args.workers

    console.print("[bold blue]F5 XC API Specification Normalization[/bold blue]")
    console.print(f"  Input:  {input_dir}")
    console.print(f"  Output: {output_dir}")

    if not input_dir.exists():
        console.print(f"[red]Input directory not found: {input_dir}[/red]")
        console.print(
            "[yellow]Run 'python -m scripts.enrich' first to enrich specifications[/yellow]",
        )
        return 1

    if args.dry_run:
        console.print("\n[yellow]DRY RUN - validating without writing output[/yellow]")
        spec_files = source_spec_files(input_dir)
        errors = [error for path in spec_files if (error := _validation_error(path))]
        for error in errors:
            console.print(f"[red]{error}[/red]")
        if not spec_files or errors:
            return 1
        console.print(f"\n[green]Validated {len(spec_files)} specifications[/green]")
        return 0

    # Run normalization pipeline
    stats = normalize_all_specs(
        input_dir=input_dir,
        output_dir=output_dir,
        config=config,
        parallel=not args.no_parallel,
    )

    # Generate report
    report_path = report_dir / "normalization-report.json"
    generate_report(stats, report_path)

    # Print summary
    print_summary(stats)

    if (
        stats.files_processed == 0
        or stats.files_failed > 0
        or stats.errors
        or stats.files_succeeded != stats.files_processed
    ):
        console.print(f"\n[red]Normalization failed for {stats.files_failed} files[/red]")
        return 1

    console.print(
        f"\n[bold green]Successfully normalized {stats.files_succeeded} specifications![/bold green]",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
