# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=use-implicit-booleaness-not-comparison  # Explicit empty results document the expected shape.

"""Unit tests for DescriptionEnricher."""

import pytest

from scripts.utils.description_enricher import (
    DescriptionEnricher,
    DescriptionEnrichmentStats,
    get_description_enricher,
    get_domain_descriptions,
)


@pytest.fixture
def enricher():
    """Create enricher with default config."""
    return DescriptionEnricher()


@pytest.fixture
def spec_with_info():
    """Create a spec with info section."""
    return {
        "info": {
            "title": "F5 XC Virtual API",
            "description": "Original description",
            "x-f5xc-cli-domain": "virtual",
        },
        "paths": {},
    }


@pytest.fixture
def spec_without_domain():
    """Create a spec without domain classification."""
    return {
        "info": {
            "title": "Unknown API",
            "description": "Some API",
        },
        "paths": {},
    }


@pytest.fixture
def spec_without_info():
    """Create a spec without info section."""
    return {
        "paths": {},
    }


class TestDescriptionEnricherBasics:
    """Test basic enricher functionality."""

    def test_initialization(self):
        """Test enricher initializes correctly."""
        enricher = DescriptionEnricher()
        assert enricher is not None
        assert enricher.config_path is not None

    def test_config_version(self, enricher):
        """Test config version is loaded."""
        version = enricher.get_config_version()
        assert version is not None
        assert isinstance(version, str)

    def test_configured_domains(self, enricher):
        """Test configured domains list."""
        domains = enricher.get_configured_domains()
        assert isinstance(domains, list)
        # Virtual domain should be configured (from prototype)
        assert "virtual" in domains

    def test_stats_initialization(self, enricher):
        """Test stats are initialized to zero."""
        stats = enricher.get_stats()
        assert stats["specs_processed"] == 0
        assert stats["descriptions_applied"] == 0
        assert stats["descriptions_skipped"] == 0
        assert stats["domains_without_config"] == []


class TestDescriptionRetrieval:
    """Test description retrieval methods."""

    def test_get_short_description(self, enricher):
        """Test getting short description for virtual domain."""
        desc = enricher.get_description("virtual", tier="short")
        assert desc is not None
        assert len(desc) <= 60
        assert "load balancing" in desc.lower() or "http" in desc.lower()

    def test_get_medium_description(self, enricher):
        """Test getting medium description for virtual domain."""
        desc = enricher.get_description("virtual", tier="medium")
        assert desc is not None
        assert len(desc) <= 150

    def test_get_long_description(self, enricher):
        """Test getting long description for virtual domain."""
        desc = enricher.get_description("virtual", tier="long")
        assert desc is not None
        assert len(desc) <= 500

    def test_get_description_unknown_domain(self, enricher):
        """Test getting description for unknown domain."""
        desc = enricher.get_description("unknown_domain", tier="short")
        assert desc is None

    def test_get_all_descriptions(self, enricher):
        """Test getting all description tiers."""
        descs = enricher.get_all_descriptions("virtual")
        assert descs is not None
        assert "short" in descs
        assert "medium" in descs
        assert "long" in descs

    def test_has_description(self, enricher):
        """Test has_description method."""
        assert enricher.has_description("virtual") is True
        assert enricher.has_description("unknown_domain") is False


class TestSpecEnrichment:
    """Test spec enrichment functionality."""

    def test_enrich_spec_with_domain(self, enricher, spec_with_info):
        """Test enriching spec with known domain."""
        result = enricher.enrich_spec(spec_with_info, domain="virtual")
        assert "info" in result
        assert "description" in result["info"]
        assert "x-f5xc-summary" in result["info"]  # New: summary should also be added
        # Should use enriched long description
        desc = result["info"]["description"]
        assert len(desc) > 0
        # Should be enriched (not original)
        assert desc != "Original description"

    def test_enrich_spec_extracts_domain(self, enricher, spec_with_info):
        """Test enriching spec that has x-f5xc-cli-domain."""
        result = enricher.enrich_spec(spec_with_info)
        # Should extract domain from x-f5xc-cli-domain
        assert result["info"]["description"] != "Original description"

    def test_enrich_spec_without_domain(self, enricher, spec_without_domain):
        """Test enriching spec without domain classification."""
        result = enricher.enrich_spec(spec_without_domain)
        # Should skip enrichment, keep original description
        assert result["info"]["description"] == "Some API"

    def test_enrich_spec_unknown_domain(self, enricher, spec_with_info):
        """Test enriching spec with unknown domain."""
        result = enricher.enrich_spec(spec_with_info, domain="unknown_domain")
        # Should skip enrichment, keep original description
        assert result["info"]["description"] == "Original description"

    def test_enrich_spec_without_info(self, enricher, spec_without_info):
        """Test enriching spec without info section."""
        result = enricher.enrich_spec(spec_without_info, domain="virtual")
        # Should add info section with description and summary
        assert "info" in result
        assert "description" in result["info"]
        assert "x-f5xc-summary" in result["info"]


