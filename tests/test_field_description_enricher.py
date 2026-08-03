# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unit tests for FieldDescriptionEnricher."""

from pathlib import Path

import pytest

from scripts.utils.field_description_enricher import FieldDescriptionEnricher


@pytest.fixture
def enricher():
    """Create enricher with default config."""
    return FieldDescriptionEnricher()


@pytest.fixture
def simple_spec():
    """Create a simple OpenAPI spec for testing."""
    return {
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "email": {"type": "string"},
                        "id": {"type": "string"},
                    },
                },
            },
        },
    }


class TestFieldDescriptionEnricherBasics:
    """Test basic enricher functionality."""

    def test_initialization(self):
        """Test enricher initializes with default config."""
        enricher = FieldDescriptionEnricher()
        assert enricher.preserve_existing is True
        assert len(enricher.description_patterns) > 0
        assert len(enricher.example_generators) > 0

    def test_config_loading_missing_file(self):
        """Test enricher loads defaults when config file missing."""
        enricher = FieldDescriptionEnricher(config_path=Path("/nonexistent/path.yaml"))
        assert enricher.preserve_existing is True
        assert len(enricher.description_patterns) == 7
        assert "kebab-case-name" in enricher.example_generators

    def test_stats_initialization(self):
        """Test enrichment stats start at zero."""
        enricher = FieldDescriptionEnricher()
        stats = enricher.get_stats()
        assert stats["descriptions_added"] == 0
        assert stats["examples_added"] == 0
        assert stats["properties_processed"] == 0
        assert stats["schemas_processed"] == 0


class TestPatternMatching:
    """Test high-confidence pattern matching."""

    def test_name_field_matches(self, enricher):
        """Test that name field matches pattern."""
        description = enricher._find_description("name")
        assert description is not None
        assert "name" in description.lower()

    def test_email_field_matches(self, enricher):
        """Test that email field matches pattern."""
        description = enricher._find_description("email")
        assert description is not None
        assert "email" in description.lower()

    def test_port_field_matches(self, enricher):
        """Test that port field matches pattern."""
        description = enricher._find_description("port")
        assert description is not None
        assert "port" in description.lower()

    def test_uuid_field_matches(self, enricher):
        """Test that uuid field matches pattern."""
        description = enricher._find_description("uuid")
        assert description is not None
        assert "uuid" in description.lower()

    def test_ip_field_matches(self, enricher):
        """Test that ip field matches pattern."""
        description = enricher._find_description("ip")
        assert description is not None
        assert "ip" in description.lower().replace("ipv", "ip")

    def test_generic_field_gets_fallback_description(self, enricher):
        """Test that generic field names get fallback descriptions for 100% coverage."""
        # The enricher now provides fallback descriptions for all fields
        # to ensure 100% description coverage
        assert enricher._find_description("value") is not None
        assert enricher._find_description("data") is not None
        assert enricher._find_description("config") is not None


class TestExampleGeneration:
    """Test example generation."""

    def test_name_example_generation(self, enricher):
        """Test that name field gets realistic example."""
        example = enricher._generate_example("name")
        assert example is not None
        assert isinstance(example, str)
        assert len(example) <= 63

    def test_email_example_generation(self, enricher):
        """Test that email field gets email example."""
        example = enricher._generate_example("email")
        assert example == "user@example.com"

    def test_ipv4_example_generation(self, enricher):
        """Test that ipv4 field gets IP example."""
        example = enricher._generate_example("ipv4")
        assert example == "192.0.2.1"

    def test_port_example_generation(self, enricher):
        """Test that port field gets valid port example."""
        example = enricher._generate_example("port")
        assert example == 8080
        assert 1 <= example <= 65535

    def test_uuid_example_generation(self, enricher):
        """Test that uuid field gets valid UUID example."""
        example = enricher._generate_example("uuid")
        assert example == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def test_timestamp_example_generation(self, enricher):
        """Test that timestamp field gets ISO 8601 example."""
        example = enricher._generate_example("timestamp")
        assert example is not None
        assert "T" in example  # ISO 8601 format indicator
        assert "Z" in example  # UTC timezone

    def test_no_match_no_example(self, enricher):
        """Test that unmatched fields don't get examples."""
        example = enricher._generate_example("arbitrary_field")
        assert example is None


class TestPropertyEnrichment:
    """Test property-level enrichment."""

    def test_property_with_no_description(self, enricher):
        """Test enriching property with no existing description."""
        prop = {"type": "string"}
        enricher._enrich_property(prop, "name")

        assert "description" in prop
        assert prop["description"] == "Human-readable name for the resource"
        assert "x-f5xc-example" in prop

    def test_property_preserves_existing_description(self, enricher):
        """Test that existing descriptions are preserved."""
        existing_desc = "Custom description for this field"
        prop = {"type": "string", "description": existing_desc}
        enricher._enrich_property(prop, "name")

        # Description should remain unchanged
        assert prop["description"] == existing_desc

    def test_property_preserves_existing_example(self, enricher):
        """Test that existing examples are preserved."""
        prop = {"type": "string", "example": "custom-value"}
        original_example = prop["example"]
        enricher._enrich_property(prop, "name")

        # Example should remain unchanged
        assert prop["example"] == original_example
        assert "x-f5xc-example" not in prop

    def test_stats_incremented_on_enrichment(self, enricher):
        """Test that stats are updated during enrichment."""
        prop = {"type": "string"}
        enricher._enrich_property(prop, "email")

        stats = enricher.get_stats()
        assert stats["descriptions_added"] >= 1
        assert stats["examples_added"] >= 1


