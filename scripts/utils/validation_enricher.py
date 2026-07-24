# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Validation Rule Enricher for OpenAPI specifications.

Adds OpenAPI validation constraints (pattern, minLength, maxLength, minimum, maximum, etc.)
to schema properties based on field types and names.

Conservative approach: only applies well-established validation rules.
Respects existing constraints (never overwrites).
Merges with discovery constraints when available.

Version: v3.0.0 - Uses x-f5xc-* namespace constants
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from scripts.utils.extension_constants import X_F5XC_VALIDATION


@dataclass
class ValidationEnrichmentStats:
    """Statistics from validation enrichment."""

    patterns_added: int = 0
    constraints_added: int = 0
    properties_processed: int = 0
    schemas_processed: int = 0
    conflicts_detected: int = 0

    def to_dict(self) -> dict[str, int]:
        """Convert stats to dictionary."""
        return {
            "patterns_added": self.patterns_added,
            "constraints_added": self.constraints_added,
            "properties_processed": self.properties_processed,
            "schemas_processed": self.schemas_processed,
            "conflicts_detected": self.conflicts_detected,
        }


class ValidationEnricher:
    """Add OpenAPI validation constraints to properties.

    Applies validation rules based on field types and names.
    Configuration-driven from validation_rules.yaml.
    Preserves existing constraints and merges with discovery data.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize enricher with validation rule configuration.

        Args:
            config_path: Path to validation_rules.yaml config.
                        Defaults to config/validation_rules.yaml.
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "validation_rules.yaml"

        self.config_path = config_path
        self.type_defaults: dict[str, dict[str, Any]] = {}
        self.validation_patterns: list[dict[str, Any]] = []
        self._compiled_patterns: list[tuple[re.Pattern, dict]] = []
        self.merge_discovery_constraints = True
        self.reconciliation_strategy = "existing > discovery > inferred"
        self.stats = ValidationEnrichmentStats()

        self._load_config()

    def _load_config(self) -> None:
        """Load validation rules from YAML config."""
        if not self.config_path.exists():
            self._use_default_config()
            return

        try:
            with self.config_path.open() as f:
                config = yaml.safe_load(f) or {}

            self.type_defaults = config.get("type_defaults", {})
            self.validation_patterns = config.get("validation_patterns", [])
            self.merge_discovery_constraints = config.get(
                "merge_discovery_constraints",
                True,
            )
            self.reconciliation_strategy = config.get(
                "reconciliation_strategy",
                "existing > discovery > inferred",
            )

            self._compile_patterns()
        except Exception:
            self._use_default_config()

    def _use_default_config(self) -> None:
        """Use built-in default validation rules."""
        self.type_defaults = {
            "string": {
                "maxLength": 1024,
            },
            "integer": {},
        }

        self.validation_patterns = [
            {
                "pattern": r"\bemail$",
                "format": "email",
                "regex_pattern": r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$",
                "confidence": 0.99,
            },
            {
                "pattern": r"\bport$",
                "minimum": 1,
                "maximum": 65535,
                "confidence": 0.99,
            },
            {
                "pattern": r"\b(vlan)?_?id$",
                "minimum": 1,
                "maximum": 4094,
                "confidence": 0.95,
            },
            {
                "pattern": r"\burl$",
                "format": "uri",
                "confidence": 0.95,
            },
            {
                "pattern": r"\buuid$",
                "format": "uuid",
                "confidence": 0.99,
            },
            {
                "pattern": r"\btimestamp$",
                "format": "date-time",
                "confidence": 0.95,
            },
        ]

        self.merge_discovery_constraints = True
        self.reconciliation_strategy = "existing > discovery > inferred"

    def _compile_patterns(self) -> None:
        """Compile regex patterns for efficient matching."""
        for pattern_config in self.validation_patterns:
            pattern_str = pattern_config.get("pattern", "")
            if not pattern_str:
                continue

            try:
                compiled = re.compile(pattern_str)
                self._compiled_patterns.append((compiled, pattern_config))
            except re.error:
                continue

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Enrich OpenAPI specification with validation rules.

        Args:
            spec: OpenAPI specification dictionary

        Returns:
            Specification with added validation constraints
        """
        return self._enrich_recursive(spec)

    def _enrich_recursive(self, obj: Any) -> Any:
        """Recursively traverse and enrich spec object.

        Args:
            obj: Object to process (dict, list, or primitive)

        Returns:
            Enriched object
        """
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                if key == "properties" and isinstance(value, dict):
                    result[key] = self._enrich_properties(value)
                elif key == "schemas" and isinstance(value, dict):
                    result[key] = {
                        schema_name: self._enrich_schema(schema, schema_name)
                        for schema_name, schema in value.items()
                    }
                else:
                    result[key] = self._enrich_recursive(value)
            return result

        if isinstance(obj, list):
            return [self._enrich_recursive(item) for item in obj]

        return obj

    def _enrich_schema(self, schema: dict[str, Any], _schema_name: str) -> dict[str, Any]:
        """Enrich a single schema definition.

        Args:
            schema: Schema definition
            _schema_name: Name of the schema (unused, kept for interface compatibility)

        Returns:
            Enriched schema
        """
        self.stats.schemas_processed += 1
        result = schema.copy()

        if "properties" in result and isinstance(result["properties"], dict):
            result["properties"] = self._enrich_properties(result["properties"])

        if "items" in result:
            result["items"] = self._enrich_recursive(result["items"])

        if "oneOf" in result:
            result["oneOf"] = [self._enrich_recursive(item) for item in result["oneOf"]]

        if "allOf" in result:
            result["allOf"] = [self._enrich_recursive(item) for item in result["allOf"]]

        if "anyOf" in result:
            result["anyOf"] = [self._enrich_recursive(item) for item in result["anyOf"]]

        return result

    def _enrich_properties(self, properties: dict[str, Any]) -> dict[str, Any]:
        """Enrich all properties in a properties object.

        Args:
            properties: Properties dictionary from schema

        Returns:
            Enriched properties dictionary
        """
        result = {}

        for prop_name, prop_schema in properties.items():
            self.stats.properties_processed += 1
            enriched = prop_schema.copy() if isinstance(prop_schema, dict) else prop_schema

            if isinstance(enriched, dict):
                self._enrich_property(enriched, prop_name)

            result[prop_name] = enriched

        return result

    def _enrich_property(self, prop: dict[str, Any], prop_name: str) -> None:
        """Enrich a single property with validation constraints.

        Modifies prop dict in-place. Never overwrites existing constraints.

        Args:
            prop: Property definition to enrich
            prop_name: Name of the property
        """
        # Apply pattern-based validation rules FIRST (more specific than type defaults)
        self._apply_pattern_rules(prop, prop_name)

        # Apply type defaults (generic fallback for unconstrained fields)
        self._apply_type_defaults(prop)

        # Merge with discovery constraints if available
        if self.merge_discovery_constraints:
            self._merge_discovery_constraints(prop, prop_name)

        # Reconcile any conflicts
        self._reconcile_conflicts(prop)

    def _apply_type_defaults(self, prop: dict[str, Any]) -> None:
        """Apply type-level default validation constraints.

        Args:
            prop: Property definition to update
        """
        prop_type = prop.get("type")
        if not prop_type or prop_type not in self.type_defaults:
            return

        defaults = self.type_defaults[prop_type]

        for key, value in defaults.items():
            # Skip metadata keys that are for configuration documentation, not OpenAPI spec
            if key in ["comment", "confidence"]:
                continue

            # Only apply if not already present (preserve existing)
            if key not in prop:
                prop[key] = value
                self.stats.constraints_added += 1

    def _apply_pattern_rules(self, prop: dict[str, Any], prop_name: str) -> None:
        """Apply pattern-based validation rules.

        Args:
            prop: Property definition to update
            prop_name: Name of the property
        """
        prop_type = prop.get("type")
        is_numeric = prop_type in ("integer", "number")

        for compiled_pattern, rule_config in self._compiled_patterns:
            if not compiled_pattern.search(prop_name):
                continue

            # Apply each constraint from the rule
            for constraint_key in ["format", "minimum", "maximum", "regex_pattern"]:
                if constraint_key not in rule_config:
                    continue

                # Numeric bounds are meaningless on non-numeric fields. A
                # name-pattern like `\b(vlan)?_?id$` matches a bare string `id`
                # too — never stamp minimum/maximum unless the field is numeric.
                if constraint_key in ("minimum", "maximum") and not is_numeric:
                    continue

                # Map regex_pattern to OpenAPI pattern constraint
                openapi_key = "pattern" if constraint_key == "regex_pattern" else constraint_key

                # Never overwrite existing constraints
                if openapi_key not in prop:
                    prop[openapi_key] = rule_config[constraint_key]
                    self.stats.patterns_added += 1
                    self.stats.constraints_added += 1

    def _merge_discovery_constraints(self, prop: dict[str, Any], _prop_name: str) -> None:
        """Merge constraints from discovery data if available.

        Discovery constraints are stored in x-f5xc-validation extension.

        Args:
            prop: Property definition to update
            _prop_name: Name of the property (unused, kept for interface compatibility)
        """
        discovery_rules = prop.get(X_F5XC_VALIDATION, {})
        if not discovery_rules:
            return

        # Merge discovery constraints following reconciliation strategy
        for key, value in discovery_rules.items():
            if key not in prop:
                # Only add if not already present
                prop[key] = value
                self.stats.constraints_added += 1

    def _reconcile_conflicts(self, prop: dict[str, Any]) -> None:
        """Reconcile conflicts between different constraint sources.

        Priority: existing > discovery > inferred

        Args:
            prop: Property definition to reconcile
        """
        # Check for conflicting min/max constraints
        if "minimum" in prop and "minLength" in prop:
            # Type mismatch detected
            self.stats.conflicts_detected += 1

        if "maximum" in prop and "maxLength" in prop:
            # Type mismatch detected
            self.stats.conflicts_detected += 1

        # Check for conflicting format and pattern
        if "format" in prop and "pattern" in prop:
            # This is actually valid - format for structured types, pattern for regex
            # No conflict
            pass

    def get_stats(self) -> dict[str, int]:
        """Get enrichment statistics.

        Returns:
            Dictionary with enrichment metrics
        """
        return self.stats.to_dict()
