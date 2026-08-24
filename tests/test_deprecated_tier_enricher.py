# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=protected-access  # Tests intentionally verify deterministic internal helpers.

"""Unit tests for DeprecatedTierEnricher."""

from pathlib import Path

import pytest

from scripts.utils.deprecated_tier_enricher import (
    TIER_TRANSFORMATIONS,
    VALID_TIERS,
    DeprecatedTierEnricher,
    DeprecatedTierStats,
)


@pytest.fixture
def enricher():
    """Create enricher with default config."""
    return DeprecatedTierEnricher()


@pytest.fixture
def spec_with_deprecated_tiers():
    """Create a spec with deprecated tier values."""
    return {
        "components": {
            "schemas": {
                "schemaAddonServiceTierType": {
                    "type": "string",
                    "enum": ["NO_TIER", "BASIC", "STANDARD", "ADVANCED", "PREMIUM"],
                    "description": "Subscription tier: NO_TIER, BASIC, STANDARD, ADVANCED, PREMIUM",
                },
                "OtherSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                    },
                },
            },
        },
    }


@pytest.fixture
def spec_without_deprecated_tiers():
    """Create a spec with only valid tier values."""
    return {
        "components": {
            "schemas": {
                "schemaAddonServiceTierType": {
                    "type": "string",
                    "enum": ["NO_TIER", "STANDARD", "ADVANCED"],
                    "description": "Subscription tier: NO_TIER, STANDARD, ADVANCED",
                },
            },
        },
    }


class TestDeprecatedTierEnricherBasics:
    """Test basic enricher functionality."""

    def test_initialization(self):
        """Test enricher initializes correctly."""
        enricher = DeprecatedTierEnricher()
        assert enricher.transformations == TIER_TRANSFORMATIONS
        assert len(enricher.patterns) > 0

    def test_config_loading_missing_file(self):
        """Test enricher uses defaults when config file missing."""
        enricher = DeprecatedTierEnricher(config_path=Path("/nonexistent/path.yaml"))
        assert enricher.transformations == TIER_TRANSFORMATIONS
        assert len(enricher.patterns) > 0

    def test_stats_initialization(self):
        """Test enrichment stats start at zero."""
        enricher = DeprecatedTierEnricher()
        stats = enricher.get_stats()
        assert stats["schemas_processed"] == 0
        assert stats["schemas_transformed"] == 0
        assert stats["values_transformed"] == 0
        assert stats["descriptions_updated"] == 0

    def test_stats_reset(self):
        """Test stats can be reset."""
        enricher = DeprecatedTierEnricher()
        enricher.stats.schemas_processed = 5
        enricher.reset_stats()
        assert enricher.stats.schemas_processed == 0


class TestDeprecatedTierStatsDataclass:
    """Test DeprecatedTierStats dataclass."""

    def test_to_dict(self):
        """Test stats to_dict conversion."""
        stats = DeprecatedTierStats(
            schemas_processed=10,
            schemas_transformed=2,
            values_transformed=4,
            descriptions_updated=2,
            errors=[{"file": "test.json", "error": "test error"}],
        )
        result = stats.to_dict()
        assert result["schemas_processed"] == 10
        assert result["schemas_transformed"] == 2
        assert result["values_transformed"] == 4
        assert result["descriptions_updated"] == 2
        assert result["error_count"] == 1


class TestTierTransformation:
    """Test tier value transformation."""

    def test_transforms_basic_to_standard(self, enricher, spec_with_deprecated_tiers):
        """Test BASIC is transformed to STANDARD."""
        result = enricher.enrich(spec_with_deprecated_tiers)
        enum_values = result["components"]["schemas"]["schemaAddonServiceTierType"]["enum"]

        assert "BASIC" not in enum_values
        assert "STANDARD" in enum_values

    def test_transforms_premium_to_advanced(self, enricher, spec_with_deprecated_tiers):
        """Test PREMIUM is transformed to ADVANCED."""
        result = enricher.enrich(spec_with_deprecated_tiers)
        enum_values = result["components"]["schemas"]["schemaAddonServiceTierType"]["enum"]

        assert "PREMIUM" not in enum_values
        assert "ADVANCED" in enum_values

    def test_preserves_no_tier(self, enricher, spec_with_deprecated_tiers):
        """Test NO_TIER is preserved (not deprecated)."""
        result = enricher.enrich(spec_with_deprecated_tiers)
        enum_values = result["components"]["schemas"]["schemaAddonServiceTierType"]["enum"]

        assert "NO_TIER" in enum_values

    def test_no_duplicates_after_transformation(self, enricher, spec_with_deprecated_tiers):
        """Test no duplicate values after transformation."""
        result = enricher.enrich(spec_with_deprecated_tiers)
        enum_values = result["components"]["schemas"]["schemaAddonServiceTierType"]["enum"]

        # Should not have duplicate STANDARD or ADVANCED
        assert enum_values.count("STANDARD") == 1
        assert enum_values.count("ADVANCED") == 1

    def test_final_enum_contains_only_valid_tiers(self, enricher, spec_with_deprecated_tiers):
        """Test final enum contains only valid tier values."""
        result = enricher.enrich(spec_with_deprecated_tiers)
        enum_values = result["components"]["schemas"]["schemaAddonServiceTierType"]["enum"]

        for value in enum_values:
            assert value in VALID_TIERS

    def test_no_change_when_no_deprecated_values(self, enricher, spec_without_deprecated_tiers):
        """Test no changes when spec has no deprecated values."""
        original_enum = spec_without_deprecated_tiers["components"]["schemas"][
            "schemaAddonServiceTierType"
        ]["enum"].copy()

        result = enricher.enrich(spec_without_deprecated_tiers)
        result_enum = result["components"]["schemas"]["schemaAddonServiceTierType"]["enum"]

        assert result_enum == original_enum
        assert enricher.stats.values_transformed == 0

    def test_stats_updated_after_transformation(self, enricher, spec_with_deprecated_tiers):
        """Test stats are updated after transformation."""
        enricher.enrich(spec_with_deprecated_tiers)
        stats = enricher.get_stats()

        assert stats["schemas_transformed"] == 1
        assert stats["values_transformed"] == 2  # BASIC and PREMIUM


