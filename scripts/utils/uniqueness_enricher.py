# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Uniqueness Awareness Enricher for OpenAPI specifications.

This enricher adds uniqueness scope metadata to enable downstream tools
(Terraform, CLI, AI assistants, IDE) to validate name conflicts before
API submission.

Architecture:
- Reads: x-f5xc-namespace-profile from spec.info (spec-level, structured object)
- Writes: x-f5xc-uniqueness to components.schemas (schema-level)
- Hybrid pattern: Spec-level inference + Schema-level application

Uniqueness Scopes:
- platform: Globally unique across entire F5 XC
- tenant: Unique within tenant
- namespace: Unique within namespace (most common)

Configuration: config/constraint_patterns.yaml (uniqueness_patterns section)

Issue #503 - Phase 5 expansion of constraint metadata system
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .build_stamp import artifact_timestamp
from .extension_constants import X_F5XC_NAMESPACE_PROFILE, X_F5XC_UNIQUENESS

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class UniquenessStats:
    """Statistics for uniqueness enrichment."""

    schemas_enriched: int = 0
    platform_scoped: int = 0
    tenant_scoped: int = 0
    namespace_scoped: int = 0
    already_had_uniqueness: int = 0
    overrides_applied: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary for pipeline reporting."""
        return {
            "schemas_enriched": self.schemas_enriched,
            "platform_scoped": self.platform_scoped,
            "tenant_scoped": self.tenant_scoped,
            "namespace_scoped": self.namespace_scoped,
            "already_had_uniqueness": self.already_had_uniqueness,
            "overrides_applied": self.overrides_applied,
            "error_count": len(self.errors),
            "errors": self.errors,
        }


class UniquenessEnricher:
    """Enrich OpenAPI schemas with uniqueness scope metadata.

    Reads x-f5xc-namespace-profile from spec.info (spec-level, structured object).
    Writes x-f5xc-uniqueness to components.schemas (schema-level).
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize enricher with configuration.

        Args:
            config_path: Optional path to constraint_patterns.yaml.
                        Defaults to config/constraint_patterns.yaml
        """
        self.config_path = config_path or (
            Path(__file__).parent.parent.parent / "config" / "constraint_patterns.yaml"
        )
        self.config: dict[str, Any] = {}
        self.extension_name: str = X_F5XC_UNIQUENESS
        self.stats = UniquenessStats()

        self._load_config()

    def _load_config(self) -> None:
        """Load uniqueness_patterns from constraint_patterns.yaml."""
        try:
            with self.config_path.open() as f:
                full_config = yaml.safe_load(f) or {}
                self.config = full_config.get("uniqueness_patterns", {})

                if not self.config:
                    logger.warning(
                        "No uniqueness_patterns found in %s",
                        self.config_path,
                    )
                else:
                    logger.info(
                        "Loaded uniqueness patterns from %s",
                        self.config_path,
                    )

        except FileNotFoundError:
            logger.warning("Configuration file not found: %s", self.config_path)
            self.config = {}
        except yaml.YAMLError:
            logger.exception("Error parsing configuration")
            self.config = {}

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Enrich all schemas with uniqueness metadata.

        1. Read namespace profile from spec.info["x-f5xc-namespace-profile"]
        2. Extract the recommended default namespace to derive scope
        3. For each schema in components.schemas:
            - Skip if already has x-f5xc-uniqueness (idempotent)
            - Convert schema name to resource type (HTTPLoadBalancer → http_loadbalancer)
            - Check resource_overrides, else use namespace_scope_mapping
            - Build uniqueness extension with scope, within, fields, metadata
            - Add to schema

        Args:
            spec: OpenAPI specification dictionary

        Returns:
            Enriched specification
        """
        # Validate the profile before the recoverable schema-enrichment boundary.
        # A missing or scalar profile is a contract failure, not a default scope.
        namespace_scope = self._extract_scope_from_profile(
            spec.get("info", {}).get(X_F5XC_NAMESPACE_PROFILE)
        )

        # Enrich each schema. Per-schema failures are recorded and re-raised by
        # _enrich_schema so callers never receive a partially enriched result.
        schemas = spec.get("components", {}).get("schemas", {})
        for schema_name, schema in schemas.items():
            self._enrich_schema(schema, schema_name, namespace_scope)

        return spec

    @staticmethod
    def _extract_scope_from_profile(profile: object) -> str:
        """Extract a namespace scope string from the structured namespace profile.

        The namespace profile is a structured object with constraint, recommendation,
        and classification sections. This method extracts the effective scope by
        checking the recommendation.primary field, falling back to the first allowed
        namespace in constraint.allowed.

        Args:
            profile: The structured x-f5xc-namespace-profile value.

        Returns:
            One of "system", "shared", or "any".
        """
        if not isinstance(profile, dict):
            type_name = type(profile).__name__
            raise TypeError(
                f"{X_F5XC_NAMESPACE_PROFILE} must be a structured object, got {type_name}"
            )

        primary_ns = profile.get("recommendation", {}).get("primary", "")
        if primary_ns == "system":
            return "system"
        if primary_ns == "shared":
            return "shared"

        # Fallback: if only one allowed namespace in constraint.allowed
        allowed = profile.get("constraint", {}).get("allowed", [])
        if allowed == ["system"]:
            return "system"
        if allowed == ["shared"]:
            return "shared"

        return "any"

    def _enrich_schema(
        self,
        schema: dict,
        schema_name: str,
        namespace_scope: str,
    ) -> None:
        """Add uniqueness metadata to a single schema.

        Args:
            schema: Schema definition
            schema_name: Name of the schema (PascalCase)
            namespace_scope: Namespace scope from spec.info
        """
        try:
            # 1. Idempotency check
            if self.extension_name in schema:
                self.stats.already_had_uniqueness += 1
                self.stats.schemas_enriched += 1
                return

            # 2. Convert schema name to resource type
            resource_type = self._schema_name_to_resource_type(schema_name)

            # 3. Check for resource override, else use namespace scope mapping
            if resource_type in self.config.get("resource_overrides", {}):
                uniqueness = self._build_uniqueness_from_override(resource_type)
                self.stats.overrides_applied += 1
            else:
                uniqueness = self._derive_uniqueness_from_namespace_scope(
                    namespace_scope,
                )

            # 4. Add to schema
            schema[self.extension_name] = uniqueness
            self.stats.schemas_enriched += 1

            # Update scope counters
            scope = uniqueness.get("scope")
            if scope == "platform":
                self.stats.platform_scoped += 1
            elif scope == "tenant":
                self.stats.tenant_scoped += 1
            else:
                self.stats.namespace_scoped += 1

            logger.debug(
                "Added %s=%s for schema '%s'",
                self.extension_name,
                scope,
                schema_name,
            )

        except Exception as e:
            logger.exception("Error enriching schema %s", schema_name)
            self.stats.errors.append(
                {
                    "error": str(e),
                    "schema_name": schema_name,
                },
            )
            raise

    def _schema_name_to_resource_type(self, schema_name: str) -> str:
        """Convert PascalCase schema name to snake_case resource type.

        Examples:
            HTTPLoadBalancer → http_loadbalancer
            Certificate → certificate
            AWSVPCSite → aws_vpc_site

        Args:
            schema_name: PascalCase schema name

        Returns:
            snake_case resource type
        """
        # Insert underscores before uppercase letters that follow lowercase
        # or before uppercase letters that are followed by lowercase (for acronyms)
        s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", schema_name)
        s2 = re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1)

        # Convert to lowercase and clean up
        resource_type = s2.lower().strip("_")

        # Remove duplicate underscores
        return re.sub("_+", "_", resource_type)

    def _derive_uniqueness_from_namespace_scope(self, namespace_scope: str) -> dict:
        """Map namespace scope to uniqueness constraints.

        Args:
            namespace_scope: Namespace scope value (system, shared, any)

        Returns:
            Uniqueness metadata dictionary
        """
        mapping = self.config.get("namespace_scope_mapping", {}).get(
            namespace_scope,
            {},
        )

        return {
            "scope": mapping.get("scope", "namespace"),
            "within": mapping.get("within", ["namespace"]),
            "fields": self.config.get("default_fields", ["metadata.name", "name"]),
            "caseSensitive": self.config.get("case_sensitive", True),
            "constraintExplanation": mapping.get("explanation", ""),
            "metadata": {
                "source": "inferred",
                "confidence": mapping.get("confidence", 0.95),
                "validatedAt": artifact_timestamp(),
            },
        }

    def _build_uniqueness_from_override(self, resource_type: str) -> dict:
        """Apply resource-specific uniqueness override.

        Args:
            resource_type: Resource type in snake_case

        Returns:
            Uniqueness metadata dictionary
        """
        override = self.config.get("resource_overrides", {}).get(resource_type, {})

        return {
            "scope": override.get("scope", "namespace"),
            "within": override.get("within", ["namespace"]),
            "fields": self.config.get("default_fields", ["metadata.name", "name"]),
            "caseSensitive": self.config.get("case_sensitive", True),
            "constraintExplanation": override.get("explanation", ""),
            "metadata": {
                "source": "inferred",
                "confidence": override.get("confidence", 0.95),
                "validatedAt": artifact_timestamp(),
            },
        }

    def get_stats(self) -> dict[str, Any]:
        """Get enrichment statistics.

        Returns:
            Statistics dictionary
        """
        return self.stats.to_dict()

    def reset_stats(self) -> None:
        """Reset enrichment statistics."""
        self.stats = UniquenessStats()
