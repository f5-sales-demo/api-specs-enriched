#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unified F5 XC API Enrichment Pipeline.

Single command to process all specifications from original → enriched.
Combines enrich, normalize, and merge steps into one atomic operation.
Outputs ONLY merged domain specs (no individual files).

Pipeline flow:
    specs/original/ (READ-ONLY)
        ↓
    [Enrich: branding, acronyms, grammar] (in memory)
        ↓
    [Normalize: fix $refs, clean operations] (in memory)
        ↓
    [Merge: combine by domain]
        ↓
    docs/specifications/api/
        ├── api_security.json
        ├── applications.json
        ├── bigip.json
        ├── billing.json
        ├── cdn.json
        ├── config.json
        ├── identity.json
        ├── infrastructure.json
        ├── infrastructure_protection.json
        ├── load_balancer.json
        ├── networking.json
        ├── nginx.json
        ├── observability.json
        ├── other.json
        ├── security.json
        ├── service_mesh.json
        ├── shape_security.json
        ├── subscriptions.json
        ├── tenant_management.json
        ├── vpn.json
        ├── openapi.json    (master combined spec)
        └── index.json      (spec metadata)

Usage:
    python -m scripts.pipeline --version 2.1.208              # Full pipeline
    python -m scripts.pipeline --version 2.1.208 --dry-run    # Analyze without writing
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import re
import shutil
import sys
import tempfile
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from openapi_spec_validator import validate as validate_openapi
from openapi_spec_validator.validation.exceptions import (
    OpenAPIValidationError,
    ValidatorDetectError,
)
from rich.console import Console
from rich.table import Table

# Import processing modules
from scripts.enrich import _validate_config as _validate_enrichment_config
from scripts.normalize import _validate_config as _validate_normalization_config
from scripts.package_config import load_packaged_yaml, packaged_config_path
from scripts.utils import (
    AcronymEnricher,
    AcronymNormalizer,
    BestPracticesEnricher,
    BrandingTransformer,
    BrandingValidator,
    ConflictsWithEnricher,
    ConsistencyValidator,
    ConstrainedFieldsEnricher,
    ConstraintEnricher,
    DefaultValueEnricher,
    DependencyEnricher,
    DescriptionEnricher,
    DescriptionStructureTransformer,
    DescriptionValidator,
    ErrorResolutionEnricher,
    ExampleFieldEnricher,
    ExternalDocsEnricher,
    FieldDescriptionEnricher,
    GrammarImprover,
    GuidedWorkflowEnricher,
    MinimumConfigurationEnricher,
    NamespaceProfileEnricher,
    OperationDescriptionEnricher,
    OperationMetadataEnricher,
    PropertyDescriptionShortEnricher,
    ReadOnlyEnricher,
    ReferencesEnricher,
    ResourceExamplesEnricher,
    SchemaConstraintProjector,
    SchemaFixer,
    SchemaOverrideEnricher,
    ValidationEnricher,
    ValidationExporter,
    categorize_spec,
)
from scripts.utils.batch_processor import BatchSpecProcessor
from scripts.utils.build_stamp import artifact_timestamp
from scripts.utils.component_canonicalization import (
    CanonicalizationResult,
    canonicalize_source_components,
)
from scripts.utils.critical_resources import load_critical_resources
from scripts.utils.domain_metadata import (
    calculate_complexity,
    get_domain_icon,
    get_metadata,
    get_primary_resources_metadata,
)
from scripts.utils.extension_constants import (
    X_F5XC_CATEGORY,
    X_F5XC_CLI_DOMAIN,
    X_F5XC_CLI_METADATA,
    X_F5XC_COMPLEXITY,
    X_F5XC_CRITICAL_RESOURCES,
    X_F5XC_DESCRIPTION_MEDIUM,
    X_F5XC_DESCRIPTION_SHORT,
    X_F5XC_ICON,
    X_F5XC_IS_PREVIEW,
    X_F5XC_LOGO_SVG,
    X_F5XC_OPERATION_ALIASES,
    X_F5XC_OPERATION_METADATA,
    X_F5XC_PRIMARY_RESOURCES,
    X_F5XC_RELATED_DOMAINS,
    X_F5XC_REQUIRES_TIER,
    X_F5XC_USE_CASES,
)
from scripts.utils.json_writer import write_json_file
from scripts.utils.memory_profiler import MemoryProfiler
from scripts.utils.minimal_defaults_exporter import MinimalDefaultsExporter
from scripts.utils.namespace_profiles_exporter import NamespaceProfilesExporter
from scripts.utils.resource_version_enricher import declare_resource_versions
from scripts.utils.server_variables import ServerVariableHelper
from scripts.utils.source_graph_validator import (
    HTTP_METHODS,
    SourceGraphValidationError,
    source_spec_files,
    validate_source_files,
    validate_source_graph,
)
from scripts.utils.technical_text import prose_target_fields

console = Console()
logger = logging.getLogger(__name__)


def _packaged_pipeline_config() -> dict[str, Any]:
    """Compose the pipeline contract from the packaged enrichment and normalization YAML."""
    config = load_packaged_yaml("enrichment.yaml")
    normalization = load_packaged_yaml("normalization.yaml")
    config["normalization"] = normalization["normalization"]
    return config


# Public for processing APIs and tests; its bytes come only from packaged YAML.
DEFAULT_CONFIG = _packaged_pipeline_config()


@dataclass
class PipelineStats:
    """Statistics for the complete pipeline run."""

    files_processed: int = 0
    files_succeeded: int = 0
    files_failed: int = 0
    enrichment_changes: int = 0
    normalization_changes: int = 0
    schemas_fixed: int = 0
    descriptions_generated: int = 0
    consistency_issues: int = 0
    minimum_configs_added: int = 0
    domains_created: int = 0
    paths_merged: int = 0
    schemas_merged: int = 0
    source_operations: int = 0
    canonical_operations: int = 0
    explicit_operation_aliases: int = 0
    resource_version_get_responses: int = 0
    resource_version_replace_requests: int = 0
    resource_version_declarations: int = 0
    component_occurrences: int = 0
    component_canonical_keys: int = 0
    component_conflict_name_groups: int = 0
    component_renamed_occurrences: int = 0
    component_shared_occurrences: int = 0
    component_accounting: dict[str, Any] = field(default_factory=dict)
    naming_constraints_projected: int = 0
    best_practices_enriched: int = 0
    guided_workflows_added: int = 0
    error_resolutions_added: int = 0
    conflicts_with_added: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


def load_config(config_path: Path | None = None) -> dict:
    """Load packaged pipeline configuration with an optional validated overlay."""
    canonical = _packaged_pipeline_config()
    overlay: dict[str, Any] = {}
    if config_path is not None:
        if not config_path.is_file():
            raise FileNotFoundError(f"pipeline configuration not found: {config_path}")
        with config_path.open() as f:
            document = yaml.safe_load(f)
        if document is not None:
            if not isinstance(document, dict):
                raise TypeError("pipeline configuration must be an object")
            overlay = document
    config = _deep_merge(canonical, overlay)
    _validate_pipeline_config(config)
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


def _validate_pipeline_config(config: dict[str, Any]) -> None:
    """Validate the composed enrichment and normalization contract."""
    normalization = config["normalization"]
    enrichment = {key: value for key, value in config.items() if key != "normalization"}
    _validate_enrichment_config(enrichment)
    _validate_normalization_config({"normalization": normalization})


def load_spec(spec_path: Path) -> dict[str, Any]:
    """Load an OpenAPI specification from JSON file."""
    with spec_path.open() as f:
        return json.load(f)


def save_spec(spec: dict[str, Any], output_path: Path, indent: int = 2) -> None:
    """Save an OpenAPI specification to JSON file.

    Runs ``SchemaFixer.inject_max_items`` as the last step before
    serialization so Checkov CKV_OPENAPI_21 passes on the committed
    JSON without the synthetic bound leaking into ``x-f5xc-constraints``
    (ConstraintEnricher has already run at this point). Delegates to
    ``write_json_file``, which applies Biome formatting so the output
    satisfies Super-Linter's BIOME_FORMAT check at commit time.
    """
    spec = SchemaFixer().inject_max_items(spec)
    write_json_file(spec, output_path, indent=indent, ensure_ascii=False)


# =============================================================================
# ENRICHMENT FUNCTIONS
# =============================================================================