class TestStatistics:
    """Test statistics tracking."""

    def test_stats_after_successful_enrichment(self, enricher, spec_with_info):
        """Test stats after successful enrichment."""
        enricher.enrich_spec(spec_with_info, domain="virtual")
        stats = enricher.get_stats()
        assert stats["specs_processed"] == 1
        assert stats["descriptions_applied"] == 1
        assert stats["descriptions_skipped"] == 0

    def test_stats_after_skipped_enrichment(self, enricher, spec_without_domain):
        """Test stats after skipped enrichment."""
        enricher.enrich_spec(spec_without_domain)
        stats = enricher.get_stats()
        assert stats["specs_processed"] == 1
        assert stats["descriptions_applied"] == 0
        assert stats["descriptions_skipped"] == 1

    def test_stats_tracks_missing_domains(self, enricher, spec_with_info):
        """Test stats tracks domains without config."""
        enricher.enrich_spec(spec_with_info, domain="unknown_domain1")
        enricher.enrich_spec(spec_with_info, domain="unknown_domain2")
        stats = enricher.get_stats()
        assert "unknown_domain1" in stats["domains_without_config"]
        assert "unknown_domain2" in stats["domains_without_config"]


class TestModuleLevelFunctions:
    """Test module-level convenience functions."""

    def test_get_description_enricher_singleton(self):
        """Test singleton pattern for module-level enricher."""
        e1 = get_description_enricher()
        e2 = get_description_enricher()
        assert e1 is e2

    def test_get_domain_descriptions_convenience(self):
        """Test convenience function for getting descriptions."""
        descs = get_domain_descriptions("virtual")
        assert descs is not None
        assert "short" in descs
        assert "medium" in descs
        assert "long" in descs

    def test_get_domain_descriptions_unknown(self):
        """Test convenience function for unknown domain."""
        descs = get_domain_descriptions("unknown_domain")
        assert descs is None


class TestDescriptionStats:
    """Test DescriptionEnrichmentStats dataclass."""

    def test_stats_dataclass_defaults(self):
        """Test stats dataclass has correct defaults."""
        stats = DescriptionEnrichmentStats()
        assert stats.specs_processed == 0
        assert stats.descriptions_applied == 0
        assert stats.descriptions_skipped == 0
        assert stats.domains_without_config == []

    def test_stats_to_dict(self):
        """Test stats conversion to dictionary."""
        stats = DescriptionEnrichmentStats(
            specs_processed=10,
            descriptions_applied=5,
            descriptions_skipped=3,
            domains_without_config=["a", "b"],
        )
        result = stats.to_dict()
        assert result["specs_processed"] == 10
        assert result["descriptions_applied"] == 5
        assert result["descriptions_skipped"] == 3
        assert result["domains_without_config"] == ["a", "b"]


class TestDescriptionValidation:
    """Test description length validation."""

    def test_short_description_length(self, enricher):
        """Verify short descriptions are within 60 chars."""
        for domain in enricher.get_configured_domains():
            desc = enricher.get_description(domain, tier="short")
            if desc:
                assert len(desc) <= 60, f"Short description for {domain} exceeds 60 chars"

    def test_medium_description_length(self, enricher):
        """Verify medium descriptions are within 150 chars."""
        for domain in enricher.get_configured_domains():
            desc = enricher.get_description(domain, tier="medium")
            if desc:
                assert len(desc) <= 150, f"Medium description for {domain} exceeds 150 chars"

    def test_long_description_length(self, enricher):
        """Verify long descriptions are within 500 chars."""
        for domain in enricher.get_configured_domains():
            desc = enricher.get_description(domain, tier="long")
            if desc:
                assert len(desc) <= 500, f"Long description for {domain} exceeds 500 chars"


