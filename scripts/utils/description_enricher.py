# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Domain Description Enricher for OpenAPI specifications.

Applies enriched domain descriptions from config/domain_descriptions.yaml
to OpenAPI specification info section:
- info.x-f5xc-summary: Medium tier description (max 150 chars) for CLI banners
- info.description: Long tier description (max 500 chars) for documentation

Description Tiers:
- short: max 60 chars - CLI help columns, badges, index entries
- medium: max 150 chars - CLI banners, tooltips, info.x-f5xc-summary field
- long: max 500 chars - Full documentation, AI context, info.description field

Usage:
    enricher = DescriptionEnricher()
    spec = enricher.enrich_spec(spec, domain="virtual")
    stats = enricher.get_stats()
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .extension_constants import X_F5XC_CLI_DOMAIN, X_F5XC_DESCRIPTION_LONG, X_F5XC_SUMMARY


@dataclass
class DescriptionEnrichmentStats:
    """Statistics from description enrichment."""

    specs_processed: int = 0
    descriptions_applied: int = 0
    descriptions_skipped: int = 0
    domains_without_config: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            "specs_processed": self.specs_processed,
            "descriptions_applied": self.descriptions_applied,
            "descriptions_skipped": self.descriptions_skipped,
            "domains_without_config": self.domains_without_config,
        }