def enrich_spec(spec: dict[str, Any], config: dict) -> tuple[dict[str, Any], dict[str, int]]:
    """Apply enrichment transformations to a specification.

    Returns (enriched_spec, stats_dict) where stats_dict contains:
        - field_count: number of text fields processed
        - schemas_fixed: number of schemas fixed by SchemaFixer
        - descriptions_generated: number of descriptions auto-generated
        - consistency_issues: number of consistency issues found
        - domains_normalized: number of domain names normalized (RFC 2606)
    """
    # `title` is INTENTIONALLY omitted from the default list. Title is a
    # metadata field that downstream codegens and doc tools compare
    # byte-for-byte against upstream; rewriting it breaks those tools.
    # See design spec 2026-04-22 §3.1.
    target_fields = config["target_fields"]
    prose_fields = prose_target_fields(target_fields)
    grammar_config = config["grammar"]

    # Initialize enrichment utilities
    acronym_normalizer = AcronymNormalizer()
    branding_transformer = BrandingTransformer()
    description_structure_transformer = DescriptionStructureTransformer()
    grammar_improver = GrammarImprover(
        capitalize_sentences=grammar_config["capitalize_sentences"],
        ensure_punctuation=grammar_config["ensure_punctuation"],
        normalize_whitespace=grammar_config["normalize_whitespace"],
        fix_double_spaces=grammar_config["fix_double_spaces"],
        trim_whitespace=grammar_config["trim_whitespace"],
    )
    schema_fixer = SchemaFixer()
    description_validator = DescriptionValidator()
    consistency_validator = ConsistencyValidator()

    # Count fields before
    field_count = _count_text_fields(spec, prose_fields)

    # Phase 1: Upstream validation (assert upstream-injected fields exist)
    upstream_warnings = validate_upstream_spec(spec)
    for warning in upstream_warnings:
        logger.warning(warning)

    # Apply enrichments in order:
    # 0. Correct upstream requiredness markers BEFORE anything derives from them.
    #    x-ves-required is F5's own marker and is wrong in both directions (#1142);
    #    step 16 below derives x-f5xc-required-for.create from it, so a correction
    #    applied later in the merge phase would fix the marker and leave the derived
    #    field asserting the opposite. corrections_only=True so the additive halves
    #    stay in the merge phase: injecting a property this early runs it through the
    #    whole enrichment chain, which wrapped an injected bare $ref in allOf and
    #    changed a shape downstream codegen special-cases.
    spec = SchemaOverrideEnricher().enrich_spec(spec, corrections_only=True)

    # 1. Branding transformations first (most specific)
    spec = branding_transformer.transform_spec(spec, prose_fields)

    # 2. Description structure normalization (extract examples, validation rules)
    spec = description_structure_transformer.transform_spec(spec, prose_fields)

    # 3. Acronym normalization
    spec = acronym_normalizer.normalize_spec(spec, prose_fields)

    # 4. Grammar improvements
    spec = grammar_improver.improve_spec(spec, prose_fields)

    # 5. Sanitize script tags in descriptions (prevent Spectral security warnings)
    spec, _ = _sanitize_script_tags(spec, prose_fields)

    # 6. Normalize domain names (RFC 2606 compliance + lowercase hostnames)
    spec, domain_normalize_count = _normalize_domain_names(spec, prose_fields)

    # 7. Schema fixes (fix format-without-type issues)
    spec = schema_fixer.fix_spec(spec)
    schema_stats = schema_fixer.get_stats()

    # 8. Description validation (auto-generate missing descriptions)
    spec = description_validator.validate_and_generate(spec)
    desc_stats = description_validator.get_stats()

    # 10. Consistency validation (report issues without auto-fixing)
    consistency_validator.validate(spec)
    consistency_stats = consistency_validator.get_stats()

    # 11. Field-level description enrichment (add realistic descriptions and examples)
    field_description_enricher = FieldDescriptionEnricher()
    spec = field_description_enricher.enrich_spec(spec)
    field_desc_stats = field_description_enricher.get_stats()
    print(f"DEBUG: Field description enricher stats: {field_desc_stats}")

    # 12. Property short description enrichment (Issue #330)
    # Generate 80-150 char descriptions for properties with long descriptions (>300 chars)
    prop_desc_short_enricher = PropertyDescriptionShortEnricher()
    spec = prop_desc_short_enricher.enrich_spec(spec)
    prop_desc_short_stats = prop_desc_short_enricher.get_stats()

    # 13. Field-level validation rule enrichment (add min/max, patterns, formats)
    validation_enricher = ValidationEnricher()
    spec = validation_enricher.enrich_spec(spec)
    validation_stats = validation_enricher.get_stats()

    # 13.5. Constraint enrichment (add x-f5xc-constraints from patterns)
    with packaged_config_path("constraint_patterns.yaml") as constraint_config_path:
        constraint_enricher = ConstraintEnricher(config_path=constraint_config_path)
    spec = constraint_enricher.enrich_spec(spec)
    constraint_stats = constraint_enricher.get_stats()

    # 14. Operation description enrichment (DRY-compliant, noun-first purpose descriptions)
    operation_description_enricher = OperationDescriptionEnricher()
    spec = operation_description_enricher.enrich_spec(spec)
    op_desc_stats = operation_description_enricher.get_stats()

    # 15. Operation metadata enrichment (add danger levels, required fields, side effects)
    operation_metadata_enricher = OperationMetadataEnricher()
    spec = operation_metadata_enricher.enrich_spec(spec)
    op_stats = operation_metadata_enricher.get_stats()

    # 16. Minimum configuration enrichment (add x-ves-minimum-configuration extensions)
    minimum_config_enricher = MinimumConfigurationEnricher()
    spec = minimum_config_enricher.enrich_spec(spec)
    min_config_stats = minimum_config_enricher.get_stats()

    # 17. ReadOnly field enrichment (mark API-computed fields as readOnly)
    readonly_enricher = ReadOnlyEnricher()
    spec = readonly_enricher.enrich_spec(spec)
    readonly_stats = readonly_enricher.get_stats()

    # Note: Namespace profile enrichment runs in merge_specs_by_domain() since
    # the pipeline merges individual specs into domain files and creates new info sections.

    # Note: Server-applied default value enrichment runs in merge_specs_by_domain() (Issue #449)
    # because it requires merged schemas - individual specs don't have the full resource schemas.

    # Note: Best practices and guided workflow enrichment moved to merge_specs_by_domain()
    # These enrichers require domain context which is only available after merging.
    # See Issue #314 for details.

    # Sanitize after every enricher that can create example metadata. This pass
    # is intentionally narrower than the prose pipeline so API-shaped values
    # are otherwise preserved byte-for-byte.
    spec = _sanitize_documentation_examples(spec, branding_transformer)

    return spec, {
        "field_count": field_count,
        "schemas_fixed": schema_stats.get("fixes_applied", 0),
        "descriptions_generated": desc_stats.get("operations_generated", 0)
        + desc_stats.get("schemas_generated", 0),
        "consistency_issues": consistency_stats.get("total_issues", 0),
        "domains_normalized": domain_normalize_count,
        "field_descriptions_added": field_desc_stats.get("descriptions_added", 0),
        "field_examples_added": field_desc_stats.get("examples_added", 0),
        "short_descriptions_added": prop_desc_short_stats.get("short_descriptions_added", 0),
        "short_descriptions_from_extraction": prop_desc_short_stats.get(
            "descriptions_from_extraction",
            0,
        ),
        "short_descriptions_from_config": prop_desc_short_stats.get(
            "descriptions_from_config",
            0,
        ),
        "validation_rules_added": validation_stats.get("patterns_added", 0),
        "validation_constraints_added": validation_stats.get("constraints_added", 0),
        "operation_descriptions_applied": op_desc_stats.get("descriptions_applied", 0),
        "operation_desc_exact_matches": op_desc_stats.get("exact_matches", 0),
        "operation_desc_pattern_matches": op_desc_stats.get("pattern_matches", 0),
        "operation_desc_method_fallbacks": op_desc_stats.get("method_fallbacks", 0),
        "operations_enriched": op_stats.get("operations_enriched", 0),
        "required_fields_added": op_stats.get("required_fields_added", 0),
        "danger_levels_assigned": op_stats.get("danger_levels_assigned", 0),
        "side_effects_documented": op_stats.get("side_effects_documented", 0),
        "minimum_configs_added": min_config_stats.get("minimum_configs_added", 0),
        "readonly_fields_marked": readonly_stats.get("total_fields_marked", 0),
        "readonly_metadata_schemas": readonly_stats.get("metadata_schemas_matched", 0),
        "readonly_objectref_schemas": readonly_stats.get("object_ref_schemas_matched", 0),
        "constraints_added": constraint_stats.get("constraints_added", 0),
        "constraint_coverage": constraint_stats.get("coverage_percentage", 0),
        "constraint_pattern_matches": constraint_stats.get("pattern_matches", 0),
        "constraint_avg_confidence": constraint_stats.get("average_confidence", 0),
        # Note: namespace_profiles_added stats tracked in merge_specs_by_domain()
        # Note: best_practices, guided_workflows, and server_defaults stats tracked
        # in merge_specs_by_domain() since they require merged schemas
    }


def _count_text_fields(spec: dict[str, Any], target_fields: list[str]) -> int:
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


# =============================================================================
# NORMALIZATION FUNCTIONS
# =============================================================================


def _remove_ref_siblings(spec: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Remove non-compliant siblings from $ref objects.

    OAS3 requires $ref to stand alone. When enrichment annotations (x-*
    vendor extensions or 'default') are present, the $ref is wrapped in
    allOf so annotations become valid schema-level properties.

    Returns (modified_spec, count_of_refs_wrapped).
    """
    wrapped_count = 0

    def clean_recursive(obj: Any) -> Any:
        nonlocal wrapped_count

        if isinstance(obj, dict):
            if "$ref" in obj:
                extras: dict[str, Any] = {}
                for key, value in obj.items():
                    if key != "$ref" and (key.startswith("x-") or key == "default"):
                        extras[key] = clean_recursive(value)
                if extras:
                    wrapped_count += 1
                    result: dict[str, Any] = {"allOf": [{"$ref": obj["$ref"]}]}
                    result.update(extras)
                    return result
                return {"$ref": obj["$ref"]}

            result = {}
            for key, value in obj.items():
                result[key] = clean_recursive(value)
            return result

        if isinstance(obj, list):
            return [clean_recursive(item) for item in obj]

        return obj

    cleaned_spec = clean_recursive(spec)
    return cleaned_spec, wrapped_count


def normalize_spec(spec: dict[str, Any], config: dict) -> tuple[dict[str, Any], dict[str, int]]:
    """Apply normalization to fix structural issues.

    Returns (normalized_spec, stats_dict).
    """
    norm_config = config["normalization"]
    stats: dict[str, int] = {
        "ref_siblings_removed": 0,
        "types_normalized": 0,
    }

    # 0. Remove properties that are siblings to $ref (OpenAPI compliance)
    spec, count = _remove_ref_siblings(spec)
    stats["ref_siblings_removed"] = count

    # 1. Normalize types. Source graph defects are rejected before enrichment;
    # normalization must never fabricate or discard API contracts.
    if norm_config["type_standardization"]:
        spec, count = _normalize_types(spec)
        stats["types_normalized"] = count

    return spec, stats


def _normalize_types(spec: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Standardize type values to lowercase."""
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


def _normalize_domain_names(
    spec: dict[str, Any],
    target_fields: list[str],
) -> tuple[dict[str, Any], int]:
    """Normalize domain names in documentation to RFC 2606 compliant examples.

    RFC 2606 reserves specific domains for documentation:
    - example.com, example.org, example.net
    - *.example (for any TLD)

    This function:
    1. Replaces non-compliant domains (foo.com, bar.com, etc.) with example.com
    2. Normalizes DNS hostnames to lowercase (Www.Example.com -> www.example.com)

    Args:
        spec: OpenAPI specification dictionary.
        target_fields: List of field names to process (e.g., description, summary).

    Returns:
        Tuple of (modified_spec, normalize_count).
    """
    normalize_count = 0

    # Non-RFC compliant domains to replace with example.com
    non_compliant_domains = [
        r"\bfoo\.com\b",
        r"\bbar\.com\b",
        r"\bbaz\.com\b",
        r"\btest\.com\b",
        r"\bdemo\.com\b",
        r"\bsample\.com\b",
        r"\bmysite\.com\b",
        r"\bmydomain\.com\b",
        r"\byourdomain\.com\b",
        r"\byoursite\.com\b",
        r"\bacmecorp\.com\b",
        r"\bacme\.com\b",
    ]

    # Pattern to match URLs and normalize hostname case
    # Matches http(s)://HOSTNAME or just HOSTNAME patterns
    url_pattern = re.compile(
        r"(https?://)?([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,})",
        re.IGNORECASE,
    )

    def normalize_url_case(match: re.Match) -> str:
        """Normalize hostname portion of URL to lowercase."""
        protocol = match.group(1) or ""
        hostname = match.group(2)
        # Only lowercase the hostname, preserve the protocol case
        return protocol.lower() + hostname.lower()

    def normalize_text(text: str) -> tuple[str, int]:
        """Normalize domains and URL case in text."""
        changes = 0
        result = text

        # First, replace non-compliant domains with example.com
        for pattern in non_compliant_domains:
            new_result = re.sub(pattern, "example.com", result, flags=re.IGNORECASE)
            if new_result != result:
                changes += len(re.findall(pattern, result, flags=re.IGNORECASE))
                result = new_result

        # Then normalize URL/hostname case to lowercase
        # Find all URLs and check if any have uppercase
        matches = list(url_pattern.finditer(result))
        for match in reversed(matches):  # Reverse to preserve positions during replacement
            original = match.group(0)
            normalized = normalize_url_case(match)
            if original != normalized:
                result = result[: match.start()] + normalized + result[match.end() :]
                changes += 1

        return result, changes

    def normalize_recursive(obj: Any) -> Any:
        nonlocal normalize_count

        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                if key in target_fields and isinstance(value, str):
                    normalized, changes = normalize_text(value)
                    result[key] = normalized
                    normalize_count += changes
                else:
                    result[key] = normalize_recursive(value)
            return result

        if isinstance(obj, list):
            return [normalize_recursive(item) for item in obj]

        return obj

    return normalize_recursive(spec), normalize_count


_EMBEDDED_EXAMPLE_PATTERN = re.compile(
    r"(?im)(\bx-example:\s*)(?P<value>[^\r\n]+)",
)


def _sanitize_documentation_examples(
    spec: dict[str, Any],
    transformer: BrandingTransformer,
) -> dict[str, Any]:
    """Sanitize example metadata without running the general prose pipeline.

    Example values are API-shaped data, so grammar and acronym rewriting can
    corrupt them. Apply only configured documentation-value replacements and
    reserved-domain normalization, then handle examples embedded in titles.
    """
    example_fields = ["x-f5xc-example", "x-ves-example"]
    result = transformer.transform_spec(spec, example_fields)
    result, _ = _normalize_domain_names(result, example_fields)
    return _sanitize_embedded_examples(result, transformer)


def _sanitize_embedded_examples(
    spec: dict[str, Any],
    transformer: BrandingTransformer,
) -> dict[str, Any]:
    """Sanitize embedded ``x-example`` lines while preserving title metadata.

    Some upstream titles contain a multiline metadata block with an example
    value. Running the normal enrichment chain on ``title`` would rewrite API
    contract text, so only the value on an embedded ``x-example`` line is sent
    through the configured documentation-value transformations.
    """

    def sanitize_title(title: str) -> str:
        def replace(match: re.Match[str]) -> str:
            value = transformer.transform_text(
                match.group("value"),
                field_name="x-f5xc-example",
            )
            normalized, _ = _normalize_domain_names({"value": value}, ["value"])
            return f"{match.group(1)}{normalized['value']}"

        return _EMBEDDED_EXAMPLE_PATTERN.sub(replace, title)

    def sanitize_recursive(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                key: sanitize_title(value)
                if key == "title" and isinstance(value, str)
                else sanitize_recursive(value)
                for key, value in obj.items()
            }
        if isinstance(obj, list):
            return [sanitize_recursive(item) for item in obj]
        return obj

    return sanitize_recursive(spec)


# =============================================================================
# UPSTREAM VALIDATION
# =============================================================================


def validate_upstream_spec(spec: dict[str, Any]) -> list[str]:
    """Phase 1: Validate upstream-injected fields exist.

    Returns a list of warning strings. Empty list means all checks passed.
    Does NOT modify the spec — validation only.
    """
    warnings: list[str] = []

    if "contact" not in spec.get("info", {}):
        warnings.append("Upstream regression: info.contact is missing")

    servers = spec.get("servers", [])
    if not servers:
        warnings.append("Upstream regression: servers array is missing or empty")

    security_schemes = spec.get("components", {}).get("securitySchemes", {})
    if not security_schemes:
        warnings.append("Upstream regression: components.securitySchemes is missing")

    if not spec.get("security"):
        warnings.append("Upstream regression: global security array is missing")

    paths = spec.get("paths", {})
    ops_without_tags = []
    seen_ids: dict[str, str] = {}
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "delete", "patch", "head", "options"):
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            if not op.get("tags"):
                ops_without_tags.append(f"{method.upper()} {path}")
            op_id = op.get("operationId", "")
            if op_id:
                if op_id in seen_ids:
                    warnings.append(
                        f"Upstream regression: duplicate operationId '{op_id}' "
                        f"at {method.upper()} {path} and {seen_ids[op_id]}"
                    )
                seen_ids[op_id] = f"{method.upper()} {path}"

    if ops_without_tags:
        warnings.append(
            f"Upstream regression: {len(ops_without_tags)} operations missing tags (first: {ops_without_tags[0]})"
        )

    def _has_script_tags(obj: Any) -> bool:
        if isinstance(obj, str):
            return "<script" in obj.lower()
        if isinstance(obj, dict):
            return any(_has_script_tags(v) for v in obj.values())
        if isinstance(obj, list):
            return any(_has_script_tags(item) for item in obj)
        return False

    if _has_script_tags(spec.get("info", {})):
        warnings.append("Upstream regression: <script> tags found in info section")

    return warnings


