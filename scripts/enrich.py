#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Automated enrichment pipeline for F5 XC API specifications.

Applies acronym normalization, grammar improvements, and branding transformations
to all OpenAPI specification files. Fully automated - no manual intervention required.
"""

import argparse
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml
from openapi_spec_validator import validate
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from scripts.package_config import load_packaged_yaml, packaged_config_path
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
    SchemaFixer,
    UniquenessEnricher,
)
from scripts.utils.console_ui_enricher import ConsoleUIEnricher
from scripts.utils.json_writer import write_json_file
from scripts.utils.source_graph_validator import (
    select_source_specs,
    source_spec_files,
    validate_source_graph,
)
from scripts.utils.spec_batch import publish_spec_batch
from scripts.utils.technical_text import prose_target_fields

console = Console()


def _packaged_enrichment_config() -> dict[str, Any]:
    """Compose standalone enrichment with packaged opt-in discovery controls."""
    config = load_packaged_yaml("enrichment.yaml")
    discovery = load_packaged_yaml("discovery_enrichment.yaml")
    execution = discovery["discovery_enrichment"]
    config["discovery_enrichment"] = {
        "enabled": execution["enabled"],
        "discovered_specs_dir": execution["discovered_specs_dir"],
    }
    return config


# Public for processing APIs and tests; its bytes come only from the packaged YAML.
DEFAULT_CONFIG = _packaged_enrichment_config()


def require_noncanonical_discovery_output(
    output_dir: Path,
    canonical_output_dir: Path,
    *,
    explicitly_selected: bool,
) -> None:
    """Keep live discovery evidence outside the publishable generated tree."""
    if not explicitly_selected:
        raise ValueError("discovery enrichment requires an explicit noncanonical --output-dir")
    output = output_dir.resolve()
    canonical = canonical_output_dir.resolve()
    if output == canonical or canonical in output.parents:
        raise ValueError("discovery enrichment cannot write canonical publishable output")


_ALLOWED_CONFIG_SECTIONS = frozenset(
    {
        "branding",
        "changelog",
        "consistency_validation",
        "deprecated_tiers",
        "description_structure",
        "description_validation",
        "discovery_enrichment",
        "grammar",
        "output",
        "paths",
        "preserve_fields",
        "processing",
        "schema_fixes",
        "tags",
        "target_fields",
    }
)
_STRICT_CONFIG_SECTIONS = {
    "paths": frozenset({"original", "enriched", "reports", "discovered"}),
    "branding": frozenset({"protected_patterns", "replacements"}),
    "grammar": frozenset(
        {
            "capitalize_sentences",
            "ensure_punctuation",
            "normalize_whitespace",
            "fix_double_spaces",
            "trim_whitespace",
        }
    ),
    "description_structure": frozenset(
        {
            "normalize_leading_spaces",
            "preserve_bullet_indentation",
            "extract_examples",
            "remove_extracted_examples",
            "extract_validation_rules",
            "remove_extracted_validation",
            "extract_required",
            "remove_extracted_required",
        }
    ),
    "schema_fixes": frozenset(
        {"fix_format_without_type", "format_type_mapping", "rename_properties"}
    ),
    "tags": frozenset({"generate_metadata", "assign_to_operations"}),
    "description_validation": frozenset(
        {
            "auto_generate_operation_descriptions",
            "auto_generate_schema_descriptions",
            "description_prefix",
        }
    ),
    "consistency_validation": frozenset(
        {
            "validate_parameters",
            "validate_schemas",
            "validate_operation_ids",
            "severity_threshold",
        }
    ),
    "changelog": frozenset({"generate_diff", "diff_format", "detailed_changes"}),
    "processing": frozenset({"parallel_workers", "batch_size", "log_level"}),
    "output": frozenset({"json_indent", "sort_keys", "preserve_filenames"}),
    "discovery_enrichment": frozenset({"enabled", "discovered_specs_dir"}),
    "deprecated_tiers": frozenset({"enabled", "patterns", "transformations", "valid_tiers"}),
}
_OBSOLETE_CONFIG_KEYS = frozenset(
    {
        "continue_on_error",
        "create_missing_components",
        "create_stub_component",
        "fail_on_error",
        "fix_orphan_refs",
        "inline_orphan_request_bodies",
        "remove_empty_operations",
        "remove_orphan_operations",
        "remove_ref_siblings",
        "validate_after_enrichment",
    }
)

# Cache for discovery enricher singleton (loaded once, reused)
_DISCOVERY_CACHE: dict[str, Any] = {"enricher": None, "config": None, "signature": None}
_DISCOVERY_INPUT_NAMES = ("openapi.json", "session.json")


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
    """Load packaged enrichment configuration with an optional validated overlay."""
    canonical = _packaged_enrichment_config()
    overlay: dict[str, Any] = {}
    if config_path is not None:
        if not config_path.is_file():
            raise FileNotFoundError(f"enrichment configuration not found: {config_path}")
        with config_path.open() as f:
            document = yaml.safe_load(f)
        if document is not None:
            if not isinstance(document, dict):
                raise TypeError("enrichment configuration must be an object")
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
    """Reject unsupported execution controls instead of silently ignoring them."""
    if not isinstance(config, dict):
        raise TypeError("enrichment configuration must be an object")
    unknown_sections = sorted(set(config) - _ALLOWED_CONFIG_SECTIONS)
    if unknown_sections:
        raise ValueError(f"unsupported enrichment configuration sections: {unknown_sections}")

    def reject_obsolete(value: Any, location: str) -> None:
        if isinstance(value, dict):
            obsolete = sorted(set(value) & _OBSOLETE_CONFIG_KEYS)
            if obsolete:
                raise ValueError(
                    f"obsolete enrichment configuration keys at {location}: {obsolete}"
                )
            for key, child in value.items():
                reject_obsolete(child, f"{location}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reject_obsolete(child, f"{location}[{index}]")

    reject_obsolete(config, "config")

    for section, allowed_keys in _STRICT_CONFIG_SECTIONS.items():
        if section not in config:
            continue
        section_config = config[section]
        if not isinstance(section_config, dict):
            raise TypeError(f"enrichment configuration {section!r} must be an object")
        unknown_keys = sorted(set(section_config) - allowed_keys)
        if unknown_keys:
            raise ValueError(
                f"unsupported enrichment configuration keys in {section!r}: {unknown_keys}"
            )


def load_discovery_enricher(config: dict) -> DiscoveryEnricher | None:
    """Load discovery enricher with discovery data.

    Args:
        config: Enrichment configuration

    Returns:
        Initialized DiscoveryEnricher, or ``None`` only when explicitly disabled.
    """
    discovery_config = config["discovery_enrichment"]
    if not discovery_config["enabled"]:
        return None

    discovered_dir = Path(discovery_config["discovered_specs_dir"])

    if not discovered_dir.is_dir():
        raise FileNotFoundError(
            f"discovery enrichment is enabled but data is missing: {discovered_dir}"
        )

    # Load discovery enrichment config from separate file if exists
    document = load_packaged_yaml("discovery_enrichment.yaml")
    detailed_config = document["discovery_enrichment"]
    if not isinstance(detailed_config, dict):
        raise TypeError(
            "config/discovery_enrichment.yaml must define discovery_enrichment as an object"
        )
    discovery_config = _deep_merge(detailed_config, discovery_config)

    signature = _discovery_data_signature(discovered_dir)
    if (
        _DISCOVERY_CACHE["enricher"] is not None
        and _DISCOVERY_CACHE["config"] == discovery_config
        and _DISCOVERY_CACHE["signature"] == signature
    ):
        return _DISCOVERY_CACHE["enricher"]

    enricher = DiscoveryEnricher(discovery_config)

    try:
        enricher.load_discovery_data(discovered_dir)
    except Exception as error:
        raise RuntimeError(f"failed to load required discovery data: {error}") from error
    console.print(f"[green]Loaded discovery data from {discovered_dir}[/green]")
    _DISCOVERY_CACHE["enricher"] = enricher
    _DISCOVERY_CACHE["config"] = discovery_config
    _DISCOVERY_CACHE["signature"] = signature
    return enricher


def _discovery_data_signature(discovered_dir: Path) -> str:
    """Hash the exact required discovery inputs for the in-process cache."""
    digest = hashlib.sha256()
    paths = [discovered_dir / name for name in _DISCOVERY_INPUT_NAMES]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"required discovery inputs are missing: {missing}")
    for path in paths:
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


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
    validate_source_graph(spec)
    validate(spec)
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


def _findings_error(label: str, findings: list[dict[str, Any]]) -> ValueError:
    """Build a deterministic actionable error for validator findings."""
    sample = json.dumps(findings[:5], sort_keys=True, ensure_ascii=False)
    return ValueError(f"{label} found {len(findings)} issue(s); first findings: {sample}")


def _validate_enrichment_findings(
    spec: dict[str, Any],
    target_fields: list[str],
    consistency_validator: ConsistencyValidator,
) -> None:
    """Make every configured consistency and branding finding release-blocking."""
    consistency_findings = consistency_validator.validate(spec)
    if consistency_findings:
        raise _findings_error("consistency validation", consistency_findings)

    legacy_findings = BrandingValidator().validate_spec(spec, target_fields)
    if legacy_findings:
        raise _findings_error("branding validation", legacy_findings)


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
        _validate_config(config)
        spec = load_spec(spec_path)
        validate_source_graph(spec)
        validate(spec)
        original_field_count = count_text_fields(
            spec,
            prose_target_fields(config["target_fields"]),
        )

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
        with packaged_config_path("constraint_patterns.yaml") as constraint_config_path:
            constraint_enricher = ConstraintEnricher(config_path=constraint_config_path)

        grammar_config = config["grammar"]
        grammar_improver = GrammarImprover(
            capitalize_sentences=grammar_config["capitalize_sentences"],
            ensure_punctuation=grammar_config["ensure_punctuation"],
            normalize_whitespace=grammar_config["normalize_whitespace"],
            fix_double_spaces=grammar_config["fix_double_spaces"],
            trim_whitespace=grammar_config["trim_whitespace"],
        )

        target_fields = config["target_fields"]
        prose_fields = prose_target_fields(target_fields)

        # Initialize XKS/XCS branding normalizer
        branding_normalizer = BrandingNormalizer()

        # Apply enrichments in order
        # 0. Deprecated tier transformation (BASIC→STANDARD, PREMIUM→ADVANCED)
        spec = deprecated_tier_enricher.enrich(spec)

        # 1. Branding transformations first (most specific)
        # 1a. Legacy Volterra→F5 branding
        spec = branding_transformer.transform_spec(spec, prose_fields)
        # 1b. Industry-standard XKS/XCS terminology normalization
        spec = branding_normalizer.normalize_spec(spec, prose_fields)

        # 2. Description structure normalization (extract examples, validation rules, X-required)
        spec = description_structure_transformer.transform_spec(spec, prose_fields)

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
        spec = acronym_normalizer.normalize_spec(spec, prose_fields)

        # 6. Grammar improvements
        spec = grammar_improver.improve_spec(spec, prose_fields)

        # 7. Description validation and generation (auto-generate missing descriptions)
        spec = description_validator.validate_and_generate(spec)

        # 9. Discovery enrichment (add x-discovered-* extensions)
        discovery_enrichments = 0
        discovery_enricher = load_discovery_enricher(config)
        if discovery_enricher:
            spec = discovery_enricher.enrich_with_discoveries(spec)
            discovery_stats = discovery_enricher.get_stats()
            discovery_enrichments = discovery_stats.get("fields_enriched", 0)

        # Normalize descriptions produced by late enrichers before the final
        # fail-closed validators. Acronym normalization runs first so direct
        # source forms and any normalized VES-I/O prose reach the branding pass.
        spec = acronym_normalizer.normalize_spec(spec, prose_fields)
        spec = branding_normalizer.normalize_spec(spec, prose_fields)
        spec = branding_transformer.transform_spec(spec, prose_fields)

        _validate_enrichment_findings(spec, prose_fields, consistency_validator)

        output_config = config["output"]
        save_spec(
            spec,
            output_path,
            indent=output_config["json_indent"],
            sort_keys=output_config["sort_keys"],
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
                "legacy_branding_remaining": 0,
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
            validation_passed=True,
        )

    except Exception as error:
        return EnrichmentResult(
            filename=filename,
            success=False,
            error=str(error),
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

    _validate_config(config)
    selection = select_source_specs(input_dir)
    spec_files = list(selection.files)
    if not spec_files:
        console.print(f"[yellow]No specification files found in {input_dir}[/yellow]")
        return stats

    console.print(f"[blue]Found {len(spec_files)} specification files to enrich[/blue]")

    if config["discovery_enrichment"]["enabled"]:
        try:
            load_discovery_enricher(config)
        except Exception as error:
            stats.files_processed = len(spec_files)
            stats.errors.append({"file": "<discovery>", "error": str(error)})
            _abort_enrichment_batch(stats, "required discovery data could not be loaded")
            return stats

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
            task = progress.add_task("Enriching specifications...", total=len(spec_files))

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

        if (
            stats.files_failed
            or stats.validation_failed
            or stats.errors
            or stats.files_succeeded != stats.files_processed
        ):
            _abort_enrichment_batch(stats, "one or more specifications failed validation")
            return stats

        try:
            publish_spec_batch(staging_dir, output_dir, selection)
        except Exception as error:
            stats.errors.append({"file": "<publication>", "error": str(error)})
            _abort_enrichment_batch(stats, "transactional publication failed")

    return stats


def _abort_enrichment_batch(stats: EnrichmentStats, reason: str) -> None:
    """Report a transaction abort without claiming unpublished partial successes."""
    stats.errors.append({"file": "<batch>", "error": f"enrichment transaction aborted: {reason}"})
    stats.files_failed = stats.files_processed
    stats.files_succeeded = 0
    stats.validation_failed = stats.files_processed
    stats.validation_passed = 0
    stats.acronyms_normalized = 0
    stats.grammar_improved = 0
    stats.branding_transformed = 0
    stats.schemas_fixed = 0
    stats.descriptions_generated = 0
    stats.required_fields_extracted = 0
    stats.consistency_issues = 0
    stats.discovery_enrichments = 0


def _update_stats(stats: EnrichmentStats, result: EnrichmentResult) -> None:
    """Update statistics from an enrichment result."""
    if result.success:
        stats.files_succeeded += 1
        stats.schemas_fixed += result.changes.get("schemas_fixed", 0)
        stats.descriptions_generated += result.changes.get("descriptions_generated", 0)
        stats.consistency_issues += result.changes.get("consistency_issues", 0)
        stats.discovery_enrichments += result.changes.get("discovery_enrichments", 0)
        if result.validation_passed:
            stats.validation_passed += 1
        else:
            stats.validation_failed += 1
            if result.error:
                stats.errors.append({"file": result.filename, "error": result.error})
    else:
        stats.files_failed += 1
        if not result.validation_passed:
            stats.validation_failed += 1
        if result.error:
            stats.errors.append({"file": result.filename, "error": result.error})


def generate_report(stats: EnrichmentStats, output_path: Path) -> None:
    """Generate enrichment report."""
    report = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
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
        validate_source_graph(spec)
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
        help="Path to configuration file (default: packaged config/enrichment.yaml)",
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
    canonical_output_dir = Path(config["paths"]["enriched"])
    output_dir = args.output_dir or canonical_output_dir
    report_dir = args.report_dir or Path(config["paths"]["reports"])

    # Override workers if specified
    if args.workers:
        config["processing"]["parallel_workers"] = args.workers

    # Enable discovery enrichment if requested
    if args.use_discovery:
        config["discovery_enrichment"]["enabled"] = True

    console.print("[bold blue]F5 XC API Specification Enrichment[/bold blue]")
    console.print(f"  Input:  {input_dir}")
    console.print(f"  Output: {output_dir}")

    if config["discovery_enrichment"]["enabled"]:
        try:
            require_noncanonical_discovery_output(
                output_dir,
                canonical_output_dir,
                explicitly_selected=args.output_dir is not None,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            return 2
        console.print("  [green]Discovery enrichment: enabled[/green]")

    if args.validate_only:
        # Just validate existing enriched specs
        console.print("\n[blue]Validating existing enriched specifications...[/blue]")
        if not output_dir.exists():
            console.print(f"[red]Enriched specification directory not found: {output_dir}[/red]")
            return 1
        spec_files = source_spec_files(output_dir)
        if not spec_files:
            console.print(f"[red]No OpenAPI specifications found in {output_dir}[/red]")
            return 1
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

    if not input_dir.exists():
        console.print(f"[red]Input directory not found: {input_dir}[/red]")
        console.print(
            "[yellow]Run 'python scripts/download.py' first to download specifications[/yellow]",
        )
        return 1

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

    if (
        stats.files_processed == 0
        or stats.files_failed > 0
        or stats.validation_failed > 0
        or stats.errors
        or stats.files_succeeded != stats.files_processed
    ):
        console.print(f"\n[red]Enrichment failed for {stats.files_failed} files[/red]")
        return 1

    console.print(
        f"\n[bold green]Successfully enriched {stats.files_succeeded} specifications![/bold green]",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