class TestRootDescriptionEnrichment:
    """Test root/master spec description enrichment."""

    def test_root_description_exists(self, enricher):
        """Test that root domain has descriptions configured."""
        assert enricher.has_description("root") is True

    def test_root_short_description(self, enricher):
        """Test root short description is properly configured."""
        desc = enricher.get_description("root", tier="short")
        assert desc is not None
        assert len(desc) <= 60
        # Should describe platform capabilities (noun-first pattern, no "F5" branding)
        assert "cloud" in desc.lower() or "platform" in desc.lower()

    def test_root_medium_description(self, enricher):
        """Test root medium description is properly configured."""
        desc = enricher.get_description("root", tier="medium")
        assert desc is not None
        assert len(desc) <= 150
        # Should describe product capabilities (noun-first pattern)
        assert any(
            keyword in desc.lower() for keyword in ["multi-cloud", "application", "services"]
        )

    def test_root_long_description(self, enricher):
        """Test root long description is substantial and informative."""
        desc = enricher.get_description("root", tier="long")
        assert desc is not None
        assert len(desc) > 100  # Should be substantial
        assert len(desc) <= 500
        # Should mention key domains
        assert any(keyword in desc.lower() for keyword in ["load balancing", "waf", "dns", "cdn"])

    def test_root_all_descriptions(self, enricher):
        """Test root has all description tiers configured."""
        descs = enricher.get_all_descriptions("root")
        assert descs is not None
        assert "short" in descs
        assert "medium" in descs
        assert "long" in descs

    def test_root_in_configured_domains(self, enricher):
        """Test root is in the list of configured domains."""
        domains = enricher.get_configured_domains()
        assert "root" in domains


class TestInfoSummaryEnrichment:
    """Test info.x-f5xc-summary field enrichment."""

    def test_summary_applied_with_domain(self, enricher, spec_with_info):
        """Test that medium description is applied to info.x-f5xc-summary."""
        result = enricher.enrich_spec(spec_with_info, domain="virtual")
        assert "x-f5xc-summary" in result["info"]
        medium_desc = enricher.get_description("virtual", tier="medium")
        assert result["info"]["x-f5xc-summary"] == medium_desc

    def test_summary_length_within_limit(self, enricher):
        """Verify summary (medium tier) is within 150 char limit for all domains."""
        for domain in enricher.get_configured_domains():
            spec = {"info": {"x-ves-cli-domain": domain}, "paths": {}}
            result = enricher.enrich_spec(spec, domain=domain)
            if "x-f5xc-summary" in result.get("info", {}):
                assert len(result["info"]["x-f5xc-summary"]) <= 150, (
                    f"{domain} summary exceeds 150 chars"
                )

    def test_summary_not_applied_without_medium(self, enricher):
        """Test that info.x-f5xc-summary is not added when medium description is empty."""
        # Temporarily add a test domain with no medium description
        enricher.descriptions["test_domain_no_medium"] = {
            "short": "Short desc",
            "medium": "",  # Empty medium
            "long": "Long description text that is substantial.",
        }
        spec = {"info": {}, "paths": {}}
        result = enricher.enrich_spec(spec, domain="test_domain_no_medium")
        assert "x-f5xc-summary" not in result["info"]
        assert "description" in result["info"]

    def test_summary_applied_to_spec_without_info(self, enricher, spec_without_info):
        """Test that info section is created with both description and summary."""
        result = enricher.enrich_spec(spec_without_info, domain="virtual")
        assert "info" in result
        assert "description" in result["info"]
        assert "x-f5xc-summary" in result["info"]

    def test_root_domain_has_summary(self, enricher):
        """Test that root domain gets summary in master spec."""
        spec = {"info": {"x-ves-cli-domain": "root"}, "paths": {}}
        result = enricher.enrich_spec(spec, domain="root")
        assert "x-f5xc-summary" in result["info"]
        medium_desc = enricher.get_description("root", tier="medium")
        assert result["info"]["x-f5xc-summary"] == medium_desc

    def test_summary_and_description_are_different(self, enricher, spec_with_info):
        """Test that summary (medium) and description (long) are different tiers."""
        result = enricher.enrich_spec(spec_with_info, domain="virtual")
        summary = result["info"].get("x-f5xc-summary", "")
        description = result["info"].get("description", "")
        # Summary should be shorter than description
        assert len(summary) < len(description)
        # They should not be identical
        assert summary != description

    def test_all_configured_domains_get_summary(self, enricher):
        """Test that all configured domains with medium descriptions get summary."""
        for domain in enricher.get_configured_domains():
            medium_desc = enricher.get_description(domain, tier="medium")
            if medium_desc:  # Only test domains with medium configured
                spec = {"info": {}, "paths": {}}
                result = enricher.enrich_spec(spec, domain=domain)
                assert "x-f5xc-summary" in result["info"], f"Domain {domain} missing summary"
                assert result["info"]["x-f5xc-summary"] == medium_desc
