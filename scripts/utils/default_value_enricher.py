# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Server-applied default value enricher for OpenAPI specifications.

This enricher adds discovered server-applied default values to resource schemas,
enabling AI assistants and CLI tools to understand what values the server will
apply when fields are omitted.

Adds:
- OpenAPI standard 'default' field with the server-applied value
- x-f5xc-server-default: true marker to indicate the default is server-applied

Issue: #449 - Enrich API specs with server-applied default values
"""

import copy
import json
import logging
import re
from collections.abc import Set
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .extension_constants import (
    X_F5XC_CLI_DOMAIN,
    X_F5XC_RECOMMENDED_ONEOF_VARIANT,
    X_F5XC_RECOMMENDED_VALUE,
    X_F5XC_SERVER_DEFAULT,
    X_F5XC_SERVER_DEFAULT_VALUE,
)

logger = logging.getLogger(__name__)

_ONEOF_DECLARATION_PREFIX = "x-ves-oneof-field-"
_DOMAIN_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class DefaultValueConfigError(ValueError):
    """Raised when discovered-default configuration is invalid."""


def _canonical_json_value(value: Any, context: str) -> str:
    """Serialize one JSON value with scalar types preserved for exact comparison."""
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise DefaultValueConfigError(f"{context} must be a finite JSON value: {exc}") from exc


def exact_json_value_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without conflating booleans and numbers."""
    return _canonical_json_value(left, "left value") == _canonical_json_value(right, "right value")


class _StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _StrictSafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    """Construct a mapping without YAML's implicit last-key-wins behavior."""
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_yaml_mapping(config_path: Path) -> dict[str, Any]:
    """Load a required YAML mapping with duplicate-key detection."""
    try:
        text = config_path.read_text()
    except OSError as exc:
        raise DefaultValueConfigError(
            f"discovered defaults configuration {config_path} cannot be read: {exc}",
        ) from exc

    loader = _StrictSafeLoader(text)
    try:
        document = loader.get_single_data()
    except yaml.YAMLError as exc:
        raise DefaultValueConfigError(
            f"discovered defaults configuration {config_path} contains malformed YAML: {exc}",
        ) from exc
    finally:
        loader.dispose()

    if not isinstance(document, dict):
        raise DefaultValueConfigError(
            f"discovered defaults configuration {config_path} must contain a YAML mapping",
        )
    return document


def _require_known_keys(
    value: dict[str, Any],
    *,
    required: Set[str] = frozenset(),
    optional: Set[str] = frozenset(),
    context: str,
) -> None:
    """Reject missing, unknown, and non-string mapping keys."""
    non_string = [key for key in value if not isinstance(key, str)]
    if non_string:
        raise DefaultValueConfigError(f"{context} has non-string keys: {non_string!r}")
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required - optional)
    if missing or unknown:
        raise DefaultValueConfigError(f"{context} has missing={missing}, unknown={unknown}")


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    """Return a mapping or raise a contextual configuration error."""
    if not isinstance(value, dict):
        raise DefaultValueConfigError(f"{context} must be a mapping")
    if any(not isinstance(key, str) or not key for key in value):
        raise DefaultValueConfigError(f"{context} keys must be non-empty strings")
    return value


@dataclass
class DefaultValueEnrichmentStats:
    """Statistics for default value enrichment."""

    schemas_processed: int = 0
    schemas_matched: int = 0
    defaults_added: int = 0
    nested_defaults_added: int = 0
    recommended_added: int = 0
    nested_recommended_added: int = 0
    oneof_recommended_added: int = 0
    markers_added: int = 0
    configured_entries: int = 0
    applied_config_entries: int = 0
    unapplied_config_entries: list[str] = field(default_factory=list)
    partition_residual_config_entries: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            "schemas_processed": self.schemas_processed,
            "schemas_matched": self.schemas_matched,
            "defaults_added": self.defaults_added,
            "nested_defaults_added": self.nested_defaults_added,
            "recommended_added": self.recommended_added,
            "nested_recommended_added": self.nested_recommended_added,
            "oneof_recommended_added": self.oneof_recommended_added,
            "markers_added": self.markers_added,
            "configured_entries": self.configured_entries,
            "applied_config_entries": self.applied_config_entries,
            "unapplied_config_entries": self.unapplied_config_entries,
            "partition_residual_config_entries": self.partition_residual_config_entries,
            "error_count": len(self.errors),
            "errors": self.errors,
        }


