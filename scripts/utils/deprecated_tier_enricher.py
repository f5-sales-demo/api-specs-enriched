# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Deprecated tier transformation enricher for OpenAPI specifications.

This enricher transforms deprecated subscription tier values to their
current equivalents in enum schemas. Pre-release cleanup to ensure
only valid tier values are published.

F5 XC subscription tiers:
- NO_TIER: Foundational/not applicable
- STANDARD: Base subscription tier
- ADVANCED: Premium subscription tier

Transformations applied:
- BASIC → STANDARD (legacy tier replaced)
- PREMIUM → ADVANCED (legacy tier replaced)

Version: v3.0.0 - Uses x-f5xc-* namespace constants
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import yaml

# Precompiled regex patterns for performance (used in hot paths)
_WHITESPACE_NORMALIZE_PATTERN = re.compile(r"\s+")
_COMMA_PERIOD_PATTERN = re.compile(r",\s*\.")

logger = logging.getLogger(__name__)


# Tier value transformations (deprecated → current)
TIER_TRANSFORMATIONS: dict[str, str] = {
    "BASIC": "STANDARD",
    "PREMIUM": "ADVANCED",
}

# Valid tier values after transformation
VALID_TIERS: list[str] = ["NO_TIER", "STANDARD", "ADVANCED"]


@dataclass
class DeprecatedTierStats:
    """Statistics for deprecated tier transformation."""

    schemas_processed: int = 0
    schemas_transformed: int = 0
    values_transformed: int = 0
    descriptions_updated: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            "schemas_processed": self.schemas_processed,
            "schemas_transformed": self.schemas_transformed,
            "values_transformed": self.values_transformed,
            "descriptions_updated": self.descriptions_updated,
            "error_count": len(self.errors),
            "errors": self.errors,
        }


