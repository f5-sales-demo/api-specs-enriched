# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Cross-field dependency enricher for OpenAPI specifications.

Reads dependency definitions from minimum_configs.yaml and stamps
x-f5xc-requires extensions on schema properties, enabling AI assistants
and CLI tools to understand which sub-fields are required when a parent
field is chosen.

Adds:
- x-f5xc-requires: array of dependency descriptors on parent properties

Issue: #152 - Enrich API specs with cross-field dependency metadata
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .extension_constants import X_F5XC_REQUIRES

logger = logging.getLogger(__name__)

# Matches a cross-resource requirement declared in config in the resource-and-scope
# form (for example, a protected_domain in the same namespace). It is parsed into a
# structured requires_resource descriptor so downstream tooling (the
# terraform-provider-xcsh preflight codegen) can mechanically derive an apply-time
# prerequisite check instead of string-parsing the opaque requires_field. Issue #967.
_RESOURCE_REQUIREMENT_RE = re.compile(
    r"^resource:\s*(?P<resource>[a-z0-9_]+)\s*\(\s*(?P<scope>[^)]+?)\s*\)\s*$"
)


def _parse_resource_requirement(requires: str) -> dict[str, str] | None:
    """Parse a ``resource:<name> (<scope>)`` requirement into a structured form.

    Returns ``{"resource": <name>, "scope": <normalized scope>}`` when the string
    matches the cross-resource form, else ``None`` (e.g. a sibling-field reference
    such as ``spec.enable_waf``). Scope text is normalized to snake_case
    (``"same namespace"`` -> ``"same_namespace"``).
    """
    match = _RESOURCE_REQUIREMENT_RE.match(requires)
    if not match:
        return None
    scope = "_".join(match.group("scope").lower().split())
    return {"resource": match.group("resource"), "scope": scope}


@dataclass
class DependencyEnrichmentStats:
    """Statistics for dependency enrichment."""

    schemas_processed: int = 0
    schemas_matched: int = 0
    dependencies_stamped: int = 0
    dependencies_skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            "schemas_processed": self.schemas_processed,
            "schemas_matched": self.schemas_matched,
            "dependencies_stamped": self.dependencies_stamped,
            "dependencies_skipped": self.dependencies_skipped,
            "error_count": len(self.errors),
            "errors": self.errors,
        }