class DefaultValueEnricher:
    """Enrich OpenAPI specs with discovered server-applied default values.

    Configuration-driven enricher that adds:
    - OpenAPI 'default' field with server-applied values
    - x-f5xc-server-default marker for tooling awareness

    Uses config/discovered_defaults.yaml for all definitions.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize enricher with configuration.

        Args:
            config_path: Optional path to config file.
                        Defaults to config/discovered_defaults.yaml
        """
        self.config_path = (
            config_path
            or Path(__file__).parent.parent.parent / "config" / "discovered_defaults.yaml"
        )
        self.config: dict[str, Any] = {}
        self.resources: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, Any] = {}
        self.stats = DefaultValueEnrichmentStats()
        self._compiled_patterns: dict[str, re.Pattern[str]] = {}

        self._load_config()

    def _load_config(self) -> None:
        """Load, validate, and atomically install configuration."""
        config = _load_yaml_mapping(self.config_path)
        _require_known_keys(
            config,
            required={"version", "description", "settings", "resources"},
            context=f"discovered defaults configuration {self.config_path}",
        )
        for key in ("version", "description"):
            if not isinstance(config[key], str) or not config[key]:
                raise DefaultValueConfigError(f"{key} must be a non-empty string")

        settings = _require_mapping(config["settings"], "settings")
        _require_known_keys(
            settings,
            required={"use_openapi_default", "add_marker_extension"},
            context="settings",
        )
        for setting_name, setting_value in settings.items():
            if not isinstance(setting_value, bool):
                raise DefaultValueConfigError(f"settings.{setting_name} must be a boolean")

        resources = _require_mapping(config["resources"], "resources")
        compiled_patterns: dict[str, re.Pattern[str]] = {}
        for resource_name, untyped_resource in resources.items():
            context = f"resources.{resource_name}"
            resource = _require_mapping(untyped_resource, context)
            _require_known_keys(
                resource,
                required={"description", "schema_pattern"},
                optional={
                    "defaults",
                    "enums",
                    "nested",
                    "notes",
                    "oneof_recommended",
                    "origin_server_types",
                    "recommended",
                    "ui_vs_server_defaults",
                },
                context=context,
            )
            for key in ("description", "schema_pattern"):
                if not isinstance(resource[key], str) or not resource[key]:
                    raise DefaultValueConfigError(f"{context}.{key} must be a non-empty string")

            for key in ("defaults", "recommended"):
                if key in resource:
                    _require_mapping(resource[key], f"{context}.{key}")
            if "oneof_recommended" in resource:
                self._validate_oneof_mapping(
                    resource["oneof_recommended"],
                    f"{context}.oneof_recommended",
                )
            if "nested" in resource:
                self._validate_nested_mapping(resource["nested"], f"{context}.nested")
            for key in ("enums", "origin_server_types", "ui_vs_server_defaults"):
                if key in resource:
                    _require_mapping(resource[key], f"{context}.{key}")
            if "notes" in resource and (
                not isinstance(resource["notes"], list)
                or any(not isinstance(note, str) or not note for note in resource["notes"])
            ):
                raise DefaultValueConfigError(f"{context}.notes must be a list of strings")

            for output_path, configured_value in self._flatten_writes(resource).items():
                _canonical_json_value(configured_value, f"{context}.{output_path}")

            try:
                compiled_patterns[resource_name] = re.compile(resource["schema_pattern"])
            except re.error as exc:
                raise DefaultValueConfigError(
                    f"{context}.schema_pattern has invalid regex "
                    f"{resource['schema_pattern']!r}: {exc}",
                ) from exc

        self.config = config
        self.resources = resources
        self.settings = settings
        self._compiled_patterns = compiled_patterns
        logger.info("Loaded discovered_defaults from %s", self.config_path)
        logger.info("Found %d resource definitions", len(self.resources))

    @classmethod
    def _validate_nested_mapping(cls, value: Any, context: str) -> None:
        """Validate recursive structured nested-default configuration."""
        nested = _require_mapping(value, context)
        for property_name, untyped_config in nested.items():
            property_context = f"{context}.{property_name}"
            nested_config = _require_mapping(untyped_config, property_context)
            _require_known_keys(
                nested_config,
                optional={"defaults", "enums", "nested", "oneof_recommended", "recommended"},
                context=property_context,
            )
            for key in ("defaults", "recommended", "enums"):
                if key in nested_config:
                    _require_mapping(nested_config[key], f"{property_context}.{key}")
            if "oneof_recommended" in nested_config:
                cls._validate_oneof_mapping(
                    nested_config["oneof_recommended"],
                    f"{property_context}.oneof_recommended",
                )
            if "nested" in nested_config:
                cls._validate_nested_mapping(
                    nested_config["nested"],
                    f"{property_context}.nested",
                )

    @staticmethod
    def _validate_oneof_mapping(value: Any, context: str) -> None:
        """Validate recommended oneOf group-to-variant mappings."""
        oneof = _require_mapping(value, context)
        for group_name, variant_name in oneof.items():
            if not isinstance(variant_name, str) or not variant_name:
                raise DefaultValueConfigError(
                    f"{context}.{group_name} must be a non-empty string",
                )

    def enrich_spec(
        self,
        spec: dict[str, Any],
        *,
        allow_partition_residuals: bool = False,
    ) -> dict[str, Any]:
        """Enrich OpenAPI specification with server-applied default values.

        Args:
            spec: OpenAPI specification dictionary
            allow_partition_residuals: Whether this explicitly identified domain
                partition may leave configuration entries for other partitions
                unapplied. The merged master specification must pass ``False``.

        Returns:
            Enriched specification
        """
        if not isinstance(allow_partition_residuals, bool):
            raise TypeError("allow_partition_residuals must be a boolean")

        enriched_spec = copy.deepcopy(spec)
        if allow_partition_residuals:
            domain = enriched_spec.get("info", {}).get(X_F5XC_CLI_DOMAIN)
            if not isinstance(domain, str) or _DOMAIN_NAME.fullmatch(domain) is None:
                raise DefaultValueConfigError(
                    "partition residuals require an exact x-f5xc-cli-domain identity",
                )

        schemas = self._get_schemas(enriched_spec)
        previous_stats = self.stats
        self.stats = DefaultValueEnrichmentStats()
        try:
            if not self.resources:
                logger.debug("No resource definitions loaded, skipping default value enrichment")
                return enriched_spec

            configured, applied = self._audit_config_reachability(schemas)
            self.stats.configured_entries = len(configured)
            self.stats.applied_config_entries = len(applied)
            self.stats.unapplied_config_entries = sorted(configured - applied)
            if allow_partition_residuals:
                self.stats.partition_residual_config_entries = list(
                    self.stats.unapplied_config_entries
                )
            else:
                self._require_all_entries_reachable(configured, applied)

            logger.info("Enriching %d schemas with server-applied defaults", len(schemas))

            for schema_name, schema in schemas.items():
                self.stats.schemas_processed += 1
                self._enrich_schema(schema_name, schema, schemas)
        except Exception:
            self.stats = previous_stats
            raise

        logger.info("Default value enrichment complete: %s", self.stats.to_dict())
        return enriched_spec

    @staticmethod
    def _get_schemas(spec: dict[str, Any]) -> dict[str, Any]:
        """Return the schema mapping from an OpenAPI document."""
        schemas = spec.get("components", {}).get("schemas", {})
        if not isinstance(schemas, dict):
            raise TypeError("components.schemas must be a mapping")
        return schemas

    @staticmethod
    def _require_all_entries_reachable(configured: set[str], applied: set[str]) -> None:
        """Reject configured entries that cannot affect the current specification."""
        unapplied = sorted(configured - applied)
        if unapplied:
            raise DefaultValueConfigError(
                f"{len(unapplied)} configured enrichment entries do not reach the input "
                f"schemas: {unapplied}",
            )

    @property
    def compiled_patterns(self) -> dict[str, re.Pattern[str]]:
        """Return an isolated copy of validated resource schema patterns."""
        return dict(self._compiled_patterns)

    @classmethod
    def _collect_nested_config_entries(
        cls,
        resource_name: str,
        nested: dict[str, Any],
        prefix: str,
    ) -> set[str]:
        """Collect every configured nested enrichment entry by semantic path."""
        entries: set[str] = set()
        for parent_name, nested_config in nested.items():
            node_prefix = f"{prefix}.{parent_name}"
            for section in ("defaults", "recommended", "oneof_recommended"):
                entries.update(
                    f"{resource_name}.{node_prefix}.{section}.{entry_name}"
                    for entry_name in nested_config.get(section, {})
                )
            entries.update(
                cls._collect_nested_config_entries(
                    resource_name,
                    nested_config.get("nested", {}),
                    f"{node_prefix}.nested",
                ),
            )
        return entries

    def _reachable_nested_config_entries(
        self,
        resource_name: str,
        nested: dict[str, Any],
        schema: dict[str, Any],
        all_schemas: dict[str, Any],
        prefix: str,
    ) -> set[str]:
        """Return nested entries that reach a property or target schema."""
        reached: set[str] = set()
        properties = schema.get("properties", {})
        for parent_name, nested_config in nested.items():
            if parent_name not in properties:
                continue
            node_prefix = f"{prefix}.{parent_name}"
            target_properties, target_schema = self._resolve_nested_target(
                properties[parent_name],
                all_schemas,
            )
            for section in ("defaults", "recommended"):
                reached.update(
                    f"{resource_name}.{node_prefix}.{section}.{field_name}"
                    for field_name in nested_config.get(section, {})
                    if field_name in target_properties
                )
            if target_schema is not None:
                reached.update(
                    f"{resource_name}.{node_prefix}.oneof_recommended.{group_name}"
                    for group_name, variant_name in nested_config.get(
                        "oneof_recommended", {}
                    ).items()
                    if self._oneof_recommendation_applies(
                        target_schema,
                        group_name,
                        variant_name,
                    )
                )
                reached.update(
                    self._reachable_nested_config_entries(
                        resource_name,
                        nested_config.get("nested", {}),
                        target_schema,
                        all_schemas,
                        f"{node_prefix}.nested",
                    ),
                )
        return reached

    def _audit_config_reachability(
        self,
        schemas: dict[str, Any],
    ) -> tuple[set[str], set[str]]:
        """Account for every entry belonging to a resource present in this spec."""
        configured: set[str] = set()
        reached: set[str] = set()
        for resource_name, resource_config in self.resources.items():
            matching_schemas = [
                schema
                for schema_name, schema in schemas.items()
                if self._compiled_patterns[resource_name].search(schema_name)
            ]
            if not matching_schemas:
                continue
            for section in ("defaults", "recommended", "oneof_recommended"):
                section_entries = {
                    f"{resource_name}.{section}.{entry_name}"
                    for entry_name in resource_config.get(section, {})
                }
                configured.update(section_entries)
                if section == "oneof_recommended":
                    reached.update(
                        f"{resource_name}.{section}.{group_name}"
                        for group_name, variant_name in resource_config.get(section, {}).items()
                        if any(
                            self._oneof_recommendation_applies(
                                schema,
                                group_name,
                                variant_name,
                            )
                            for schema in matching_schemas
                        )
                    )
                else:
                    reached.update(
                        entry
                        for entry in section_entries
                        if any(
                            entry.rsplit(".", maxsplit=1)[-1] in schema.get("properties", {})
                            for schema in matching_schemas
                        )
                    )
            nested = resource_config.get("nested", {})
            configured.update(
                self._collect_nested_config_entries(resource_name, nested, "nested"),
            )
            for schema in matching_schemas:
                reached.update(
                    self._reachable_nested_config_entries(
                        resource_name,
                        nested,
                        schema,
                        schemas,
                        "nested",
                    ),
                )
        return configured, reached

    def _enrich_schema(
        self,
        schema_name: str,
        schema: dict[str, Any],
        all_schemas: dict[str, Any],
    ) -> None:
        """Enrich individual schema with server-applied default values.

        Args:
            schema_name: Name of the schema
            schema: Schema definition
            all_schemas: All schemas from the spec for $ref resolution
        """
        resource_configs = self._match_resources(schema_name)
        if not resource_configs:
            return

        self._reject_ambiguous_matches(schema_name, resource_configs)
        self.stats.schemas_matched += 1
        for _, resource_config in resource_configs:
            # Apply top-level defaults
            defaults = resource_config.get("defaults", {})
            self._apply_defaults_to_properties(schema, defaults)

            # Apply nested defaults
            nested = resource_config.get("nested", {})
            self._apply_nested_defaults(schema, nested, all_schemas)

            # Apply recommended values for required fields
            recommended = resource_config.get("recommended", {})
            self._apply_recommended_to_properties(schema, recommended)

            # Apply nested recommended values (within nested objects like http_health_check)
            self._apply_nested_recommended(schema, nested, all_schemas)

            # Apply OneOf recommended variants (top-level)
            oneof_recommended = resource_config.get("oneof_recommended", {})
            self._apply_oneof_recommended(schema, oneof_recommended)

            # Apply nested OneOf recommended variants (within $ref schemas)
            self._apply_nested_oneof_recommended(schema, nested, all_schemas)

    def _match_resources(self, schema_name: str) -> list[tuple[str, dict[str, Any]]]:
        """Return every resource configuration matching a schema name.

        Uses pre-compiled regex patterns for efficient matching.

        Args:
            schema_name: Name of the schema to match

        Returns:
            Resource configurations in declared order
        """
        return [
            (resource_name, self.resources[resource_name])
            for resource_name, pattern in self._compiled_patterns.items()
            if pattern.search(schema_name)
        ]

    @classmethod
    def _flatten_writes(
        cls,
        resource_config: dict[str, Any],
        prefix: str = "",
    ) -> dict[str, Any]:
        """Flatten configured output slots for overlap conflict detection."""
        writes: dict[str, Any] = {}
        for section in ("defaults", "recommended", "oneof_recommended"):
            writes.update(
                {
                    f"{prefix}{section}.{entry_name}": entry_value
                    for entry_name, entry_value in resource_config.get(section, {}).items()
                },
            )
        for parent_name, nested_config in resource_config.get("nested", {}).items():
            writes.update(
                cls._flatten_writes(
                    nested_config,
                    f"{prefix}nested.{parent_name}.",
                ),
            )
        return writes

    @classmethod
    def _reject_ambiguous_matches(
        cls,
        schema_name: str,
        resource_configs: list[tuple[str, dict[str, Any]]],
    ) -> None:
        """Reject overlapping resource patterns that assign conflicting values."""
        assignments: dict[str, tuple[str, Any]] = {}
        for resource_name, resource_config in resource_configs:
            for output_path, value in cls._flatten_writes(resource_config).items():
                previous = assignments.get(output_path)
                if previous is not None and not exact_json_value_equal(previous[1], value):
                    raise DefaultValueConfigError(
                        f"schema {schema_name!r} has ambiguous {output_path!r} values from "
                        f"resources {previous[0]!r} and {resource_name!r}",
                    )
                assignments[output_path] = (resource_name, value)

    def _apply_defaults_to_properties(
        self,
        schema: dict[str, Any],
        defaults: dict[str, Any],
    ) -> None:
        """Apply default values to schema properties.

        Args:
            schema: Schema definition
            defaults: Dictionary of property_name -> default_value
        """
        properties = schema.get("properties", {})
        if not properties:
            return

        for prop_name, default_value in defaults.items():
            if prop_name in properties:
                self._apply_server_default(
                    properties[prop_name],
                    default_value,
                    nested=False,
                )

    def _apply_server_default(
        self,
        property_schema: dict[str, Any],
        default_value: Any,
        *,
        nested: bool,
    ) -> None:
        """Represent one measured server default without invalid OpenAPI nulls."""
        existing_marker = property_schema.get(X_F5XC_SERVER_DEFAULT)
        if X_F5XC_SERVER_DEFAULT in property_schema and existing_marker is not True:
            raise DefaultValueConfigError(
                "existing x-f5xc-server-default marker conflicts with measured default"
            )

        existing_typed = property_schema.get(X_F5XC_SERVER_DEFAULT_VALUE)
        if "default" in property_schema and X_F5XC_SERVER_DEFAULT_VALUE in property_schema:
            raise DefaultValueConfigError(
                "property declares both default and x-f5xc-server-default-value"
            )
        if default_value is None:
            if "default" in property_schema and not exact_json_value_equal(
                property_schema["default"], None
            ):
                raise DefaultValueConfigError(
                    "existing OpenAPI default conflicts with measured null default"
                )
            if X_F5XC_SERVER_DEFAULT_VALUE in property_schema and not exact_json_value_equal(
                existing_typed,
                {"type": "null", "value": None},
            ):
                raise DefaultValueConfigError(
                    "existing typed server default conflicts with measured null default"
                )
        else:
            if "default" in property_schema and not exact_json_value_equal(
                property_schema["default"], default_value
            ):
                raise DefaultValueConfigError(
                    "existing OpenAPI default conflicts with measured server default"
                )
            if X_F5XC_SERVER_DEFAULT_VALUE in property_schema:
                raise DefaultValueConfigError(
                    "existing typed server default conflicts with measured non-null default"
                )

        if self.settings["use_openapi_default"]:
            if default_value is None:
                property_schema.pop("default", None)
                property_schema[X_F5XC_SERVER_DEFAULT_VALUE] = {
                    "type": "null",
                    "value": None,
                }
            else:
                property_schema["default"] = default_value
                property_schema.pop(X_F5XC_SERVER_DEFAULT_VALUE, None)
            if nested:
                self.stats.nested_defaults_added += 1
            else:
                self.stats.defaults_added += 1

        if self.settings["add_marker_extension"]:
            property_schema[X_F5XC_SERVER_DEFAULT] = True
            self.stats.markers_added += 1

    @staticmethod
    def _resolve_nested_target(
        parent_property: dict[str, Any],
        all_schemas: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Resolve inline, array-item, direct-ref, or allOf-wrapped schemas."""
        schema_node = parent_property
        if parent_property.get("type") == "array" and isinstance(
            parent_property.get("items"), dict
        ):
            schema_node = parent_property["items"]

        nested_properties = schema_node.get("properties", {})
        target_schema = schema_node if nested_properties else None
        references: list[Any] = []
        if "$ref" in schema_node:
            references.append(schema_node["$ref"])
        all_of = schema_node.get("allOf", [])
        if not isinstance(all_of, list):
            raise DefaultValueConfigError("nested schema allOf must be an array")
        references.extend(
            item["$ref"] for item in all_of if isinstance(item, dict) and "$ref" in item
        )
        if any(not isinstance(ref, str) or not ref for ref in references):
            raise DefaultValueConfigError("nested schema references must be non-empty strings")
        unique_references = sorted(set(references))
        if len(unique_references) > 1:
            raise DefaultValueConfigError(
                f"nested schema has ambiguous reference targets: {unique_references}"
            )
        ref_path = unique_references[0] if unique_references else None
        if isinstance(ref_path, str):
            ref_schema = all_schemas.get(ref_path.split("/")[-1])
            if isinstance(ref_schema, dict):
                target_schema = ref_schema
                nested_properties = ref_schema.get("properties", {})
            else:
                target_schema = None
                nested_properties = {}
        return nested_properties, target_schema

    def _apply_nested_defaults(
        self,
        schema: dict[str, Any],
        nested: dict[str, dict[str, Any]],
        all_schemas: dict[str, Any],
        depth: int = 0,
        max_depth: int = 5,
    ) -> None:
        """Apply nested default values to schema properties.

        For nested objects like http_health_check within healthcheck,
        this applies defaults to properties within the nested object.
        Supports both inline properties and $ref references.
        Recurses into sub-nested configs up to max_depth levels.

        Nested configuration is structured with explicit defaults, recommended,
        oneof_recommended, and nested sections.

        Args:
            schema: Schema definition
            nested: Dictionary of property_name -> nested config
            all_schemas: All schemas from the spec for $ref resolution
            depth: Current recursion depth
            max_depth: Maximum recursion depth to prevent infinite loops
        """
        if not nested or depth >= max_depth:
            return

        properties = schema.get("properties", {})
        if not properties:
            return

        for parent_prop_name, nested_config in nested.items():
            if parent_prop_name not in properties:
                continue

            parent_prop = properties[parent_prop_name]

            nested_properties, target_schema = self._resolve_nested_target(
                parent_prop,
                all_schemas,
            )

            if not nested_properties:
                continue

            nested_defaults = nested_config.get("defaults", {})

            for nested_prop_name, default_value in nested_defaults.items():
                if nested_prop_name in nested_properties:
                    self._apply_server_default(
                        nested_properties[nested_prop_name],
                        default_value,
                        nested=True,
                    )

            # Recurse into sub-nested configs
            sub_nested = nested_config.get("nested", {}) if isinstance(nested_config, dict) else {}
            if sub_nested and target_schema:
                self._apply_nested_defaults(
                    target_schema, sub_nested, all_schemas, depth + 1, max_depth
                )

    def _apply_recommended_to_properties(
        self,
        schema: dict[str, Any],
        recommended: dict[str, Any],
    ) -> None:
        """Apply recommended values to schema properties.

        Recommended values are suggested values for required fields that match
        what the F5 XC web interface pre-populates. Unlike defaults (which are
        server-applied when omitted), recommended values are suggestions for
        fields that must be explicitly provided.

        Args:
            schema: Schema definition
            recommended: Dictionary of property_name -> recommended_value
        """
        if not recommended:
            return

        properties = schema.get("properties", {})
        if not properties:
            return

        for prop_name, recommended_value in recommended.items():
            if prop_name in properties:
                prop_schema = properties[prop_name]

                # Add x-f5xc-recommended-value extension
                if X_F5XC_RECOMMENDED_VALUE in prop_schema and not exact_json_value_equal(
                    prop_schema[X_F5XC_RECOMMENDED_VALUE], recommended_value
                ):
                    raise DefaultValueConfigError(
                        "existing recommended value conflicts with configured recommendation"
                    )
                prop_schema[X_F5XC_RECOMMENDED_VALUE] = recommended_value
                self.stats.recommended_added += 1

    def _apply_nested_recommended(
        self,
        schema: dict[str, Any],
        nested: dict[str, dict[str, Any]],
        all_schemas: dict[str, Any],
        depth: int = 0,
        max_depth: int = 5,
    ) -> None:
        """Apply recommended values to nested object properties.

        For nested objects like http_health_check within healthcheck,
        this applies x-f5xc-recommended-value to properties within the nested object.
        Supports both inline properties and $ref references.
        Recurses into sub-nested configs up to max_depth levels.

        Only processes nested configs that have a 'recommended' sub-key.

        Args:
            schema: Schema definition
            nested: Dictionary of property_name -> nested config (with recommended sub-key)
            all_schemas: All schemas from the spec for $ref resolution
            depth: Current recursion depth
            max_depth: Maximum recursion depth to prevent infinite loops
        """
        if not nested or depth >= max_depth:
            return

        properties = schema.get("properties", {})
        if not properties:
            return

        for parent_prop_name, nested_config in nested.items():
            if parent_prop_name not in properties:
                continue

            parent_prop = properties[parent_prop_name]

            nested_properties, target_schema = self._resolve_nested_target(
                parent_prop,
                all_schemas,
            )

            if not nested_properties:
                continue

            # Apply recommended values at this level
            if "recommended" in nested_config:
                nested_recommended = nested_config["recommended"]
                for nested_prop_name, recommended_value in nested_recommended.items():
                    if nested_prop_name in nested_properties:
                        nested_prop_schema = nested_properties[nested_prop_name]
                        if X_F5XC_RECOMMENDED_VALUE in nested_prop_schema and not (
                            exact_json_value_equal(
                                nested_prop_schema[X_F5XC_RECOMMENDED_VALUE],
                                recommended_value,
                            )
                        ):
                            raise DefaultValueConfigError(
                                "existing nested recommendation conflicts with configured value"
                            )
                        nested_prop_schema[X_F5XC_RECOMMENDED_VALUE] = recommended_value
                        self.stats.nested_recommended_added += 1

            # Recurse into sub-nested configs
            sub_nested = nested_config.get("nested", {}) if isinstance(nested_config, dict) else {}
            if sub_nested and target_schema:
                self._apply_nested_recommended(
                    target_schema, sub_nested, all_schemas, depth + 1, max_depth
                )

    def _apply_nested_oneof_recommended(
        self,
        schema: dict[str, Any],
        nested: dict[str, dict[str, Any]],
        all_schemas: dict[str, Any],
        depth: int = 0,
        max_depth: int = 5,
    ) -> None:
        """Apply OneOf recommended variants to nested schemas referenced via $ref.

        For nested objects like http_health_check within healthcheck,
        this applies x-f5xc-recommended-oneof-variant to the referenced schema
        when the nested config contains an 'oneof_recommended' sub-key.
        Recurses into sub-nested configs up to max_depth levels.

        Args:
            schema: Schema definition
            nested: Dictionary of property_name -> nested config (with oneof_recommended sub-key)
            all_schemas: All schemas from the spec for $ref resolution
            depth: Current recursion depth
            max_depth: Maximum recursion depth to prevent infinite loops
        """
        if not nested or depth >= max_depth:
            return

        properties = schema.get("properties", {})
        if not properties:
            return

        for parent_prop_name, nested_config in nested.items():
            if parent_prop_name not in properties:
                continue

            parent_prop = properties[parent_prop_name]
            _, target_schema = self._resolve_nested_target(parent_prop, all_schemas)

            # Apply oneof_recommended at this level
            if "oneof_recommended" in nested_config and target_schema is not None:
                nested_oneof_recommended = nested_config["oneof_recommended"]
                self._apply_oneof_recommended(target_schema, nested_oneof_recommended)

            # Recurse into sub-nested configs
            sub_nested = nested_config.get("nested", {}) if isinstance(nested_config, dict) else {}
            if sub_nested and target_schema:
                self._apply_nested_oneof_recommended(
                    target_schema, sub_nested, all_schemas, depth + 1, max_depth
                )

    def _apply_oneof_recommended(
        self,
        schema: dict[str, Any],
        oneof_recommended: dict[str, str],
    ) -> None:
        """Apply recommended OneOf variant extension to schema.

        For schemas with OneOf fields (like health_check with http_health_check,
        tcp_health_check variants), this marks the recommended variant.

        The x-f5xc-recommended-oneof-variant extension is added at the schema level
        for each OneOf group, indicating which variant is recommended.

        Args:
            schema: Schema definition
            oneof_recommended: Dictionary of oneof_group_name -> recommended_variant
        """
        if not oneof_recommended:
            return

        # Add recommendations only to schemas that declare the exact group and
        # variant. Resource regexes can intentionally span related schemas that
        # do not all expose the same oneOf groups; an entirely unbound config
        # entry is rejected by the graph-wide reachability audit.
        for oneof_group, recommended_variant in oneof_recommended.items():
            if not self._oneof_recommendation_applies(
                schema,
                oneof_group,
                recommended_variant,
            ):
                continue
            # Store as a nested dict keyed by group name
            if X_F5XC_RECOMMENDED_ONEOF_VARIANT not in schema:
                schema[X_F5XC_RECOMMENDED_ONEOF_VARIANT] = {}
            elif not isinstance(schema[X_F5XC_RECOMMENDED_ONEOF_VARIANT], dict):
                raise DefaultValueConfigError(
                    "existing recommended oneOf extension must be a mapping"
                )
            variants = schema[X_F5XC_RECOMMENDED_ONEOF_VARIANT]
            if oneof_group in variants and variants[oneof_group] != recommended_variant:
                raise DefaultValueConfigError(
                    f"existing recommendation for oneOf group {oneof_group!r} conflicts"
                )
            variants[oneof_group] = recommended_variant
            self.stats.oneof_recommended_added += 1

    @staticmethod
    def _oneof_recommendation_applies(
        schema: dict[str, Any],
        group_name: str,
        variant_name: str,
    ) -> bool:
        """Validate one declared oneOf group and return whether it exists here."""
        declaration = schema.get(f"{_ONEOF_DECLARATION_PREFIX}{group_name}")
        if declaration is None:
            return False
        if not isinstance(declaration, str):
            raise DefaultValueConfigError(
                f"oneOf group {group_name!r} declaration must be a JSON string",
            )
        try:
            variants = json.loads(declaration)
        except json.JSONDecodeError as exc:
            raise DefaultValueConfigError(
                f"oneOf group {group_name!r} declaration is not valid JSON",
            ) from exc
        if (
            not isinstance(variants, list)
            or not variants
            or any(not isinstance(value, str) or not value for value in variants)
            or len(set(variants)) != len(variants)
        ):
            raise DefaultValueConfigError(
                f"oneOf group {group_name!r} declaration must be a nonempty unique string list",
            )
        if variant_name not in variants:
            raise DefaultValueConfigError(
                f"recommended oneOf variant {variant_name!r} is not declared by group "
                f"{group_name!r}",
            )
        return True

    def get_stats(self) -> dict[str, Any]:
        """Get enrichment statistics.

        Returns:
            Statistics dictionary
        """
        return self.stats.to_dict()
