# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Centralized constants for x-f5xc-* OpenAPI extension namespace.

This module provides:
1. Unified namespace prefix for all enrichment extensions
2. Field name constants to avoid string duplication
3. Validation utilities for extension compliance

All enrichment code should import field names from this module
rather than using hardcoded strings.

Version: v3.0.0 - Clean break, no backward compatibility
"""

from __future__ import annotations

# =============================================================================
# NAMESPACE PREFIX
# =============================================================================

X_F5XC_PREFIX = "x-f5xc-"

# =============================================================================
# SPEC-LEVEL EXTENSIONS (info section)
# =============================================================================

X_F5XC_CLI_DOMAIN = "x-f5xc-cli-domain"
X_F5XC_CLI_METADATA = "x-f5xc-cli-metadata"
X_F5XC_UPSTREAM_TIMESTAMP = "x-f5xc-upstream-timestamp"
X_F5XC_UPSTREAM_ETAG = "x-f5xc-upstream-etag"
X_F5XC_ENRICHED_VERSION = "x-f5xc-enriched-version"
X_F5XC_GLOSSARY = "x-f5xc-glossary"
X_F5XC_DISCOVERED_AT = "x-f5xc-discovered-at"
X_F5XC_API_URL = "x-f5xc-api-url"
X_F5XC_API_REFERENCE_URL = "x-f5xc-api-reference-url"
X_F5XC_RESPONSE_TIME_MS = "x-f5xc-response-time-ms"

# Domain-level extensions for operational knowledge (Issue #314)
X_F5XC_BEST_PRACTICES = "x-f5xc-best-practices"
X_F5XC_GUIDED_WORKFLOWS = "x-f5xc-guided-workflows"
X_F5XC_ACRONYMS = "x-f5xc-acronyms"

# Console navigation tree (Issue #679)
X_F5XC_CONSOLE_NAVIGATION = "x-f5xc-console-navigation"

# =============================================================================
# SCHEMA-LEVEL EXTENSIONS (component schemas)
# =============================================================================

X_F5XC_MINIMUM_CONFIGURATION = "x-f5xc-minimum-configuration"
X_F5XC_NAMESPACE_PROFILE = "x-f5xc-namespace-profile"
X_F5XC_DISPLAYORDER = "x-f5xc-displayorder"
X_F5XC_TERRAFORM_RESOURCE = "x-f5xc-terraform-resource"
X_F5XC_DISPLAY_NAME = "x-f5xc-display-name"
X_F5XC_ACTION = "x-f5xc-action"

# Evidence-backed multi-interface role contract (Issue #1441)
X_F5XC_INTERFACE_CONTRACT = "x-f5xc-interface-contract"

# Console UI enrichment (Issue #679)
X_F5XC_CONSOLE = "x-f5xc-console"

# =============================================================================
# PROPERTY-LEVEL EXTENSIONS (schema properties)
# =============================================================================

X_F5XC_DESCRIPTION = "x-f5xc-description"
X_F5XC_VALIDATION = "x-f5xc-validation"
X_F5XC_EXAMPLES = "x-f5xc-examples"
X_F5XC_EXAMPLE = "x-f5xc-example"
X_F5XC_COMPLETION = "x-f5xc-completion"
X_F5XC_DEFAULTS = "x-f5xc-defaults"
X_F5XC_REQUIRED_FOR_OPERATIONS = "x-f5xc-required-for-operations"
X_F5XC_REQUIRED_FOR = "x-f5xc-required-for"
X_F5XC_CONDITIONS = "x-f5xc-conditions"
X_F5XC_DEPRECATED = "x-f5xc-deprecated"
X_F5XC_SERVER_DEFAULT = "x-f5xc-server-default"
X_F5XC_RECOMMENDED_VALUE = "x-f5xc-recommended-value"
X_F5XC_RECOMMENDED_ONEOF_VARIANT = "x-f5xc-recommended-oneof-variant"
X_F5XC_CONFLICTS_WITH = "x-f5xc-conflicts-with"
X_F5XC_CONSTRAINTS = "x-f5xc-constraints"
X_F5XC_REQUIRES = "x-f5xc-requires"
X_F5XC_REFERENCES = "x-f5xc-references"
X_F5XC_FIELD_EXAMPLES = "x-f5xc-field-examples"
X_F5XC_UNIQUENESS = "x-f5xc-uniqueness"
X_F5XC_SENSITIVE = "x-f5xc-sensitive"

# Original (misspelled) upstream property key, preserved when the buffer zone
# renames the presented property name (api-specs #686)
X_F5XC_WIRE_NAME = "x-f5xc-wire-name"

# Console UI field enrichment (Issue #679)
X_F5XC_CONSOLE_FIELD = "x-f5xc-console-field"

# =============================================================================
# OPERATION-LEVEL EXTENSIONS (path operations)
# =============================================================================

X_F5XC_REQUIRED_FIELDS = "x-f5xc-required-fields"
X_F5XC_DANGER_LEVEL = "x-f5xc-danger-level"
X_F5XC_CONFIRMATION_REQUIRED = "x-f5xc-confirmation-required"
X_F5XC_SIDE_EFFECTS = "x-f5xc-side-effects"
X_F5XC_OPERATION_ROLE = "x-f5xc-operation-role"

# Discovery-derived extensions for live API behavior (Issue #314)
X_F5XC_DISCOVERED_RESPONSE_TIME = "x-f5xc-discovered-response-time"
X_F5XC_DISCOVERED_RATE_LIMITS = "x-f5xc-discovered-rate-limits"
X_F5XC_DISCOVERED_ERROR_CATALOG = "x-f5xc-discovered-error-catalog"

# =============================================================================
# INDEX-LEVEL EXTENSIONS (index.json metadata)
# =============================================================================

# Single category field for CLI, UI, docs, and Terraform grouping (DRY)
X_F5XC_CATEGORY = "x-f5xc-category"
X_F5XC_PRIMARY_RESOURCES = "x-f5xc-primary-resources"
X_F5XC_CRITICAL_RESOURCES = "x-f5xc-critical-resources"

# Description tiers - used at BOTH index-level (domains) and property-level (Issue #330)
# - Property level: 80-150 chars (short), 150-300 chars (medium) for properties with >300 char descriptions
# - Index level: ~60 chars (short), ~150 chars (medium), ~500 chars (long) for domain descriptions
X_F5XC_DESCRIPTION_SHORT = "x-f5xc-description-short"
X_F5XC_DESCRIPTION_MEDIUM = "x-f5xc-description-medium"
X_F5XC_DESCRIPTION_LONG = "x-f5xc-description-long"
X_F5XC_COMPLEXITY = "x-f5xc-complexity"
X_F5XC_REQUIRES_TIER = "x-f5xc-requires-tier"
X_F5XC_IS_PREVIEW = "x-f5xc-is-preview"
X_F5XC_USE_CASES = "x-f5xc-use-cases"
X_F5XC_ICON = "x-f5xc-icon"
X_F5XC_LOGO_SVG = "x-f5xc-logo-svg"
X_F5XC_RELATED_DOMAINS = "x-f5xc-related-domains"
X_F5XC_DOC_SECTION = "x-f5xc-doc-section"

# =============================================================================
# F5 NATIVE EXTENSIONS TO PRESERVE (DO NOT MODIFY)
# =============================================================================
#
# These come from F5's published swagger and the pipeline carries them through
# verbatim. Enrichment adds x-f5xc-* extensions alongside them; it does not
# rewrite these.
#
# One documented exception, and one caveat about the extension it applies to.
#
# `x-ves-required` is the primary requiredness signal these specs carry. It is
# F5's, not ours — 2586 occurrences in the upstream api-specs repository — and it
# only ever appears with the value "true", so a field is "required" when the marker
# is present and unmarked otherwise. There is no standard signal to fall back on:
# measured 2026-07-30, 0 of 14,729 schemas carry a JSON Schema `required` array.
# Anything generated from these specs therefore inherits this marker's accuracy
# exactly.
#
# It is not accurate. Verified against the live API on 2026-07-30, in both
# directions:
#
#   marked but not enforced   siteUpgradeSWRequest.force. POST .../upgrade_sw
#                             omitting force returns 200; omitting force AND
#                             version returns 400 "version empty in the request",
#                             naming version and never mentioning force.
#   enforced but unmarked     registrationApprovalReq.passport. POST
#                             .../registration/{name}/approve without it returns
#                             500 "Validation approval: Passport is required".
#
# The second one is why terraform-provider-xcsh#636 failed with a 500 and why that
# repository carries tools/action-derived-fields.json — a downstream workaround that
# exists only because the spec did not say the field was required.
#
# So: do not treat an unmarked field as safely optional, and do not treat a marked
# one as genuinely enforced, without a live call. Corrections belong in
# config/schema_overrides.yaml via set_property_extensions /
# remove_property_extensions, which is the sanctioned way to modify one of these
# and the only place that should. Each correction records the call that
# established it. See issue #1142.
#
# Two sibling representations of the same fact, both tracked separately, because
# three disagreeing sources is the real shape of this problem:
#   #1150  x-f5xc-minimum-configuration.required_fields — derived from "all
#          properties" rather than from any requiredness signal, on 12,377 of
#          14,729 schemas.
#   #1151  the requiredness sentence F5 puts in the property description, present
#          on 3,628 of them and carried through verbatim.
# There is also a second UPSTREAM signal: ves.io.schema.rules.message.required
# inside x-ves-validation-rules. It travels with x-ves-required on 3613 properties
# and disagrees on 43, and the pipeline derives x-f5xc-required-for from both — so
# correcting only the marker does not change what consumers see.

PRESERVED_NATIVE_EXTENSIONS = frozenset(
    [
        "x-ves-proto-package",
        "x-ves-proto-file",
        "x-ves-proto-message",
        "x-ves-proto-service",
        "x-ves-proto-rpc",
        "x-displayname",
        "x-ves-oneof",
        "x-ves-default",
        "x-ves-required",
    ],
)

# Pattern prefix for F5 native OneOf field extensions (used for conflict derivation)
# Extensions like x-ves-oneof-field-{group_name} define mutually exclusive fields
# These are preserved (not in frozenset since it's a prefix pattern match)
X_VES_ONEOF_FIELD_PREFIX = "x-ves-oneof-field-"

# =============================================================================
# VALID EXTENSIONS SET
# =============================================================================

# All valid x-f5xc-* extension names
VALID_X_F5XC_EXTENSIONS = frozenset(
    [
        # Spec-level
        X_F5XC_CLI_DOMAIN,
        X_F5XC_CLI_METADATA,
        X_F5XC_UPSTREAM_TIMESTAMP,
        X_F5XC_UPSTREAM_ETAG,
        X_F5XC_ENRICHED_VERSION,
        X_F5XC_GLOSSARY,
        X_F5XC_DISCOVERED_AT,
        X_F5XC_API_URL,
        X_F5XC_API_REFERENCE_URL,
        X_F5XC_RESPONSE_TIME_MS,
        # Domain-level (Issue #314)
        X_F5XC_BEST_PRACTICES,
        X_F5XC_GUIDED_WORKFLOWS,
        X_F5XC_ACRONYMS,
        # Schema-level
        X_F5XC_MINIMUM_CONFIGURATION,
        X_F5XC_NAMESPACE_PROFILE,
        X_F5XC_DISPLAYORDER,
        X_F5XC_TERRAFORM_RESOURCE,
        X_F5XC_DISPLAY_NAME,
        X_F5XC_ACTION,
        X_F5XC_INTERFACE_CONTRACT,
        # Property-level
        X_F5XC_DESCRIPTION,
        X_F5XC_VALIDATION,
        X_F5XC_EXAMPLES,
        X_F5XC_EXAMPLE,
        X_F5XC_COMPLETION,
        X_F5XC_DEFAULTS,
        X_F5XC_REQUIRED_FOR_OPERATIONS,
        X_F5XC_REQUIRED_FOR,
        X_F5XC_CONDITIONS,
        X_F5XC_DEPRECATED,
        X_F5XC_SERVER_DEFAULT,
        X_F5XC_RECOMMENDED_VALUE,
        X_F5XC_RECOMMENDED_ONEOF_VARIANT,
        X_F5XC_CONFLICTS_WITH,
        X_F5XC_CONSTRAINTS,
        X_F5XC_REQUIRES,
        X_F5XC_REFERENCES,
        X_F5XC_FIELD_EXAMPLES,
        X_F5XC_UNIQUENESS,
        X_F5XC_SENSITIVE,
        X_F5XC_WIRE_NAME,
        # Operation-level
        X_F5XC_REQUIRED_FIELDS,
        X_F5XC_DANGER_LEVEL,
        X_F5XC_CONFIRMATION_REQUIRED,
        X_F5XC_SIDE_EFFECTS,
        X_F5XC_OPERATION_ROLE,
        # Discovery-derived (Issue #314)
        X_F5XC_DISCOVERED_RESPONSE_TIME,
        X_F5XC_DISCOVERED_RATE_LIMITS,
        X_F5XC_DISCOVERED_ERROR_CATALOG,
        # Index-level
        X_F5XC_CATEGORY,
        X_F5XC_PRIMARY_RESOURCES,
        X_F5XC_CRITICAL_RESOURCES,
        X_F5XC_DESCRIPTION_SHORT,
        X_F5XC_DESCRIPTION_MEDIUM,
        X_F5XC_DESCRIPTION_LONG,
        X_F5XC_COMPLEXITY,
        X_F5XC_REQUIRES_TIER,
        X_F5XC_IS_PREVIEW,
        X_F5XC_USE_CASES,
        X_F5XC_ICON,
        X_F5XC_LOGO_SVG,
        X_F5XC_RELATED_DOMAINS,
        X_F5XC_DOC_SECTION,
        # Console UI enrichment (Issue #679)
        X_F5XC_CONSOLE,
        X_F5XC_CONSOLE_FIELD,
        X_F5XC_CONSOLE_NAVIGATION,
    ],
)


# =============================================================================
# VALIDATION UTILITIES
# =============================================================================


def is_valid_extension(field_name: str) -> bool:
    """Check if a field name is a valid x-f5xc-* extension.

    Args:
        field_name: The field name to validate

    Returns:
        True if the field is a valid x-f5xc-* extension
    """
    return field_name in VALID_X_F5XC_EXTENSIONS


def is_preserved_native(field_name: str) -> bool:
    """Check if a field is an F5 native extension that must be preserved.

    Args:
        field_name: The field name to check

    Returns:
        True if the field is a preserved F5 native extension
    """
    return field_name in PRESERVED_NATIVE_EXTENSIONS


def validate_no_invalid_extensions(obj: dict, path: str = "") -> list[str]:
    """Validate that an object contains no invalid custom extensions.

    Checks that all custom fields (x-*) are either:
    - Valid x-f5xc-* extensions
    - Preserved F5 native extensions

    Args:
        obj: Dictionary to validate
        path: Current path for error reporting

    Returns:
        List of validation errors (empty if valid)
    """
    errors: list[str] = []

    for key, value in obj.items():
        current_path = f"{path}.{key}" if path else key

        # Check if this is a custom extension field that violates namespace rules
        if key.startswith("x-") and not (is_valid_extension(key) or is_preserved_native(key)):
            errors.append(
                f"{current_path}: Invalid extension '{key}' (must use x-f5xc-* namespace)",
            )

        # Recursively check nested objects
        if isinstance(value, dict):
            errors.extend(validate_no_invalid_extensions(value, current_path))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    errors.extend(validate_no_invalid_extensions(item, f"{current_path}[{i}]"))

    return errors
