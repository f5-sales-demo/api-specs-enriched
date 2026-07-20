#!/usr/bin/env python3
"""Project naming constraints onto standard JSON-Schema keywords (plan item D).

F5 XC naming rules are enriched into the ``x-f5xc-constraints`` vendor extension.
A downstream consumer using a standard OpenAPI/JSON-Schema validator or code
generator does not read vendor extensions, so it cannot pull those rules
deterministically. This projector runs dead-last in the pipeline (after all
enrichers and the constraint reconciler) and mirrors the naming constraint's
``pattern``/``minLength``/``maxLength``/``format`` up to the standard property
level, overwriting any stale generic value a prior stage may have left there.

Scope: only ``category == "naming"`` constraints are projected, keeping this a
targeted change that serves the resource-naming use case without altering the
thousands of non-naming constraints in the specs.

Usage:
    projector = SchemaConstraintProjector()
    spec = projector.enrich_spec(spec)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Standard JSON-Schema keywords that are safe and meaningful to mirror.
_MIRRORED_KEYS = ("pattern", "minLength", "maxLength", "format")
_CONSTRAINT_KEY = "x-f5xc-constraints"
_NAMING_CATEGORY = "naming"
# Naming/identifier formats that must be projected regardless of the constraint's
# source category (e.g. workload volume names arrive via discovery with
# category="discovery" but format="dns-label").
_NAMING_FORMATS = frozenset({"dns-label", "fqdn", "hostname"})


class SchemaConstraintProjector:
    """Mirror naming ``x-f5xc-constraints`` onto standard JSON-Schema keywords."""

    def __init__(self) -> None:
        """Initialize the projector with empty statistics."""
        self.stats: dict[str, int] = {"properties_projected": 0}

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Project naming constraints across every schema in the spec.

        Args:
            spec: OpenAPI specification dictionary (mutated in place).

        Returns:
            The same spec, with naming constraints mirrored to standard keys.
        """
        schemas = spec.get("components", {}).get("schemas")
        if isinstance(schemas, dict):
            for schema in schemas.values():
                self._visit(schema)
        logger.info(
            "Projected %d naming constraints to standard JSON Schema",
            self.stats["properties_projected"],
        )
        return spec

    def _visit(self, node: Any) -> None:
        """Recursively walk a schema node, projecting naming constraints."""
        if isinstance(node, dict):
            constraint = node.get(_CONSTRAINT_KEY)
            if isinstance(constraint, dict) and self._is_naming(constraint):
                self._project(node, constraint)
            for value in node.values():
                self._visit(value)
        elif isinstance(node, list):
            for item in node:
                self._visit(item)

    @staticmethod
    def _is_naming(constraint: dict[str, Any]) -> bool:
        """Return True if the constraint is naming-related.

        True when its category is ``naming`` or its format is a naming/identifier
        format (covers discovery-sourced volume names carrying category ``discovery``).
        """
        return (
            constraint.get("category") == _NAMING_CATEGORY
            or constraint.get("format") in _NAMING_FORMATS
        )

    def _project(self, schema: dict[str, Any], constraint: dict[str, Any]) -> None:
        """Copy mirrored keys from the constraint block to the standard schema."""
        projected = False
        for key in _MIRRORED_KEYS:
            if key in constraint:
                schema[key] = constraint[key]
                projected = True
        if projected:
            self.stats["properties_projected"] += 1

    def get_stats(self) -> dict[str, int]:
        """Return projection statistics."""
        return dict(self.stats)
