# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=use-implicit-booleaness-not-comparison  # Explicit empty results document the expected shape.

"""Unit tests for AcronymEnricher."""

import pytest

from scripts.utils.acronym_enricher import (
    AcronymEnricher,
    AcronymEnrichmentStats,
    AcronymEntry,
    AcronymExtension,
)


@pytest.fixture
def enricher():
    """Create enricher with default config."""
    return AcronymEnricher()


@pytest.fixture
def sample_index():
    """Create a sample index for enrichment."""
    return {
        "version": "1.0.0",
        "specifications": [],
    }


class TestAcronymEnricherBasics:
    """Test basic enricher functionality."""

    def test_initialization(self):
        """Test enricher initializes correctly."""
        enricher = AcronymEnricher()
        assert enricher is not None
        assert enricher.config_path is not None

    def test_config_version(self, enricher):
        """Test config version is loaded."""
        version = enricher.get_config_version()
        assert version is not None
        assert isinstance(version, str)
        assert version == "1.0.0"

    def test_stats_initialization(self, enricher):
        """Test stats are initialized correctly."""
        stats = enricher.get_stats()
        assert stats["indexes_processed"] == 0
        assert stats["acronyms_loaded"] > 0  # Should load from config
        assert stats["categories_loaded"] > 0  # Should load categories


class TestAcronymRetrieval:
    """Test acronym retrieval methods."""

    def test_get_acronym_tcp(self, enricher):
        """Test getting TCP acronym details."""
        entry = enricher.get_acronym("TCP")
        assert entry is not None
        assert isinstance(entry, AcronymEntry)
        assert entry.acronym == "TCP"
        assert entry.expansion == "Transmission Control Protocol"

    def test_get_acronym_case_insensitive(self, enricher):
        """Test acronym lookup is case insensitive."""
        entry_upper = enricher.get_acronym("HTTP")
        entry_lower = enricher.get_acronym("http")
        entry_mixed = enricher.get_acronym("Http")

        assert entry_upper is not None
        assert entry_lower is not None
        assert entry_mixed is not None
        assert entry_upper.acronym == entry_lower.acronym == entry_mixed.acronym

    def test_get_acronym_unknown(self, enricher):
        """Test getting unknown acronym returns None."""
        entry = enricher.get_acronym("UNKNOWN_ACRONYM_XYZ")
        assert entry is None

    def test_get_all_acronyms(self, enricher):
        """Test getting all configured acronyms."""
        acronyms = enricher.get_all_acronyms()
        assert isinstance(acronyms, list)
        assert len(acronyms) > 50  # Should have many acronyms

    def test_has_acronym(self, enricher):
        """Test has_acronym method."""
        assert enricher.has_acronym("TCP") is True
        assert enricher.has_acronym("HTTP") is True
        assert enricher.has_acronym("UNKNOWN_XYZ") is False

    def test_get_expansion(self, enricher):
        """Test get_expansion method."""
        expansion = enricher.get_expansion("API")
        assert expansion == "Application Programming Interface"

        expansion_unknown = enricher.get_expansion("UNKNOWN_XYZ")
        assert expansion_unknown is None


class TestAcronymStructure:
    """Test AcronymEntry dataclass structure."""

    def test_acronym_has_required_fields(self, enricher):
        """Test acronym entries have required fields."""
        entry = enricher.get_acronym("TCP")
        assert entry is not None

        assert hasattr(entry, "acronym")
        assert hasattr(entry, "expansion")
        assert hasattr(entry, "category")

    def test_acronym_entry_to_dict(self, enricher):
        """Test acronym entry to_dict method."""
        entry = enricher.get_acronym("TCP")
        assert entry is not None

        entry_dict = entry.to_dict()
        assert isinstance(entry_dict, dict)
        assert "acronym" in entry_dict
        assert "expansion" in entry_dict
        assert "category" in entry_dict

    def test_acronym_has_valid_category(self, enricher):
        """Test acronyms have valid categories."""
        categories = enricher.get_categories()
        acronyms = enricher.get_all_acronyms()

        for acronym in acronyms:
            assert acronym.category in categories or acronym.category == "Other"


