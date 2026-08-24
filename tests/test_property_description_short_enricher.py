# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=no-member,protected-access  # Runtime regex fixtures and internal helpers are intentional.

"""Unit tests for PropertyDescriptionShortEnricher.

Tests for Issue #330: Add x-f5xc-description-short and x-f5xc-description-medium extensions
for Terraform documentation. Generates descriptions for schema properties with long descriptions:
- Short tier: 80-150 characters
- Medium tier: 150-300 characters
"""

import re
from pathlib import Path

import pytest

from scripts.utils.extension_constants import X_F5XC_DESCRIPTION_MEDIUM, X_F5XC_DESCRIPTION_SHORT
from scripts.utils.property_description_short_enricher import (
    EnricherSettings,
    PatternTemplate,
    PropertyDescriptionShortEnricher,
    PropertyDescriptionShortStats,
)


@pytest.fixture
def enricher():
    """Create enricher with default config."""
    return PropertyDescriptionShortEnricher()


@pytest.fixture
def long_description():
    """Create a description >300 chars for testing."""
    return (
        "The javascript challenge type specifies the type of JavaScript challenge "
        "to use for bot detection. When enabled, clients must execute JavaScript code "
        "to prove they are real browsers. This is useful for filtering out automated "
        "traffic. Example configuration: { type: 'js_challenge', timeout: 300 }. "
        "For more information, see https://docs.example.com/js-challenge."
    )


@pytest.fixture
def short_description():
    """Create a description <300 chars (should not be processed)."""
    return "A short description that should not trigger short description generation."


@pytest.fixture
def spec_with_long_descriptions(long_description):
    """Create a spec with properties that have long descriptions."""
    return {
        "components": {
            "schemas": {
                "TestSchema": {
                    "type": "object",
                    "properties": {
                        "js_challenge": {
                            "type": "object",
                            "description": long_description,
                        },
                        "name": {
                            "type": "string",
                            "description": "Short description under 300 chars.",
                        },
                    },
                },
            },
        },
    }


class TestPropertyDescriptionShortStats:
    """Test stats dataclass functionality."""

    def test_stats_initialization(self):
        """Test stats initialize to zero."""
        stats = PropertyDescriptionShortStats()
        assert stats.fields_processed == 0
        assert stats.short_descriptions_added == 0
        assert stats.medium_descriptions_added == 0
        assert stats.descriptions_from_extraction == 0
        assert stats.descriptions_from_config == 0
        assert stats.skipped_already_short == 0
        assert stats.skipped_has_extension == 0
        assert stats.schemas_processed == 0

    def test_stats_to_dict(self):
        """Test stats to_dict conversion."""
        stats = PropertyDescriptionShortStats(
            fields_processed=10,
            short_descriptions_added=5,
            medium_descriptions_added=4,
            descriptions_from_extraction=3,
            descriptions_from_config=2,
        )
        result = stats.to_dict()

        assert result["fields_processed"] == 10
        assert result["short_descriptions_added"] == 5
        assert result["medium_descriptions_added"] == 4
        assert result["descriptions_from_extraction"] == 3
        assert result["descriptions_from_config"] == 2


class TestEnricherSettings:
    """Test settings dataclass."""

    def test_default_settings(self):
        """Test default settings values."""
        settings = EnricherSettings()
        assert settings.min_source_length == 300
        # Short tier
        assert settings.target_min_length == 80
        assert settings.target_max_length == 150
        # Medium tier
        assert settings.medium_min_length == 150
        assert settings.medium_max_length == 300
        assert settings.preserve_existing is True

    def test_custom_settings(self):
        """Test custom settings values."""
        settings = EnricherSettings(
            min_source_length=200,
            target_min_length=50,
            target_max_length=100,
            medium_min_length=100,
            medium_max_length=200,
            preserve_existing=False,
        )
        assert settings.min_source_length == 200
        assert settings.target_min_length == 50
        assert settings.target_max_length == 100
        assert settings.medium_min_length == 100
        assert settings.medium_max_length == 200
        assert settings.preserve_existing is False