def _sanitize_script_tags(
    spec: dict[str, Any],
    target_fields: list[str],
) -> tuple[dict[str, Any], int]:
    """Escape <script> tags from description fields.

    Spectral's no-script-tags-in-markdown rule flags descriptions containing
    <script> tags as a security warning. This function escapes them to HTML entities
    while preserving the documentation content.
    """
    sanitize_count = 0

    def sanitize_recursive(obj: Any) -> Any:
        nonlocal sanitize_count

        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                if key in target_fields and isinstance(value, str):
                    if "<script" in value.lower():
                        sanitized = re.sub(
                            r"<script",
                            "&lt;script",
                            value,
                            flags=re.IGNORECASE,
                        )
                        sanitized = re.sub(
                            r"</script>",
                            "&lt;/script&gt;",
                            sanitized,
                            flags=re.IGNORECASE,
                        )
                        result[key] = sanitized
                        sanitize_count += 1
                    else:
                        result[key] = value
                else:
                    result[key] = sanitize_recursive(value)
            return result

        if isinstance(obj, list):
            return [sanitize_recursive(item) for item in obj]

        return obj

    return sanitize_recursive(spec), sanitize_count


# =============================================================================
# MERGE FUNCTIONS
# =============================================================================


def _canonical_json(value: Any) -> str:
    """Serialize JSON with scalar types preserved for exact comparisons."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _insert_exact_component(
    target: dict[str, Any],
    category: str,
    name: str,
    value: Any,
    context: str,
) -> bool:
    """Insert one canonical component or reject a non-identical duplicate.

    Returns ``True`` when the key was inserted and ``False`` for an exact
    duplicate. Combining partial schemas is contract fabrication and is never
    permitted.
    """
    if name not in target:
        target[name] = copy.deepcopy(value)
        return True
    if _canonical_json(target[name]) != _canonical_json(value):
        raise ValueError(f"{context}: conflicting canonical components.{category}.{name}")
    return False


_OPERATION_ALIAS_EXTENSION = X_F5XC_OPERATION_ALIASES
_WIRE_OPERATION_FIELDS = frozenset(
    {"callbacks", "parameters", "requestBody", "responses", "security", "servers"},
)


class PathMemberConflictError(ValueError):
    """Raised when specifications define incompatible members at one API path."""


@dataclass(frozen=True)
class OperationAliasContract:
    """One explicitly approved pair of wire-equivalent operation identities."""

    path: str
    method: str
    canonical_operation_id: str
    alternate_operation_id: str
    canonical_service_identity: str
    alternate_service_identity: str

    @property
    def endpoint(self) -> tuple[str, str]:
        """Return the normalized path-and-method identity."""
        return self.path, self.method

    @property
    def operation_ids(self) -> frozenset[str]:
        """Return the exact two operation identities approved by this contract."""
        return frozenset({self.canonical_operation_id, self.alternate_operation_id})


def load_operation_aliases(
    config_path: Path | None = None,
) -> dict[tuple[str, str], OperationAliasContract]:
    """Load and strictly validate explicit operation aliases."""
    if config_path is None:
        document = load_packaged_yaml("operation_aliases.yaml")
    else:
        try:
            document = yaml.safe_load(config_path.read_text())
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise RuntimeError(f"cannot load operation aliases from {config_path}: {exc}") from exc

    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("operation alias config must be an object with version: 1")
    entries = document.get("aliases")
    if not isinstance(entries, list):
        raise TypeError("operation alias config aliases must be a list")

    required_fields = {
        "alternate_operation_id",
        "alternate_service_identity",
        "canonical_operation_id",
        "canonical_service_identity",
        "method",
        "path",
    }
    contracts: dict[tuple[str, str], OperationAliasContract] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != required_fields:
            raise ValueError(
                f"operation alias entry {index} must contain exactly {sorted(required_fields)}",
            )
        if any(not isinstance(entry[field], str) or not entry[field] for field in required_fields):
            raise ValueError(f"operation alias entry {index} fields must be non-empty strings")

        method = entry["method"].lower()
        if method not in HTTP_METHODS:
            raise ValueError(f"operation alias entry {index} has invalid HTTP method {method!r}")
        contract = OperationAliasContract(
            path=entry["path"],
            method=method,
            canonical_operation_id=entry["canonical_operation_id"],
            alternate_operation_id=entry["alternate_operation_id"],
            canonical_service_identity=entry["canonical_service_identity"],
            alternate_service_identity=entry["alternate_service_identity"],
        )
        if contract.canonical_operation_id == contract.alternate_operation_id:
            raise ValueError(f"operation alias entry {index} identities must differ")
        if contract.canonical_service_identity not in contract.canonical_operation_id:
            raise ValueError(f"operation alias entry {index} canonical identity is inconsistent")
        if contract.alternate_service_identity not in contract.alternate_operation_id:
            raise ValueError(f"operation alias entry {index} alternate identity is inconsistent")
        if contract.endpoint in contracts:
            raise ValueError(f"duplicate operation alias endpoint {contract.endpoint!r}")
        contracts[contract.endpoint] = contract

    return contracts


def _resolve_local_pointer(spec: dict[str, Any], ref: str) -> Any:
    """Resolve a local JSON Pointer or fail closed."""
    if not ref.startswith("#/"):
        raise PathMemberConflictError(f"wire-shape comparison rejects non-local $ref {ref!r}")
    current: Any = spec
    for encoded_token in ref[2:].split("/"):
        token = encoded_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise PathMemberConflictError(f"wire-shape comparison cannot resolve {ref!r}")
    return current


def _normalize_service_metadata(value: str, contract: OperationAliasContract | None) -> str:
    """Normalize the configured identity only in explicit non-wire metadata.

    Recursive schemas retain their reference identity in the resolved comparison so
    equivalent cycles terminate deterministically.  Resolved protobuf message names
    also retain their source service identity.  Callers must restrict this helper to
    those two metadata locations: payload keys and scalar values are wire data and
    must never be rewritten for an alias comparison.
    """
    if contract is None:
        return value
    return value.replace(
        contract.alternate_service_identity,
        contract.canonical_service_identity,
    )


def _resolved_wire_value(
    spec: dict[str, Any],
    value: Any,
    contract: OperationAliasContract | None,
    ref_stack: tuple[str, ...] = (),
) -> Any:
    """Resolve wire data recursively while ignoring documentation-only enrichment."""
    if isinstance(value, dict):
        if "$ref" in value:
            ref = value["$ref"]
            if not isinstance(ref, str):
                raise PathMemberConflictError("wire-shape comparison found a malformed $ref")
            if ref in ref_stack:
                return {"$recursiveRef": _normalize_service_metadata(ref, contract)}
            resolved = _resolved_wire_value(
                spec,
                _resolve_local_pointer(spec, ref),
                contract,
                (*ref_stack, ref),
            )
            siblings = {
                key: child
                for key, child in value.items()
                if key != "$ref" and not key.startswith("x-f5xc-")
            }
            if siblings:
                return {
                    "$resolved": resolved,
                    "$siblings": _resolved_wire_value(spec, siblings, contract, ref_stack),
                }
            return resolved

        resolved_object: dict[str, Any] = {}
        for key, child in value.items():
            if key.startswith("x-f5xc-"):
                continue
            if key == "x-ves-proto-message" and isinstance(child, str):
                resolved_object[key] = _normalize_service_metadata(child, contract)
            else:
                resolved_object[key] = _resolved_wire_value(spec, child, contract, ref_stack)
        return resolved_object
    if isinstance(value, list):
        return [_resolved_wire_value(spec, child, contract, ref_stack) for child in value]
    return value


def _operation_wire_shape(
    spec: dict[str, Any],
    operation: dict[str, Any],
    contract: OperationAliasContract | None = None,
) -> dict[str, Any]:
    """Return the recursively resolved request/response wire contract."""
    wire_operation = {
        key: value for key, value in operation.items() if key in _WIRE_OPERATION_FIELDS
    }
    return _resolved_wire_value(spec, wire_operation, contract)


def _operation_without_generated_alias(operation: dict[str, Any]) -> dict[str, Any]:
    """Return an operation without the extension generated by this merge."""
    return {key: value for key, value in operation.items() if key != _OPERATION_ALIAS_EXTENSION}


def _merge_path_member(
    *,
    path: str,
    member: str,
    existing: Any,
    existing_spec: dict[str, Any],
    incoming: Any,
    incoming_spec: dict[str, Any],
    aliases: dict[tuple[str, str], OperationAliasContract],
) -> tuple[Any, dict[str, Any]]:
    """Merge one path member without losing a conflicting API contract."""
    method = member.lower()
    if method not in HTTP_METHODS:
        if existing == incoming:
            return existing, existing_spec
        raise PathMemberConflictError(f"conflicting path member {member!r} at {path}")

    if not isinstance(existing, dict) or not isinstance(incoming, dict):
        raise PathMemberConflictError(f"malformed {method.upper()} operation collision at {path}")

    existing_base = _operation_without_generated_alias(existing)
    incoming_base = _operation_without_generated_alias(incoming)
    if existing_base == incoming_base:
        if _operation_wire_shape(existing_spec, existing_base) != _operation_wire_shape(
            incoming_spec,
            incoming_base,
        ):
            raise PathMemberConflictError(
                f"identical {method.upper()} operation at {path} resolves to divergent wire shapes",
            )
        return existing, existing_spec

    contract = aliases.get((path, method))
    existing_id = existing_base.get("operationId")
    incoming_id = incoming_base.get("operationId")
    if not isinstance(existing_id, str) or not isinstance(incoming_id, str):
        raise PathMemberConflictError(
            f"malformed {method.upper()} operation identity collision at {path}",
        )
    operation_ids = frozenset({existing_id, incoming_id})
    if contract is None or operation_ids != contract.operation_ids:
        raise PathMemberConflictError(
            f"unconfigured {method.upper()} operation collision at {path}: "
            f"{sorted(str(value) for value in operation_ids)!r}",
        )

    existing_shape = _operation_wire_shape(existing_spec, existing_base, contract)
    incoming_shape = _operation_wire_shape(incoming_spec, incoming_base, contract)
    if existing_shape != incoming_shape:
        raise PathMemberConflictError(
            f"configured {method.upper()} operation alias at {path} has divergent wire shapes",
        )

    if existing_base.get("operationId") == contract.canonical_operation_id:
        canonical_operation = existing_base
        canonical_spec = existing_spec
    else:
        canonical_operation = incoming_base
        canonical_spec = incoming_spec
    canonical_operation = copy.deepcopy(canonical_operation)
    canonical_operation[_OPERATION_ALIAS_EXTENSION] = [
        {
            "operationId": contract.alternate_operation_id,
            "relationship": "wire-equivalent",
        },
    ]
    return canonical_operation, canonical_spec


def _merge_path_item(
    target_paths: dict[str, Any],
    path: str,
    incoming_path_item: Any,
    incoming_spec: dict[str, Any],
    owners: dict[tuple[str, str], dict[str, Any]],
    aliases: dict[tuple[str, str], OperationAliasContract],
) -> int:
    """Merge one Path Item Object and return the number of members added."""
    if not isinstance(incoming_path_item, dict):
        raise PathMemberConflictError(f"path item at {path} must be an object")
    target_path_item = target_paths.setdefault(path, {})
    if not isinstance(target_path_item, dict):
        raise PathMemberConflictError(f"target path item at {path} must be an object")

    added = 0
    # Operations establish the canonical owner used for configured alias
    # metadata; JSON object key order must not affect that decision.
    members = sorted(
        incoming_path_item.items(),
        key=lambda item: (item[0].lower() not in HTTP_METHODS, item[0]),
    )
    for member, incoming in members:
        owner_key = (path, member)
        if member not in target_path_item:
            target_path_item[member] = copy.deepcopy(incoming)
            owners[owner_key] = incoming_spec
            added += 1
            continue

        if member.lower() not in HTTP_METHODS and target_path_item[member] != incoming:
            path_aliases = [contract for contract in aliases.values() if contract.path == path]
            if len(path_aliases) == 1 and (
                member.startswith("x-") or member in {"description", "summary"}
            ):
                contract = path_aliases[0]
                canonical_owner = owners.get((path, contract.method))
                if canonical_owner is owners[owner_key]:
                    continue
                if canonical_owner is incoming_spec:
                    target_path_item[member] = copy.deepcopy(incoming)
                    owners[owner_key] = incoming_spec
                    continue
        merged, owner = _merge_path_member(
            path=path,
            member=member,
            existing=target_path_item[member],
            existing_spec=owners[owner_key],
            incoming=incoming,
            incoming_spec=incoming_spec,
            aliases=aliases,
        )
        target_path_item[member] = merged
        owners[owner_key] = owner
    return added


def validate_operation_alias_accounting(
    specs: dict[str, dict[str, Any]],
    aliases: dict[tuple[str, str], OperationAliasContract] | None = None,
    *,
    require_all_aliases: bool = True,
) -> dict[str, int]:
    """Validate all source operation collisions and return identity accounting."""
    alias_contracts = aliases if aliases is not None else load_operation_aliases()
    occurrences: dict[tuple[str, str], list[tuple[str, dict[str, Any], dict[str, Any]]]] = (
        defaultdict(list)
    )
    identity_endpoints: dict[str, tuple[str, str]] = {}
    operation_count = 0
    for filename, spec in specs.items():
        for path, path_item in spec.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            for method in HTTP_METHODS:
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue
                operation_count += 1
                operation_id = operation.get("operationId")
                if not isinstance(operation_id, str) or not operation_id:
                    raise PathMemberConflictError(
                        f"{filename}: {method.upper()} {path} is missing operationId",
                    )
                endpoint = (path, method)
                previous_endpoint = identity_endpoints.setdefault(operation_id, endpoint)
                if previous_endpoint != endpoint:
                    raise PathMemberConflictError(
                        f"operationId {operation_id!r} is used by multiple endpoints",
                    )
                occurrences[endpoint].append((filename, operation, spec))

    exercised_aliases: set[tuple[str, str]] = set()
    explicit_aliases = 0
    identical_duplicates = 0
    for (path, method), group in sorted(occurrences.items()):
        if len(group) == 1:
            continue
        _, merged_operation, owner_spec = group[0]
        for _, operation, spec in group[1:]:
            before_id = merged_operation.get("operationId")
            merged_operation, owner_spec = _merge_path_member(
                path=path,
                member=method,
                existing=merged_operation,
                existing_spec=owner_spec,
                incoming=operation,
                incoming_spec=spec,
                aliases=alias_contracts,
            )
            if operation.get("operationId") == before_id:
                identical_duplicates += 1
            else:
                explicit_aliases += 1
                exercised_aliases.add((path, method))

    if require_all_aliases and exercised_aliases != set(alias_contracts):
        missing = sorted(set(alias_contracts) - exercised_aliases)
        unexpected = sorted(exercised_aliases - set(alias_contracts))
        raise PathMemberConflictError(
            f"operation alias allowlist does not match source collisions: "
            f"missing={missing}, unexpected={unexpected}",
        )

    return {
        "source_operations": operation_count,
        "unique_operation_ids": len(identity_endpoints),
        "canonical_operations": len(occurrences),
        "explicit_aliases": explicit_aliases,
        "identical_duplicates": identical_duplicates,
    }


def _validate_unique_merged_operation_ids(paths: dict[str, Any], context: str) -> None:
    """Reject one operationId being used by distinct merged endpoints."""
    identities: dict[str, tuple[str, str]] = {}
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id:
                raise PathMemberConflictError(
                    f"{context}: {method.upper()} {path} is missing operationId",
                )
            endpoint = (path, method)
            previous = identities.setdefault(operation_id, endpoint)
            if previous != endpoint:
                raise PathMemberConflictError(
                    f"{context}: operationId {operation_id!r} is used by multiple endpoints",
                )


def create_base_spec(title: str, description: str, version: str) -> dict[str, Any]:
    """Create a base OpenAPI specification structure.

    Delegates to ServerVariableHelper for centralized server variable management.
    """
    helper = ServerVariableHelper()
    return helper.create_base_spec(title, description, version)


def get_api_data_target_domain(path: str) -> str | None:
    """Determine the correct target domain for /api/data/ paths based on resource semantics.

    Args:
        path: API path to analyze

    Returns:
        Target domain name if path matches /api/data/ pattern, None otherwise
    """
    if "/api/data/" not in path:
        return None

    # Map resource patterns in /api/data/ paths to their semantic domains
    # Order matters: more specific patterns first
    data_routing = [
        (r"/app_security/", "virtual"),
        (r"/app_firewall/", "virtual"),
        (r"/dns_", "dns"),
        (r"/access_logs", "observability"),
        (r"/audit_logs", "observability"),
        (r"/alerts", "observability"),
        (r"/site/", "sites"),
        (r"/virtual_k8s/", "sites"),
        (r"/graph/site", "sites"),
        (r"/graph/connectivity", "telemetry_and_insights"),
        (r"/graph/service", "telemetry_and_insights"),
        (r"/graph/lb_cache", "telemetry_and_insights"),
        (r"/discovered_services/", "telemetry_and_insights"),
        (r"/status_at_site", "telemetry_and_insights"),
        (r"/flow", "telemetry_and_insights"),
        (r"/infraprotect/", "ddos"),
        (r"/network_policy", "network_security"),
        (r"/service_policy", "network_security"),
        (r"/forward_proxy_policy", "network_security"),
        (r"/fast_acl/", "network_security"),
        (r"/segments/", "network_security"),
        (r"/bigip/", "bigip"),
        (r"/workloads/", "container_services"),
        (r"/cloud_connects", "cloud_infrastructure"),
        (r"/nfv_services/", "service_mesh"),
        (r"/virtual_network/", "service_mesh"),
        (r"/dc_cluster_groups/", "network"),
        (r"/upgrade_status", "vpm_and_node_management"),
    ]

    for pattern, target_domain in data_routing:
        if re.search(pattern, path):
            return target_domain

    # Default: if no specific match, return None (let filename-based categorization handle it)
    return None


def add_domain_metadata_to_spec(spec: dict[str, Any], domain: str) -> None:
    """Add domain classification metadata to spec (idempotent).

    Adds x-f5xc-cli-domain extension to the spec's info section.
    Preserves existing values if already present (idempotent behavior).

    Args:
        spec: OpenAPI specification to enhance
        domain: Domain classification (e.g., "virtual", "cdn")
    """
    if "info" not in spec:
        spec["info"] = {}

    info = spec["info"]

    # Idempotent: preserve existing x-f5xc-cli-domain
    if X_F5XC_CLI_DOMAIN not in info:
        info[X_F5XC_CLI_DOMAIN] = domain


def _apply_merged_schema_enrichments(
    spec: dict[str, Any],
    *,
    allow_partition_residuals: bool,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Apply graph-wide schema enrichments to one independent merged graph."""
    stats: dict[str, int] = {}

    default_values = DefaultValueEnricher()
    spec = default_values.enrich_spec(
        spec,
        allow_partition_residuals=allow_partition_residuals,
    )
    default_stats = default_values.get_stats()
    if default_stats.get("error_count"):
        raise RuntimeError(f"default value enrichment failed: {default_stats.get('errors', [])}")
    stats["server_defaults_added"] = default_stats.get("defaults_added", 0) + default_stats.get(
        "nested_defaults_added", 0
    )
    stats["server_default_partition_residuals"] = len(
        default_stats.get("partition_residual_config_entries", [])
    )

    schema_overrides = SchemaOverrideEnricher()
    spec = schema_overrides.enrich_spec(spec)
    stats["schema_overrides_applied"] = schema_overrides.get_stats().get("properties_injected", 0)

    conflicts = ConflictsWithEnricher()
    spec = conflicts.enrich_spec(spec)
    stats["conflicts_with_added"] = conflicts.get_stats().get("conflicts_added", 0)

    dependencies = DependencyEnricher()
    spec = dependencies.enrich_spec(spec)
    stats["dependencies_added"] = dependencies.get_stats().get("dependencies_added", 0)

    references = ReferencesEnricher.from_config()
    spec = references.enrich_spec(spec)
    stats["references_stamped"] = references.get_stats().get("references_stamped", 0)

    examples = ExampleFieldEnricher()
    spec = examples.enrich_spec(spec)
    stats["example_fields_stamped"] = examples.get_stats().get("fields_stamped", 0)

    constrained = ConstrainedFieldsEnricher()
    spec = constrained.enrich_spec(spec)
    stats["constrained_fields_added"] = constrained.get_stats().get("constraints_applied", 0)

    namespace_profiles = NamespaceProfileEnricher()
    spec = namespace_profiles.enrich_spec(spec)
    namespace_stats = namespace_profiles.get_stats()
    if namespace_stats.get("errors"):
        raise RuntimeError(f"namespace profile enrichment failed: {namespace_stats['errors']}")
    stats["namespace_profiles_added"] = namespace_stats.get("specs_enriched", 0)

    spec, _ = _remove_ref_siblings(spec)
    projector = SchemaConstraintProjector()
    spec = projector.enrich_spec(spec)
    stats["naming_constraints_projected"] = projector.get_stats().get("properties_projected", 0)
    return spec, stats


