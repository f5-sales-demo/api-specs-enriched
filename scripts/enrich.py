#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Automated enrichment pipeline for F5 XC API specifications.

Applies acronym normalization, grammar improvements, and branding transformations
to all OpenAPI specification files. Fully automated - no manual intervention required.
"""

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from openapi_spec_validator import validate
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from scripts.utils import (
    AcronymNormalizer,
    BrandingNormalizer,
    BrandingTransformer,
    BrandingValidator,
    ConsistencyValidator,
    ConstraintEnricher,
    DeprecatedTierEnricher,
    DescriptionEnricher,
    DescriptionStructureTransformer,
    DescriptionValidator,
    DiscoveryEnricher,
    ExternalDocsEnricher,
    FieldMetadataEnricher,
    GrammarImprover,
    MinimumConfigurationEnricher,
    NamespaceProfileEnricher,
    ProseSpellingTransformer,
    SchemaFixer,
    UniquenessEnricher,
)
from scripts.utils.console_ui_enricher import ConsoleUIEnricher
from scripts.utils.json_writer import write_json_file

console = Console()


# Default configuration
DEFAULT_CONFIG = {
    "paths": {
        "original": "specs/original",
        "enriched": "docs/specifications/api",
        "reports": "reports",
        "discovered": "specs/discovered",
    },
    "target_fields": ["description", "summary", "title", "x-displayname"],
    "preserve_fields": ["operationId", "$ref", "x-ves-proto-rpc", "x-ves-proto-service"],
    "grammar": {
        "capitalize_sentences": True,
        "ensure_punctuation": True,
        "normalize_whitespace": True,
        "fix_double_spaces": True,
        "trim_whitespace": True,
    },
    "validation": {
        "validate_after_enrichment": True,
        "fail_on_error": False,
    },
    "processing": {
        "parallel_workers": 4,
        "continue_on_error": True,
    },
    "output": {
        "json_indent": 2,
        "sort_keys": False,
    },
    "discovery_enrichment": {
        "enabled": False,
        "discovered_specs_dir": "specs/discovered",
    },
}

# Cache for discovery enricher singleton (loaded once, reused)
_DISCOVERY_CACHE: dict[str, Any] = {"enricher": None, "config": None}


@dataclass
class EnrichmentStats:
    """Statistics for enrichment processing."""

    files_processed: int = 0
    files_succeeded: int = 0
    files_failed: int = 0
    acronyms_normalized: int = 0
    grammar_improved: int = 0
    branding_transformed: int = 0
    schemas_fixed: int = 0
    descriptions_generated: int = 0
    required_fields_extracted: int = 0
    validation_passed: int = 0
    validation_failed: int = 0
    consistency_issues: int = 0
    discovery_enrichments: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EnrichmentResult:
    """Result of enriching a single specification file."""

    filename: str
    success: bool
    changes: dict[str, int] = field(default_factory=dict)
    validation_passed: bool = True
    error: str | None = None


def load_config(config_path: Path | None = None) -> dict:
    """Load configuration from YAML file or use defaults."""
    if config_path and config_path.exists():
        with config_path.open() as f:
            config = yaml.safe_load(f) or {}
            # Deep merge with defaults
            return _deep_merge(DEFAULT_CONFIG, config)
    return DEFAULT_CONFIG


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_discovery_enricher(config: dict) -> DiscoveryEnricher | None:
    """Load discovery enricher with discovery data.

    Args:
        config: Enrichment configuration

    Returns:
        Initialized DiscoveryEnricher or None if disabled/unavailable
    """
    discovery_config = config.get("discovery_enrichment", {})
    if not discovery_config.get("enabled", False):
        return None

    # Return cached enricher if config hasn't changed
    if _DISCOVERY_CACHE["enricher"] is not None and _DISCOVERY_CACHE["config"] == discovery_config:
        return _DISCOVERY_CACHE["enricher"]

    discovered_dir = Path(
        discovery_config.get("discovered_specs_dir", "specs/discovered"),
    )

    if not discovered_dir.exists():
        console.print(
            f"[yellow]Discovery data not found: {discovered_dir}[/yellow]",
        )
        console.print("[yellow]Run 'make discover' to generate discovery data[/yellow]")
        return None

    # Load discovery enrichment config from separate file if exists
    discovery_config_path = Path("config/discovery_enrichment.yaml")
    if discovery_config_path.exists():
        with discovery_config_path.open() as f:
            full_discovery_config = yaml.safe_load(f) or {}
            discovery_config = _deep_merge(discovery_config, full_discovery_config)

    enricher = DiscoveryEnricher(discovery_config)

    try:
        enricher.load_discovery_data(discovered_dir)
        console.print(f"[green]Loaded discovery data from {discovered_dir}[/green]")
        _DISCOVERY_CACHE["enricher"] = enricher
        _DISCOVERY_CACHE["config"] = discovery_config
        return enricher
    except Exception as e:
        console.print(f"[red]Failed to load discovery data: {e}[/red]")
        return None


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

    Runs ``SchemaFixer.inject_max_items`` as the last step before
    serialization so Checkov CKV_OPENAPI_21 passes on the committed
    JSON without the synthetic bound leaking into ``x-f5xc-constraints``
    (ConstraintEnricher has already run at this point). Delegates to
    ``write_json_file``, which applies Biome formatting so the output
    satisfies Super-Linter's BIOME_FORMAT check at commit time.
    """
    spec = SchemaFixer().inject_max_items(spec)
    write_json_file(
        spec,
        output_path,
        indent=indent,
        sort_keys=sort_keys,
        ensure_ascii=False,
    )


