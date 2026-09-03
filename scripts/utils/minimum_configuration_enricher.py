# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Minimum configuration metadata enricher for OpenAPI specifications.

This enricher adds minimum configuration metadata to resource schemas,
enabling AI assistants and CLI tools to generate working configurations.

Adds three OpenAPI extensions:
- x-f5xc-minimum-configuration: Schema-level minimum config definition
- x-f5xc-required-for: Field-level context requirements
- x-f5xc-cli-domain: Domain classification for CLI routing

Issue: #292 - Migrated from x-ves-* to x-f5xc-* namespace
"""

import json
import logging
import re
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .domain_categorizer import DomainCategorizer
from .extension_constants import (
    X_F5XC_CLI_DOMAIN,
    X_F5XC_MINIMUM_CONFIGURATION,
    X_F5XC_REQUIRED_FOR,
    X_VES_ONEOF_FIELD_PREFIX,
)

# Precompiled regex pattern for performance (used in hot paths)
_CAMELCASE_TO_SNAKE_PATTERN = re.compile(r"(?<!^)(?=[A-Z])")

logger = logging.getLogger(__name__)


class MinimumConfigurationContractError(ValueError):
    """Raised when guidance refers to a field outside the current schema graph."""


class _ExampleUnavailableError(ValueError):
    """Raised when a schema cannot yield one deterministic executable example."""


def _resolve_schema(node: dict[str, Any], schemas: dict[str, Any]) -> dict[str, Any]:
    """Resolve a local schema reference or allOf wrapper."""
    current = node
    seen: set[str] = set()
    while isinstance(current, dict):
        reference = current.get("$ref")
        if not reference and isinstance(current.get("allOf"), list) and current["allOf"]:
            first = current["allOf"][0]
            reference = first.get("$ref") if isinstance(first, dict) else None
        if not isinstance(reference, str):
            return current
        prefix = "#/components/schemas/"
        if not reference.startswith(prefix) or reference in seen:
            raise MinimumConfigurationContractError(f"unresolvable schema reference {reference}")
        seen.add(reference)
        name = reference[len(prefix) :]
        resolved = schemas.get(name)
        if not isinstance(resolved, dict):
            raise MinimumConfigurationContractError(f"schema reference {reference} is missing")
        current = resolved
    raise MinimumConfigurationContractError("schema node is not an object")


def _resolve_field_path(root: dict[str, Any], field_path: str, schemas: dict[str, Any]) -> None:
    node = root
    for raw_part in field_path.split("."):
        part = raw_part.removesuffix("[]")
        node = _resolve_schema(node, schemas)
        if node.get("type") == "array":
            node = _resolve_schema(node.get("items", {}), schemas)
        properties = node.get("properties", {})
        if part not in properties:
            raise MinimumConfigurationContractError(
                f"minimum configuration path {field_path} is missing"
            )
        node = properties[part]


def validate_minimum_configuration_paths(
    spec: dict[str, Any], config: dict[str, Any], *, resource: str | None = None
) -> None:
    """Prove guidance fields and choice groups resolve through the live schema graph."""
    schemas = spec.get("components", {}).get("schemas", {})
    resources = config.get("resources", {})
    names = [resource] if resource else sorted(resources)
    for name in names:
        resource_config = resources.get(name)
        if not isinstance(resource_config, dict):
            raise MinimumConfigurationContractError(f"minimum configuration {name} is missing")
        candidates = [
            f"{name}CreateRequest",
            f"schema{name}CreateRequest",
            f"views{name}CreateRequest",
        ]
        root_name = next((candidate for candidate in candidates if candidate in schemas), None)
        if root_name is None:
            raise MinimumConfigurationContractError(f"create schema for {name} is missing")
        root = schemas[root_name]
        for field_path in resource_config.get("required_fields", []):
            _resolve_field_path(root, field_path, schemas)
        for group in resource_config.get("mutually_exclusive_groups", []):
            if not isinstance(group, dict) or not group.get("name"):
                raise MinimumConfigurationContractError(f"{name} has a malformed choice group")
            fields = group.get("fields")
            if not isinstance(fields, list) or len(fields) < 2:
                raise MinimumConfigurationContractError(
                    f"{name}.{group.get('name')} must contain at least two fields"
                )
            for field_path in fields:
                _resolve_field_path(root, field_path, schemas)

        if name == "securemesh_site_v2":
            create_spec = _resolve_schema(root["properties"]["spec"], schemas)
            for group in resource_config.get("mutually_exclusive_groups", []):
                encoded = create_spec.get(f"{X_VES_ONEOF_FIELD_PREFIX}{group['name']}")
                declared = json.loads(encoded) if isinstance(encoded, str) else encoded
                expected = [path.removeprefix("spec.") for path in group["fields"]]
                if declared != expected:
                    raise MinimumConfigurationContractError(
                        f"securemesh_site_v2.{group['name']} does not match the schema choice group"
                    )


@dataclass
class MinimumConfigurationStats:
    """Statistics for minimum configuration enrichment."""

    schemas_enriched: int = 0
    schemas_auto_generated: int = 0
    minimum_configs_added: int = 0
    minimum_configs_auto_generated: int = 0
    required_fields_added: int = 0
    field_requirements_added: int = 0
    example_yamls_generated: int = 0
    example_jsons_generated: int = 0
    cli_domains_added: int = 0
    cli_domains_preserved: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            "schemas_enriched": self.schemas_enriched,
            "schemas_auto_generated": self.schemas_auto_generated,
            "minimum_configs_added": self.minimum_configs_added,
            "minimum_configs_auto_generated": self.minimum_configs_auto_generated,
            "required_fields_added": self.required_fields_added,
            "field_requirements_added": self.field_requirements_added,
            "example_yamls_generated": self.example_yamls_generated,
            "example_jsons_generated": self.example_jsons_generated,
            "cli_domains_added": self.cli_domains_added,
            "cli_domains_preserved": self.cli_domains_preserved,
            "error_count": len(self.errors),
            "errors": self.errors,
        }


class MinimumConfigurationEnricher:
    """Enrich OpenAPI specs with minimum configuration metadata.

    Configuration-driven enricher that adds:
    - Minimum viable configuration examples for each resource (YAML and JSON)
    - Required fields for functional configurations
    - curl command examples for API interaction
    - Domain and resource type classification

    Uses config/minimum_configs.yaml for all definitions.
    Uses DomainCategorizer singleton for domain auto-mapping.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize enricher with configuration.

        Args:
            config_path: Optional path to config file.
                        Defaults to config/minimum_configs.yaml
        """
        self.config_path = (
            config_path or Path(__file__).parent.parent.parent / "config" / "minimum_configs.yaml"
        )
        self.domain_categorizer = DomainCategorizer()
        self.config: dict[str, Any] = {}
        self.resources: dict[str, dict[str, Any]] = {}
        self._schemas: dict[str, Any] = {}
        self.stats = MinimumConfigurationStats()

        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        try:
            with self.config_path.open() as f:
                self.config = yaml.safe_load(f) or {}
                self.resources = self.config.get("resources", {})
                logger.info("Loaded minimum_configs from %s", self.config_path)
                logger.info("Found %d resource definitions", len(self.resources))
        except FileNotFoundError:
            logger.exception("Configuration file not found: %s", self.config_path)
            self.config = {}
            self.resources = {}
        except yaml.YAMLError:
            logger.exception("Error parsing configuration")
            self.config = {}
            self.resources = {}

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Enrich OpenAPI specification with minimum configuration metadata.

        Args:
            spec: OpenAPI specification dictionary

        Returns:
            Enriched specification
        """
        if not self.resources:
            logger.warning("No resource definitions loaded, skipping enrichment")
            return spec

        schemas = spec.get("components", {}).get("schemas", {})
        self._schemas = schemas
        logger.info("Enriching %d schemas with minimum configuration metadata", len(schemas))
        # Normalize the complete graph first. Request schemas routinely refer
        # to components that appear later in iteration order; example output
        # must not depend on which schema happened to be visited first.
        for schema in schemas.values():
            self._add_auto_generated_field_requirements(schema)
        for schema_name, schema in schemas.items():
            self._enrich_schema(schema_name, schema)

        logger.info("Minimum configuration enrichment complete: %s", self.stats.to_dict())
        return spec

    def _enrich_schema(
        self,
        schema_name: str,
        schema: dict[str, Any],
    ) -> None:
        """Enrich individual schema with minimum configuration metadata.

        Handles both configured resources (from config/minimum_configs.yaml) and
        unconfigured resources (via auto-generation). x-f5xc-cli-domain is idempotent
        and will preserve existing values.

        Args:
            schema_name: Name of the schema
            schema: Schema definition
        """
        resource_type = self._detect_resource_type(schema_name)

        try:
            # Check if x-f5xc-cli-domain already exists (idempotent behavior)
            has_existing_cli_domain = X_F5XC_CLI_DOMAIN in schema

            if (
                resource_type
                and resource_type in self.resources
                and self._is_configured_request_schema(schema_name, resource_type)
            ):
                # Explicit configuration exists
                resource_config = self.resources[resource_type]
                self._enrich_from_config(schema, schema_name, resource_type, resource_config)
            else:
                # Auto-generate for unconfigured resources
                self._enrich_with_auto_generation(schema, schema_name, resource_type)
                self.stats.schemas_auto_generated += 1

            # Preserve existing x-f5xc-cli-domain or add domain via categorizer
            if not has_existing_cli_domain or X_F5XC_CLI_DOMAIN not in schema:
                domain = self._get_domain_for_resource(resource_type or "", schema_name)
                schema[X_F5XC_CLI_DOMAIN] = domain
                self.stats.cli_domains_added += 1
            else:
                self.stats.cli_domains_preserved += 1

            self.stats.schemas_enriched += 1

        except Exception as e:
            logger.exception("Error enriching schema %s", schema_name)
            self.stats.errors.append(
                {
                    "schema": schema_name,
                    "error": str(e),
                    "resource_type": resource_type,
                },
            )

    def _enrich_from_config(
        self,
        schema: dict[str, Any],
        schema_name: str,
        resource_type: str | None,
        resource_config: dict[str, Any],
    ) -> None:
        """Enrich schema using explicit configuration.

        Args:
            schema: Schema to enrich
            schema_name: Schema name
            resource_type: Detected resource type
            resource_config: Configuration from config file
        """
        logger.debug("Enriching %s from config (resource: %s)", schema_name, resource_type)
        # Apply configured requiredness before generating or validating an
        # executable payload. A supplied example describes the create shape;
        # replace requests can have materially different path/body fields and
        # therefore receive their own schema-shaped payload.
        if schema_name.endswith("ReplaceRequest"):
            minimum_config = self._auto_generate_minimum_config(schema, schema_name)
            minimum_config["description"] = resource_config.get("description", "")
            minimum_config["mutually_exclusive_groups"] = resource_config.get(
                "mutually_exclusive_groups", []
            )
            schema[X_F5XC_MINIMUM_CONFIGURATION] = minimum_config
            self.stats.minimum_configs_auto_generated += 1
            return

        self._add_field_requirements(schema, resource_config)
        # Add x-f5xc-minimum-configuration at schema level
        minimum_config = {
            "description": resource_config.get("description", ""),
            "required_fields": self._extract_required_fields(resource_type, schema),
            "mutually_exclusive_groups": resource_config.get("mutually_exclusive_groups", []),
            "example_yaml": resource_config.get("example_yaml", ""),
            "example_json": resource_config.get("example_json", ""),
        }

        schema[X_F5XC_MINIMUM_CONFIGURATION] = minimum_config
        self.stats.minimum_configs_added += 1

    def _enrich_with_auto_generation(
        self,
        schema: dict[str, Any],
        schema_name: str,
        resource_type: str | None,
    ) -> None:
        """Auto-generate minimum configuration for unconfigured resources.

        Args:
            schema: Schema to enrich
            schema_name: Schema name
            resource_type: Detected or inferred resource type
        """
        logger.debug("Auto-generating config for %s (resource: %s)", schema_name, resource_type)
        auto_config = self._auto_generate_minimum_config(schema, schema_name)

        schema[X_F5XC_MINIMUM_CONFIGURATION] = auto_config
        self.stats.minimum_configs_auto_generated += 1

    def _auto_generate_minimum_config(
        self,
        schema: dict[str, Any],
        schema_name: str,
        *,
        required_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Auto-generate minimum configuration from schema inspection.

        Args:
            schema: OpenAPI schema
            schema_name: Schema name
            required_fields: Optional configured field paths that must be materialized

        Returns:
            Generated minimum configuration dictionary
        """
        required_fields = (
            self._extract_required_fields_from_schema(schema)
            if required_fields is None
            else required_fields
        )
        result: dict[str, Any] = {
            "description": f"Minimum configuration for {schema_name}",
            "required_fields": required_fields,
            "mutually_exclusive_groups": [],
        }
        try:
            example = self._schema_example(schema, required_fields=frozenset(required_fields))
        except (_ExampleUnavailableError, MinimumConfigurationContractError) as exc:
            result["diagnostic"] = {
                "reasonCode": (
                    "unresolved-oneof"
                    if isinstance(exc, _ExampleUnavailableError)
                    else "schema-unresolved"
                ),
                "message": str(exc),
            }
            return result
        result["example_json"] = json.dumps(example, indent=2)
        result["example_yaml"] = yaml.safe_dump(example, sort_keys=False).rstrip()
        self.stats.example_yamls_generated += 1
        self.stats.example_jsons_generated += 1
        return result

    def _expanded_schema(
        self, schema: dict[str, Any], active_refs: frozenset[str] = frozenset()
    ) -> tuple[dict[str, Any], frozenset[str]]:
        """Resolve local refs and merge every allOf layer without losing siblings."""
        merged: dict[str, Any] = {}
        refs = active_refs
        reference = schema.get("$ref")
        if isinstance(reference, str):
            if reference in refs:
                return {}, refs
            prefix = "#/components/schemas/"
            target = (
                self._schemas.get(reference.removeprefix(prefix))
                if reference.startswith(prefix)
                else None
            )
            if not isinstance(target, dict):
                raise MinimumConfigurationContractError(
                    f"unresolvable schema reference {reference}"
                )
            refs = refs | {reference}
            merged, refs = self._expanded_schema(target, refs)
        for member in schema.get("allOf", []):
            if isinstance(member, dict):
                expanded, member_refs = self._expanded_schema(member, refs)
                merged = self._merge_example_schema(merged, expanded)
                refs = refs | member_refs
        local = {key: value for key, value in schema.items() if key not in {"$ref", "allOf"}}
        return self._merge_example_schema(merged, local), refs

    @staticmethod
    def _merge_example_schema(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in overlay.items():
            if key == "properties" and isinstance(value, dict):
                merged[key] = {**merged.get(key, {}), **value}
            elif key == "required" and isinstance(value, list):
                merged[key] = list(dict.fromkeys([*merged.get(key, []), *value]))
            else:
                merged[key] = value
        return merged

    def _schema_example(
        self,
        schema: dict[str, Any],
        *,
        required_fields: frozenset[str] = frozenset(),
        active_refs: frozenset[str] = frozenset(),
    ) -> Any:
        """Materialize a deterministic value containing only declared properties."""
        resolved, refs = self._expanded_schema(schema, active_refs)
        variants = resolved.get("oneOf")
        if isinstance(variants, list) and variants:
            if len(variants) != 1:
                raise _ExampleUnavailableError(
                    "No concrete oneOf member was selected for a required field."
                )
            member = variants[0]
            if not isinstance(member, dict):
                raise _ExampleUnavailableError("The selected oneOf member is not a schema object.")
            return self._schema_example(member, active_refs=refs)

        schema_type = resolved.get("type")
        if schema_type is None and isinstance(resolved.get("properties"), dict):
            schema_type = "object"
        expected_types = {
            "object": dict,
            "array": list,
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
        }
        for key in ("example", "x-f5xc-example", "x-f5xc-recommended-value", "default"):
            value = resolved.get(key)
            expected = expected_types.get(schema_type)
            if value is not None and (expected is None or isinstance(value, expected)):
                if schema_type in {"integer", "number"} and isinstance(value, bool):
                    continue
                return value
        enum = resolved.get("enum")
        if isinstance(enum, list) and enum:
            return enum[0]
        if schema_type == "object":
            properties = resolved.get("properties") or {}
            required = set(resolved.get("required") or []) | set(required_fields)
            for name, child in properties.items():
                if not isinstance(child, dict):
                    continue
                required_for = child.get(X_F5XC_REQUIRED_FOR) or {}
                if child.get("x-ves-required") == "true" or (
                    isinstance(required_for, dict)
                    and (
                        required_for.get("minimum_config") is True
                        or required_for.get("create") is True
                    )
                ):
                    required.add(name)
            result: dict[str, Any] = {}
            for name in sorted(required):
                child = properties.get(name)
                if not isinstance(child, dict):
                    raise MinimumConfigurationContractError(
                        f"required example path {name} is not a declared property"
                    )
                nested = frozenset(
                    field[len(name) + 1 :]
                    for field in required_fields
                    if field.startswith(f"{name}.")
                )
                result[name] = self._schema_example(child, required_fields=nested, active_refs=refs)
            return result
        if schema_type == "array":
            items = resolved.get("items")
            if not isinstance(items, dict):
                raise MinimumConfigurationContractError("required array has no item schema")
            return [self._schema_example(items, active_refs=refs)]
        numeric_minimum: int | float = resolved.get("minimum", 0)
        validation_rules = resolved.get("x-ves-validation-rules") or {}
        if isinstance(validation_rules, dict):
            lower_bounds = [
                value
                for key, value in validation_rules.items()
                if key.endswith(".gte") and isinstance(value, (str, int, float))
            ]
            if lower_bounds:
                with suppress(ValueError):
                    numeric_minimum = max(float(value) for value in lower_bounds)
        scalar_defaults = {
            "integer": int(numeric_minimum),
            "number": numeric_minimum,
            "boolean": False,
            "string": "value",
            None: "value",
        }
        if schema_type in scalar_defaults:
            return scalar_defaults[schema_type]
        raise MinimumConfigurationContractError(f"unsupported schema type {schema_type!r}")

    def _extract_required_fields_from_schema(self, schema: dict[str, Any]) -> list[str]:
        """Extract required fields from explicit API-contract markers.

        Args:
            schema: OpenAPI schema

        Returns:
            List of required field names
        """
        # A missing requiredness signal means optional. ``required_fields`` is
        # MCP/IDE guidance, not a guess based on every property in a schema.
        # The native marker may already have been corrected by
        # schema_overrides.yaml; x-f5xc-required-for.create is its normalized
        # equivalent when supplied by an earlier enrichment step.
        required = list(schema.get("required", []) or [])
        known_required = set(required)
        for name, property_schema in (schema.get("properties", {}) or {}).items():
            if not isinstance(property_schema, dict):
                continue
            required_for = property_schema.get(X_F5XC_REQUIRED_FOR, {})
            if (
                property_schema.get("x-ves-required") == "true"
                or (isinstance(required_for, dict) and required_for.get("create") is True)
            ) and name not in known_required:
                required.append(name)
                known_required.add(name)
        return required

    def _generate_example_yaml(self, schema_name: str, required_fields: list[str]) -> str:
        """Generate example YAML from schema information.

        Args:
            schema_name: Schema name
            required_fields: List of required field names

        Returns:
            Generated example YAML string
        """
        lines = [
            "# Minimal example for " + schema_name,
            "metadata:",
            "  name: example",
            "  namespace: default",
        ]

        if required_fields:
            spec_fields = [
                f"  {field}: value"
                for field in required_fields[:5]
                if field not in ["metadata", "apiVersion", "kind", "spec"]
            ]
            if spec_fields:
                lines.append("spec:")
                lines.extend(spec_fields)
            else:
                lines.append("spec: {}")
        else:
            lines.append("spec: {}")

        return "\n".join(lines)

    def _generate_example_json(self, _schema_name: str, required_fields: list[str]) -> str:
        """Generate example JSON from schema information.

        Args:
            _schema_name: Schema name (reserved for future type-specific generation)
            required_fields: List of required field names

        Returns:
            Generated example JSON string
        """
        example = {
            "metadata": {
                "name": "example",
                "namespace": "default",
            },
        }

        if required_fields:
            spec_fields = {
                field: "value"
                for field in required_fields[:5]
                if field not in ["metadata", "apiVersion", "kind", "spec"]
            }
            if spec_fields:
                example["spec"] = spec_fields
            else:
                example["spec"] = {}
        else:
            example["spec"] = {}

        return json.dumps(example, indent=2)

    def _add_auto_generated_field_requirements(self, schema: dict[str, Any]) -> None:
        """Add x-f5xc-required-for to schema properties based on multiple indicators.

        Checks:
        1. Schema's required array (OpenAPI standard)
        2. x-ves-required: "true" (F5's original required indicator)
        3. Validation rules that indicate required status

        OneOf variant fields are excluded from indicators 2 and 3 —
        their signals are conditional on variant selection.

        Args:
            schema: Schema definition
        """
        properties = schema.get("properties", {})
        if not properties:
            return

        required_list = schema.get("required", []) or []
        oneof_members = self._collect_oneof_members(schema)

        for field_name, field_schema in properties.items():
            if not isinstance(field_schema, dict):
                continue

            is_required = field_name in required_list or (
                field_name not in oneof_members
                and (
                    field_schema.get("x-ves-required") == "true"
                    or self._has_required_validation_rules(field_schema)
                )
            )

            field_requirements = {
                "minimum_config": is_required,
                "create": is_required,
                "update": False,
                "read": False,
            }
            field_schema[X_F5XC_REQUIRED_FOR] = field_requirements
            self.stats.field_requirements_added += 1

    def _has_required_validation_rules(self, field_schema: dict[str, Any]) -> bool:
        """Check if validation rules indicate field is required.

        Examines x-ves-validation-rules for constraints that imply the field
        must have a non-zero/non-empty value:
        - message.required: "true" - explicit required flag
        - uint32.gte: N (N >= 1) - minimum value constraint
        - repeated.min_items: N (N >= 1) - minimum array items
        - string.min_bytes: N (N >= 1) - minimum string length

        Args:
            field_schema: Field schema definition

        Returns:
            True if validation rules indicate the field is required
        """
        rules = field_schema.get("x-ves-validation-rules", {})
        if not rules:
            return False

        # Direct required indicator
        if rules.get("ves.io.schema.rules.message.required") == "true":
            return True

        # Minimum value constraints that fail with default (0)
        gte_value = rules.get("ves.io.schema.rules.uint32.gte")
        if gte_value is not None:
            try:
                if int(gte_value) >= 1:
                    return True
            except (ValueError, TypeError):
                pass

        # Array minimum items constraint
        min_items = rules.get("ves.io.schema.rules.repeated.min_items")
        if min_items is not None:
            try:
                if int(min_items) >= 1:
                    return True
            except (ValueError, TypeError):
                pass

        # String minimum bytes constraint
        min_bytes = rules.get("ves.io.schema.rules.string.min_bytes")
        if min_bytes is not None:
            try:
                if int(min_bytes) >= 1:
                    return True
            except (ValueError, TypeError):
                pass

        return False

    @staticmethod
    def _collect_oneof_members(schema: dict[str, Any]) -> frozenset[str]:
        """Collect field names that are members of x-ves-oneof-field-* groups.

        Args:
            schema: Parent schema containing x-ves-oneof-field-* annotations

        Returns:
            Immutable set of field names belonging to any oneOf group
        """
        members: list[str] = []
        for key, value in schema.items():
            if key.startswith(X_VES_ONEOF_FIELD_PREFIX) and isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        members.extend(parsed)
                except (json.JSONDecodeError, TypeError):
                    pass
        return frozenset(members)

    @staticmethod
    def _is_resource_schema(schema_name: str) -> bool:
        """Check if schema is a top-level resource schema (not a sub-schema).

        Only top-level resource schemas (CreateSpecType, CreateRequest, etc.)
        should receive config-driven enrichment. Sub-schemas matched via
        partial name matching get auto-generated enrichment instead.
        """
        resource_suffixes = (
            "CreateSpecType",
            "UpdateSpecType",
            "GetSpecType",
            "ReplaceSpecType",
            "DeleteSpecType",
            "CreateRequest",
            "ReplaceRequest",
        )
        return any(schema_name.endswith(suffix) for suffix in resource_suffixes)

    @staticmethod
    def _is_configured_request_schema(schema_name: str, resource_type: str) -> bool:
        """Return whether a supplied executable example targets this request.

        Resource detection deliberately accepts partial names, but doing so for
        examples caused a resource's CRUD envelope to leak into similarly named
        custom/action requests. Supplied examples belong only to the canonical
        resource Create/Replace request shapes.
        """
        base = schema_name
        for suffix in ("CreateRequest", "ReplaceRequest"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        else:
            return False
        if base == resource_type:
            return True
        return any(base == f"{prefix}{resource_type}" for prefix in ("views", "api", "schema"))

    def _detect_resource_type(self, schema_name: str) -> str | None:
        """Detect resource type from schema name.

        Maps schema names to resource types defined in config.
        Handles variations like CreateSpecType, UpdateSpecType, viewshttp_loadbalancerCreateSpecType.

        Args:
            schema_name: Name of the schema

        Returns:
            Resource type if found, None otherwise
        """
        # Direct match
        if schema_name in self.resources:
            return schema_name

        # Remove common prefixes (e.g., "views", "api", "schema")
        # Skip stripping when remainder starts with underscore — the prefix
        # is part of the resource name (e.g., "api_definition"), not a namespace.
        working_name = schema_name
        for prefix in ["views", "api", "schema"]:
            if working_name.startswith(prefix):
                remaining = working_name[len(prefix) :]
                if remaining and remaining[0] != "_":
                    working_name = remaining
                break

        # Remove common suffixes in order of specificity
        for suffix in [
            "CreateSpecType",
            "UpdateSpecType",
            "GetSpecType",
            "DeleteSpecType",
            "SpecType",
            "Spec",
            "Type",
            "Request",
            "Response",
            "Create",
            "Update",
        ]:
            if working_name.endswith(suffix):
                base_name = working_name[: -len(suffix)]
                if base_name in self.resources:
                    return base_name

        # Try converting case variations (e.g., HttpLoadbalancer -> http_loadbalancer)
        snake_case = _CAMELCASE_TO_SNAKE_PATTERN.sub("_", working_name).lower()
        if snake_case in self.resources:
            return snake_case

        # Try partial matching for compound names
        for resource in self.resources:
            if resource in working_name.lower():
                return resource

        return None

    def _extract_required_fields(
        self,
        resource_type: str | None,
        schema: dict[str, Any],
    ) -> list[str]:
        """Extract minimum required fields for resource.

        Uses config-defined required fields if present.
        Falls back to schema's required array.

        Args:
            resource_type: Type of resource
            schema: Schema definition

        Returns:
            List of required field names
        """
        # Use config-defined required fields if present
        if resource_type:
            config_required = self.resources.get(resource_type, {}).get("required_fields", [])
            if config_required:
                self.stats.required_fields_added += 1
                return config_required

        # Fallback to schema required array
        return schema.get("required", [])

    def _add_field_requirements(
        self,
        schema: dict[str, Any],
        resource_config: dict[str, Any],
    ) -> None:
        """Add x-f5xc-required-for to schema properties.

        Uses explicit configuration as primary source, but also checks
        x-ves-required and validation rules for fields not in config.
        OneOf variant fields are excluded from schema-inferred required
        signals — their validation rules are conditional on variant selection.

        Args:
            schema: Schema definition
            resource_config: Resource configuration from config file
        """
        properties = schema.get("properties", {})
        if not properties:
            return

        required_fields = self._extract_required_fields_list(resource_config)
        oneof_members = self._collect_oneof_members(schema)

        for field_name, field_schema in properties.items():
            if not isinstance(field_schema, dict):
                continue

            is_required = field_name in required_fields or (
                field_name not in oneof_members
                and (
                    field_schema.get("x-ves-required") == "true"
                    or self._has_required_validation_rules(field_schema)
                )
            )

            field_requirements = {
                "minimum_config": is_required,
                "create": is_required,
                "update": False,
                "read": False,
            }
            field_schema[X_F5XC_REQUIRED_FOR] = field_requirements
            self.stats.field_requirements_added += 1

    def _extract_required_fields_list(self, resource_config: dict[str, Any]) -> list[str]:
        """Extract required fields list from resource config.

        Args:
            resource_config: Resource configuration

        Returns:
            List of required field names
        """
        required = resource_config.get("required_fields", [])
        # Convert nested paths (e.g., "metadata.name") to top-level field names for property matching
        field_names = []
        for field_path in required:
            # Get the top-level field name
            top_level = field_path.split(".")[0]
            if top_level not in field_names:
                field_names.append(top_level)
        return field_names

    def _get_domain_for_resource(self, resource_type: str, schema_name: str) -> str:
        """Get domain classification for resource.

        Priority:
        1. Explicit domain in config
        2. DomainCategorizer mapping
        3. Resource name inference
        4. Fallback

        Args:
            resource_type: Type of resource
            schema_name: Schema name for categorizer

        Returns:
            Domain classification
        """
        # Check config
        config_domain = self.resources.get(resource_type, {}).get("domain")
        if config_domain:
            return config_domain

        # Try DomainCategorizer
        try:
            domain = self.domain_categorizer.categorize(schema_name)
            if domain:
                return domain
        except Exception as e:
            logger.debug("DomainCategorizer failed for %s: %s", schema_name, e)

        # Fallback: infer from resource type
        if "virtual" in resource_type.lower() or "loadbalancer" in resource_type.lower():
            return "virtual"
        if "waf" in resource_type.lower() or "firewall" in resource_type.lower():
            return "waf"
        if "pool" in resource_type.lower():
            return "virtual"

        return "other"

    def get_stats(self) -> dict[str, Any]:
        """Get enrichment statistics.

        Returns:
            Statistics dictionary
        """
        return self.stats.to_dict()