class DependencyEnricher:
    """Enrich OpenAPI specs with cross-field dependency metadata.

    Configuration-driven enricher that reads dependency definitions from
    minimum_configs.yaml and stamps x-f5xc-requires on matching schema
    properties.

    Uses config/minimum_configs.yaml resources.*.dependencies for definitions.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize enricher with configuration.

        Args:
            config_path: Optional path to minimum_configs.yaml.
                        Defaults to config/minimum_configs.yaml
        """
        self.config_path = (
            config_path or Path(__file__).parent.parent.parent / "config" / "minimum_configs.yaml"
        )
        self.resources: dict[str, dict[str, Any]] = {}
        self.stats = DependencyEnrichmentStats()
        self._compiled_patterns: dict[str, re.Pattern[str]] = {}

        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from YAML file and build dependency mapping."""
        try:
            with self.config_path.open() as f:
                config = yaml.safe_load(f) or {}
                raw_resources = config.get("resources", {})

                for resource_name, resource_config in raw_resources.items():
                    deps = resource_config.get("dependencies")
                    if not deps:
                        continue

                    self.resources[resource_name] = {
                        "dependencies": deps,
                    }
                    # Match schema names like http_loadbalancerCreateSpecType,
                    # origin_poolCreateSpecType, etc.
                    pattern = rf"{re.escape(resource_name)}.*SpecType"
                    try:
                        self._compiled_patterns[resource_name] = re.compile(pattern)
                    except re.error as e:
                        logger.warning(
                            "Invalid regex pattern for %s: %s - %s",
                            resource_name,
                            pattern,
                            e,
                        )

                logger.info(
                    "Loaded dependencies from %s for %d resources",
                    self.config_path,
                    len(self.resources),
                )
        except FileNotFoundError:
            logger.warning("Configuration file not found: %s", self.config_path)
            self.resources = {}
        except yaml.YAMLError:
            logger.exception("Error parsing configuration")
            self.resources = {}

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Enrich OpenAPI specification with cross-field dependency metadata.

        Args:
            spec: OpenAPI specification dictionary

        Returns:
            Enriched specification
        """
        if not self.resources:
            logger.debug("No dependency definitions loaded, skipping")
            return spec

        schemas = spec.get("components", {}).get("schemas", {})
        logger.info("Enriching %d schemas with dependency metadata", len(schemas))

        for schema_name, schema in schemas.items():
            self.stats.schemas_processed += 1
            self._enrich_schema(schema_name, schema)

        logger.info("Dependency enrichment complete: %s", self.stats.to_dict())
        return spec

    def _enrich_schema(self, schema_name: str, schema: dict[str, Any]) -> None:
        """Enrich individual schema with dependency extensions.

        Args:
            schema_name: Name of the schema
            schema: Schema definition
        """
        resource_config = self._match_resource(schema_name)
        if not resource_config:
            return

        self.stats.schemas_matched += 1

        try:
            dependencies = resource_config.get("dependencies", [])
            for dep in dependencies:
                self._stamp_dependency(schema, dep, schema_name)
        except Exception as e:
            logger.exception("Error enriching schema %s with dependencies", schema_name)
            self.stats.errors.append(
                {
                    "schema": schema_name,
                    "error": str(e),
                },
            )

    def _match_resource(self, schema_name: str) -> dict[str, Any] | None:
        """Match schema name to resource configuration.

        Args:
            schema_name: Name of the schema to match

        Returns:
            Resource configuration if matched, None otherwise
        """
        for resource_name, pattern in self._compiled_patterns.items():
            if pattern.search(schema_name):
                return self.resources.get(resource_name)
        return None

    def _parse_field_path(self, field_path: str) -> list[str]:
        """Parse a dot-notation field path into segments.

        Strips the 'spec.' prefix and array brackets from path segments.

        Args:
            field_path: Dot-notation path like 'spec.use_tls.tls_config'

        Returns:
            List of path segments, e.g. ['use_tls', 'tls_config']
        """
        parts = field_path.split(".")
        # Strip 'spec.' prefix
        if parts and parts[0] == "spec":
            parts = parts[1:]
        # Strip array notation like [] from segments
        return [re.sub(r"\[\]", "", p) for p in parts]

    def _stamp_dependency(
        self,
        schema: dict[str, Any],
        dep: dict[str, Any],
        schema_name: str,
    ) -> None:
        """Stamp a single dependency as x-f5xc-requires on the parent property.

        Args:
            schema: Schema definition
            dep: Dependency descriptor from config
            schema_name: Schema name for logging
        """
        field_path = dep.get("field", "")
        if not field_path:
            self.stats.dependencies_skipped += 1
            return

        segments = self._parse_field_path(field_path)
        if not segments:
            self.stats.dependencies_skipped += 1
            return

        # The first segment is the parent property on the schema
        parent_prop = segments[0]
        properties = schema.get("properties", {})

        if parent_prop not in properties:
            logger.debug(
                "Property '%s' not found on schema %s, skipping dependency",
                parent_prop,
                schema_name,
            )
            self.stats.dependencies_skipped += 1
            return

        prop_schema = properties[parent_prop]

        # Build the requires descriptor
        # Remaining segments describe what's required within the parent
        required_field = ".".join(segments[1:]) if len(segments) > 1 else parent_prop
        requires_entry: dict[str, Any] = {
            "field": required_field,
        }

        # Copy relevant fields from the dependency config
        if "required" in dep:
            requires_entry["required"] = dep["required"]
        if "requires" in dep:
            # Alternative format: 'requires' references another top-level field
            # (opaque string, preserved for existing consumers).
            requires_entry["requires_field"] = dep["requires"]
            # Additionally, if it names a cross-resource requirement
            # ("resource:<name> (<scope>)"), emit a structured descriptor so the
            # provider can auto-derive an apply-time preflight. Issue #967.
            resource_requirement = _parse_resource_requirement(str(dep["requires"]))
            if resource_requirement is not None:
                requires_entry["requires_resource"] = resource_requirement
        if "reason" in dep:
            requires_entry["reason"] = dep["reason"]
        if "min_items" in dep:
            requires_entry["min_items"] = dep["min_items"]

        # Append to existing x-f5xc-requires or create new list
        existing = prop_schema.get(X_F5XC_REQUIRES, [])
        existing.append(requires_entry)
        prop_schema[X_F5XC_REQUIRES] = existing
        self.stats.dependencies_stamped += 1

    def get_stats(self) -> dict[str, Any]:
        """Return enrichment statistics.

        Returns:
            Dictionary of enrichment statistics
        """
        return self.stats.to_dict()

    def reset_stats(self) -> None:
        """Reset enrichment statistics."""
        self.stats = DependencyEnrichmentStats()
