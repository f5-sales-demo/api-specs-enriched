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
    python -m scripts.pipeline              # Full pipeline
    python -m scripts.pipeline --dry-run    # Analyze without writing
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

import yaml
from rich.console import Console
from rich.table import Table

# Import processing modules
from scripts.merge_specs import load_critical_resources
from scripts.utils import (
    AcronymEnricher,
    AcronymNormalizer,
    BestPracticesEnricher,
    BrandingTransformer,
    ConflictsWithEnricher,
    ConsistencyValidator,
    ConstrainedFieldsEnricher,
    ConstraintEnricher,
    ConstraintReconciler,
    DefaultValueEnricher,
    DependencyEnricher,
    DescriptionEnricher,
    DescriptionStructureTransformer,
    DescriptionValidator,
    DiscoveryEnricher,
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
    get_version_from_tags,
)
from scripts.utils.batch_processor import BatchSpecProcessor
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
    X_F5XC_PRIMARY_RESOURCES,
    X_F5XC_RELATED_DOMAINS,
    X_F5XC_REQUIRES_TIER,
    X_F5XC_USE_CASES,
)
from scripts.utils.json_writer import write_json_file
from scripts.utils.memory_profiler import MemoryProfiler
from scripts.utils.minimal_defaults_exporter import MinimalDefaultsExporter
from scripts.utils.namespace_profiles_exporter import NamespaceProfilesExporter
from scripts.utils.server_variables import ServerVariableHelper

