# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Operation Description Enricher for OpenAPI specifications.

Applies enriched operation descriptions from config/operation_descriptions.yaml
to operation metadata purpose field. Provides noun-first, DRY-compliant descriptions
that complement the downstream xcsh CLI pattern: xcsh <domain-group> <verb> <resource-type>

Since users already provide the verb (create/list/get/delete), descriptions focus on
WHAT the resource is/does rather than the action being performed.

Description Tiers:
- short: max 60 chars - CLI help text, tooltips
- medium: max 150 chars - Extended help, summaries
- long: max 500 chars - Full documentation, AI context

Matching Strategy:
1. Exact resource match (e.g., "http_loadbalancer")
2. Pattern-based match (regex against resource type)
3. HTTP method fallback (generic descriptions by method)

Usage:
    enricher = OperationDescriptionEnricher()
    spec = enricher.enrich_spec(spec)
    stats = enricher.get_stats()

Integration:
    Applied BEFORE OperationMetadataEnricher to provide better purpose descriptions.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .extension_constants import X_F5XC_OPERATION_METADATA


@dataclass
class OperationDescriptionStats:
    """Statistics from operation description enrichment."""

    operations_processed: int = 0
    descriptions_applied: int = 0
    exact_matches: int = 0
    pattern_matches: int = 0
    method_fallbacks: int = 0
    descriptions_skipped: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            "operations_processed": self.operations_processed,
            "descriptions_applied": self.descriptions_applied,
            "exact_matches": self.exact_matches,
            "pattern_matches": self.pattern_matches,
            "method_fallbacks": self.method_fallbacks,
            "descriptions_skipped": self.descriptions_skipped,
        }