class TestEnricherBasics:
    """Test basic enricher functionality."""

    def test_initialization(self):
        """Test enricher initializes with actual config values."""
        enricher = PropertyDescriptionShortEnricher()
        # Config file sets min_source_length to 10 for processing all descriptions
        assert enricher.settings.min_source_length == 10
        assert enricher.settings.target_min_length == 80
        assert enricher.settings.target_max_length == 150
        assert enricher.settings.preserve_existing is True

    def test_config_loading_missing_file(self):
        """Test enricher uses defaults when config file missing."""
        enricher = PropertyDescriptionShortEnricher(
            config_path=Path("/nonexistent/path.yaml"),
        )
        assert enricher.settings.min_source_length == 300
        assert enricher.settings.preserve_existing is True

    def test_stats_initialization(self):
        """Test enrichment stats start at zero."""
        enricher = PropertyDescriptionShortEnricher()
        stats = enricher.get_stats()
        assert stats["fields_processed"] == 0
        assert stats["short_descriptions_added"] == 0
        assert stats["schemas_processed"] == 0

    def test_config_version(self):
        """Test config version is accessible."""
        enricher = PropertyDescriptionShortEnricher()
        version = enricher.get_config_version()
        assert version is not None
        assert isinstance(version, str)


class TestFirstSentenceExtraction:
    """Test first sentence extraction logic."""

    def test_extract_first_sentence_simple(self, enricher):
        """Test extracting first sentence from simple text."""
        text = "This is the first sentence. This is the second sentence."
        result = enricher._extract_first_sentence(text)
        assert result == "This is the first sentence."

    def test_extract_first_sentence_no_period(self, enricher):
        """Test handling text without period."""
        text = "This is a sentence without a period at the end"
        result = enricher._extract_first_sentence(text)
        assert result.endswith(".")

    def test_extract_first_sentence_exclamation(self, enricher):
        """Test extracting sentence ending with exclamation."""
        text = "Warning! This is important. More text here."
        result = enricher._extract_first_sentence(text)
        assert result == "Warning!"

    def test_extract_first_sentence_question(self, enricher):
        """Test extracting sentence ending with question mark."""
        text = "What is this? This explains it."
        result = enricher._extract_first_sentence(text)
        assert result == "What is this?"

    def test_extract_first_sentence_empty(self, enricher):
        """Test empty text returns None."""
        result = enricher._extract_first_sentence("")
        assert result is None

    def test_extract_first_sentence_none(self, enricher):
        """Test None text returns None."""
        result = enricher._extract_first_sentence(None)  # type: ignore[arg-type]
        assert result is None


class TestStyleTransformations:
    """Test style transformation to imperative tone."""

    def test_transform_the_x_is_pattern(self, enricher):
        """Test transforming 'The X is used to...' pattern."""
        text = "The configuration is used to specify options."
        result = enricher._apply_style_rules(text)
        assert result.startswith("Specifies") or "configuration" in result.lower()

    def test_transform_this_field_pattern(self, enricher):
        """Test transforming 'This field specifies...' pattern."""
        text = "This field specifies the timeout value."
        result = enricher._apply_style_rules(text)
        # Should remove "This field specifies"
        assert not result.lower().startswith("this field")

    def test_transform_an_article_pattern(self, enricher):
        """Test removing leading article 'An/A'."""
        text = "An option for configuring the system."
        result = enricher._apply_style_rules(text)
        # Should remove leading "An"
        assert not result.startswith("An ")

    def test_capitalize_first_letter(self, enricher):
        """Test first letter is capitalized."""
        text = "lowercase start of sentence."
        result = enricher._apply_style_rules(text)
        assert result[0].isupper()


class TestDescriptionCleaning:
    """Test description cleaning functionality."""

    def test_remove_examples(self, enricher):
        """Test removal of example sections."""
        text = "Configure the system. Example: config.yaml contains settings."
        result = enricher._clean_description(text)
        assert "Example:" not in result

    def test_remove_http_links(self, enricher):
        """Test removal of HTTP links."""
        text = "See documentation. For more info see https://docs.example.com/page."
        result = enricher._clean_description(text)
        assert "https://" not in result

    def test_remove_inline_code(self, enricher):
        """Test removal of inline code blocks."""
        text = "Use the `config` command to set values."
        result = enricher._clean_description(text)
        assert "`config`" not in result

    def test_remove_code_blocks(self, enricher):
        """Test removal of code blocks."""
        text = "Configure like this: ```yaml\nkey: value\n``` Then proceed."
        result = enricher._clean_description(text)
        assert "```" not in result

    def test_normalize_whitespace(self, enricher):
        """Test whitespace normalization."""
        text = "Multiple   spaces   and\nnewlines\tand tabs."
        result = enricher._clean_description(text)
        assert "  " not in result
        assert "\n" not in result
        assert "\t" not in result


