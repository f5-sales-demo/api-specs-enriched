# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Constraint Reconciler Module.

Reconciles discovered API constraints from x-discovered-* extensions
into standard OpenAPI specification fields. Discovery data from the
live API is treated as the source of truth.

Key behaviors:
- Discovered values REPLACE existing published values (default mode)
- Only x-discovered-* fields with OpenAPI equivalents are removed
- Custom x-discovered-* fields (no OpenAPI equivalent) are preserved
- Audit trail maintained with x-original-* and x-reconciled-* extensions
"""

from dataclasses import dataclass, field
from typing import Any, ClassVar

from .build_stamp import artifact_timestamp


@dataclass
class ReconciliationStats:
    """Statistics from constraint reconciliation."""

    reconciled: int = 0
    skipped: int = 0
    preserved: int = 0
    fields: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            "reconciled": self.reconciled,
            "skipped": self.skipped,
            "preserved": self.preserved,
            "fields": self.fields.copy(),
        }


class ConstraintReconciler:
    """Reconciles discovered API constraints into standard OpenAPI fields.

    This class processes specifications that have been enriched with
    x-discovered-* extensions and reconciles those values into the
    standard OpenAPI schema fields (minLength, maxLength, pattern, etc.).

    Discovery values are treated as the source of truth from the live API.
    """

    # Mapping from x-discovered-* extensions to standard OpenAPI fields
    # Note: Discovery enricher uses hyphenated field names
    FIELD_MAPPING: ClassVar[dict[str, str]] = {
        "x-discovered-max-length": "maxLength",
        "x-discovered-min-length": "minLength",
        "x-discovered-pattern": "pattern",
        "x-discovered-format": "format",
        "x-discovered-enum-values": "enum",
        "x-discovered-minimum": "minimum",
        "x-discovered-maximum": "maximum",
        "x-discovered-type": "type",
    }

    def __init__(self, config: dict) -> None:
        """Initialize the constraint reconciler.

        Args:
            config: Reconciliation configuration from YAML
        """
        self.config = config
        self.mode = config.get("mode", "replace")  # Default: discovery is source of truth
        self.confidence_threshold = config.get("confidence_threshold", 0.8)
        self.min_sample_size = config.get("min_sample_size", 5)
        self.field_rules = config.get("field_rules", {})
        self.audit_enabled = config.get("audit_enabled", True)
        self.stats = ReconciliationStats()

    def reconcile_spec(self, spec: dict) -> tuple[dict, dict]:
        """Reconcile all discovered constraints in a specification.

        Args:
            spec: OpenAPI specification with x-discovered-* extensions

        Returns:
            Tuple of (modified_spec, reconciliation_report)
        """
        # Reset stats for this spec
        self.stats = ReconciliationStats()

        # Process component schemas
        if "components" in spec and "schemas" in spec["components"]:
            for schema_name, schema in spec["components"]["schemas"].items():
                if isinstance(schema, dict):
                    self._reconcile_schema(schema, schema_name)

        # Process inline schemas in paths
        if "paths" in spec:
            self._reconcile_paths(spec["paths"])

        return spec, self._generate_report()

    def _reconcile_schema(self, schema: dict, path: str = "") -> None:
        """Recursively reconcile a schema and its nested properties.

        Args:
            schema: Schema to reconcile
            path: Current path for logging
        """
        # Process direct properties
        if "properties" in schema:
            for prop_name, prop_schema in schema["properties"].items():
                if isinstance(prop_schema, dict):
                    self._reconcile_property(prop_schema, f"{path}/{prop_name}")
                    # Recurse into nested objects
                    if prop_schema.get("type") == "object":
                        self._reconcile_schema(prop_schema, f"{path}/{prop_name}")

        # Handle array items
        if schema.get("type") == "array" and "items" in schema:
            items = schema["items"]
            if isinstance(items, dict):
                self._reconcile_schema(items, f"{path}/items")

        # Handle allOf/oneOf/anyOf
        for combiner in ["allOf", "oneOf", "anyOf"]:
            if combiner in schema:
                for i, sub_schema in enumerate(schema[combiner]):
                    if isinstance(sub_schema, dict):
                        self._reconcile_schema(sub_schema, f"{path}/{combiner}[{i}]")

    def _reconcile_property(self, prop: dict, _path: str) -> None:
        """Reconcile discovered fields into standard OpenAPI fields.

        Args:
            prop: Property schema to reconcile
            _path: Property path for logging (unused, kept for future debugging)
        """
        # Check sample size and confidence thresholds
        sample_size = prop.get("x-discovered-sample-size", 0)
        confidence = prop.get("x-discovered-confidence", 1.0)

        # Skip if below confidence thresholds
        if 0 < sample_size < self.min_sample_size:
            self.stats.skipped += 1
            return
        if confidence < self.confidence_threshold:
            self.stats.skipped += 1
            return

        reconciled_any = False
        fields_to_remove = []

        # Process each mapped field
        for discovered_field, standard_field in self.FIELD_MAPPING.items():
            if discovered_field in prop:
                discovered_value = prop[discovered_field]
                published_value = prop.get(standard_field)

                if self._should_reconcile(standard_field, published_value, discovered_value):
                    # Store original value for audit (if overwriting)
                    if self.audit_enabled and published_value is not None:
                        prop[f"x-original-{standard_field}"] = published_value

                    # Apply discovered value to standard field
                    prop[standard_field] = discovered_value
                    reconciled_any = True
                    self.stats.reconciled += 1
                    self.stats.fields[standard_field] = self.stats.fields.get(standard_field, 0) + 1

                # Mark mapped field for removal (reconciled to standard field)
                fields_to_remove.append(discovered_field)

        # Remove only mapped x-discovered-* fields (those reconciled to standard fields)
        for discovered_field in fields_to_remove:
            prop.pop(discovered_field, None)

        # Count preserved x-discovered-* fields (no OpenAPI equivalent)
        for key in list(prop.keys()):
            if key.startswith("x-discovered-") and key not in self.FIELD_MAPPING:
                self.stats.preserved += 1

        # Add reconciliation marker
        if reconciled_any and self.audit_enabled:
            prop["x-reconciled-from-discovery"] = True
            prop["x-reconciled-at"] = artifact_timestamp()
            if sample_size > 0:
                prop["x-reconciled-sample-size"] = sample_size

    def _should_reconcile(
        self,
        field: str,
        published: Any,
        discovered: Any,
    ) -> bool:
        """Determine if discovered value should replace published.

        Args:
            field: Standard OpenAPI field name
            published: Currently published value (or None)
            discovered: Discovered value from live API

        Returns:
            True if discovered value should replace published
        """
        rule = self.field_rules.get(field, {})
        mode = rule.get("mode", self.mode)

        if mode == "add_missing":
            return published is None
        if mode == "tighten":
            return published is None or self._is_tighter(field, published, discovered)
        return mode == "replace"

    def _is_tighter(self, field: str, published: Any, discovered: Any) -> bool:
        """Check if discovered constraint is stricter than published.

        Args:
            field: Standard OpenAPI field name
            published: Published constraint value
            discovered: Discovered constraint value

        Returns:
            True if discovered is tighter/stricter
        """
        if field == "maxLength":
            if isinstance(published, int) and isinstance(discovered, int):
                return discovered < published
        elif field == "minLength":
            if isinstance(published, int) and isinstance(discovered, int):
                return discovered > published
        elif field == "maximum":
            if isinstance(published, (int, float)) and isinstance(discovered, (int, float)):
                return discovered < published
        elif field == "minimum":
            if isinstance(published, (int, float)) and isinstance(discovered, (int, float)):
                return discovered > published
        elif field == "enum" and isinstance(published, list) and isinstance(discovered, list):
            # Tighter if discovered is a subset and smaller
            return set(discovered).issubset(set(published)) and len(discovered) < len(
                published,
            )
        return False

    def _reconcile_paths(self, paths: dict) -> None:
        """Reconcile inline schemas in path operations.

        Args:
            paths: OpenAPI paths object
        """
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if not isinstance(operation, dict):
                    continue

                # Check requestBody schemas
                if "requestBody" in operation:
                    request_body = operation["requestBody"]
                    if isinstance(request_body, dict):
                        content = request_body.get("content", {})
                        for media_obj in content.values():
                            if isinstance(media_obj, dict) and "schema" in media_obj:
                                self._reconcile_schema(
                                    media_obj["schema"],
                                    f"{path}/{method}/requestBody",
                                )

                # Check response schemas
                responses = operation.get("responses", {})
                for status, response in responses.items():
                    if isinstance(response, dict):
                        content = response.get("content", {})
                        for media_obj in content.values():
                            if isinstance(media_obj, dict) and "schema" in media_obj:
                                self._reconcile_schema(
                                    media_obj["schema"],
                                    f"{path}/{method}/responses/{status}",
                                )

    def _generate_report(self) -> dict:
        """Generate reconciliation report.

        Returns:
            Report dictionary with timestamp, mode, and statistics
        """
        return {
            "timestamp": artifact_timestamp(),
            "mode": self.mode,
            "confidence_threshold": self.confidence_threshold,
            "min_sample_size": self.min_sample_size,
            "statistics": self.stats.to_dict(),
        }

    def get_stats(self) -> dict[str, Any]:
        """Get reconciliation statistics.

        Returns:
            Dictionary of statistics
        """
        return self.stats.to_dict()

    def reset_stats(self) -> None:
        """Reset statistics for a new reconciliation run."""
        self.stats = ReconciliationStats()