class DeprecatedTierEnricher:
    """Transform deprecated tier values in OpenAPI specs.

    Pre-release cleanup enricher that transforms deprecated subscription
    tier values to their current equivalents:
    - BASIC → STANDARD
    - PREMIUM → ADVANCED

    Uses config/enrichment.yaml for pattern matching rules.
    """

    # Default schema patterns to match tier enums
    DEFAULT_PATTERNS: ClassVar[list[str]] = [
        r".*AddonServiceTierType$",
        r".*TierType$",
    ]

    # Tier value replacements for API curl examples
    TIER_REPLACEMENTS: ClassVar[dict[str, str]] = {
        "subscription_basic_tier": "subscription_standard_tier",
        "subscription_premium_tier": "subscription_advanced_tier",
        "basic_tier": "standard_tier",
        "premium_tier": "advanced_tier",
        "BASIC": "STANDARD",
        "PREMIUM": "ADVANCED",
    }

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize enricher with configuration.

        Args:
            config_path: Optional path to enrichment config file.
                        Defaults to config/enrichment.yaml
        """
        self.config_path = (
            config_path or Path(__file__).parent.parent.parent / "config" / "enrichment.yaml"
        )
        self.config: dict[str, Any] = {}
        self.patterns: list[re.Pattern[str]] = []
        self.transformations: dict[str, str] = dict(TIER_TRANSFORMATIONS)
        self._compiled_transformation_patterns: list[
            tuple[re.Pattern[str], re.Pattern[str], str]
        ] = []
        self.stats = DeprecatedTierStats()

        self._load_config()
        self._compile_transformation_patterns()

    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        try:
            with self.config_path.open() as f:
                full_config = yaml.safe_load(f) or {}
                self.config = full_config.get("deprecated_tiers", {})

            if not self.config.get("enabled", True):
                logger.info("Deprecated tier transformation is disabled")
                return

            # Load patterns from config or use defaults
            pattern_strs = self.config.get("patterns", self.DEFAULT_PATTERNS)
            self.patterns = [re.compile(p) for p in pattern_strs]

            # Load transformations from config or use defaults
            config_transformations = self.config.get("transformations", {})
            if config_transformations:
                self.transformations = config_transformations

            logger.info(
                "Loaded deprecated tier config: %d patterns, %d transformations",
                len(self.patterns),
                len(self.transformations),
            )
        except FileNotFoundError:
            logger.warning(
                "Config file not found: %s, using defaults",
                self.config_path,
            )
            self.patterns = [re.compile(p) for p in self.DEFAULT_PATTERNS]
        except yaml.YAMLError:
            logger.exception("Failed to parse config")
            self.patterns = [re.compile(p) for p in self.DEFAULT_PATTERNS]

    def _compile_transformation_patterns(self) -> None:
        """Precompile transformation patterns for performance."""
        self._compiled_transformation_patterns = []
        for deprecated, current in self.transformations.items():
            # Pattern for list items like "- BASIC:"
            list_pattern = re.compile(rf"(-\s*){deprecated}(:)", re.IGNORECASE)
            # Pattern for standalone mentions
            standalone_pattern = re.compile(rf"\b{deprecated}\b")
            self._compiled_transformation_patterns.append(
                (list_pattern, standalone_pattern, current),
            )

    def enrich(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Transform deprecated tier values in specification.

        Args:
            spec: OpenAPI specification dictionary

        Returns:
            Cleaned specification
        """
        if not self.config.get("enabled", True):
            return spec

        # Process schemas in components
        components = spec.get("components", {})
        schemas = components.get("schemas", {})

        for schema_name, schema_def in schemas.items():
            self.stats.schemas_processed += 1

            if self._matches_tier_pattern(schema_name):
                self._clean_tier_schema(schema_name, schema_def)

        return spec

    def _matches_tier_pattern(self, schema_name: str) -> bool:
        """Check if schema name matches tier enum patterns.

        Args:
            schema_name: Name of the schema

        Returns:
            True if schema matches a tier pattern
        """
        return any(pattern.match(schema_name) for pattern in self.patterns)

    def _clean_tier_schema(self, schema_name: str, schema: dict[str, Any]) -> None:
        """Transform deprecated tier values in schema enum.

        Args:
            schema_name: Name of the schema
            schema: Schema definition dictionary
        """
        enum_values = schema.get("enum", [])
        if not enum_values:
            return

        # Check if it contains deprecated tier values
        deprecated_present = [v for v in enum_values if v in self.transformations]
        if not deprecated_present:
            return

        logger.info(
            "Transforming tier schema: %s (%s)",
            schema_name,
            ", ".join(f"{v}→{self.transformations[v]}" for v in deprecated_present),
        )
        self.stats.schemas_transformed += 1

        # Transform deprecated values to current equivalents
        transformed_enum = []
        seen_values: set[str] = set()
        for v in enum_values:
            if v in self.transformations:
                new_value = self.transformations[v]
                self.stats.values_transformed += 1
                # Only add if not already present (avoid duplicates)
                if new_value not in seen_values:
                    transformed_enum.append(new_value)
                    seen_values.add(new_value)
            elif v not in seen_values:
                transformed_enum.append(v)
                seen_values.add(v)

        schema["enum"] = transformed_enum

        # Update description to reflect transformation
        self._update_description(schema)

    def _update_description(
        self,
        schema: dict[str, Any],
    ) -> None:
        """Update schema description after transforming deprecated values.

        Args:
            schema: Schema definition dictionary
        """
        original_desc = schema.get("description", "")

        # Replace references to deprecated tiers with their current equivalents
        new_desc = original_desc
        for list_pattern, standalone_pattern, current in self._compiled_transformation_patterns:
            # Replace mentions like "- BASIC: basic\n" with "- STANDARD: standard\n"
            new_desc = list_pattern.sub(rf"\g<1>{current}\g<2>", new_desc)
            # Replace standalone mentions
            new_desc = standalone_pattern.sub(current, new_desc)

        # Clean up any double spaces or trailing commas
        new_desc = _WHITESPACE_NORMALIZE_PATTERN.sub(" ", new_desc)
        new_desc = _COMMA_PERIOD_PATTERN.sub(".", new_desc)
        new_desc = new_desc.strip()

        if new_desc != original_desc:
            schema["description"] = new_desc
            self.stats.descriptions_updated += 1

    def get_stats(self) -> dict[str, Any]:
        """Get enrichment statistics.

        Returns:
            Dictionary of statistics
        """
        return self.stats.to_dict()

    def reset_stats(self) -> None:
        """Reset statistics for new enrichment run."""
        self.stats = DeprecatedTierStats()