console = Console()
logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_CONFIG = {
    "paths": {
        "original": "specs/original",
        "enriched": "docs/specifications/api",
        "reports": "reports",
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
    "normalization": {
        "fix_orphan_refs": True,
        "create_missing_components": True,
        "inline_orphan_request_bodies": True,
        "remove_empty_objects": True,
        "type_standardization": True,
    },
    "processing": {
        "parallel_workers": 4,
        "continue_on_error": True,
    },
    "output": {
        "json_indent": 2,
        "sort_keys": False,
    },
}


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
    discovery_enriched: int = 0
    constraints_reconciled: int = 0
    constraints_preserved: int = 0
    naming_constraints_projected: int = 0
    best_practices_enriched: int = 0
    guided_workflows_added: int = 0
    error_resolutions_added: int = 0
    conflicts_with_added: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


def load_config(config_path: Path | None = None) -> dict:
    """Load configuration from YAML file or use defaults."""
    if config_path and config_path.exists():
        with config_path.open() as f:
            config = yaml.safe_load(f) or {}
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
    target_fields = config.get("target_fields", ["description", "summary"])
    grammar_config = config.get("grammar", {})

    # Initialize enrichment utilities
    acronym_normalizer = AcronymNormalizer()
    branding_transformer = BrandingTransformer()
    description_structure_transformer = DescriptionStructureTransformer()
    grammar_improver = GrammarImprover(
        capitalize_sentences=grammar_config.get("capitalize_sentences", True),
        ensure_punctuation=grammar_config.get("ensure_punctuation", True),
        normalize_whitespace=grammar_config.get("normalize_whitespace", True),
        fix_double_spaces=grammar_config.get("fix_double_spaces", True),
        trim_whitespace=grammar_config.get("trim_whitespace", True),
        use_language_tool=False,  # Disable for pipeline performance
    )
    schema_fixer = SchemaFixer()
    description_validator = DescriptionValidator()
    consistency_validator = ConsistencyValidator()

    # Count fields before
    field_count = _count_text_fields(spec, target_fields)

    # Phase 1: Upstream validation (assert upstream-injected fields exist)
    upstream_warnings = validate_upstream_spec(spec)
    for warning in upstream_warnings:
        logger.warning(warning)

    # Fallback: assign tags to operations upstream left untagged
    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        # Derive tag from path prefix (e.g., /api/config/... -> config)
        parts = [p for p in path.split("/") if p and p != "api" and not p.startswith("{")]
        tag = parts[0] if parts else "other"
        for method in ("get", "post", "put", "delete", "patch", "head", "options"):
            op = path_item.get(method)
            if isinstance(op, dict) and not op.get("tags"):
                op["tags"] = [tag]

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
    spec = branding_transformer.transform_spec(spec, target_fields)

    # 2. Description structure normalization (extract examples, validation rules)
    spec = description_structure_transformer.transform_spec(spec, target_fields)

    # 3. Acronym normalization
    spec = acronym_normalizer.normalize_spec(spec, target_fields)

    # 4. Grammar improvements
    spec = grammar_improver.improve_spec(spec, target_fields)

    # 5. Sanitize script tags in descriptions (prevent Spectral security warnings)
    spec, _ = _sanitize_script_tags(spec, target_fields)

    # 6. Normalize domain names (RFC 2606 compliance + lowercase hostnames)
    spec, domain_normalize_count = _normalize_domain_names(spec, target_fields)

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
    constraint_enricher = ConstraintEnricher(config_path=Path("config/constraint_patterns.yaml"))
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

    # Close grammar improver resources
    grammar_improver.close()

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
    norm_config = config.get("normalization", {})
    stats: dict[str, int] = {
        "ref_siblings_removed": 0,
        "orphan_refs_fixed": 0,
        "orphan_request_bodies_inlined": 0,
        "empty_operations_removed": 0,
        "types_normalized": 0,
    }

    # 0. Remove properties that are siblings to $ref (OpenAPI compliance)
    spec, count = _remove_ref_siblings(spec)
    stats["ref_siblings_removed"] = count

    # 1. Fix orphan $refs
    if norm_config.get("fix_orphan_refs", True):
        spec, count = _fix_orphan_refs(spec)
        stats["orphan_refs_fixed"] = count

    # 2. Inline orphan requestBodies
    if norm_config.get("inline_orphan_request_bodies", True):
        spec, count = _inline_orphan_request_bodies(spec)
        stats["orphan_request_bodies_inlined"] = count

    # 3. Remove empty operations
    if norm_config.get("remove_empty_objects", True):
        spec, count = _remove_empty_operations(spec)
        stats["empty_operations_removed"] = count

    # 4. Normalize types
    if norm_config.get("type_standardization", True):
        spec, count = _normalize_types(spec)
        stats["types_normalized"] = count

    return spec, stats


def _fix_orphan_refs(spec: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Fix orphan $ref references by creating missing components."""
    # Collect all $refs
    all_refs: set[str] = set()

    def collect_refs(obj: Any) -> None:
        if isinstance(obj, dict):
            if "$ref" in obj and isinstance(obj["$ref"], str):
                all_refs.add(obj["$ref"])
            for value in obj.values():
                collect_refs(value)
        elif isinstance(obj, list):
            for item in obj:
                collect_refs(item)

    collect_refs(spec)

    # Get existing components
    existing: dict[str, set[str]] = defaultdict(set)
    for comp_type in ["schemas", "responses", "parameters", "requestBodies"]:
        if comp_type in spec.get("components", {}):
            existing[comp_type] = set(spec["components"][comp_type].keys())

    # Find orphans
    fixed_count = 0
    if "components" not in spec:
        spec["components"] = {}

    for ref in all_refs:
        match = re.match(r"^#/components/(\w+)/(.+)$", ref)
        if match:
            comp_type, comp_name = match.groups()
            if comp_name not in existing.get(comp_type, set()):
                # Create stub component
                if comp_type not in spec["components"]:
                    spec["components"][comp_type] = {}

                if comp_name not in spec["components"][comp_type]:
                    spec["components"][comp_type][comp_name] = _create_stub(comp_type, comp_name)
                    fixed_count += 1

    return spec, fixed_count


def _create_stub(comp_type: str, comp_name: str) -> dict[str, Any]:
    """Create a stub component definition."""

    def create_schema_stub(name: str) -> dict[str, Any]:
        return {
            "type": "object",
            "description": f"Auto-generated stub for {name}",
            "x-generated": True,
        }

    def create_request_body_stub(name: str) -> dict[str, Any]:
        return {
            "description": f"Auto-generated stub for {name}",
            "content": {"application/json": {"schema": {"type": "object"}}},
            "x-generated": True,
        }

    def create_response_stub(name: str) -> dict[str, Any]:
        return {
            "description": f"Auto-generated stub response for {name}",
            "x-generated": True,
        }

    def create_default_stub(name: str) -> dict[str, Any]:
        return {
            "description": f"Auto-generated stub for {name}",
            "x-generated": True,
        }

    stub_factories: dict[str, Callable[[str], dict[str, Any]]] = {
        "schemas": create_schema_stub,
        "requestBodies": create_request_body_stub,
        "responses": create_response_stub,
    }
    return stub_factories.get(comp_type, create_default_stub)(comp_name)


def _inline_orphan_request_bodies(spec: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Convert orphan requestBody $refs to inline definitions."""
    inlined_count = 0
    existing = set(spec.get("components", {}).get("requestBodies", {}).keys())

    for path_item in spec.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue

        for method in ["get", "post", "put", "delete", "patch"]:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue

            request_body = operation.get("requestBody")
            if isinstance(request_body, dict) and "$ref" in request_body:
                match = re.match(r"^#/components/requestBodies/(.+)$", request_body["$ref"])
                if match and match.group(1) not in existing:
                    operation["requestBody"] = {
                        "description": f"Request body (originally referenced {match.group(1)})",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                    inlined_count += 1

    return spec, inlined_count


def _remove_empty_operations(spec: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Remove operations that have empty {} values."""
    removed_count = 0
    paths_to_remove = []

    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue

        methods_to_remove = []
        for method in ["get", "post", "put", "delete", "patch", "options", "head", "trace"]:
            if method in path_item:
                operation = path_item[method]
                is_empty_dict = operation == {}
                has_no_critical_fields = (
                    isinstance(operation, dict)
                    and not operation.get("operationId")
                    and not operation.get("responses")
                    and not operation.get("summary")
                    and not operation.get("description")
                )
                if is_empty_dict or has_no_critical_fields:
                    methods_to_remove.append(method)

        for method in methods_to_remove:
            del path_item[method]
            removed_count += 1

        remaining = [
            m
            for m in ["get", "post", "put", "delete", "patch", "options", "head", "trace"]
            if m in path_item
        ]
        if not remaining:
            paths_to_remove.append(path)

    for path in paths_to_remove:
        del spec["paths"][path]

    return spec, removed_count


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


def _merge_schema_union(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Union-merge a schema: add missing keys at every level from source.

    Skips merging when schemas have incompatible ``type`` values
    (e.g., one is ``object`` and the other is ``string``) since those
    represent different schemas that share a name.
    """
    if target.get("type") != source.get("type") and "type" in target and "type" in source:
        return
    for key, value in source.items():
        if key not in target:
            target[key] = value
        elif isinstance(value, dict) and isinstance(target[key], dict):
            if "$ref" not in target[key] and "$ref" not in value:
                _merge_schema_union(target[key], value)
        elif isinstance(value, list) and isinstance(target[key], list) and key == "enum":
            existing = {str(v) for v in target[key]}
            for item in value:
                if str(item) not in existing:
                    target[key].append(item)
                    existing.add(str(item))


def ensure_unique_operation_ids(
    paths: dict[str, Any],
    existing_ids: set[str],
    source_prefix: str,
) -> tuple[dict[str, Any], set[str], int]:
    """Ensure all operationIds in paths are unique.

    When merging specs, duplicate operationIds violate OpenAPI 3.0 requirements.
    This function prefixes duplicates with the source name to ensure uniqueness.

    Args:
        paths: Dict of path -> path_item to process.
        existing_ids: Set of operationIds already used across merged specs.
        source_prefix: Prefix to add for deduplication (derived from source filename).

    Returns:
        Tuple of (modified_paths, updated_existing_ids, dedup_count).
    """
    modified_paths = {}
    dedup_count = 0
    http_methods = {"get", "post", "put", "delete", "patch", "options", "head", "trace"}

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            modified_paths[path] = path_item
            continue

        modified_path_item = {}
        for key, value in path_item.items():
            if key.lower() not in http_methods:
                modified_path_item[key] = value
                continue

            if not isinstance(value, dict):
                modified_path_item[key] = value
                continue

            operation = value.copy()
            op_id = operation.get("operationId", "")

            if op_id:
                if op_id in existing_ids:
                    # Generate unique operationId by prefixing with source
                    new_op_id = f"{source_prefix}_{op_id}"
                    # Handle case where prefixed ID also exists
                    counter = 1
                    while new_op_id in existing_ids:
                        new_op_id = f"{source_prefix}_{op_id}_{counter}"
                        counter += 1
                    operation["operationId"] = new_op_id
                    dedup_count += 1
                    op_id = new_op_id

                existing_ids.add(op_id)

            modified_path_item[key] = operation

        modified_paths[path] = modified_path_item

    return modified_paths, existing_ids, dedup_count


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


def merge_specs_by_domain(
    specs: dict[str, dict[str, Any]],
    version: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Merge specifications grouped by domain.

    Ensures operationId uniqueness across merged specs by prefixing
    duplicates with the source filename.

    Returns (merged_specs_by_domain, stats).
    """
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
        "operationIds_deduplicated": 0,
        "best_practices_enriched": 0,
        "guided_workflows_added": 0,
        "server_defaults_added": 0,
        "conflicts_with_added": 0,
        "schema_overrides_applied": 0,
        "namespace_profiles_added": 0,
    }

    # Load description enricher for domain-specific descriptions
    description_enricher = DescriptionEnricher()

    # Load enrichers that require domain context (Issue #314)
    # These run after merging when domain is known
    best_practices_enricher = BestPracticesEnricher()
    guided_workflow_enricher = GuidedWorkflowEnricher()
    namespace_profile_enricher_domain = NamespaceProfileEnricher()

    # Load enrichers that require merged schemas (Issue #449)
    # Server-applied defaults need the full merged schema to match patterns
    default_value_enricher = DefaultValueEnricher()

    # Load conflicts-with enricher (Issue #494)
    # Auto-derives mutual exclusivity from x-ves-oneof-field-* extensions
    conflicts_with_enricher = ConflictsWithEnricher()

    # Load dependency enricher — stamps x-f5xc-requires for cross-field deps
    dependency_enricher = DependencyEnricher()

    # Load references enricher — stamps x-f5xc-references for resource-reference deps
    references_enricher = ReferencesEnricher.from_config()

    # Load example-field enricher — derives per-field create examples from example_yaml
    example_field_enricher = ExampleFieldEnricher()

    # Load constrained fields enricher — stamps enum/range constraints from config
    constrained_fields_enricher = ConstrainedFieldsEnricher()

    # Load schema override enricher (Issue #294)
    # Injects missing oneOf variants from schema_overrides.yaml before conflicts-with
    schema_override_enricher = SchemaOverrideEnricher()

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
        existing_operation_ids: set[str] = set()  # Track operationIds within domain

        for filename, spec in spec_list:
            # Extract source name for prefix (remove .json and common patterns)
            source_prefix = re.sub(r"\.json$", "", filename)
            source_prefix = re.sub(r"^ves\.io\.schema\.", "", source_prefix)
            source_prefix = re.sub(r"[^a-zA-Z0-9_]", "_", source_prefix)

            # Process paths with operationId deduplication
            spec_paths = spec.get("paths", {})
            deduplicated_paths, existing_operation_ids, dedup_count = ensure_unique_operation_ids(
                spec_paths,
                existing_operation_ids,
                source_prefix,
            )
            stats["operationIds_deduplicated"] += dedup_count

            # Merge deduplicated paths
            # Skip domain-specific paths when not merging into their target domains
            is_cdn_domain = domain == "cdn"
            is_data_intelligence_domain = domain == "data_intelligence"
            is_virtual_domain = domain == "virtual"
            is_auth_domain = domain == "authentication"
            is_threat_campaign_domain = domain == "threat_campaign"

            for path, path_item in deduplicated_paths.items():
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

                if path not in merged_spec["paths"]:
                    merged_spec["paths"][path] = path_item
                    stats["paths"] += 1
                else:
                    for method, operation in path_item.items():
                        if method not in merged_spec["paths"][path]:
                            merged_spec["paths"][path][method] = operation
                            stats["paths"] += 1
                        elif isinstance(operation, dict) and isinstance(
                            merged_spec["paths"][path].get(method), dict
                        ):
                            existing = merged_spec["paths"][path][method]
                            for k, v in operation.items():
                                if k not in existing:
                                    existing[k] = v

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
                    if name not in target_comps:
                        target_comps[name] = comp
                        if comp_type == "schemas":
                            stats["schemas"] += 1
                        elif comp_type == "requestBodies":
                            stats["requestBodies"] += 1
                    elif isinstance(comp, dict) and isinstance(target_comps[name], dict):
                        _merge_schema_union(target_comps[name], comp)

            # Collect tags
            all_tags.extend(spec.get("tags", []))
            for path_item in spec.get("paths", {}).values():
                for operation in path_item.values():
                    if isinstance(operation, dict):
                        all_tags.extend({"name": tag} for tag in operation.get("tags", []))

        # Deduplicate tags
        seen = set()
        unique_tags = []
        for tag in all_tags:
            name = tag.get("name") if isinstance(tag, dict) else tag
            if name and name not in seen:
                unique_tags.append(tag if isinstance(tag, dict) else {"name": tag})
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

        # Server-applied defaults: add discovered defaults to schema properties (Issue #449)
        merged_spec = default_value_enricher.enrich_spec(merged_spec)
        dv_stats = default_value_enricher.get_stats()
        stats["server_defaults_added"] = max(
            stats["server_defaults_added"],
            dv_stats.get("defaults_added", 0),
        )

        # Schema overrides: inject missing oneOf variants (Issue #294)
        merged_spec = schema_override_enricher.enrich_spec(merged_spec)
        so_stats = schema_override_enricher.get_stats()
        stats["schema_overrides_applied"] += so_stats.get("properties_injected", 0)
        schema_override_enricher.reset_stats()

        # Conflicts-with: auto-derive mutual exclusivity from x-ves-oneof-field-* (Issue #494)
        merged_spec = conflicts_with_enricher.enrich_spec(merged_spec)
        cw_stats = conflicts_with_enricher.get_stats()
        stats["conflicts_with_added"] += cw_stats.get("conflicts_added", 0)
        # Reset stats for next domain to avoid double-counting
        conflicts_with_enricher.reset_stats()

        # Cross-field dependencies: stamp x-f5xc-requires from minimum_configs.yaml
        merged_spec = dependency_enricher.enrich_spec(merged_spec)
        dep_stats = dependency_enricher.get_stats()
        stats.setdefault("dependencies_added", 0)
        stats["dependencies_added"] += dep_stats.get("dependencies_added", 0)
        dependency_enricher.reset_stats()

        # Resource references: stamp x-f5xc-references on ObjectRefType properties
        merged_spec = references_enricher.enrich_spec(merged_spec)
        ref_stats = references_enricher.get_stats()
        stats.setdefault("references_stamped", 0)
        stats["references_stamped"] += ref_stats.get("references_stamped", 0)
        references_enricher.reset_stats()

        # Per-field create examples: derive x-f5xc-field-examples from example_yaml
        merged_spec = example_field_enricher.enrich_spec(merged_spec)
        ex_stats = example_field_enricher.get_stats()
        stats.setdefault("example_fields_stamped", 0)
        stats["example_fields_stamped"] += ex_stats.get("fields_stamped", 0)
        example_field_enricher.reset_stats()

        # Constrained fields: stamp enum/range constraints from minimum_configs.yaml
        merged_spec = constrained_fields_enricher.enrich_spec(merged_spec)
        cf_stats = constrained_fields_enricher.get_stats()
        stats.setdefault("constrained_fields_added", 0)
        stats["constrained_fields_added"] += cf_stats.get("constraints_applied", 0)
        constrained_fields_enricher.reset_stats()

        # Namespace profiles: add x-f5xc-namespace-profile to info + per-schema
        merged_spec = namespace_profile_enricher_domain.enrich_spec(merged_spec)
        np_stats = namespace_profile_enricher_domain.get_stats()
        stats["namespace_profiles_added"] += np_stats.get("specs_enriched", 0)
        namespace_profile_enricher_domain.reset_stats()

        # Final cleanup: strip any $ref siblings introduced by enrichers
        merged_spec, _ = _remove_ref_siblings(merged_spec)

        merged[domain] = merged_spec
        stats["domains"] += 1

    return merged, stats


def create_master_spec(domain_specs: dict[str, dict[str, Any]], version: str) -> dict[str, Any]:
    """Create a master specification combining all domains.

    Ensures operationId uniqueness across all domains by prefixing
    cross-domain duplicates with the domain name.
    """
    # Load enriched description for root/master spec
    enricher = DescriptionEnricher()
    root_desc = enricher.get_description("root", tier="long")

    master = create_base_spec(
        title="F5 Distributed Cloud API",
        description=root_desc or "Complete F5 Distributed Cloud API specification",
        version=version,
    )

    # Apply medium tier to info.x-f5xc-summary for root spec
    master = enricher.enrich_spec(master, domain="root")

    all_tags = []
    existing_operation_ids: set[str] = set()  # Track operationIds across all domains

    for domain, spec in domain_specs.items():
        # Process paths with operationId deduplication across domains
        spec_paths = spec.get("paths", {})
        deduplicated_paths, existing_operation_ids, _ = ensure_unique_operation_ids(
            spec_paths,
            existing_operation_ids,
            domain,  # Use domain name as prefix for cross-domain deduplication
        )

        # Merge deduplicated paths
        for path, path_item in deduplicated_paths.items():
            if path not in master["paths"]:
                master["paths"][path] = path_item

        # Merge components (union merge for schema superset)
        for comp_type in ["schemas", "responses", "parameters", "requestBodies"]:
            source_comps = spec.get("components", {}).get(comp_type, {})
            target_comps = master["components"].setdefault(comp_type, {})
            for name, comp in source_comps.items():
                if name not in target_comps:
                    target_comps[name] = comp
                elif isinstance(comp, dict) and isinstance(target_comps[name], dict):
                    _merge_schema_union(target_comps[name], comp)

        all_tags.extend(spec.get("tags", []))

    # Deduplicate tags
    seen: set[str] = set()
    unique_tags = []
    for tag in all_tags:
        name = tag.get("name") if isinstance(tag, dict) else tag
        if name and name not in seen:
            unique_tags.append(tag if isinstance(tag, dict) else {"name": tag})
            seen.add(name)

    def get_tag_name(t: dict[str, Any]) -> str:
        return t.get("name", "")

    master["tags"] = sorted(unique_tags, key=get_tag_name)

    return master


def create_spec_index(domain_specs: dict[str, dict[str, Any]], version: str) -> dict[str, Any]:
    """Create an index file listing all available specifications."""
    index: dict[str, Any] = {
        "version": version,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
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


# =============================================================================
# MAIN PIPELINE
# =============================================================================


def get_version() -> str:
    """Get version from git tags or generate date-based version.

    Uses tag-based versioning to eliminate race conditions from file-based versioning.
    """
    version = get_version_from_tags()
    if version == "0.0.0":
        # Fallback to date-based version if no tags exist
        return datetime.now(tz=timezone.utc).strftime("%Y.%m.%d")
    return version


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    config: dict,
    dry_run: bool = False,
) -> PipelineStats:
    """Run the complete enrichment pipeline.

    Processes specs in memory (enrich → normalize → discovery → reconcile) then merges by domain.
    No individual files are written - only merged domain specs.

    Args:
        input_dir: Directory containing original specifications (READ-ONLY).
        output_dir: Directory for merged domain specs output.
        config: Pipeline configuration.
        dry_run: Analyze without writing output.

    Returns:
        PipelineStats with processing summary.
    """
    # Initialize memory profiler (Issue #390)
    with MemoryProfiler() as profiler:
        profiler.checkpoint("pipeline_start")

        stats = PipelineStats()

        # Find all spec files
        spec_files = sorted(input_dir.glob("*.json"))
        if not spec_files:
            console.print(f"[yellow]No specification files found in {input_dir}[/yellow]")
            return stats

        console.print(f"[blue]Found {len(spec_files)} specification files[/blue]")
        profiler.checkpoint("specs_discovered")

        # Create output directory and clean old files
        if not dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)

            # Remove all existing JSON spec files to ensure clean state
            # This prevents rogue/stale files from previous runs
            for json_file in output_dir.glob("*.json"):
                json_file.unlink()
                console.print(f"[dim]Cleaned: {json_file.name}[/dim]")

        # Load discovery enrichment configuration
        discovery_config_path = Path("config/discovery_enrichment.yaml")
        discovery_config: dict = {}
        if discovery_config_path.exists():
            with discovery_config_path.open() as f:
                discovery_config = yaml.safe_load(f) or {}

        # Check if discovery enrichment is enabled
        discovery_enabled = config.get("discovery_enrichment", {}).get("enabled", False)
        discovery_enricher = None
        discovery_data = None

        if discovery_enabled:
            discovery_settings = discovery_config.get("discovery_enrichment", {})
            discovered_dir = Path(
                discovery_settings.get("discovered_specs_dir", "specs/discovered"),
            )

            if discovered_dir.exists() and (discovered_dir / "openapi.json").exists():
                console.print("[blue]Loading discovery data for enrichment...[/blue]")
                discovery_enricher = DiscoveryEnricher(discovery_settings)
                discovery_data = discovery_enricher.load_discovery_data(discovered_dir)
                console.print(
                    f"[green]Discovery data loaded: {len(discovery_data.schemas)} schemas, "
                    f"{len(discovery_data.paths)} paths[/green]",
                )
            else:
                console.print(
                    "[yellow]Discovery enrichment enabled but no discovery data found[/yellow]",
                )

        # Check if constraint reconciliation is enabled
        reconciliation_config = discovery_config.get("reconciliation", {})
        reconciliation_enabled = reconciliation_config.get("enabled", True) and discovery_enabled
        reconciler = None

        if reconciliation_enabled:
            reconciler = ConstraintReconciler(reconciliation_config)
            console.print(
                f"[blue]Constraint reconciliation enabled (mode: {reconciler.mode})[/blue]",
            )

        # Process specs in memory using batch processing (Issue #390 Phase 2)
        processed_specs: dict[str, dict[str, Any]] = {}
        output_config = config.get("output", {})
        indent = output_config.get("json_indent", 2)
        profiler.checkpoint("configuration_loaded")

        # Initialize batch processor with configurable batch size
        batch_size = config.get("processing", {}).get("batch_size", 20)
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

            # Collect batch stats from first spec (all specs contribute equally)
            if cache_paths:
                first_cache_path = next(iter(cache_paths.values()))
                batch_processor.load_cached_spec(first_cache_path)

                # Estimate stats (multiply by number of processed specs)
                processed_count = len(cache_paths)
                stats.files_processed = processed_count
                stats.files_succeeded = processed_count
                # Note: Actual enrichment/normalization stats collection happens below

            batch_stats = batch_processor.get_stats()
            console.print(
                f"[green]Batch processing complete: {batch_stats['specs_processed']} specs in "
                f"{batch_stats['batches_processed']} batches[/green]",
            )

        except Exception as e:
            console.print(f"[red]Batch processing failed: {e!s}[/red]")
            raise

        # Step 3-4: Batch process discovery/reconciliation (Phase 3 optimization)
        # Process in batches to avoid accumulating all specs in memory
        if (discovery_enricher and discovery_data) or reconciler:
            console.print("[blue]Applying discovery and reconciliation in batches...[/blue]")

            # Create wrapper functions with statistics tracking
            discovery_func = None
            if discovery_enricher and discovery_data:

                def apply_discovery(spec: dict) -> dict:
                    """Apply discovery enrichment and track stats."""
                    enriched = discovery_enricher.enrich_with_discoveries(spec, discovery_data)
                    discovery_stats = discovery_enricher.get_stats()
                    stats.discovery_enriched += discovery_stats.get("fields_enriched", 0)
                    return enriched

                discovery_func = apply_discovery

            reconcile_func = None
            if reconciler:

                def apply_reconciliation(spec: dict) -> tuple[dict, dict]:
                    """Apply constraint reconciliation and track stats."""
                    reconciled, reconcile_report = reconciler.reconcile_spec(spec)
                    reconcile_stats = reconcile_report.get("statistics", {})
                    stats.constraints_reconciled += reconcile_stats.get("reconciled", 0)
                    stats.constraints_preserved += reconcile_stats.get("preserved", 0)
                    return reconciled, reconcile_report

                reconcile_func = apply_reconciliation

            try:
                # Batch process discovery/reconciliation (writes back to cache)
                cache_paths = batch_processor.process_discovery_reconciliation_batch(
                    cache_paths,
                    discovery_func=discovery_func,
                    reconcile_func=reconcile_func,
                )

                batch_stats = batch_processor.get_stats()
                console.print(
                    f"[green]Discovery/reconciliation complete: {len(cache_paths)} specs processed "
                    f"in {batch_stats['batches_processed']} batches[/green]",
                )

            except Exception as e:
                console.print(f"[red]Discovery/reconciliation batch processing failed: {e!s}[/red]")
                raise

        profiler.checkpoint("discovery_reconciliation_complete", force_gc=True)

        # Step 5: Merge by domain (load from cache just-in-time for merge)
        if not dry_run and cache_paths:
            console.print("[blue]Loading processed specs from cache for merging...[/blue]")

            # Load all processed specs from cache (they've been batched during processing)
            processed_specs = {}
            for filename, cache_path in cache_paths.items():
                try:
                    processed_specs[filename] = batch_processor.load_cached_spec(cache_path)
                except Exception as e:
                    console.print(
                        f"[yellow]Warning: Failed to load {filename} from cache: {e!s}[/yellow]",
                    )
                    stats.files_failed += 1

            profiler.checkpoint("specs_loaded_for_merge", force_gc=True)

            console.print("[blue]Merging specifications by domain...[/blue]")
            version = get_version()

            domain_specs, merge_stats = merge_specs_by_domain(processed_specs, version)
            stats.domains_created = merge_stats["domains"]
            stats.paths_merged = merge_stats["paths"]
            stats.schemas_merged = merge_stats["schemas"]
            stats.best_practices_enriched = merge_stats.get("best_practices_enriched", 0)
            stats.guided_workflows_added = merge_stats.get("guided_workflows_added", 0)
            stats.conflicts_with_added = merge_stats.get("conflicts_with_added", 0)

            # Clear processed_specs to free memory before saving
            del processed_specs

            profiler.checkpoint("specs_merged", force_gc=True)

            # Clean up cache files after merge
            batch_processor.cleanup_cache()
            console.print("[dim]Cache cleanup complete[/dim]")

            # Project naming constraints onto standard JSON-Schema keywords as the
            # final, authoritative step (after discovery + reconciliation), so
            # downstream consumers can pull them without parsing x-f5xc-constraints.
            schema_projector = SchemaConstraintProjector()
            for spec in domain_specs.values():
                schema_projector.enrich_spec(spec)
            stats.naming_constraints_projected = schema_projector.get_stats()[
                "properties_projected"
            ]

            # Save domain specs
            for domain, spec in domain_specs.items():
                save_spec(spec, output_dir / f"{domain}.json", indent=indent)

            # Create master spec
            master = create_master_spec(domain_specs, version)
            save_spec(master, output_dir / "openapi.json", indent=indent)

            # Create index
            index = create_spec_index(domain_specs, version)
            save_spec(index, output_dir / "index.json", indent=indent)

            # Export validation specification for downstream consumers.
            # ValidationExporter.export() now delegates to write_json_file,
            # which applies Biome formatting at the source.
            try:
                validation_exporter = ValidationExporter()
                validation_path = output_dir / "validation.json"
                validation_exporter.export(validation_path)
                validation_stats = validation_exporter.get_stats()
                console.print(
                    f"[green]Exported validation.json: "
                    f"{validation_stats['resources_processed']} resources, "
                    f"{validation_stats['required_fields_exported']} required fields, "
                    f"{validation_stats['enum_values_exported']} enum values[/green]",
                )
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to export validation spec: {e}[/yellow]")

            # Export minimal-export-defaults.json for downstream minimum-settings
            # export (xcsh, vscode-xcsh). Walks each covered SpecType schema
            # and emits the flat per-kind defaults table.
            try:
                minimal_exporter = MinimalDefaultsExporter()
                minimal_schemas = MinimalDefaultsExporter.collect_schemas(domain_specs.values())
                minimal_artifact = minimal_exporter.export(
                    minimal_schemas,
                    output_dir / "minimal-export-defaults.json",
                    version=version,
                )
                console.print(
                    f"[green]Exported minimal-export-defaults.json: "
                    f"{len(minimal_artifact['resources'])} resources[/green]",
                )
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to export minimal defaults: {e}[/yellow]")

            # Export namespace_profiles.json — the authoritative resource->namespace
            # scope map consumed by downstream tree filtering (vscode-xcsh, xcsh).
            # Rides alongside the domain specs (like validation.json) and is read by
            # the consumer via an explicit path, not parsed as a domain spec.
            try:
                np_profiles_exporter = NamespaceProfilesExporter()
                np_profiles_artifact = np_profiles_exporter.export(
                    output_dir / "namespace_profiles.json",
                    version=version,
                )
                console.print(
                    f"[green]Exported namespace_profiles.json: "
                    f"{len(np_profiles_artifact['resources'])} resource overrides + default[/green]",
                )
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to export namespace profiles: {e}[/yellow]")

            console.print(f"[green]Created {len(domain_specs)} domain specs + master spec[/green]")

        profiler.checkpoint("pipeline_complete")

        # Save memory profiling report (Issue #390)
        report_dir = Path("reports")
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

    # Discovery enrichment stats (if any)
    if stats.discovery_enriched > 0:
        table.add_row("Discovery Enriched", str(stats.discovery_enriched))
    if stats.constraints_reconciled > 0:
        table.add_row("Constraints Reconciled", str(stats.constraints_reconciled))
    if stats.constraints_preserved > 0:
        table.add_row("Custom Extensions Preserved", str(stats.constraints_preserved))

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
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
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
            "discovery_enriched": stats.discovery_enriched,
            "constraints_reconciled": stats.constraints_reconciled,
            "constraints_preserved": stats.constraints_preserved,
            "best_practices_enriched": stats.best_practices_enriched,
            "guided_workflows_added": stats.guided_workflows_added,
            "error_resolutions_added": stats.error_resolutions_added,
            "conflicts_with_added": stats.conflicts_with_added,
        },
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
    python -m scripts.pipeline              # Full pipeline
    python -m scripts.pipeline --dry-run    # Analyze without writing

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
        "--dry-run",
        action="store_true",
        help="Analyze specs without writing output",
    )

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    # Auto-enable discovery enrichment if environment variable is set
    # This allows GitHub Actions to enable discovery without config changes
    if os.environ.get("DISCOVERY_ENRICHMENT_ENABLED", "").lower() == "true":
        if "discovery_enrichment" not in config:
            config["discovery_enrichment"] = {}
        config["discovery_enrichment"]["enabled"] = True
        console.print(
            "[blue]Discovery enrichment enabled via DISCOVERY_ENRICHMENT_ENABLED env var[/blue]",
        )

    # Determine directories
    input_dir = args.input_dir or Path(config["paths"]["original"])
    output_dir = args.output_dir or Path(config["paths"]["enriched"])
    report_dir = Path(config["paths"]["reports"])

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
        config=config,
        dry_run=args.dry_run,
    )

    # Generate report
    if not args.dry_run:
        report_path = report_dir / "pipeline-report.json"
        generate_report(stats, report_path)

    # Print summary
    print_summary(stats)

    # Exit with error if any files failed
    if stats.files_failed > 0:
        console.print(f"\n[yellow]Completed with {stats.files_failed} failures[/yellow]")
        return 1 if not config.get("processing", {}).get("continue_on_error", True) else 0

    console.print(f"\n[bold green]Pipeline complete! Output: {output_dir}[/bold green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