class OperationDescriptionEnricher:
    """Enrich OpenAPI operations with DRY-compliant, noun-first descriptions.

    Loads enriched descriptions from config/operation_descriptions.yaml and applies
    to x-f5xc-operation-metadata.purpose field using a three-tier matching strategy:
    1. Exact resource type match
    2. Pattern-based regex match
    3. HTTP method fallback

    Attributes:
        config_path: Path to operation_descriptions.yaml
        resources: Dictionary of resource_type -> description tiers
        patterns: List of compiled regex patterns with descriptions
        method_fallbacks: Dictionary of HTTP method -> description tiers
        stats: Enrichment statistics
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize enricher with operation description configuration.

        Args:
            config_path: Path to operation_descriptions.yaml config.
                        Defaults to config/operation_descriptions.yaml.
        """
        if config_path is None:
            config_path = (
                Path(__file__).parent.parent.parent / "config" / "operation_descriptions.yaml"
            )

        self.config_path = config_path
        self.resources: dict[str, dict[str, str]] = {}
        self.patterns: list[dict[str, Any]] = []
        self.method_fallbacks: dict[str, dict[str, str]] = {}
        self.stats = OperationDescriptionStats()
        self.enabled = False

        self._load_config()

    def _load_config(self) -> None:
        """Load operation descriptions from YAML configuration file."""
        if not self.config_path.exists():
            return

        try:
            with self.config_path.open() as f:
                config = yaml.safe_load(f) or {}

            self.enabled = config.get("enabled", False)
            if not self.enabled:
                return

            # Load explicit resource configurations
            resources = config.get("resources", {})
            for resource_name, resource_config in resources.items():
                if isinstance(resource_config, dict):
                    self.resources[resource_name] = {
                        "short": resource_config.get("short", ""),
                        "medium": resource_config.get("medium", ""),
                        "long": resource_config.get("long", ""),
                    }

            # Load pattern-based configurations with compiled regexes
            patterns = config.get("patterns", [])
            for pattern_config in patterns:
                if isinstance(pattern_config, dict):
                    resource_pattern = pattern_config.get("resource_pattern", "")
                    if resource_pattern:
                        self.patterns.append(
                            {
                                "regex": re.compile(resource_pattern),
                                "short": pattern_config.get("short", ""),
                                "medium": pattern_config.get("medium", ""),
                                "long": pattern_config.get("long", ""),
                            },
                        )

            # Load HTTP method fallbacks
            method_fallbacks = config.get("method_fallbacks", {})
            for method, method_config in method_fallbacks.items():
                if isinstance(method_config, dict):
                    self.method_fallbacks[method.upper()] = {
                        "short": method_config.get("short", ""),
                        "medium": method_config.get("medium", ""),
                        "long": method_config.get("long", ""),
                    }

        except yaml.YAMLError:
            # Invalid YAML - disable enrichment
            self.enabled = False

    def get_description(
        self,
        resource_type: str,
        method: str,
        tier: str = "short",
    ) -> str | None:
        """Get operation description using three-tier matching strategy.

        Matching order:
        1. Exact resource type match
        2. Pattern-based regex match (first match wins)
        3. HTTP method fallback

        Args:
            resource_type: Resource type being operated on (e.g., "http_loadbalancer")
            method: HTTP method (GET, POST, PUT, PATCH, DELETE)
            tier: Description tier to return ("short", "medium", "long")

        Returns:
            Description string or None if no match found
        """
        if not self.enabled:
            return None

        # Strategy 1: Exact resource match
        if resource_type in self.resources:
            self.stats.exact_matches += 1
            return self.resources[resource_type].get(tier, "")

        # Strategy 2: Pattern-based match (first match wins)
        for pattern_config in self.patterns:
            if pattern_config["regex"].match(resource_type):
                self.stats.pattern_matches += 1
                return pattern_config.get(tier, "")

        # Strategy 3: HTTP method fallback
        method_upper = method.upper()
        if method_upper in self.method_fallbacks:
            self.stats.method_fallbacks += 1
            return self.method_fallbacks[method_upper].get(tier, "")

        return None

    def _extract_resource_type(self, path: str) -> str | None:
        """Extract resource type from API path.

        Patterns:
        - /api/config/namespaces/{namespace}/http_loadbalancers -> http_loadbalancer
        - /api/config/namespaces/{namespace}/http_loadbalancers/{name} -> http_loadbalancer
        - /api/system/namespaces/{namespace}/origin_pools -> origin_pool

        Args:
            path: OpenAPI path (e.g., "/api/config/.../http_loadbalancers")

        Returns:
            Resource type in singular form or None if not found
        """
        # Match patterns: /{resource_types} or /{resource_types}/{id}
        # Common API patterns: /namespaces/{namespace}/{resource_types}
        path_parts = [p for p in path.split("/") if p and not p.startswith("{")]

        # Look for resource type (usually the last non-parameter part before optional {id})
        for i in range(len(path_parts) - 1, -1, -1):
            part = path_parts[i]

            # Skip common prefixes
            if part in ["api", "config", "system", "namespaces", "custom"]:
                continue

            # Convert plural to singular (simple heuristic)
            if part.endswith("s") and len(part) > 1:
                return part[:-1]  # Remove trailing 's'

            return part

        return None

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Enrich all operations in OpenAPI specification with descriptions.

        Applies descriptions to x-f5xc-operation-metadata.purpose field for each operation.
        Uses 'short' tier for purpose field (≤60 chars, suitable for CLI help).

        Args:
            spec: OpenAPI specification dictionary

        Returns:
            Specification with enriched operation metadata purpose fields
        """
        if not self.enabled:
            return spec

        paths = spec.get("paths", {})

        for path, path_item in paths.items():
            # Extract resource type from path
            resource_type = self._extract_resource_type(path)
            if not resource_type:
                continue

            # Process each HTTP method
            for method in ["get", "post", "put", "patch", "delete", "head", "options"]:
                if method not in path_item:
                    continue

                operation = path_item[method]
                self.stats.operations_processed += 1

                # Get or create x-f5xc-operation-metadata
                if X_F5XC_OPERATION_METADATA not in operation:
                    operation[X_F5XC_OPERATION_METADATA] = {}

                metadata = operation[X_F5XC_OPERATION_METADATA]

                # Get description using matching strategy (use 'short' tier for purpose)
                description = self.get_description(resource_type, method, tier="short")

                # Preserve existing purpose - only set if not already present (Issue #408)
                existing_purpose = metadata.get("purpose")
                if existing_purpose:
                    # Purpose already exists - preserve it, don't overwrite
                    self.stats.descriptions_skipped += 1
                elif description:
                    # No existing purpose - apply generated description
                    metadata["purpose"] = description
                    self.stats.descriptions_applied += 1
                else:
                    # No existing purpose and no generated description
                    self.stats.descriptions_skipped += 1

        return spec

    def get_stats(self) -> dict[str, Any]:
        """Get enrichment statistics.

        Returns:
            Dictionary of statistics
        """
        return self.stats.to_dict()

    def reset_stats(self) -> None:
        """Reset statistics counters."""
        self.stats = OperationDescriptionStats()