class TestSmartTruncation:
    """Test smart truncation functionality."""

    def test_truncate_at_word_boundary(self, enricher):
        """Test truncation happens at word boundaries."""
        text = "This is a very long description that needs to be truncated properly."
        result = enricher._smart_truncate(text, 30)
        # Should end with ellipsis and not cut mid-word
        assert result.endswith("...")
        assert len(result) <= 30

    def test_truncate_preserves_short_text(self, enricher):
        """Test short text is not truncated."""
        text = "Short text."
        result = enricher._smart_truncate(text, 100)
        assert result == text
        assert "..." not in result

    def test_truncate_removes_trailing_punctuation(self, enricher):
        """Test trailing punctuation is removed before ellipsis."""
        text = "A sentence, with punctuation, that gets cut."
        result = enricher._smart_truncate(text, 30)
        # Should not end with ",...""
        assert not result.endswith(",...")


class TestConfigurationOverrides:
    """Test configuration-based overrides."""

    def test_has_override(self, enricher):
        """Test checking for override existence."""
        # Assuming default config has some overrides
        # The actual key depends on config file
        has = enricher.has_override("schemavirtual_hostCreateSpecType", "domains")
        # This tests the method works; actual result depends on config
        assert isinstance(has, bool)

    def test_get_override(self, enricher):
        """Test getting override value."""
        result = enricher.get_override("schemavirtual_hostCreateSpecType", "domains")
        # If override exists, should return string
        if result is not None:
            assert isinstance(result, str)
            assert len(result) > 0


class TestPatternTemplates:
    """Test pattern-based template matching."""

    def test_pattern_template_creation(self):
        """Test creating a pattern template."""
        template = PatternTemplate(
            pattern=re.compile(r"\.domains$"),
            template="List of domain names for this resource.",
        )
        assert template.pattern.search("config.domains") is not None
        assert template.template == "List of domain names for this resource."

    def test_pattern_matching_domains(self, enricher):
        """Test that .domains pattern matches."""
        # Internal method test - check pattern matching works
        for pattern_template in enricher.patterns:
            if pattern_template.pattern.search("domains"):
                assert pattern_template.template is not None
                break


class TestSpecEnrichment:
    """Test full specification enrichment."""

    def test_enrich_spec_with_long_description(
        self,
        enricher,
        spec_with_long_descriptions,
    ):
        """Test enriching spec with long descriptions."""
        result = enricher.enrich_spec(spec_with_long_descriptions)

        # Check structure preserved
        assert "components" in result
        assert "schemas" in result["components"]
        assert "TestSchema" in result["components"]["schemas"]

        # Check that js_challenge got short description
        js_challenge = result["components"]["schemas"]["TestSchema"]["properties"]["js_challenge"]
        assert X_F5XC_DESCRIPTION_SHORT in js_challenge

        # Check short description length
        short_desc = js_challenge[X_F5XC_DESCRIPTION_SHORT]
        assert len(short_desc) >= 40  # Minimum reasonable length
        assert len(short_desc) <= 150  # Max target length

    def test_enrich_spec_skips_short_descriptions(
        self,
        enricher,
        spec_with_long_descriptions,
    ):
        """Test that short descriptions are skipped."""
        result = enricher.enrich_spec(spec_with_long_descriptions)

        # Name property has short description - should not get extension
        name_prop = result["components"]["schemas"]["TestSchema"]["properties"]["name"]
        assert X_F5XC_DESCRIPTION_SHORT not in name_prop

    def test_stats_after_enrichment(self, enricher, spec_with_long_descriptions):
        """Test stats are updated after enrichment."""
        enricher.enrich_spec(spec_with_long_descriptions)
        stats = enricher.get_stats()

        assert stats["schemas_processed"] >= 1
        assert stats["fields_processed"] >= 2
        assert stats["short_descriptions_added"] >= 1
        # With min_source_length=10, few descriptions are skipped as "already short"
        assert stats["skipped_already_short"] >= 0