class DescriptionEnricher:
    """Enrich OpenAPI specifications with domain descriptions.

    Loads enriched descriptions from config/domain_descriptions.yaml and applies:
    - 'medium' description to info.x-f5xc-summary (for CLI banners)
    - 'long' description to info.description (for documentation)

    Attributes:
        config_path: Path to domain_descriptions.yaml
        descriptions: Dictionary of domain -> description tiers
        stats: Enrichment statistics
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize enricher with description configuration.

        Args:
            config_path: Path to domain_descriptions.yaml config.
                        Defaults to config/domain_descriptions.yaml.
        """
        if config_path is None:
            config_path = (
                Path(__file__).parent.parent.parent / "config" / "domain_descriptions.yaml"
            )

        self.config_path = config_path
        self.descriptions: dict[str, dict[str, str]] = {}
        self.stats = DescriptionEnrichmentStats()
        self._config_version: str = "0.0.0"

        self._load_config()

    def _load_config(self) -> None:
        """Load domain descriptions from YAML configuration file."""
        if not self.config_path.exists():
            # No config file - will skip enrichment gracefully
            return

        try:
            with self.config_path.open() as f:
                config = yaml.safe_load(f) or {}

            self._config_version = config.get("version", "0.0.0")
            domains = config.get("domains", {})

            for domain_name, domain_config in domains.items():
                if isinstance(domain_config, dict):
                    # Extract the three description tiers
                    self.descriptions[domain_name] = {
                        "short": domain_config.get("short", ""),
                        "medium": domain_config.get("medium", ""),
                        "long": self._normalize_long_description(domain_config.get("long", "")),
                    }
        except yaml.YAMLError:
            # Invalid YAML - skip enrichment gracefully
            pass

    def _normalize_long_description(self, description: str) -> str:
        """Normalize long description by removing extra whitespace.

        Args:
            description: Raw description text (may be multi-line YAML)

        Returns:
            Normalized single-paragraph description
        """
        if not description:
            return ""

        # Join multi-line YAML blocks into single paragraph
        lines = description.strip().split("\n")
        normalized = " ".join(line.strip() for line in lines if line.strip())

        # Ensure max 500 characters
        if len(normalized) > 500:
            normalized = normalized[:497] + "..."

        return normalized

    def enrich_spec(self, spec: dict[str, Any], domain: str | None = None) -> dict[str, Any]:
        """Enrich OpenAPI specification with domain description and summary.

        Applies descriptions from configuration to the spec's info section:
        - 'medium' tier → info.x-f5xc-summary (for CLI banners, max 150 chars)
        - 'long' tier → info.description (for documentation, max 500 chars)
        - Also adds x-f5xc-description-long extension for schema-level access

        Skips if no description is configured for the domain.

        Args:
            spec: OpenAPI specification dictionary
            domain: Domain name (e.g., "virtual"). If None, tries to extract
                from spec's x-f5xc-cli-domain extension.

        Returns:
            Specification with enriched info.description and info.x-f5xc-summary
        """
        self.stats.specs_processed += 1

        # Determine domain from parameter or spec
        if domain is None:
            domain = self._extract_domain(spec)

        if domain is None:
            self.stats.descriptions_skipped += 1
            return spec

        # Check if we have descriptions for this domain
        if domain not in self.descriptions:
            self.stats.descriptions_skipped += 1
            if domain not in self.stats.domains_without_config:
                self.stats.domains_without_config.append(domain)
            return spec

        description_config = self.descriptions[domain]
        long_description = description_config.get("long", "")
        medium_description = description_config.get("medium", "")

        if not long_description:
            self.stats.descriptions_skipped += 1
            return spec

        # Ensure info section exists
        if "info" not in spec:
            spec["info"] = {}

        # Apply long description to info.description
        spec["info"]["description"] = long_description

        # Also add x-f5xc-description-long for schema-level access
        spec["info"][X_F5XC_DESCRIPTION_LONG] = long_description

        # Apply medium description as x-f5xc-summary (OAS 3.0 info object has no summary field)
        if medium_description:
            spec["info"][X_F5XC_SUMMARY] = medium_description

        self.stats.descriptions_applied += 1

        return spec

    def _extract_domain(self, spec: dict[str, Any]) -> str | None:
        """Extract domain from spec's x-f5xc-cli-domain extension.

        Args:
            spec: OpenAPI specification dictionary

        Returns:
            Domain name if found, None otherwise
        """
        info = spec.get("info", {})
        return info.get(X_F5XC_CLI_DOMAIN)

    def get_description(self, domain: str, tier: str = "long") -> str | None:
        """Get description for a domain at specified tier.

        Args:
            domain: Domain name (e.g., "virtual")
            tier: Description tier ("short", "medium", or "long")

        Returns:
            Description text if available, None otherwise
        """
        if domain not in self.descriptions:
            return None

        return self.descriptions[domain].get(tier)

    def get_all_descriptions(self, domain: str) -> dict[str, str] | None:
        """Get all description tiers for a domain.

        Args:
            domain: Domain name (e.g., "virtual")

        Returns:
            Dictionary with short/medium/long descriptions, or None if not found
        """
        return self.descriptions.get(domain)

    def has_description(self, domain: str) -> bool:
        """Check if enriched descriptions exist for a domain.

        Args:
            domain: Domain name to check

        Returns:
            True if descriptions are configured for the domain
        """
        return domain in self.descriptions

    def get_configured_domains(self) -> list[str]:
        """Get list of domains with configured descriptions.

        Returns:
            Sorted list of domain names
        """
        return sorted(self.descriptions.keys())

    def get_config_version(self) -> str:
        """Get version of loaded description configuration.

        Returns:
            Version string from config (e.g., "1.0.0")
        """
        return self._config_version

    def get_stats(self) -> dict[str, Any]:
        """Get enrichment statistics.

        Returns:
            Dictionary with enrichment metrics
        """
        return self.stats.to_dict()


# Module-level singleton cache for convenience
_ENRICHER_CACHE: dict[str, DescriptionEnricher | None] = {"instance": None}


def get_description_enricher() -> DescriptionEnricher:
    """Get or create module-level DescriptionEnricher singleton.

    Returns:
        Shared DescriptionEnricher instance
    """
    instance = _ENRICHER_CACHE["instance"]
    if instance is None:
        instance = DescriptionEnricher()
        _ENRICHER_CACHE["instance"] = instance
    return instance


def get_domain_descriptions(domain: str) -> dict[str, str] | None:
    """Convenience function to get all descriptions for a domain.

    Args:
        domain: Domain name (e.g., "virtual")

    Returns:
        Dictionary with short/medium/long descriptions, or None
    """
    return get_description_enricher().get_all_descriptions(domain)


__all__ = [
    "DescriptionEnricher",
    "DescriptionEnrichmentStats",
    "get_description_enricher",
    "get_domain_descriptions",
]
