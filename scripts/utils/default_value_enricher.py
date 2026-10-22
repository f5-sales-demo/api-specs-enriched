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

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from openapi_schema_validator import OAS30Validator, OAS31Validator
from referencing import Registry

from .extension_constants import (
    X_F5XC_RECOMMENDED_ONEOF_VARIANT,
    X_F5XC_RECOMMENDED_VALUE,
    X_F5XC_SERVER_DEFAULT,
)

logger = logging.getLogger(__name__)


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
    null_defaults_rejected: int = 0
    rejected_null_defaults: list[dict[str, str]] = field(default_factory=list)
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
            "null_defaults_rejected": self.null_defaults_rejected,
            "rejected_null_defaults": self.rejected_null_defaults,
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
        self._validator_class = OAS30Validator
        self._compiled_patterns: dict[str, re.Pattern[str]] = {}

        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        try:
            with self.config_path.open() as f:
                self.config = yaml.safe_load(f) or {}
                self.resources = self.config.get("resources", {})
                self.settings = self.config.get("settings", {})

                # Pre-compile regex patterns for performance
                for resource_name, resource_config in self.resources.items():
                    pattern = resource_config.get("schema_pattern")
                    if pattern:
                        try:
                            self._compiled_patterns[resource_name] = re.compile(pattern)
                        except re.error as e:
                            logger.warning(
                                "Invalid regex pattern for %s: %s - %s",
                                resource_name,
                                pattern,
                                e,
                            )

                logger.info("Loaded discovered_defaults from %s", self.config_path)
                logger.info("Found %d resource definitions", len(self.resources))
        except FileNotFoundError:
            logger.warning("Configuration file not found: %s", self.config_path)
            self.config = {}
            self.resources = {}
            self.settings = {}
        except yaml.YAMLError:
            logger.exception("Error parsing configuration")
            self.config = {}
            self.resources = {}
            self.settings = {}

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Enrich OpenAPI specification with server-applied default values.

        Args:
            spec: OpenAPI specification dictionary

        Returns:
            Enriched specification
        """
        if not self.resources:
            logger.debug("No resource definitions loaded, skipping default value enrichment")
            return spec

        self._validator_class = (
            OAS31Validator if str(spec.get("openapi", "")).startswith("3.1") else OAS30Validator
        )
        schemas = spec.get("components", {}).get("schemas", {})
        logger.info("Enriching %d schemas with server-applied defaults", len(schemas))

        for schema_name, schema in schemas.items():
            self.stats.schemas_processed += 1
            self._enrich_schema(schema_name, schema, schemas)

        logger.info("Default value enrichment complete: %s", self.stats.to_dict())
        return spec

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
        resource_config = self._match_resource(schema_name)

        if not resource_config:
            return

        self.stats.schemas_matched += 1

        try:
            # Apply top-level defaults
            defaults = resource_config.get("defaults", {})
            self._apply_defaults_to_properties(schema, defaults, all_schemas)

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

        except Exception as e:
            logger.exception("Error enriching schema %s with defaults", schema_name)
            self.stats.errors.append(
                {
                    "schema": schema_name,
                    "error": str(e),
                },
            )

    def _match_resource(self, schema_name: str) -> dict[str, Any] | None:
        """Match schema name to resource configuration.

        Uses pre-compiled regex patterns for efficient matching.

        Args:
            schema_name: Name of the schema to match

        Returns:
            Resource configuration if matched, None otherwise
        """
        for resource_name, pattern in self._compiled_patterns.items():
            if pattern.search(schema_name):
                return self.resources.get(resource_name, {})

        return None

    def _apply_defaults_to_properties(
        self,
        schema: dict[str, Any],
        defaults: dict[str, Any],
        all_schemas: dict[str, Any] | None = None,
    ) -> None:
        """Apply default values to schema properties.

        Args:
            schema: Schema definition
            defaults: Dictionary of property_name -> default_value
            all_schemas: Local reference targets for validating null defaults
        """
        properties = schema.get("properties", {})
        if not properties:
            return

        use_default = self.settings.get("use_openapi_default", True)
        add_marker = self.settings.get("add_marker_extension", True)

        for prop_name, default_value in defaults.items():
            if prop_name in properties:
                prop_schema = properties[prop_name]
                if default_value is None and self._reject_null_default(
                    prop_schema, all_schemas, prop_name
                ):
                    continue

                # Add OpenAPI standard 'default' field
                if use_default:
                    prop_schema["default"] = default_value
                    self.stats.defaults_added += 1

                # Add marker extension to indicate server-applied default
                if add_marker:
                    prop_schema[X_F5XC_SERVER_DEFAULT] = True
                    self.stats.markers_added += 1

    def _reject_null_default(
        self,
        schema: dict[str, Any],
        all_schemas: dict[str, Any] | None,
        property_name: str,
    ) -> bool:
        """Retain discovery evidence without promoting an invalid request default."""
        document = {
            "type": "object",
            "properties": {"value": schema},
            "components": {"schemas": all_schemas or {}},
        }
        try:
            # An explicit empty registry prohibits remote reference retrieval.
            allowed = self._validator_class(document, registry=Registry()).is_valid({"value": None})
        except Exception:
            allowed = False
        if allowed:
            return False
        self.stats.null_defaults_rejected += 1
        self.stats.rejected_null_defaults.append(
            {"property": property_name, "reason": "null_not_accepted_by_request_schema"}
        )
        return True

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

        Handles two config formats:
        1. Flat: {prop_name: default_value} - legacy format
        2. Structured: {defaults: {prop_name: default_value}, recommended: {...}}

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

        use_default = self.settings.get("use_openapi_default", True)
        add_marker = self.settings.get("add_marker_extension", True)

        for parent_prop_name, nested_config in nested.items():
            if parent_prop_name not in properties:
                continue

            parent_prop = properties[parent_prop_name]

            # Resolve nested properties (inline, via $ref, or via allOf wrapping)
            nested_properties = parent_prop.get("properties", {})
            ref_schema = None

            if "$ref" in parent_prop:
                ref_path = parent_prop["$ref"]
                ref_schema_name = ref_path.split("/")[-1]
                if ref_schema_name in all_schemas:
                    ref_schema = all_schemas[ref_schema_name]
                    nested_properties = ref_schema.get("properties", {})
            elif "allOf" in parent_prop:
                for item in parent_prop["allOf"]:
                    if "$ref" in item:
                        ref_path = item["$ref"]
                        ref_schema_name = ref_path.split("/")[-1]
                        if ref_schema_name in all_schemas:
                            ref_schema = all_schemas[ref_schema_name]
                            nested_properties = ref_schema.get("properties", {})
                        break

            if not nested_properties:
                continue

            # Handle structured format with 'defaults' sub-key
            if "defaults" in nested_config:
                nested_defaults = nested_config["defaults"]
            else:
                # Legacy flat format - entire nested_config is the defaults dict
                nested_defaults = nested_config

            for nested_prop_name, default_value in nested_defaults.items():
                if nested_prop_name in nested_properties:
                    nested_prop_schema = nested_properties[nested_prop_name]
                    if default_value is None and self._reject_null_default(
                        nested_prop_schema, all_schemas, f"{parent_prop_name}.{nested_prop_name}"
                    ):
                        continue

                    if use_default:
                        nested_prop_schema["default"] = default_value
                        self.stats.nested_defaults_added += 1

                    if add_marker:
                        nested_prop_schema[X_F5XC_SERVER_DEFAULT] = True
                        self.stats.markers_added += 1

            # Recurse into sub-nested configs
            sub_nested = nested_config.get("nested", {}) if isinstance(nested_config, dict) else {}
            if sub_nested:
                target_schema = ref_schema or parent_prop
                if target_schema:
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

            # Resolve nested properties (inline, via $ref, or via allOf wrapping)
            nested_properties = parent_prop.get("properties", {})
            ref_schema = None

            if "$ref" in parent_prop:
                ref_path = parent_prop["$ref"]
                ref_schema_name = ref_path.split("/")[-1]
                if ref_schema_name in all_schemas:
                    ref_schema = all_schemas[ref_schema_name]
                    nested_properties = ref_schema.get("properties", {})
            elif "allOf" in parent_prop:
                for item in parent_prop["allOf"]:
                    if "$ref" in item:
                        ref_path = item["$ref"]
                        ref_schema_name = ref_path.split("/")[-1]
                        if ref_schema_name in all_schemas:
                            ref_schema = all_schemas[ref_schema_name]
                            nested_properties = ref_schema.get("properties", {})
                        break

            if not nested_properties:
                continue

            # Apply recommended values at this level
            if "recommended" in nested_config:
                nested_recommended = nested_config["recommended"]
                for nested_prop_name, recommended_value in nested_recommended.items():
                    if nested_prop_name in nested_properties:
                        nested_prop_schema = nested_properties[nested_prop_name]
                        nested_prop_schema[X_F5XC_RECOMMENDED_VALUE] = recommended_value
                        self.stats.nested_recommended_added += 1

            # Recurse into sub-nested configs
            sub_nested = nested_config.get("nested", {}) if isinstance(nested_config, dict) else {}
            if sub_nested:
                target_schema = ref_schema or parent_prop
                if target_schema:
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
            ref_schema = None

            # Handle $ref to another schema - resolve and apply to referenced schema
            if "$ref" in parent_prop:
                ref_path = parent_prop["$ref"]
                ref_schema_name = ref_path.split("/")[-1]
                if ref_schema_name in all_schemas:
                    ref_schema = all_schemas[ref_schema_name]

            # Apply oneof_recommended at this level
            if "oneof_recommended" in nested_config and ref_schema is not None:
                nested_oneof_recommended = nested_config["oneof_recommended"]
                self._apply_oneof_recommended(ref_schema, nested_oneof_recommended)

            # Recurse into sub-nested configs
            sub_nested = nested_config.get("nested", {}) if isinstance(nested_config, dict) else {}
            if sub_nested:
                target_schema = ref_schema or parent_prop
                if target_schema:
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

        # Add the recommended OneOf variants at the schema level
        for oneof_group, recommended_variant in oneof_recommended.items():
            # Store as a nested dict keyed by group name
            if X_F5XC_RECOMMENDED_ONEOF_VARIANT not in schema:
                schema[X_F5XC_RECOMMENDED_ONEOF_VARIANT] = {}
            schema[X_F5XC_RECOMMENDED_ONEOF_VARIANT][oneof_group] = recommended_variant
            self.stats.oneof_recommended_added += 1

    def get_stats(self) -> dict[str, Any]:
        """Get enrichment statistics.

        Returns:
            Statistics dictionary
        """
        return self.stats.to_dict()