class TestSpecEnrichment:
    """Test full specification enrichment."""

    def test_enrich_simple_spec(self, enricher, simple_spec):
        """Test enriching a simple OpenAPI spec."""
        result = enricher.enrich_spec(simple_spec)

        # Check that spec structure is preserved
        assert "components" in result
        assert "schemas" in result["components"]
        assert "User" in result["components"]["schemas"]

        # Check that name field was enriched
        user_props = result["components"]["schemas"]["User"]["properties"]
        assert "description" in user_props["name"]
        assert "x-f5xc-example" in user_props["name"]

        # Check that email field was enriched
        assert "description" in user_props["email"]
        assert "x-f5xc-example" in user_props["email"]

        # Check that id field was enriched (100% coverage - all fields get descriptions)
        assert "description" in user_props["id"]
        assert "x-f5xc-example" in user_props["id"]

    def test_stats_after_full_enrichment(self, enricher, simple_spec):
        """Test that stats are correctly updated after full enrichment."""
        enricher.enrich_spec(simple_spec)
        stats = enricher.get_stats()

        assert stats["schemas_processed"] > 0
        assert stats["properties_processed"] > 0
        assert stats["descriptions_added"] > 0
        assert stats["examples_added"] > 0

    def test_nested_schemas_processed(self, enricher):
        """Test that nested schemas are processed."""
        spec = {
            "components": {
                "schemas": {
                    "Address": {
                        "type": "object",
                        "properties": {
                            "street": {"type": "string"},
                            "port": {"type": "integer"},
                        },
                    },
                    "Person": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "address": {"$ref": "#/components/schemas/Address"},
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        stats = enricher.get_stats()

        # Both schemas should be processed
        assert stats["schemas_processed"] >= 2

        # Port field should get description
        port_prop = result["components"]["schemas"]["Address"]["properties"]["port"]
        assert "description" in port_prop

    def test_arrays_and_compositions_handled(self, enricher):
        """Test that arrays and schema compositions are handled."""
        spec = {
            "components": {
                "schemas": {
                    "Item": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                    "ItemArray": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Item"},
                    },
                    "Combined": {
                        "allOf": [
                            {"$ref": "#/components/schemas/Item"},
                            {
                                "type": "object",
                                "properties": {
                                    "email": {"type": "string"},
                                },
                            },
                        ],
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        # Should not raise any errors
        assert result is not None
        assert "components" in result


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_spec(self, enricher):
        """Test enriching empty spec."""
        result = enricher.enrich_spec({})
        assert result == {}

    def test_spec_without_schemas(self, enricher):
        """Test enriching spec without schemas section."""
        spec = {"info": {"title": "Test API"}, "paths": {}}
        result = enricher.enrich_spec(spec)
        assert result is not None

    def test_null_values_handled(self, enricher):
        """Test that null values are handled safely."""
        spec = {
            "components": {
                "schemas": {
                    "Test": {
                        "type": "object",
                        "properties": {
                            "name": None,
                            "email": {"type": "string"},
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        # Should not raise, should preserve null
        assert result["components"]["schemas"]["Test"]["properties"]["name"] is None

    def test_non_dict_schema_values(self, enricher):
        """Test that non-dict values are preserved."""
        spec = {
            "components": {
                "schemas": {
                    "Test": {
                        "type": "string",
                        # Not all schemas have properties
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        assert result["components"]["schemas"]["Test"]["type"] == "string"

    def test_multiple_enrichment_preserves_additions(self, enricher):
        """Test that running enrichment twice doesn't duplicate."""
        spec = {
            "components": {
                "schemas": {
                    "Test": {"type": "object", "properties": {"name": {"type": "string"}}},
                },
            },
        }

        # First enrichment
        result1 = enricher.enrich_spec(spec)
        desc1 = result1["components"]["schemas"]["Test"]["properties"]["name"]["description"]

        # Reset stats and enrich again
        enricher.stats.descriptions_added = 0
        enricher.stats.examples_added = 0

        # Create fresh enricher to test with preserved flag
        enricher2 = FieldDescriptionEnricher()
        enricher2.preserve_existing = True

        result2 = enricher2.enrich_spec(result1)
        desc2 = result2["components"]["schemas"]["Test"]["properties"]["name"]["description"]

        # Description should be identical (not duplicated)
        assert desc1 == desc2


class TestExampleStringConversion:
    """Test x-f5xc-example string type conversion (Issue #136 fix)."""

    def test_example_is_always_string_type_for_numeric(self, enricher):
        """Verify x-f5xc-example is string even for numeric values (port: 8080)."""
        prop = {"type": "integer"}
        enricher._enrich_property(prop, "port")

        assert "x-f5xc-example" in prop
        assert isinstance(prop["x-f5xc-example"], str), "x-f5xc-example must be string type"
        assert prop["x-f5xc-example"] == "8080"

    def test_example_is_always_string_type_for_boolean(self, enricher):
        """Verify x-f5xc-example is string for boolean values."""
        prop = {"type": "boolean"}
        enricher._enrich_property(prop, "enabled")

        # Note: boolean fields don't currently have examples in default config
        # but this test ensures if they did, they'd be strings
        if "x-f5xc-example" in prop:
            assert isinstance(prop["x-f5xc-example"], str), "x-f5xc-example must be string type"

    def test_example_is_always_string_type_for_uuid(self, enricher):
        """Verify x-f5xc-example is string for UUID values."""
        prop = {"type": "string", "format": "uuid"}
        enricher._enrich_property(prop, "uuid")

        assert "x-f5xc-example" in prop
        assert isinstance(prop["x-f5xc-example"], str), "x-f5xc-example must be string type"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