def validate_spec(spec: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate an OpenAPI specification."""
    try:
        validate(spec)
        return True, None
    except OpenAPIValidationError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Validation error: {e}"


def count_text_fields(spec: dict[str, Any], target_fields: list[str]) -> int:
    """Count the number of text fields in a specification."""
    count = 0

    def _count_recursive(obj: Any) -> None:
        nonlocal count
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in target_fields and isinstance(value, str):
                    count += 1
                else:
                    _count_recursive(value)
        elif isinstance(obj, list):
            for item in obj:
                _count_recursive(item)

    _count_recursive(spec)
    return count


def enrich_spec_file(
    spec_path: Path,
    output_path: Path,
    config: dict,
) -> EnrichmentResult:
    """Enrich a single specification file.

    Args:
        spec_path: Path to the original specification file.
        output_path: Path to save the enriched specification.
        config: Enrichment configuration.

    Returns:
        EnrichmentResult with processing details.
    """
    filename = spec_path.name

    try:
        # Load specification
        spec = load_spec(spec_path)
        original_field_count = count_text_fields(spec, config.get("target_fields", []))

        # Initialize enrichment utilities
        acronym_normalizer = AcronymNormalizer()
        branding_transformer = BrandingTransformer()
        deprecated_tier_enricher = DeprecatedTierEnricher()
        description_structure_transformer = DescriptionStructureTransformer()
        schema_fixer = SchemaFixer()
        field_metadata_enricher = FieldMetadataEnricher()
        minimum_configuration_enricher = MinimumConfigurationEnricher()
        description_validator = DescriptionValidator()
        consistency_validator = ConsistencyValidator()
        constraint_enricher = ConstraintEnricher(
            config_path=Path("config/constraint_patterns.yaml"),
        )

        grammar_config = config.get("grammar", {})
        grammar_improver = GrammarImprover(
            capitalize_sentences=grammar_config.get("capitalize_sentences", True),
            ensure_punctuation=grammar_config.get("ensure_punctuation", True),
            normalize_whitespace=grammar_config.get("normalize_whitespace", True),
            fix_double_spaces=grammar_config.get("fix_double_spaces", True),
            trim_whitespace=grammar_config.get("trim_whitespace", True),
            use_language_tool=True,
        )

        target_fields = config.get(
            "target_fields",
            ["description", "summary", "title", "x-displayname"],
        )

        # Initialize XKS/XCS branding normalizer
        branding_normalizer = BrandingNormalizer()

        # Apply enrichments in order
        # 0. Deprecated tier transformation (BASIC→STANDARD, PREMIUM→ADVANCED)
        spec = deprecated_tier_enricher.enrich(spec)

        # 1. Branding transformations first (most specific)
        # 1a. Legacy Volterra→F5 branding
        spec = branding_transformer.transform_spec(spec, target_fields)
        spec = ProseSpellingTransformer().transform_spec(spec)
        # 1b. Industry-standard XKS/XCS terminology normalization
        spec = branding_normalizer.normalize_spec(spec, target_fields)

        # 2. Description structure normalization (extract examples, validation rules, X-required)
        spec = description_structure_transformer.transform_spec(spec, target_fields)

        # 3. Schema fixes (add missing type field where format exists)
        spec = schema_fixer.fix_spec(spec)

        # 4. Field metadata enrichment (add unified x-f5xc-* field-level metadata)
        spec = field_metadata_enricher.enrich_spec(spec)

        # 4.5. Minimum configuration enrichment (add x-f5xc-minimum-configuration)
        spec = minimum_configuration_enricher.enrich_spec(spec)

        # 4.6. Namespace profile enrichment (add x-f5xc-namespace-profile)
        namespace_profile_enricher = NamespaceProfileEnricher()
        spec = namespace_profile_enricher.enrich_spec(spec)

        # 4.6.5. Uniqueness enrichment (add x-f5xc-uniqueness derived from namespace scope)
        uniqueness_enricher = UniquenessEnricher()
        spec = uniqueness_enricher.enrich_spec(spec)

        # 4.6.6. Console UI enrichment (add x-f5xc-console navigation and form metadata)
        console_ui_enricher = ConsoleUIEnricher()
        spec = console_ui_enricher.enrich_spec(spec)

        # 4.7. External docs enrichment (add externalDocs with F5 documentation links)
        external_docs_enricher = ExternalDocsEnricher()
        spec = external_docs_enricher.enrich_spec(spec, filename=spec_path.name)

        # 4.8. Domain description enrichment (apply DRY descriptions from config)
        description_enricher = DescriptionEnricher()
        spec = description_enricher.enrich_spec(spec)

        # 4.9. Constraint enrichment (add x-f5xc-constraints from patterns)
        spec = constraint_enricher.enrich_spec(spec)

        # 5. Acronym normalization
        spec = acronym_normalizer.normalize_spec(spec, target_fields)

        # 6. Grammar improvements
        spec = grammar_improver.improve_spec(spec, target_fields)

        # 7. Description validation and generation (auto-generate missing descriptions)
        spec = description_validator.validate_and_generate(spec)

        # 9. Discovery enrichment (add x-discovered-* extensions)
        discovery_enrichments = 0
        discovery_enricher = load_discovery_enricher(config)
        if discovery_enricher:
            spec = discovery_enricher.enrich_with_discoveries(spec)
            discovery_stats = discovery_enricher.get_stats()
            discovery_enrichments = discovery_stats.get("fields_enriched", 0)

        # Close grammar improver resources
        grammar_improver.close()

        # Run consistency validation (read-only, generates report)
        consistency_validator.validate(spec)

        # Validate branding was applied correctly
        branding_validator = BrandingValidator()
        legacy_findings = branding_validator.validate_spec(spec, target_fields)

        # Validate enriched spec
        validation_config = config.get("validation", {})
        validation_passed = True
        validation_error = None

        if validation_config.get("validate_after_enrichment", True):
            validation_passed, validation_error = validate_spec(spec)

        # Save enriched specification
        output_config = config.get("output", {})
        save_spec(
            spec,
            output_path,
            indent=output_config.get("json_indent", 2),
            sort_keys=output_config.get("sort_keys", False),
        )

        # Collect stats from all transformers
        deprecated_tier_stats = deprecated_tier_enricher.get_stats()
        branding_normalizer_stats = branding_normalizer.get_stats()
        schema_stats = schema_fixer.get_stats()
        desc_stats = description_validator.get_stats()
        consistency_stats = consistency_validator.get_stats()
        minimum_config_stats = minimum_configuration_enricher.get_stats()
        namespace_profile_stats = namespace_profile_enricher.get_stats()
        constraint_stats = constraint_enricher.get_stats()
        _ = console_ui_enricher.get_stats()

        return EnrichmentResult(
            filename=filename,
            success=True,
            changes={
                "text_fields_processed": original_field_count,
                "legacy_branding_remaining": len(legacy_findings),
                "deprecated_tiers_transformed": deprecated_tier_stats.get(
                    "values_transformed",
                    0,
                ),
                "managed_k8s_transformations": branding_normalizer_stats.get(
                    "managed_k8s_transformations", 0
                ),
                "virtual_k8s_transformations": branding_normalizer_stats.get(
                    "virtual_k8s_transformations", 0
                ),
                "glossary_terms_added": branding_normalizer_stats.get("glossary_terms_added", 0),
                "schemas_fixed": schema_stats.get("fixes_applied", 0),
                "descriptions_generated": desc_stats.get("operations_generated", 0),
                "consistency_issues": consistency_stats.get("total_issues", 0),
                "minimum_configs_added": minimum_config_stats.get("minimum_configs_added", 0),
                "namespace_profiles_added": namespace_profile_stats.get("specs_enriched", 0),
                "discovery_enrichments": discovery_enrichments,
                "constraints_added": constraint_stats.get("constraints_added", 0),
                "constraint_coverage": constraint_stats.get("coverage_percentage", 0),
                "constraint_pattern_matches": constraint_stats.get("pattern_matches", 0),
                "constraint_avg_confidence": constraint_stats.get("average_confidence", 0),
            },
            validation_passed=validation_passed,
            error=validation_error if not validation_passed else None,
        )

    except Exception as e:
        return EnrichmentResult(
            filename=filename,
            success=False,
            error=str(e),
            validation_passed=False,
        )


def process_spec_wrapper(args: tuple) -> EnrichmentResult:
    """Wrapper for multiprocessing."""
    spec_path, output_path, config = args
    return enrich_spec_file(spec_path, output_path, config)


def enrich_all_specs(
    input_dir: Path,
    output_dir: Path,
    config: dict,
    parallel: bool = True,
) -> EnrichmentStats:
    """Enrich all specification files in a directory.

    Args:
        input_dir: Directory containing original specifications.
        output_dir: Directory to save enriched specifications.
        config: Enrichment configuration.
        parallel: Enable parallel processing.

    Returns:
        EnrichmentStats with processing summary.
    """
    stats = EnrichmentStats()

    # Find all JSON spec files
    spec_files = sorted(input_dir.glob("*.json"))
    if not spec_files:
        console.print(f"[yellow]No specification files found in {input_dir}[/yellow]")
        return stats

    console.print(f"[blue]Found {len(spec_files)} specification files to enrich[/blue]")

    # Prepare output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    processing_config = config.get("processing", {})
    workers = processing_config.get("parallel_workers", 4) if parallel else 1
    continue_on_error = processing_config.get("continue_on_error", True)

    # Prepare arguments for processing
    process_args = [(spec_file, output_dir / spec_file.name, config) for spec_file in spec_files]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Enriching specifications...", total=len(spec_files))

        if parallel and workers > 1:
            # Parallel processing
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
                    except Exception as e:
                        stats.files_failed += 1
                        stats.errors.append({"file": filename, "error": str(e)})
                        if not continue_on_error:
                            raise

                    stats.files_processed += 1
                    progress.update(task, advance=1)
        else:
            # Sequential processing
            for args in process_args:
                try:
                    result = process_spec_wrapper(args)
                    _update_stats(stats, result)
                except Exception as e:
                    stats.files_failed += 1
                    stats.errors.append({"file": args[0].name, "error": str(e)})
                    if not continue_on_error:
                        raise

                stats.files_processed += 1
                progress.update(task, advance=1)

    return stats


def _update_stats(stats: EnrichmentStats, result: EnrichmentResult) -> None:
    """Update statistics from an enrichment result."""
    if result.success:
        stats.files_succeeded += 1
        if result.validation_passed:
            stats.validation_passed += 1
        else:
            stats.validation_failed += 1
            if result.error:
                stats.errors.append({"file": result.filename, "error": result.error})
    else:
        stats.files_failed += 1
        if result.error:
            stats.errors.append({"file": result.filename, "error": result.error})


def generate_report(stats: EnrichmentStats, output_path: Path) -> None:
    """Generate enrichment report."""
    report = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "summary": {
            "files_processed": stats.files_processed,
            "files_succeeded": stats.files_succeeded,
            "files_failed": stats.files_failed,
            "validation_passed": stats.validation_passed,
            "validation_failed": stats.validation_failed,
        },
        "errors": stats.errors,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    console.print(f"[green]Report saved to {output_path}[/green]")


def print_summary(stats: EnrichmentStats) -> None:
    """Print enrichment summary to console."""
    table = Table(title="Enrichment Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Files Processed", str(stats.files_processed))
    table.add_row("Files Succeeded", str(stats.files_succeeded))
    table.add_row("Files Failed", str(stats.files_failed))
    table.add_row("Validation Passed", str(stats.validation_passed))
    table.add_row("Validation Failed", str(stats.validation_failed))

    console.print(table)

    if stats.errors:
        console.print(f"\n[red]Errors ({len(stats.errors)}):[/red]")
        for error in stats.errors[:10]:  # Show first 10 errors
            console.print(f"  - {error['file']}: {error['error'][:100]}...")
        if len(stats.errors) > 10:
            console.print(f"  ... and {len(stats.errors) - 10} more errors")


def _validate_single_spec_file(spec_file: Path) -> tuple[bool, str]:
    """Validate a single spec file and return result with error message if any."""
    try:
        spec = load_spec(spec_file)
        valid, error = validate_spec(spec)
        if valid:
            return True, ""
        return False, f"Invalid: {spec_file.name}: {error}"
    except Exception as e:
        return False, f"Error: {spec_file.name}: {e}"


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Enrich F5 XC API specifications with automated improvements",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/enrichment.yaml"),
        help="Path to configuration file",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="Override input directory for original specs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override output directory for enriched specs",
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
        "--validate-only",
        action="store_true",
        help="Only validate existing enriched specs",
    )
    parser.add_argument(
        "--use-discovery",
        action="store_true",
        help="Enable discovery enrichment (adds x-discovered-* extensions)",
    )

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    # Determine directories
    input_dir = args.input_dir or Path(config["paths"]["original"])
    output_dir = args.output_dir or Path(config["paths"]["enriched"])
    report_dir = args.report_dir or Path(config["paths"]["reports"])

    # Override workers if specified
    if args.workers:
        config["processing"]["parallel_workers"] = args.workers

    # Enable discovery enrichment if requested
    if args.use_discovery:
        if "discovery_enrichment" not in config:
            config["discovery_enrichment"] = {}
        config["discovery_enrichment"]["enabled"] = True

    console.print("[bold blue]F5 XC API Specification Enrichment[/bold blue]")
    console.print(f"  Input:  {input_dir}")
    console.print(f"  Output: {output_dir}")

    if config.get("discovery_enrichment", {}).get("enabled", False):
        console.print("  [green]Discovery enrichment: enabled[/green]")

    if not input_dir.exists():
        console.print(f"[red]Input directory not found: {input_dir}[/red]")
        console.print(
            "[yellow]Run 'python scripts/download.py' first to download specifications[/yellow]",
        )
        return 1

    if args.validate_only:
        # Just validate existing enriched specs
        console.print("\n[blue]Validating existing enriched specifications...[/blue]")
        spec_files = sorted(output_dir.glob("*.json"))
        passed = 0
        failed = 0
        for spec_file in spec_files:
            is_valid, error_msg = _validate_single_spec_file(spec_file)
            if is_valid:
                passed += 1
            else:
                failed += 1
                console.print(f"[red]{error_msg}[/red]")
        console.print(f"\n[green]Passed: {passed}[/green], [red]Failed: {failed}[/red]")
        return 0 if failed == 0 else 1

    # Run enrichment pipeline
    stats = enrich_all_specs(
        input_dir=input_dir,
        output_dir=output_dir,
        config=config,
        parallel=not args.no_parallel,
    )

    # Generate report
    report_path = report_dir / "enrichment-report.json"
    generate_report(stats, report_path)

    # Print summary
    print_summary(stats)

    # Exit with error if any files failed
    if stats.files_failed > 0:
        console.print(f"\n[yellow]Completed with {stats.files_failed} failures[/yellow]")
        return 1 if not config.get("processing", {}).get("continue_on_error", True) else 0

    console.print(
        f"\n[bold green]Successfully enriched {stats.files_succeeded} specifications![/bold green]",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