def merge_specs_by_domain(
    specs: dict[str, dict[str, Any]],
    version: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Merge specifications grouped by domain.

    Rejects path-member conflicts and models only explicitly configured,
    wire-equivalent operation aliases.

    Returns (merged_specs_by_domain, stats).
    """
    operation_aliases = load_operation_aliases()
    alias_accounting = validate_operation_alias_accounting(specs, operation_aliases)

    # Group specs by domain
    domain_specs: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for filename, spec in specs.items():
        domain = categorize_spec(filename)
        domain_specs[domain].append((filename, spec))

        # Also add specs to CDN domain if they contain CDN-specific paths
        paths = spec.get("paths", {})
        has_cdn_paths = any("/api/cdn/" in p or "/cdn_loadbalancers/" in p for p in paths)
        if has_cdn_paths and domain != "cdn":
            domain_specs["cdn"].append((filename, spec))

        # Also add specs to data_intelligence domain if they contain data-intelligence paths
        has_di_paths = any("/api/data-intelligence/" in p for p in paths)
        if has_di_paths and domain != "data_intelligence":
            domain_specs["data_intelligence"].append((filename, spec))

        # Also add specs to threat_campaign domain if they contain threat_campaign/threat_mesh paths
        has_threat_campaign_paths = any(
            "/api/waf/threat_campaign" in p or "/threat_mesh" in p for p in paths
        )
        if has_threat_campaign_paths and domain != "threat_campaign":
            domain_specs["threat_campaign"].append((filename, spec))

        # Also add specs to system domain if they contain credential management paths
        # Pattern-based detection for credential/token management under /api/web/
        has_credential_paths = any(
            "/api/web/" in p
            and ("/api_credentials" in p or "/service_credentials" in p or "/scim_token" in p)
            for p in paths
        )
        if has_credential_paths and domain != "authentication":
            domain_specs["authentication"].append((filename, spec))

        # Add specs to appropriate domains based on /api/data/ resource semantics
        # Collect unique target domains for /api/data/ paths in this spec
        data_path_domains = set()
        for path in paths:
            target_domain = get_api_data_target_domain(path)
            if target_domain and target_domain != domain:
                data_path_domains.add(target_domain)

        # Add this spec to all relevant /api/data/ target domains
        for target_domain in data_path_domains:
            domain_specs[target_domain].append((filename, spec))

    merged = {}
    stats = {
        "domains": 0,
        "paths": 0,
        "schemas": 0,
        "requestBodies": 0,
        "source_operations": alias_accounting["source_operations"],
        "canonical_operations": alias_accounting["canonical_operations"],
        "explicit_operation_aliases": alias_accounting["explicit_aliases"],
        "best_practices_enriched": 0,
        "guided_workflows_added": 0,
        "server_defaults_added": 0,
        "conflicts_with_added": 0,
        "schema_overrides_applied": 0,
        "namespace_profiles_added": 0,
    }

    # Load description enricher for domain-specific descriptions
    description_enricher = DescriptionEnricher()

    # Load enrichers that require domain context (Issue #314).
    best_practices_enricher = BestPracticesEnricher()
    guided_workflow_enricher = GuidedWorkflowEnricher()

    for domain, spec_list in sorted(domain_specs.items()):
        domain_title = domain.replace("_", " ").title()

        # Use enriched description if available, otherwise fallback to generic
        enriched_desc = description_enricher.get_description(domain, tier="long")
        description = enriched_desc or f"F5 Distributed Cloud {domain_title}"

        merged_spec = create_base_spec(
            title=domain_title,
            description=description,
            version=version,
        )

        # Apply medium tier to info.x-f5xc-summary
        merged_spec = description_enricher.enrich_spec(merged_spec, domain=domain)

        all_tags = []
        path_member_owners: dict[tuple[str, str], dict[str, Any]] = {}

        for _filename, spec in spec_list:
            spec_paths = spec.get("paths", {})
            # Skip domain-specific paths when not merging into their target domains
            is_cdn_domain = domain == "cdn"
            is_data_intelligence_domain = domain == "data_intelligence"
            is_virtual_domain = domain == "virtual"
            is_auth_domain = domain == "authentication"
            is_threat_campaign_domain = domain == "threat_campaign"

            for path, path_item in spec_paths.items():
                # Skip CDN paths if not merging into CDN domain
                if not is_cdn_domain and ("/api/cdn/" in path or "/cdn_loadbalancers/" in path):
                    continue

                # Skip threat_campaign/threat_mesh paths if not merging into threat_campaign domain
                if not is_threat_campaign_domain and (
                    "/api/waf/threat_campaign" in path or "/threat_mesh" in path
                ):
                    continue

                # Skip data-intelligence paths if not merging into data_intelligence domain
                if not is_data_intelligence_domain and "/api/data-intelligence/" in path:
                    continue

                # Skip http_loadbalancers paths if not merging into virtual domain
                if not is_virtual_domain and "/http_loadbalancers" in path:
                    continue

                # Skip credential management paths if not merging into authentication domain
                # Pattern-based: /api/web/ + (api_credentials|service_credentials|scim_token)
                is_credential_path = "/api/web/" in path and (
                    "/api_credentials" in path
                    or "/service_credentials" in path
                    or "/scim_token" in path
                )
                if not is_auth_domain and is_credential_path:
                    continue

                # Skip /api/data/ paths if not merging into their semantic target domain
                # This prevents app_security data paths from appearing in CDN domain
                data_target_domain = get_api_data_target_domain(path)
                if data_target_domain and data_target_domain != domain:
                    continue

                stats["paths"] += _merge_path_item(
                    merged_spec["paths"],
                    path,
                    path_item,
                    spec,
                    path_member_owners,
                    operation_aliases,
                )

            # Merge components
            for comp_type in [
                "schemas",
                "responses",
                "parameters",
                "requestBodies",
                "securitySchemes",
            ]:
                source_comps = spec.get("components", {}).get(comp_type, {})
                target_comps = merged_spec["components"].setdefault(comp_type, {})
                for name, comp in source_comps.items():
                    inserted = _insert_exact_component(
                        target_comps,
                        comp_type,
                        name,
                        comp,
                        f"domain {domain}",
                    )
                    if inserted:
                        if comp_type == "schemas":
                            stats["schemas"] += 1
                        elif comp_type == "requestBodies":
                            stats["requestBodies"] += 1

            # Collect tags
            all_tags.extend(spec.get("tags", []))
            for path_item in spec.get("paths", {}).values():
                for operation in path_item.values():
                    if isinstance(operation, dict):
                        all_tags.extend({"name": tag} for tag in operation.get("tags", []))

        _validate_unique_merged_operation_ids(merged_spec["paths"], f"domain {domain}")

        # Deduplicate tags
        seen = set()
        unique_tags = []
        for tag in all_tags:
            name = tag.get("name") if isinstance(tag, dict) else tag
            if name and name not in seen:
                unique_tags.append(copy.deepcopy(tag) if isinstance(tag, dict) else {"name": tag})
                seen.add(name)
        merged_spec["tags"] = sorted(unique_tags, key=lambda t: t.get("name", ""))

        # Add spec-level domain metadata (idempotent)
        add_domain_metadata_to_spec(merged_spec, domain)

        # Rewrite upstream API reference links to our published site
        external_docs_enricher = ExternalDocsEnricher()
        external_docs_enricher.enrich_spec(merged_spec, filename=f"{domain}.json")

        # Apply domain-specific enrichments now that domain is known (Issue #314)
        # Best practices: common errors, security notes, performance tips
        merged_spec = best_practices_enricher.enrich_spec(merged_spec, domain=domain)
        bp_stats = best_practices_enricher.get_stats()
        stats["best_practices_enriched"] = max(
            stats["best_practices_enriched"],
            bp_stats.get("specs_enriched", 0),
        )

        # Guided workflows: multi-step deployment workflows
        merged_spec = guided_workflow_enricher.enrich_spec(merged_spec, domain=domain)
        gw_stats = guided_workflow_enricher.get_stats()
        stats["guided_workflows_added"] = max(
            stats["guided_workflows_added"],
            gw_stats.get("workflows_added", 0),
        )

        merged_spec, graph_stats = _apply_merged_schema_enrichments(
            merged_spec,
            allow_partition_residuals=True,
        )
        for name, value in graph_stats.items():
            stats[name] = stats.get(name, 0) + value

        merged[domain] = merged_spec
        stats["domains"] += 1

    return merged, stats


def _create_master_graph(
    canonical: CanonicalizationResult,
    version: str,
) -> dict[str, Any]:
    """Assemble the master directly from the canonical source graph."""
    enricher = DescriptionEnricher()
    root_desc = enricher.get_description("root", tier="long")
    master = create_base_spec(
        title="F5 Distributed Cloud API",
        description=root_desc or "Complete F5 Distributed Cloud API specification",
        version=version,
    )
    master = enricher.enrich_spec(master, domain="root")
    for category, values in canonical.components.items():
        master["components"][category] = copy.deepcopy(values)

    all_tags: list[Any] = []
    operation_aliases = load_operation_aliases()
    validate_operation_alias_accounting(canonical.documents, operation_aliases)
    path_member_owners: dict[tuple[str, str], dict[str, Any]] = {}
    security_schemes = master["components"].setdefault("securitySchemes", {})
    for source_id in sorted(canonical.documents):
        spec = canonical.documents[source_id]
        spec_paths = spec.get("paths", {})
        for path, path_item in spec_paths.items():
            _merge_path_item(
                master["paths"],
                path,
                path_item,
                spec,
                path_member_owners,
                operation_aliases,
            )

        for name, value in spec.get("components", {}).get("securitySchemes", {}).items():
            _insert_exact_component(
                security_schemes,
                "securitySchemes",
                name,
                value,
                f"master source {source_id}",
            )

        all_tags.extend(spec.get("tags", []))

    _validate_unique_merged_operation_ids(master["paths"], "master specification")

    # Deduplicate tags
    seen: set[str] = set()
    unique_tags = []
    for tag in all_tags:
        name = tag.get("name") if isinstance(tag, dict) else tag
        if name and name not in seen:
            unique_tags.append(copy.deepcopy(tag) if isinstance(tag, dict) else {"name": tag})
            seen.add(name)

    master["tags"] = sorted(unique_tags, key=lambda tag: tag.get("name", ""))
    return master


def create_master_spec(canonical: CanonicalizationResult, version: str) -> dict[str, Any]:
    """Create the provider-facing master independently of all domain projections."""
    master = _create_master_graph(canonical, version)
    ExternalDocsEnricher().enrich_spec(master, filename="openapi.json")
    master, _ = _apply_merged_schema_enrichments(
        master,
        allow_partition_residuals=False,
    )
    return master


def create_spec_index(domain_specs: dict[str, dict[str, Any]], version: str) -> dict[str, Any]:
    """Create an index file listing all available specifications."""
    index: dict[str, Any] = {
        "version": version,
        # Derived from the upstream seed, not wall-clock: index.json is a committed
        # artifact, and this was the LAST thing making two consecutive rebuilds differ
        # (#1152) — everything else converged once the enrichers stopped stamping
        # now() into every schema.
        #
        # This is the sole index builder used by the unified pipeline.
        "timestamp": artifact_timestamp(),
        "specifications": [],
    }

    # Add critical resources list for downstream tooling (e.g., xcsh CLI)
    index[X_F5XC_CRITICAL_RESOURCES] = load_critical_resources()

    # Add error resolution data for AI assistants and CLI troubleshooting (Issue #314)
    error_resolution_enricher = ErrorResolutionEnricher()
    index = error_resolution_enricher.enrich_index(index)

    # Add guided workflows for deployment automation (Issue #314)
    guided_workflow_enricher = GuidedWorkflowEnricher()
    index = guided_workflow_enricher.enrich_index(index)

    # Add acronyms for consistent terminology (Issue #317)
    acronym_enricher = AcronymEnricher()
    index = acronym_enricher.enrich_index(index)

    # Load description enricher for multi-tier descriptions
    description_enricher = DescriptionEnricher()

    # Load resource examples enricher for tiered configuration snippets (Issue #325)
    resource_examples_enricher = ResourceExamplesEnricher()

    for domain, spec in sorted(domain_specs.items()):
        info = spec.get("info", {})
        metadata = get_metadata(domain)

        # Calculate path and schema counts
        path_count = len(spec.get("paths", {}))
        schema_count = len(spec.get("components", {}).get("schemas", {}))
        complexity = calculate_complexity(path_count, schema_count)

        # Get multi-tier descriptions (short/medium for index, long already in spec)
        domain_title = domain.replace("_", " ").title()
        description_short = description_enricher.get_description(domain, tier="short")
        description_medium = description_enricher.get_description(domain, tier="medium")

        # Get icon and primary resources for the domain
        icon_info = get_domain_icon(domain)
        # Rich metadata format for IDE tooling (Issues #267-270)
        primary_resources_metadata = get_primary_resources_metadata(domain, spec=spec)

        # Build spec entry with x-f5xc-* namespace (Issue #292)
        spec_entry = {
            "domain": domain,
            "title": info.get("title", ""),
            "description": info.get("description", ""),
            X_F5XC_DESCRIPTION_SHORT: description_short or domain_title,
            X_F5XC_DESCRIPTION_MEDIUM: description_medium or f"F5 Distributed Cloud {domain_title}",
            "file": f"{domain}.json",
            "path_count": path_count,
            "schema_count": schema_count,
            X_F5XC_COMPLEXITY: complexity,
            X_F5XC_IS_PREVIEW: metadata.get("is_preview", False),
            X_F5XC_REQUIRES_TIER: metadata.get("requires_tier", "Standard"),
            # Single category field for CLI, UI, docs, and Terraform grouping (DRY)
            X_F5XC_CATEGORY: metadata.get("category", "Other"),
            X_F5XC_USE_CASES: metadata.get("use_cases", []),
            X_F5XC_RELATED_DOMAINS: metadata.get("related_domains", []),
            # Visual identity and resource metadata (Issue #184)
            X_F5XC_ICON: icon_info["icon"],
            X_F5XC_LOGO_SVG: icon_info["logo_svg"],
            # Rich resource metadata for IDE tooling (Issues #267-270)
            X_F5XC_PRIMARY_RESOURCES: primary_resources_metadata,
        }

        # Add CLI metadata if available
        cli_metadata = metadata.get("cli_metadata")
        if cli_metadata:
            spec_entry[X_F5XC_CLI_METADATA] = cli_metadata

        # Add resource examples for domain (Issue #325)
        schemas = spec.get("components", {}).get("schemas", {})
        spec_entry = resource_examples_enricher.enrich_index_entry(spec_entry, domain, schemas)

        index["specifications"].append(spec_entry)

    return index


_SUPPORT_ARTIFACTS = {
    "index.json",
    "minimal-export-defaults.json",
    "namespace_profiles.json",
    "openapi.json",
    "validation.json",
}


def _write_staged_outputs(
    domain_specs: dict[str, dict[str, Any]],
    master_spec: dict[str, Any],
    staging_dir: Path,
    version: str,
    indent: int,
) -> None:
    """Generate every release artifact inside an isolated staging directory."""
    for domain, spec in domain_specs.items():
        save_spec(spec, staging_dir / f"{domain}.json", indent=indent)

    save_spec(master_spec, staging_dir / "openapi.json", indent=indent)

    index = create_spec_index(domain_specs, version)
    save_spec(index, staging_dir / "index.json", indent=indent)

    validation_exporter = ValidationExporter()
    validation_exporter.export(staging_dir / "validation.json")
    validation_stats = validation_exporter.get_stats()
    console.print(
        f"[green]Exported validation.json: "
        f"{validation_stats['resources_processed']} resources, "
        f"{validation_stats['required_fields_exported']} required fields, "
        f"{validation_stats['enum_values_exported']} enum values[/green]",
    )

    minimal_exporter = MinimalDefaultsExporter()
    minimal_artifact = minimal_exporter.export(
        master_spec["components"]["schemas"],
        staging_dir / "minimal-export-defaults.json",
        version=version,
    )
    console.print(
        f"[green]Exported minimal-export-defaults.json: "
        f"{len(minimal_artifact['resources'])} resources[/green]",
    )

    np_profiles_exporter = NamespaceProfilesExporter()
    np_profiles_artifact = np_profiles_exporter.export(
        staging_dir / "namespace_profiles.json",
        version=version,
    )
    console.print(
        f"[green]Exported namespace_profiles.json: "
        f"{len(np_profiles_artifact['resources'])} resource overrides + default[/green]",
    )


def _validate_staged_outputs(staging_dir: Path, domains: set[str]) -> None:
    """Verify exact membership, JSON readability, and every OpenAPI contract."""
    expected = {f"{domain}.json" for domain in domains} | _SUPPORT_ARTIFACTS
    openapi_artifacts = {f"{domain}.json" for domain in domains} | {"openapi.json"}
    entries = list(staging_dir.iterdir())
    actual = {entry.name for entry in entries}
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise RuntimeError(
            f"staged artifact set is incomplete: missing={missing}, unexpected={unexpected}",
        )
    if any(not entry.is_file() for entry in entries):
        raise RuntimeError("staged artifact directory contains a non-file entry")

    for artifact in sorted(entries):
        try:
            value = json.loads(artifact.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"staged artifact {artifact.name} is unreadable: {exc}") from exc
        if not isinstance(value, dict):
            raise TypeError(f"staged artifact {artifact.name} must contain a JSON object")
        if artifact.name in openapi_artifacts:
            try:
                validate_openapi(value)
            except (OpenAPIValidationError, ValidatorDetectError) as exc:
                raise RuntimeError(
                    f"staged OpenAPI artifact {artifact.name} is invalid: {exc}"
                ) from exc


def _promote_staged_outputs(staging_dir: Path, output_dir: Path) -> None:
    """Replace the artifact directory, restoring the previous tree on failure.

    Renaming the staged tree into place is the commit point. Failure to remove
    the recovery copy afterward is reported as cleanup residue, not as a failed
    publication with the new tree still live.
    """
    backup_dir = output_dir.parent / f".{output_dir.name}.backup-{uuid.uuid4().hex}"
    previous_output_moved = False
    try:
        if output_dir.exists():
            output_dir.rename(backup_dir)
            previous_output_moved = True
        staging_dir.rename(output_dir)
    except BaseException:
        if previous_output_moved and backup_dir.exists() and not output_dir.exists():
            backup_dir.rename(output_dir)
        raise

    if previous_output_moved:
        try:
            shutil.rmtree(backup_dir)
        except OSError as exc:
            logger.warning(
                "generated artifacts were promoted successfully, but recovery "
                "backup cleanup failed; preserved backup at %s: %s",
                backup_dir,
                exc,
            )


def _format_release_findings(findings: list[dict[str, Any]]) -> str:
    """Render every validation finding in deterministic order."""
    return json.dumps(
        sorted(findings, key=_canonical_json),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _normalize_release_text(
    spec: dict[str, Any],
    target_fields: list[str],
) -> dict[str, Any]:
    """Normalize prose created by late enrichers without mutating wire identifiers."""
    prose_fields = prose_target_fields(target_fields)
    normalized = AcronymNormalizer().normalize_spec(spec, prose_fields)
    return BrandingTransformer().transform_spec(normalized, prose_fields)


def _validate_release_findings(
    release_specs: dict[str, dict[str, Any]],
    target_fields: list[str],
) -> None:
    """Reject findings in any canonical or domain release graph."""
    failures = []
    for artifact, spec in sorted(release_specs.items()):
        consistency_findings = ConsistencyValidator().validate(spec)
        branding_findings = BrandingValidator().validate_spec(
            spec,
            prose_target_fields(target_fields),
        )
        if consistency_findings:
            failures.append(
                f"{artifact}: consistency validation found "
                f"{len(consistency_findings)} configured finding(s): "
                f"{_format_release_findings(consistency_findings)}"
            )
        if branding_findings:
            failures.append(
                f"{artifact}: branding validation found "
                f"{len(branding_findings)} finding(s): "
                f"{_format_release_findings(branding_findings)}"
            )
    if failures:
        raise RuntimeError("release validation failed: " + "; ".join(failures))


def _publish_generated_outputs(
    domain_specs: dict[str, dict[str, Any]],
    master_spec: dict[str, Any],
    output_dir: Path,
    version: str,
    indent: int,
) -> None:
    """Stage, validate, and promote a complete generated artifact tree."""
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent),
    )
    try:
        _write_staged_outputs(domain_specs, master_spec, staging_dir, version, indent)
        _validate_staged_outputs(staging_dir, set(domain_specs))
        _promote_staged_outputs(staging_dir, output_dir)
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)


_EXPECTED_SOURCE_FILES = 283
_EXPECTED_SOURCE_OPERATIONS = 1852
_EXPECTED_CANONICAL_OPERATIONS = 1851
_EXPECTED_OPERATION_ALIASES = 1
_EXPECTED_COMPONENT_OCCURRENCES = 11268
_EXPECTED_COMPONENT_CANONICAL_KEYS = 6287
_EXPECTED_COMPONENT_CONFLICT_GROUPS = 61
_EXPECTED_COMPONENT_RENAMED_OCCURRENCES = 869
_EXPECTED_COMPONENT_SHARED_OCCURRENCES = 4981
_EXPECTED_SOURCE_GET_RESPONSES = 202
_EXPECTED_SOURCE_REPLACE_REQUESTS = 167
_EXPECTED_OUTPUT_GET_RESPONSES = 411
_EXPECTED_OUTPUT_REPLACE_REQUESTS = 339
_OPERATION_METADATA_FIELDS = frozenset(
    {
        "purpose",
        "required_fields",
        "optional_fields",
        "field_docs",
        "conditions",
        "side_effects",
        "danger_level",
        "confirmation_required",
        "common_errors",
        "performance_impact",
    }
)
_FLAT_OPERATION_METADATA_FIELDS = frozenset(
    {
        "x-f5xc-required-fields",
        "x-f5xc-danger-level",
        "x-f5xc-confirmation-required",
        "x-f5xc-side-effects",
    }
)


def _require_expected_accounting(
    canonical: CanonicalizationResult,
    resource_version: dict[str, int],
    operations: dict[str, int],
) -> None:
    """Gate the measured upstream release contract before artifact projection."""
    totals = canonical.accounting.totals
    measured = {
        "source_files": len(canonical.documents),
        "source_operations": operations["source_operations"],
        "canonical_operations": operations["canonical_operations"],
        "operation_aliases": operations["explicit_aliases"],
        "component_occurrences": totals.occurrences,
        "component_canonical_keys": totals.canonical_keys,
        "component_conflict_groups": totals.conflict_name_groups,
        "component_renamed_occurrences": totals.renamed_occurrences,
        "component_shared_occurrences": totals.shared_occurrences,
        "source_get_responses": resource_version["get_responses"],
        "source_replace_requests": resource_version["replace_requests"],
    }
    expected = {
        "source_files": _EXPECTED_SOURCE_FILES,
        "source_operations": _EXPECTED_SOURCE_OPERATIONS,
        "canonical_operations": _EXPECTED_CANONICAL_OPERATIONS,
        "operation_aliases": _EXPECTED_OPERATION_ALIASES,
        "component_occurrences": _EXPECTED_COMPONENT_OCCURRENCES,
        "component_canonical_keys": _EXPECTED_COMPONENT_CANONICAL_KEYS,
        "component_conflict_groups": _EXPECTED_COMPONENT_CONFLICT_GROUPS,
        "component_renamed_occurrences": _EXPECTED_COMPONENT_RENAMED_OCCURRENCES,
        "component_shared_occurrences": _EXPECTED_COMPONENT_SHARED_OCCURRENCES,
        "source_get_responses": _EXPECTED_SOURCE_GET_RESPONSES,
        "source_replace_requests": _EXPECTED_SOURCE_REPLACE_REQUESTS,
    }
    if measured != expected:
        raise RuntimeError(
            f"upstream release accounting changed: expected={expected}, measured={measured}"
        )


def _count_output_resource_versions(specs: list[dict[str, Any]]) -> tuple[int, int]:
    """Require every and only measured output shape to carry the optional token."""
    get_responses = 0
    replace_requests = 0
    for spec in specs:
        schemas = spec.get("components", {}).get("schemas")
        if not isinstance(schemas, dict):
            raise TypeError("generated OpenAPI graph has no components.schemas object")
        for name, schema in schemas.items():
            if not isinstance(schema, dict):
                raise TypeError(f"generated schema {name!r} is not an object")
            properties = schema.get("properties", {})
            if not isinstance(properties, dict):
                raise TypeError(f"generated schema {name!r} properties is not an object")
            targeted = name.endswith(("GetResponse", "ReplaceRequest"))
            declared = "resource_version" in properties
            if targeted != declared:
                raise RuntimeError(
                    f"generated schema {name!r} resource_version declaration mismatch"
                )
            if not targeted:
                continue
            if schema.get("type") != "object":
                raise RuntimeError(f"generated schema {name!r} is not an object schema")
            declaration = properties["resource_version"]
            if not isinstance(declaration, dict) or declaration.get("type") != "string":
                raise RuntimeError(
                    f"generated schema {name!r} resource_version is not a string schema"
                )
            required = schema.get("required", [])
            if not isinstance(required, list):
                raise TypeError(f"generated schema {name!r} required is not an array")
            if "resource_version" in required:
                raise RuntimeError(f"generated schema {name!r} requires resource_version")
            if name.endswith("GetResponse"):
                get_responses += 1
            else:
                replace_requests += 1
    return get_responses, replace_requests


def _require_expected_output_resource_versions(
    specs: list[dict[str, Any]],
) -> tuple[int, int]:
    """Gate the exact generated optimistic-concurrency contract."""
    measured = _count_output_resource_versions(specs)
    expected = (
        _EXPECTED_OUTPUT_GET_RESPONSES,
        _EXPECTED_OUTPUT_REPLACE_REQUESTS,
    )
    if measured != expected:
        raise RuntimeError(
            "generated resource_version accounting changed: "
            f"expected={expected}, measured={measured}"
        )
    return measured


def _string_array(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
        and len(value) == len(set(value))
    )


def _operation_metadata_errors(metadata: object) -> list[str]:
    """Return every structural defect in one operation metadata wrapper."""
    if not isinstance(metadata, dict):
        return ["x-f5xc-operation-metadata is not an object"]
    errors: list[str] = []
    missing = sorted(_OPERATION_METADATA_FIELDS - set(metadata))
    extra = sorted(set(metadata) - _OPERATION_METADATA_FIELDS)
    if missing:
        errors.append(f"missing fields={missing}")
    if extra:
        errors.append(f"unknown fields={extra}")
    if missing or extra:
        return errors

    if not isinstance(metadata["purpose"], str) or not metadata["purpose"].strip():
        errors.append("purpose is not a non-empty string")
    required = metadata["required_fields"]
    optional = metadata["optional_fields"]
    required_is_valid = _string_array(required)
    optional_is_valid = _string_array(optional)
    if not required_is_valid:
        errors.append("required_fields is not a unique non-empty string array")
    if not optional_is_valid:
        errors.append("optional_fields is not a unique non-empty string array")
    if required_is_valid and optional_is_valid and set(required) & set(optional):
        errors.append("required_fields and optional_fields overlap")

    field_docs = metadata["field_docs"]
    if not isinstance(field_docs, dict) or any(
        not isinstance(name, str)
        or not name
        or not isinstance(description, str)
        or not description.strip()
        for name, description in getattr(field_docs, "items", lambda: ())()
    ):
        errors.append("field_docs is not a non-empty-string mapping")

    conditions = metadata["conditions"]
    if not isinstance(conditions, dict) or set(conditions) != {
        "prerequisites",
        "postconditions",
    }:
        errors.append("conditions does not contain the exact structured fields")
    elif not all(_string_array(conditions[field]) for field in sorted(conditions)):
        errors.append("conditions values are not unique non-empty string arrays")

    side_effects = metadata["side_effects"]
    if not isinstance(side_effects, dict) or any(
        effect not in {"creates", "modifies", "deletes"} or not _string_array(values)
        for effect, values in getattr(side_effects, "items", lambda: ())()
    ):
        errors.append("side_effects is not a structured effect mapping")
    danger = metadata["danger_level"]
    if danger not in {"low", "medium", "high"}:
        errors.append("danger_level is invalid")
    confirmation = metadata["confirmation_required"]
    if not isinstance(confirmation, bool) or confirmation != (danger == "high"):
        errors.append("confirmation_required disagrees with danger_level")

    common_errors = metadata["common_errors"]
    if not isinstance(common_errors, list) or any(
        not isinstance(error, dict)
        or set(error) != {"code", "message", "solution"}
        or not isinstance(error["code"], (int, str))
        or isinstance(error["code"], bool)
        or not isinstance(error["message"], str)
        or not error["message"].strip()
        or not isinstance(error["solution"], str)
        or not error["solution"].strip()
        for error in common_errors
    ):
        errors.append("common_errors is not a structured error array")

    performance = metadata["performance_impact"]
    if (
        not isinstance(performance, dict)
        or set(performance)
        != {
            "latency",
            "resource_usage",
        }
        or any(value not in {"low", "moderate", "high"} for value in performance.values())
    ):
        errors.append("performance_impact is not the exact structured contract")
    return errors


def _require_complete_operation_metadata(specs: dict[str, dict[str, Any]]) -> int:
    """Gate every operation in every candidate artifact without sampling."""
    failures: list[str] = []
    operation_count = 0
    for filename, spec in sorted(specs.items()):
        paths = spec.get("paths")
        if not isinstance(paths, dict):
            failures.append(f"{filename} paths is not an object")
            continue
        for path, path_item in sorted(paths.items()):
            if not isinstance(path_item, dict):
                failures.append(f"{filename} path {path} is not an object")
                continue
            for method, operation in sorted(path_item.items()):
                if method.lower() not in HTTP_METHODS:
                    continue
                operation_count += 1
                location = f"{filename} {method.upper()} {path}"
                if not isinstance(operation, dict):
                    failures.append(f"{location}: operation is not an object")
                    continue
                flat = sorted(_FLAT_OPERATION_METADATA_FIELDS & set(operation))
                if flat:
                    failures.append(f"{location}: flat metadata fields={flat}")
                failures.extend(
                    f"{location}: {error}"
                    for error in _operation_metadata_errors(
                        operation.get(X_F5XC_OPERATION_METADATA)
                    )
                )
    if failures:
        raise RuntimeError(
            "generated operation metadata contract failed:\n- " + "\n- ".join(failures)
        )
    return operation_count


# =============================================================================
# MAIN PIPELINE
# =============================================================================


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    report_dir: Path,
    config: dict,
    version: str,
    dry_run: bool = False,
) -> PipelineStats:
    """Run the complete enrichment pipeline.

    Processes immutable upstream specs in memory, then canonicalizes and merges by domain.
    No individual files are written - only merged domain specs.

    Args:
        input_dir: Directory containing original specifications (READ-ONLY).
        output_dir: Directory for merged domain specs output.
        report_dir: Directory for non-release diagnostic reports.
        config: Pipeline configuration.
        version: Explicit build identity for every generated artifact.
        dry_run: Analyze without writing output.

    Returns:
        PipelineStats with processing summary.
    """
    if "discovery_enrichment" in config:
        raise ValueError(
            "release pipeline configuration must not include discovery_enrichment; "
            "live discovery snapshots are not release inputs"
        )

    # Initialize memory profiler (Issue #390)
    with MemoryProfiler() as profiler:
        profiler.checkpoint("pipeline_start")

        stats = PipelineStats()

        spec_files = source_spec_files(input_dir)

        console.print(f"[blue]Found {len(spec_files)} specification files[/blue]")
        profiler.checkpoint("specs_discovered")

        # Validate the complete source graph before enrichment or destructive
        # output preparation. A documentation pipeline cannot repair API contracts.
        source_errors = validate_source_files(spec_files)
        if source_errors:
            stats.files_processed = len(spec_files)
            stats.files_failed = len(source_errors)
            stats.errors.extend(source_errors)
            console.print(
                f"[red]Source graph validation failed for {len(source_errors)} "
                "specification(s)[/red]",
            )
            return stats

        # Process specs in memory using batch processing (Issue #390 Phase 2)
        processed_specs: dict[str, dict[str, Any]] = {}
        output_config = config["output"]
        indent = output_config["json_indent"]
        profiler.checkpoint("configuration_loaded")

        # Initialize batch processor with configurable batch size
        batch_size = config["processing"]["batch_size"]
        batch_processor = BatchSpecProcessor(batch_size=batch_size)
        console.print(f"[blue]Using batch processing: {batch_size} specs per batch[/blue]")

        # Step 1-2: Batch process enrichment and normalization (disk-cached)
        try:
            cache_paths = batch_processor.process_batch(
                spec_files,
                enrich_spec,
                normalize_spec,
                config,
            )
            profiler.checkpoint("batch_processing_complete", force_gc=True)

            stats.files_processed = len(spec_files)
            stats.files_succeeded = len(cache_paths)
            missing_files = sorted({path.name for path in spec_files} - cache_paths.keys())
            stats.files_failed = len(missing_files)
            stats.errors.extend(
                {
                    "file": filename,
                    "error": "batch processing failed to produce a cached specification",
                }
                for filename in missing_files
            )

            batch_stats = batch_processor.get_stats()
            console.print(
                f"[green]Batch processing complete: {batch_stats['specs_processed']} specs in "
                f"{batch_stats['batches_processed']} batches[/green]",
            )

            if missing_files:
                batch_processor.cleanup_cache()
                console.print(
                    f"[red]Batch processing omitted {len(missing_files)} specification(s); "
                    "merge aborted[/red]",
                )
                return stats

        except Exception as e:
            batch_processor.cleanup_cache()
            console.print(f"[red]Batch processing failed: {e!s}[/red]")
            raise

        # Merge by domain (load from cache just-in-time for merge)
        if not dry_run and cache_paths:
            console.print("[blue]Loading processed specs from cache for merging...[/blue]")

            # Load all processed specs from cache (they've been batched during processing)
            processed_specs = {}
            for filename, cache_path in cache_paths.items():
                try:
                    processed_specs[filename] = batch_processor.load_cached_spec(cache_path)
                except Exception as e:
                    console.print(f"[red]Failed to load {filename} from cache: {e!s}[/red]")
                    stats.files_failed += 1
                    stats.files_succeeded -= 1
                    stats.errors.append(
                        {"file": filename, "error": f"failed to load cached specification: {e!s}"},
                    )

            if stats.errors:
                batch_processor.cleanup_cache()
                console.print("[red]Cached specification load failed; merge aborted[/red]")
                return stats

            profiler.checkpoint("specs_loaded_for_merge", force_gc=True)

            for filename, spec in sorted(processed_specs.items()):
                try:
                    validate_source_graph(spec)
                except SourceGraphValidationError as exc:
                    batch_processor.cleanup_cache()
                    raise RuntimeError(
                        f"processed source graph {filename} is invalid: {exc}"
                    ) from exc

            resource_documents, resource_accounting = declare_resource_versions(processed_specs)
            del processed_specs
            canonical = canonicalize_source_components(resource_documents)
            del resource_documents
            operation_accounting = validate_operation_alias_accounting(
                canonical.documents,
                load_operation_aliases(),
            )
            _require_expected_accounting(
                canonical,
                resource_accounting.to_dict(),
                operation_accounting,
            )

            totals = canonical.accounting.totals
            stats.resource_version_get_responses = resource_accounting.get_responses
            stats.resource_version_replace_requests = resource_accounting.replace_requests
            stats.resource_version_declarations = resource_accounting.total
            stats.component_occurrences = totals.occurrences
            stats.component_canonical_keys = totals.canonical_keys
            stats.component_conflict_name_groups = totals.conflict_name_groups
            stats.component_renamed_occurrences = totals.renamed_occurrences
            stats.component_shared_occurrences = totals.shared_occurrences
            stats.component_accounting = canonical.accounting.to_dict()

            batch_processor.cleanup_cache()
            console.print("[dim]Cache cleanup complete[/dim]")

            console.print("[blue]Creating canonical master specification...[/blue]")
            master_spec = create_master_spec(canonical, version)

            console.print("[blue]Merging specifications by domain...[/blue]")
            domain_specs, merge_stats = merge_specs_by_domain(canonical.documents, version)
            stats.domains_created = merge_stats["domains"]
            stats.paths_merged = merge_stats["paths"]
            stats.schemas_merged = merge_stats["schemas"]
            stats.source_operations = merge_stats["source_operations"]
            stats.canonical_operations = merge_stats["canonical_operations"]
            stats.explicit_operation_aliases = merge_stats["explicit_operation_aliases"]
            stats.best_practices_enriched = merge_stats.get("best_practices_enriched", 0)
            stats.guided_workflows_added = merge_stats.get("guided_workflows_added", 0)
            stats.conflicts_with_added = merge_stats.get("conflicts_with_added", 0)
            stats.naming_constraints_projected = merge_stats.get("naming_constraints_projected", 0)

            del canonical

            profiler.checkpoint("specs_merged", force_gc=True)

            target_fields = config["target_fields"]
            release_specs = {
                "openapi.json": _normalize_release_text(master_spec, target_fields),
                **{
                    f"{domain}.json": _normalize_release_text(spec, target_fields)
                    for domain, spec in sorted(domain_specs.items())
                },
            }
            master_spec = release_specs["openapi.json"]
            domain_specs = {domain: release_specs[f"{domain}.json"] for domain in domain_specs}

            _require_expected_output_resource_versions([master_spec, *domain_specs.values()])
            _require_complete_operation_metadata(release_specs)
            _validate_release_findings(release_specs, target_fields)

            # Generate every artifact into a sibling directory, validate the
            # complete set, and only then replace the current artifact tree.
            _publish_generated_outputs(
                domain_specs,
                master_spec,
                output_dir,
                version,
                indent,
            )

            console.print(f"[green]Created {len(domain_specs)} domain specs + master spec[/green]")

        profiler.checkpoint("pipeline_complete")

        # Save memory profiling report (Issue #390)
        report_dir.mkdir(parents=True, exist_ok=True)
        profiler.save_report(report_dir / "memory-profile.json")
        console.print("[blue]Memory profiling report saved to reports/memory-profile.json[/blue]")

        return stats


def print_summary(stats: PipelineStats) -> None:
    """Print pipeline summary to console."""
    table = Table(title="Pipeline Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Files Processed", str(stats.files_processed))
    table.add_row("Files Succeeded", str(stats.files_succeeded))
    table.add_row("Files Failed", str(stats.files_failed))
    table.add_row("Enrichment Changes", str(stats.enrichment_changes))
    table.add_row("Normalization Changes", str(stats.normalization_changes))
    table.add_row("Schemas Fixed", str(stats.schemas_fixed))
    table.add_row("Descriptions Generated", str(stats.descriptions_generated))
    table.add_row("Consistency Issues", str(stats.consistency_issues))
    table.add_row("Minimum Configs Added", str(stats.minimum_configs_added))
    table.add_row("Domains Created", str(stats.domains_created))
    table.add_row("Paths Merged", str(stats.paths_merged))
    table.add_row("Schemas Merged", str(stats.schemas_merged))
    table.add_row("Source Operations", str(stats.source_operations))
    table.add_row("Canonical Operations", str(stats.canonical_operations))
    table.add_row("Explicit Operation Aliases", str(stats.explicit_operation_aliases))
    table.add_row("Resource Version Declarations", str(stats.resource_version_declarations))
    table.add_row("Component Occurrences", str(stats.component_occurrences))
    table.add_row("Canonical Component Keys", str(stats.component_canonical_keys))

    # Issue #314 enrichment stats
    if stats.best_practices_enriched > 0:
        table.add_row("Best Practices Enriched", str(stats.best_practices_enriched))
    if stats.guided_workflows_added > 0:
        table.add_row("Guided Workflows Added", str(stats.guided_workflows_added))
    if stats.error_resolutions_added > 0:
        table.add_row("Error Resolutions Added", str(stats.error_resolutions_added))
    if stats.conflicts_with_added > 0:
        table.add_row("Conflicts-With Added", str(stats.conflicts_with_added))

    console.print(table)

    if stats.errors:
        console.print(f"\n[red]Errors ({len(stats.errors)}):[/red]")
        for error in stats.errors[:10]:
            console.print(f"  - {error['file']}: {error['error'][:100]}...")
        if len(stats.errors) > 10:
            console.print(f"  ... and {len(stats.errors) - 10} more errors")


def generate_report(stats: PipelineStats, output_path: Path) -> None:
    """Generate pipeline report."""
    report = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "summary": {
            "files_processed": stats.files_processed,
            "files_succeeded": stats.files_succeeded,
            "files_failed": stats.files_failed,
            "enrichment_changes": stats.enrichment_changes,
            "normalization_changes": stats.normalization_changes,
            "schemas_fixed": stats.schemas_fixed,
            "descriptions_generated": stats.descriptions_generated,
            "consistency_issues": stats.consistency_issues,
            "minimum_configs_added": stats.minimum_configs_added,
            "domains_created": stats.domains_created,
            "paths_merged": stats.paths_merged,
            "schemas_merged": stats.schemas_merged,
            "source_operations": stats.source_operations,
            "canonical_operations": stats.canonical_operations,
            "explicit_operation_aliases": stats.explicit_operation_aliases,
            "resource_version_get_responses": stats.resource_version_get_responses,
            "resource_version_replace_requests": stats.resource_version_replace_requests,
            "resource_version_declarations": stats.resource_version_declarations,
            "component_occurrences": stats.component_occurrences,
            "component_canonical_keys": stats.component_canonical_keys,
            "component_conflict_name_groups": stats.component_conflict_name_groups,
            "component_renamed_occurrences": stats.component_renamed_occurrences,
            "component_shared_occurrences": stats.component_shared_occurrences,
            "best_practices_enriched": stats.best_practices_enriched,
            "guided_workflows_added": stats.guided_workflows_added,
            "error_resolutions_added": stats.error_resolutions_added,
            "conflicts_with_added": stats.conflicts_with_added,
        },
        "component_accounting": stats.component_accounting,
        "errors": stats.errors,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    console.print(f"[green]Report saved to {output_path}[/green]")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="F5 XC API Enrichment Pipeline - unified processing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python -m scripts.pipeline --version 2.1.208              # Full pipeline
    python -m scripts.pipeline --version 2.1.208 --dry-run    # Analyze without writing

Output (merged domain specs only):
    docs/specifications/api/
        ├── api_security.json
        ├── applications.json
        ├── bigip.json
        ├── billing.json
        ├── cdn.json
        ├── config.json
        ├── identity.json
        ├── infrastructure.json
        ├── infrastructure_protection.json
        ├── load_balancer.json
        ├── networking.json
        ├── nginx.json
        ├── observability.json
        ├── other.json
        ├── security.json
        ├── service_mesh.json
        ├── shape_security.json
        ├── subscriptions.json
        ├── tenant_management.json
        ├── vpn.json
        ├── openapi.json    (master combined spec)
        └── index.json      (spec metadata)
        """,
    )
    parser.add_argument(
        "--version",
        required=True,
        type=_semantic_version,
        help="Explicit build version to stamp into every generated artifact",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to configuration overlay (default: packaged enrichment configuration)",
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
        help="Override directory for diagnostic reports",
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
    input_dir = args.input_dir or Path(config["paths"]["original"])
    output_dir = args.output_dir or Path(config["paths"]["enriched"])
    report_dir = args.report_dir or Path(config["paths"]["reports"])

    console.print("[bold blue]F5 XC API Enrichment Pipeline[/bold blue]")
    console.print(f"  Input:  {input_dir}")
    console.print(f"  Output: {output_dir}")

    if args.dry_run:
        console.print("  [yellow]Mode: DRY RUN (no files will be written)[/yellow]")

    if not input_dir.exists():
        console.print(f"[red]Input directory not found: {input_dir}[/red]")
        console.print("[yellow]Run 'make download' or 'python -m scripts.download' first[/yellow]")
        return 1

    # Run pipeline
    stats = run_pipeline(
        input_dir=input_dir,
        output_dir=output_dir,
        report_dir=report_dir,
        config=config,
        version=args.version,
        dry_run=args.dry_run,
    )

    # Generate report
    if not args.dry_run:
        report_path = report_dir / "pipeline-report.json"
        generate_report(stats, report_path)

    # Print summary
    print_summary(stats)

    # Every recorded error is fatal, even if a stage failed to increment the
    # legacy files_failed counter.
    if stats.files_failed > 0 or stats.errors:
        console.print(
            f"\n[yellow]Completed with {stats.files_failed} failed file(s) and "
            f"{len(stats.errors)} recorded error(s)[/yellow]",
        )
        return 1

    console.print(f"\n[bold green]Pipeline complete! Output: {output_dir}[/bold green]")
    return 0


def _semantic_version(value: str) -> str:
    """Validate a release build identity for argparse."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise argparse.ArgumentTypeError(f"not a semantic version: {value!r}")
    return value


if __name__ == "__main__":
    sys.exit(main())