class TestCategoryRetrieval:
    """Test category retrieval methods."""

    def test_get_categories(self, enricher):
        """Test getting all categories."""
        categories = enricher.get_categories()
        assert isinstance(categories, list)
        assert len(categories) > 0

    def test_expected_categories(self, enricher):
        """Test expected categories exist."""
        categories = enricher.get_categories()
        expected = ["Networking", "Security", "Load Balancing", "F5 Specific"]

        for expected_cat in expected:
            assert expected_cat in categories

    def test_get_acronyms_by_category(self, enricher):
        """Test getting acronyms by category."""
        networking_acronyms = enricher.get_acronyms_by_category("Networking")
        assert isinstance(networking_acronyms, list)
        assert len(networking_acronyms) > 0

        # Verify all returned acronyms are in the Networking category
        for acronym in networking_acronyms:
            assert acronym.category == "Networking"

    def test_get_acronyms_by_unknown_category(self, enricher):
        """Test getting acronyms from unknown category returns empty list."""
        acronyms = enricher.get_acronyms_by_category("Unknown Category XYZ")
        assert isinstance(acronyms, list)
        assert len(acronyms) == 0


class TestSpecificAcronyms:
    """Test specific acronyms are correctly loaded."""

    @pytest.mark.parametrize(
        ("acronym", "expected_expansion", "expected_category"),
        [
            ("TCP", "Transmission Control Protocol", "Networking"),
            ("HTTP", "Hypertext Transfer Protocol", "Networking"),
            ("WAF", "Web Application Firewall", "Security"),
            ("LB", "Load Balancer", "Load Balancing"),
            ("AWS", "Amazon Web Services", "Cloud & Infrastructure"),
            ("XC", "Distributed Cloud", "F5 Specific"),
            ("JSON", "JavaScript Object Notation", "Standards & Formats"),
            ("SSH", "Secure Shell", "Protocols"),
            ("CPU", "Central Processing Unit", "Other"),
        ],
    )
    def test_acronym_content(self, enricher, acronym, expected_expansion, expected_category):
        """Test specific acronyms have correct content."""
        entry = enricher.get_acronym(acronym)
        assert entry is not None
        assert entry.expansion == expected_expansion
        assert entry.category == expected_category


class TestIndexEnrichment:
    """Test index.json enrichment functionality."""

    def test_enrich_index(self, enricher, sample_index):
        """Test enriching index.json with acronym data."""
        enriched = enricher.enrich_index(sample_index)
        assert "x-f5xc-acronyms" in enriched

    def test_enriched_index_structure(self, enricher, sample_index):
        """Test enriched index has correct structure."""
        enriched = enricher.enrich_index(sample_index)
        acronym_data = enriched["x-f5xc-acronyms"]

        assert isinstance(acronym_data, dict)
        assert "version" in acronym_data
        assert "categories" in acronym_data
        assert "acronyms" in acronym_data

    def test_enriched_index_version(self, enricher, sample_index):
        """Test enriched index has version."""
        enriched = enricher.enrich_index(sample_index)
        version = enriched["x-f5xc-acronyms"]["version"]

        assert version == "1.0.0"

    def test_enriched_index_categories(self, enricher, sample_index):
        """Test enriched index contains categories."""
        enriched = enricher.enrich_index(sample_index)
        categories = enriched["x-f5xc-acronyms"]["categories"]

        assert isinstance(categories, list)
        assert len(categories) > 0
        assert "Networking" in categories

    def test_enriched_index_acronyms(self, enricher, sample_index):
        """Test enriched index contains acronyms list."""
        enriched = enricher.enrich_index(sample_index)
        acronyms = enriched["x-f5xc-acronyms"]["acronyms"]

        assert isinstance(acronyms, list)
        assert len(acronyms) > 0

        # Check structure of first acronym
        first = acronyms[0]
        assert "acronym" in first
        assert "expansion" in first
        assert "category" in first

    def test_enrich_index_stats_updated(self, enricher, sample_index):
        """Test stats are updated after enrichment."""
        enricher.enrich_index(sample_index)
        stats = enricher.get_stats()
        assert stats["indexes_processed"] == 1
        assert stats["enrichment_applied"] is True