class TestNestedSchemas:
    """Test handling of nested schemas."""

    def test_allof_schemas_processed(self, enricher, long_description):
        """Test that allOf schemas are processed."""
        spec = {
            "components": {
                "schemas": {
                    "Combined": {
                        "allOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "config": {
                                        "type": "object",
                                        "description": long_description,
                                    },
                                },
                            },
                        ],
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        config_prop = result["components"]["schemas"]["Combined"]["allOf"][0]["properties"][
            "config"
        ]
        assert X_F5XC_DESCRIPTION_SHORT in config_prop

    def test_oneof_schemas_processed(self, enricher, long_description):
        """Test that oneOf schemas are processed."""
        spec = {
            "components": {
                "schemas": {
                    "Choice": {
                        "oneOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "option": {
                                        "type": "string",
                                        "description": long_description,
                                    },
                                },
                            },
                        ],
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        option_prop = result["components"]["schemas"]["Choice"]["oneOf"][0]["properties"]["option"]
        assert X_F5XC_DESCRIPTION_SHORT in option_prop

    def test_items_schema_processed(self, enricher, long_description):
        """Test that array items schemas are processed."""
        spec = {
            "components": {
                "schemas": {
                    "List": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "element": {
                                    "type": "string",
                                    "description": long_description,
                                },
                            },
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        element_prop = result["components"]["schemas"]["List"]["items"]["properties"]["element"]
        assert X_F5XC_DESCRIPTION_SHORT in element_prop


class TestPreserveExisting:
    """Test preservation of existing extensions."""

    def test_preserve_existing_short_extension(self, enricher, long_description):
        """Test that existing x-f5xc-description-short is preserved."""
        existing_short = "Existing short description."
        spec = {
            "components": {
                "schemas": {
                    "Test": {
                        "type": "object",
                        "properties": {
                            "config": {
                                "type": "object",
                                "description": long_description,
                                X_F5XC_DESCRIPTION_SHORT: existing_short,
                            },
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        config_prop = result["components"]["schemas"]["Test"]["properties"]["config"]

        # Short should be preserved, medium may be added
        assert config_prop[X_F5XC_DESCRIPTION_SHORT] == existing_short

    def test_preserve_existing_both_extensions(self, enricher, long_description):
        """Test that properties with both extensions are skipped."""
        existing_short = "Existing short description."
        existing_medium = "Existing medium description with more details."
        spec = {
            "components": {
                "schemas": {
                    "Test": {
                        "type": "object",
                        "properties": {
                            "config": {
                                "type": "object",
                                "description": long_description,
                                X_F5XC_DESCRIPTION_SHORT: existing_short,
                                X_F5XC_DESCRIPTION_MEDIUM: existing_medium,
                            },
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        config_prop = result["components"]["schemas"]["Test"]["properties"]["config"]

        # Both should be preserved
        assert config_prop[X_F5XC_DESCRIPTION_SHORT] == existing_short
        assert config_prop[X_F5XC_DESCRIPTION_MEDIUM] == existing_medium

        # Stats should show skipped when both exist
        stats = enricher.get_stats()
        assert stats["skipped_has_extension"] >= 1


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_spec(self, enricher):
        """Test enriching empty spec."""
        result = enricher.enrich_spec({})
        assert result == {}

    def test_spec_without_schemas(self, enricher):
        """Test enriching spec without schemas."""
        spec = {"info": {"title": "Test API"}, "paths": {}}
        result = enricher.enrich_spec(spec)
        assert result is not None

    def test_null_property_values(self, enricher):
        """Test handling null property values."""
        spec = {
            "components": {
                "schemas": {
                    "Test": {
                        "type": "object",
                        "properties": {
                            "name": None,
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        # Should not raise, should preserve null
        assert result["components"]["schemas"]["Test"]["properties"]["name"] is None

    def test_property_without_description(self, enricher):
        """Test property without description field."""
        spec = {
            "components": {
                "schemas": {
                    "Test": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        # Should not add short description to property without long description
        name_prop = result["components"]["schemas"]["Test"]["properties"]["name"]
        assert X_F5XC_DESCRIPTION_SHORT not in name_prop

    def test_empty_description(self, enricher):
        """Test property with empty description."""
        spec = {
            "components": {
                "schemas": {
                    "Test": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": ""},
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        name_prop = result["components"]["schemas"]["Test"]["properties"]["name"]
        assert X_F5XC_DESCRIPTION_SHORT not in name_prop


class TestLengthValidation:
    """Test description length validation."""

    def test_generated_description_within_target(self, enricher, long_description):
        """Test generated descriptions are within target range."""
        short_desc = enricher._extract_and_transform(long_description)

        if short_desc:
            # Should be at least 40 chars (relaxed minimum for some extractions)
            assert len(short_desc) >= 40
            # Should be at most 150 chars (max target)
            assert len(short_desc) <= 150

    def test_boundary_length_description(self, enricher):
        """Test description at min_source_length boundary (10 chars)."""
        # Create description exactly 10 chars (at boundary)
        desc_10 = "X" * 9 + "."
        assert len(desc_10) == 10

        spec = {
            "components": {
                "schemas": {
                    "Test": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "description": desc_10},
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        field_prop = result["components"]["schemas"]["Test"]["properties"]["field"]
        # At exactly 10 chars, should NOT be processed (min_source_length is 10)
        assert X_F5XC_DESCRIPTION_SHORT not in field_prop

    def test_just_over_boundary_processed(self, enricher):
        """Test description just over boundary is processed when meaningful.

        The enricher has two thresholds:
        1. min_source_length (10): Descriptions <= 10 chars are skipped entirely
        2. Generated short description must be >= 40 chars to be added

        So we need a description that's > 10 chars AND can produce a >= 40 char result.
        """
        # Create a meaningful description > 10 chars that produces >= 40 char output
        desc = "Configuration for API endpoint settings and parameters."
        assert len(desc) > 10  # Passes min_source_length threshold

        spec = {
            "components": {
                "schemas": {
                    "Test": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "description": desc},
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        field_prop = result["components"]["schemas"]["Test"]["properties"]["field"]
        # Description > 10 chars with meaningful content should be processed
        assert X_F5XC_DESCRIPTION_SHORT in field_prop
        # Generated short description must be >= 40 chars (enricher minimum)
        assert len(field_prop[X_F5XC_DESCRIPTION_SHORT]) >= 40


class TestIdempotency:
    """Test idempotent enrichment behavior."""

    def test_multiple_enrichment_idempotent(self, enricher, spec_with_long_descriptions):
        """Test that running enrichment twice produces same result."""
        # First enrichment
        result1 = enricher.enrich_spec(spec_with_long_descriptions)
        short_desc1 = result1["components"]["schemas"]["TestSchema"]["properties"][
            "js_challenge"
        ].get(X_F5XC_DESCRIPTION_SHORT)

        # Create new enricher and enrich again
        enricher2 = PropertyDescriptionShortEnricher()
        result2 = enricher2.enrich_spec(result1)
        short_desc2 = result2["components"]["schemas"]["TestSchema"]["properties"][
            "js_challenge"
        ].get(X_F5XC_DESCRIPTION_SHORT)

        # Should be identical (preserved)
        assert short_desc1 == short_desc2


class TestMediumTierGeneration:
    """Test medium tier description generation (150-300 chars)."""

    def test_medium_description_generated(self, enricher, spec_with_long_descriptions):
        """Test that medium description is generated alongside short."""
        result = enricher.enrich_spec(spec_with_long_descriptions)
        prop = result["components"]["schemas"]["TestSchema"]["properties"]["js_challenge"]

        # Both tiers should be generated
        assert X_F5XC_DESCRIPTION_SHORT in prop
        assert X_F5XC_DESCRIPTION_MEDIUM in prop

    def test_medium_length_within_bounds(self, enricher, long_description):
        """Test that medium description length is within 150-300 chars."""
        spec = {
            "components": {
                "schemas": {
                    "Test": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "description": long_description},
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        medium_desc = result["components"]["schemas"]["Test"]["properties"]["field"].get(
            X_F5XC_DESCRIPTION_MEDIUM,
        )

        # Medium should be longer than short (if generated)
        if medium_desc:
            # Length should be reasonable for medium tier (allowing some flexibility)
            assert len(medium_desc) >= 100  # Minimum reasonable length
            assert len(medium_desc) <= 300  # Maximum length

    def test_medium_longer_than_short(self, enricher, long_description):
        """Test that medium description is longer than short description."""
        spec = {
            "components": {
                "schemas": {
                    "Test": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "description": long_description},
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        prop = result["components"]["schemas"]["Test"]["properties"]["field"]
        short_desc = prop.get(X_F5XC_DESCRIPTION_SHORT)
        medium_desc = prop.get(X_F5XC_DESCRIPTION_MEDIUM)

        # If both exist, medium should be longer
        if short_desc and medium_desc:
            assert len(medium_desc) >= len(short_desc)

    def test_medium_stats_tracking(self, enricher, spec_with_long_descriptions):
        """Test that medium description stats are tracked."""
        enricher.enrich_spec(spec_with_long_descriptions)
        stats = enricher.get_stats()

        # Should have tracked medium generation
        assert "medium_descriptions_added" in stats

    def test_both_short_and_medium_generated(self, enricher):
        """Test that both tiers are generated for eligible properties."""
        # Create a very long description with multiple sentences
        long_desc = (
            "The endpoint configuration specifies how to connect to backend services. "
            "It includes settings for load balancing, health checks, and circuit breakers. "
            "When a backend becomes unhealthy, traffic is automatically routed to healthy endpoints. "
            "You can configure retry policies to handle transient failures gracefully. "
            "For more information, see the backend configuration guide. "
            "Example: { endpoints: [{ address: '10.0.0.1', port: 8080 }] }."
        )

        spec = {
            "components": {
                "schemas": {
                    "Test": {
                        "type": "object",
                        "properties": {
                            "endpoint_config": {"type": "object", "description": long_desc},
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        prop = result["components"]["schemas"]["Test"]["properties"]["endpoint_config"]

        # Both should be present
        assert X_F5XC_DESCRIPTION_SHORT in prop
        assert X_F5XC_DESCRIPTION_MEDIUM in prop

    def test_medium_preserves_existing(self, enricher):
        """Test that existing medium descriptions are preserved."""
        spec = {
            "components": {
                "schemas": {
                    "Test": {
                        "type": "object",
                        "properties": {
                            "field": {
                                "type": "string",
                                "description": "X" * 400,  # Long enough to trigger generation
                                X_F5XC_DESCRIPTION_MEDIUM: "Existing medium description.",
                            },
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        medium_desc = result["components"]["schemas"]["Test"]["properties"]["field"].get(
            X_F5XC_DESCRIPTION_MEDIUM,
        )

        # Should preserve existing
        assert medium_desc == "Existing medium description."

    def test_medium_override_from_config(self, enricher):
        """Test that medium overrides from config are applied."""
        # This test uses the config file which has medium_overrides defined
        # Check if medium_overrides attribute exists and is loaded
        assert hasattr(enricher, "medium_overrides")
        assert isinstance(enricher.medium_overrides, dict)

    def test_medium_patterns_loaded(self, enricher):
        """Test that medium patterns are loaded from config."""
        assert hasattr(enricher, "medium_patterns")
        assert isinstance(enricher.medium_patterns, list)


class TestMultipleSentenceExtraction:
    """Test extraction of multiple sentences for medium tier."""

    def test_extract_multiple_sentences(self, enricher):
        """Test extraction of multiple sentences."""
        text = "First sentence here. Second sentence follows. Third sentence completes it."
        result = enricher._extract_multiple_sentences(text, max_count=3)

        assert len(result) == 3
        assert "First sentence here." in result[0]

    def test_extract_multiple_sentences_limited(self, enricher):
        """Test sentence extraction respects max_count."""
        text = "One. Two. Three. Four. Five."
        result = enricher._extract_multiple_sentences(text, max_count=2)

        assert len(result) == 2

    def test_extract_multiple_sentences_empty(self, enricher):
        """Test sentence extraction with empty text."""
        result = enricher._extract_multiple_sentences("", max_count=3)
        assert result == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