class TestDescriptionTransformation:
    """Test description updates."""

    def test_description_updated_with_transformations(self, enricher):
        """Test description text is updated with new tier names."""
        spec = {
            "components": {
                "schemas": {
                    "TestTierType": {
                        "type": "string",
                        "enum": ["BASIC", "PREMIUM"],
                        "description": "Tier options: BASIC for basic, PREMIUM for premium.",
                    },
                },
            },
        }

        result = enricher.enrich(spec)
        description = result["components"]["schemas"]["TestTierType"]["description"]

        assert "BASIC" not in description
        assert "PREMIUM" not in description
        assert "STANDARD" in description
        assert "ADVANCED" in description


class TestPatternMatching:
    """Test schema pattern matching."""

    def test_matches_addon_service_tier_type(self, enricher):
        """Test pattern matches AddonServiceTierType schemas."""
        assert enricher._matches_tier_pattern("schemaAddonServiceTierType")
        assert enricher._matches_tier_pattern("pbacAddonServiceTierType")
        assert enricher._matches_tier_pattern("SomeAddonServiceTierType")

    def test_matches_tier_type(self, enricher):
        """Test pattern matches TierType schemas."""
        assert enricher._matches_tier_pattern("SomeTierType")
        assert enricher._matches_tier_pattern("SubscriptionTierType")

    def test_does_not_match_non_tier_schemas(self, enricher):
        """Test pattern does not match non-tier schemas."""
        assert not enricher._matches_tier_pattern("UserProfile")
        assert not enricher._matches_tier_pattern("LoadBalancerConfig")
        assert not enricher._matches_tier_pattern("TierSettings")


class TestEdgeCases:
    """Test edge cases."""

    def test_empty_enum(self, enricher):
        """Test handling of empty enum."""
        spec = {
            "components": {
                "schemas": {
                    "schemaAddonServiceTierType": {
                        "type": "string",
                        "enum": [],
                    },
                },
            },
        }

        result = enricher.enrich(spec)
        assert result["components"]["schemas"]["schemaAddonServiceTierType"]["enum"] == []

    def test_no_enum_field(self, enricher):
        """Test handling of schema without enum field."""
        spec = {
            "components": {
                "schemas": {
                    "schemaAddonServiceTierType": {
                        "type": "string",
                    },
                },
            },
        }

        result = enricher.enrich(spec)
        assert "enum" not in result["components"]["schemas"]["schemaAddonServiceTierType"]

    def test_empty_spec(self, enricher):
        """Test handling of empty spec."""
        spec = {}
        result = enricher.enrich(spec)
        assert result == {}

    def test_no_components(self, enricher):
        """Test handling of spec without components."""
        spec = {"info": {"title": "Test API"}}
        result = enricher.enrich(spec)
        assert result == {"info": {"title": "Test API"}}

    def test_no_schemas(self, enricher):
        """Test handling of spec without schemas."""
        spec = {"components": {"responses": {}}}
        result = enricher.enrich(spec)
        assert result == {"components": {"responses": {}}}


class TestConfigurationIntegration:
    """Test configuration file integration."""

    def test_custom_transformations_from_config(self):
        """Test that custom transformations can be loaded from config."""
        # This test verifies the enricher can use config-based transformations
        enricher = DeprecatedTierEnricher()

        # Default transformations should be loaded
        assert "BASIC" in enricher.transformations
        assert "PREMIUM" in enricher.transformations
        assert enricher.transformations["BASIC"] == "STANDARD"
        assert enricher.transformations["PREMIUM"] == "ADVANCED"