class TestSpecEnrichment:
    """Test spec-level enrichment (pass-through)."""

    def test_enrich_spec_passthrough(self, enricher):
        """Test enrich_spec is a pass-through."""
        sample_spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test API"},
        }
        result = enricher.enrich_spec(sample_spec)
        assert result == sample_spec

    def test_enrich_spec_with_domain(self, enricher):
        """Test enrich_spec accepts domain parameter."""
        sample_spec = {"openapi": "3.0.3"}
        result = enricher.enrich_spec(sample_spec, domain="test")
        assert result == sample_spec


class TestAcronymEnrichmentStats:
    """Test enrichment statistics dataclass."""

    def test_stats_initialization(self):
        """Test stats initialize correctly."""
        stats = AcronymEnrichmentStats()
        assert stats.indexes_processed == 0
        assert stats.acronyms_loaded == 0
        assert stats.categories_loaded == 0
        assert stats.enrichment_applied is False

    def test_stats_to_dict(self):
        """Test stats to_dict method."""
        stats = AcronymEnrichmentStats(
            indexes_processed=2,
            acronyms_loaded=100,
            categories_loaded=8,
            enrichment_applied=True,
        )
        stats_dict = stats.to_dict()
        assert isinstance(stats_dict, dict)
        assert stats_dict["indexes_processed"] == 2
        assert stats_dict["acronyms_loaded"] == 100
        assert stats_dict["categories_loaded"] == 8
        assert stats_dict["enrichment_applied"] is True


class TestAcronymExtension:
    """Test AcronymExtension dataclass."""

    def test_extension_initialization(self):
        """Test extension initializes correctly."""
        extension = AcronymExtension()
        assert extension.version == "1.0.0"
        assert extension.categories == []
        assert extension.acronyms == []

    def test_extension_to_dict(self):
        """Test extension to_dict method."""
        entry = AcronymEntry(
            acronym="TEST",
            expansion="Test Entry",
            category="Testing",
        )
        extension = AcronymExtension(
            version="2.0.0",
            categories=["Testing"],
            acronyms=[entry],
        )
        extension_dict = extension.to_dict()

        assert isinstance(extension_dict, dict)
        assert extension_dict["version"] == "2.0.0"
        assert extension_dict["categories"] == ["Testing"]
        assert len(extension_dict["acronyms"]) == 1
        assert extension_dict["acronyms"][0]["acronym"] == "TEST"


class TestAcronymEntry:
    """Test AcronymEntry dataclass."""

    def test_entry_creation(self):
        """Test entry creation."""
        entry = AcronymEntry(
            acronym="TEST",
            expansion="Test Entry",
            category="Testing",
        )
        assert entry.acronym == "TEST"
        assert entry.expansion == "Test Entry"
        assert entry.category == "Testing"

    def test_entry_to_dict(self):
        """Test entry to_dict method."""
        entry = AcronymEntry(
            acronym="API",
            expansion="Application Programming Interface",
            category="Cloud & Infrastructure",
        )
        entry_dict = entry.to_dict()

        assert isinstance(entry_dict, dict)
        assert entry_dict["acronym"] == "API"
        assert entry_dict["expansion"] == "Application Programming Interface"
        assert entry_dict["category"] == "Cloud & Infrastructure"


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_index_enrichment(self, enricher):
        """Test enriching empty index."""
        empty_index = {}
        enriched = enricher.enrich_index(empty_index)
        assert "x-f5xc-acronyms" in enriched

    def test_multiple_enrichments(self, enricher, sample_index):
        """Test multiple enrichment calls."""
        enricher.enrich_index(sample_index)
        enricher.enrich_index(sample_index)
        stats = enricher.get_stats()
        assert stats["indexes_processed"] == 2

    def test_get_categories_returns_copy(self, enricher):
        """Test get_categories returns a copy."""
        categories1 = enricher.get_categories()
        categories2 = enricher.get_categories()
        categories1.append("Modified")
        assert "Modified" not in categories2

    def test_get_all_acronyms_returns_copy(self, enricher):
        """Test get_all_acronyms returns a copy."""
        acronyms1 = enricher.get_all_acronyms()
        acronyms2 = enricher.get_all_acronyms()
        original_len = len(acronyms2)
        acronyms1.clear()
        assert len(acronyms2) == original_len
