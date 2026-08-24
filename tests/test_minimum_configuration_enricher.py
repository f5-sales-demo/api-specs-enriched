# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=protected-access,use-implicit-booleaness-not-comparison  # Tests inspect internals and explicit result shapes.

"""Tests for MinimumConfigurationEnricher.

Tests the enrichment of OpenAPI specs with minimum configuration metadata
for AI-assisted resource creation.
"""

import pytest

from scripts.utils.extension_constants import (
    X_F5XC_CLI_DOMAIN,
    X_F5XC_MINIMUM_CONFIGURATION,
    X_F5XC_REQUIRED_FOR,
)
from scripts.utils.minimum_configuration_enricher import (
    MinimumConfigurationEnricher,
    MinimumConfigurationStats,
)


class TestMinimumConfigurationStats:
    """Test MinimumConfigurationStats dataclass."""

    def test_stats_initialization(self):
        """Test stats initialization with default values."""
        stats = MinimumConfigurationStats()
        assert stats.schemas_enriched == 0
        assert stats.minimum_configs_added == 0
        assert stats.required_fields_added == 0
        assert stats.field_requirements_added == 0
        assert stats.example_yamls_generated == 0
        assert stats.example_jsons_generated == 0
        assert stats.cli_domains_added == 0
        assert stats.errors == []

    def test_stats_to_dict(self):
        """Test stats conversion to dictionary."""
        stats = MinimumConfigurationStats()
        stats.schemas_enriched = 5
        stats.minimum_configs_added = 5
        stats.required_fields_added = 10

        result = stats.to_dict()
        assert result["schemas_enriched"] == 5
        assert result["minimum_configs_added"] == 5
        assert result["required_fields_added"] == 10
        assert "error_count" in result


class TestMinimumConfigurationEnricherBasics:
    """Test basic MinimumConfigurationEnricher functionality."""

    def test_enricher_initialization(self):
        """Test enricher initializes with config loaded."""
        enricher = MinimumConfigurationEnricher()
        assert enricher.config is not None
        assert enricher.resources is not None
        assert len(enricher.resources) > 0
        assert enricher.stats is not None

    def test_resources_loaded(self):
        """Test that all 5 priority resources are loaded."""
        enricher = MinimumConfigurationEnricher()
        expected_resources = {
            "http_loadbalancer",
            "origin_pool",
            "tcp_loadbalancer",
            "healthcheck",
            "app_firewall",
        }
        loaded_resources = set(enricher.resources.keys())
        for resource in expected_resources:
            assert resource in loaded_resources, f"Missing resource: {resource}"

    def test_domain_categorizer_initialized(self):
        """Test that DomainCategorizer singleton is initialized."""
        enricher = MinimumConfigurationEnricher()
        assert enricher.domain_categorizer is not None
        # Verify it can categorize schemas
        domain = enricher.domain_categorizer.categorize("http_loadbalancer")
        assert domain is not None


class TestResourceDetection:
    """Test resource type detection from schema names."""

    def test_detect_direct_match(self):
        """Test direct schema name match."""
        enricher = MinimumConfigurationEnricher()
        assert enricher._detect_resource_type("http_loadbalancer") == "http_loadbalancer"
        assert enricher._detect_resource_type("origin_pool") == "origin_pool"

    def test_detect_with_request_suffix(self):
        """Test detection with Request suffix."""
        enricher = MinimumConfigurationEnricher()
        assert (
            enricher._detect_resource_type("http_loadbalancerCreateRequest") == "http_loadbalancer"
        )
        assert enricher._detect_resource_type("origin_poolCreateRequest") == "origin_pool"

    def test_detect_with_spec_type_suffix(self):
        """Test detection with SpecType suffix."""
        enricher = MinimumConfigurationEnricher()
        assert (
            enricher._detect_resource_type("http_loadbalancerCreateSpecType") == "http_loadbalancer"
        )
        assert enricher._detect_resource_type("healthcheckCreateSpecType") == "healthcheck"

    def test_detect_with_response_suffix(self):
        """Test detection with Response suffix."""
        enricher = MinimumConfigurationEnricher()
        assert enricher._detect_resource_type("origin_poolCreateResponse") == "origin_pool"
        assert enricher._detect_resource_type("tcp_loadbalancerGetResponse") == "tcp_loadbalancer"

    def test_detect_partial_matching(self):
        """Test partial matching for compound names."""
        enricher = MinimumConfigurationEnricher()
        # Should find 'app_firewall' in longer name
        result = enricher._detect_resource_type("app_firewallSomeOtherType")
        assert result == "app_firewall"

    def test_detect_no_match(self):
        """Test detection with non-matching schema."""
        enricher = MinimumConfigurationEnricher()
        result = enricher._detect_resource_type("SomeRandomSchema")
        assert result is None

    def test_detect_http_loadbalancer_variants(self):
        """Test detection of various http_loadbalancer schema name variations."""
        enricher = MinimumConfigurationEnricher()
        variants = [
            "http_loadbalancerCreateRequest",
            "http_loadbalancerUpdateRequest",
            "http_loadbalancerGetResponse",
            "http_loadbalancerDeleteResponse",
            "http_loadbalancerCreateSpecType",
            "http_loadbalancerUpdateSpecType",
            "http_loadbalancerGetSpecType",
        ]
        for variant in variants:
            result = enricher._detect_resource_type(variant)
            assert result == "http_loadbalancer", f"Failed to detect {variant}"


class TestRequiredFieldExtraction:
    """Test extraction of required fields from schema."""

    def test_extract_config_required_fields(self):
        """Test extraction of required fields from config."""
        enricher = MinimumConfigurationEnricher()
        resource_type = "http_loadbalancer"
        schema = {}

        required = enricher._extract_required_fields(resource_type, schema)
        assert isinstance(required, list)
        assert len(required) > 0
        # Should include metadata.name and metadata.namespace
        assert any("metadata.name" in field for field in required)

    def test_extract_required_fields_from_nested_config(self):
        """Test extraction handles nested field paths."""
        enricher = MinimumConfigurationEnricher()
        required_fields = enricher.resources.get("origin_pool", {}).get("required_fields", [])
        assert required_fields is not None
        assert len(required_fields) > 0


class TestExampleGeneration:
    """Test example YAML and CLI command generation."""

    def test_example_yaml_generation(self):
        """Test that example YAML is configured for resources."""
        enricher = MinimumConfigurationEnricher()
        for resource in [
            "http_loadbalancer",
            "origin_pool",
            "tcp_loadbalancer",
            "healthcheck",
            "app_firewall",
        ]:
            resource_config = enricher.resources.get(resource, {})
            example_yaml = resource_config.get("example_yaml", "")
            assert example_yaml, f"No example_yaml for {resource}"
            assert "apiVersion" in example_yaml or "kind" in example_yaml, (
                f"Invalid YAML structure for {resource}"
            )

    def test_example_json_configured(self):
        """Test that example JSON is configured for resources."""
        enricher = MinimumConfigurationEnricher()
        for resource in [
            "http_loadbalancer",
            "origin_pool",
            "tcp_loadbalancer",
            "healthcheck",
            "app_firewall",
        ]:
            resource_config = enricher.resources.get(resource, {})
            example_json = resource_config.get("example_json", "")
            assert example_json, f"No example_json for {resource}"
            assert "metadata" in example_json, f"Invalid JSON structure for {resource}"


class TestDomainMapping:
    """Test domain classification for resources."""

    def test_get_domain_for_http_loadbalancer(self):
        """Test domain mapping for http_loadbalancer."""
        enricher = MinimumConfigurationEnricher()
        domain = enricher._get_domain_for_resource(
            "http_loadbalancer",
            "http_loadbalancerCreateRequest",
        )
        assert domain is not None
        assert domain in ["virtual", "cdn", "loadbalancing"]

    def test_get_domain_for_app_firewall(self):
        """Test domain mapping for app_firewall."""
        enricher = MinimumConfigurationEnricher()
        domain = enricher._get_domain_for_resource("app_firewall", "app_firewallCreateRequest")
        # app_firewall was regrouped under the `virtual` domain alongside
        # http_loadbalancer and waf — see config/domain_patterns.yaml.
        assert domain == "virtual"

    def test_get_domain_explicit_config(self):
        """Test that explicit domain in config is used."""
        enricher = MinimumConfigurationEnricher()
        # http_loadbalancer should have explicit domain configured
        domain = enricher._get_domain_for_resource(
            "http_loadbalancer",
            "http_loadbalancerCreateRequest",
        )
        assert domain is not None


class TestFullEnrichment:
    """Test full enrichment workflow."""

    def test_enrich_spec_with_resources(self):
        """Test enriching spec with resource schemas."""
        enricher = MinimumConfigurationEnricher()

        # Create minimal spec with http_loadbalancer schema
        spec = {
            "components": {
                "schemas": {
                    "http_loadbalancerCreateRequest": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "namespace": {"type": "string"},
                        },
                    },
                },
            },
        }

        enriched = enricher.enrich_spec(spec)

        # Verify schema was enriched with minimum configuration
        schema = enriched["components"]["schemas"]["http_loadbalancerCreateRequest"]
        assert X_F5XC_MINIMUM_CONFIGURATION in schema
        assert X_F5XC_CLI_DOMAIN in schema
        assert enricher.stats.minimum_configs_added > 0

    def test_enrich_preserves_original_schema(self):
        """Test that enrichment preserves original schema properties."""
        enricher = MinimumConfigurationEnricher()

        original_type = "object"
        original_properties = {"name": {"type": "string"}}

        spec = {
            "components": {
                "schemas": {
                    "http_loadbalancerCreateRequest": {
                        "type": original_type,
                        "properties": original_properties,
                    },
                },
            },
        }

        enriched = enricher.enrich_spec(spec)
        schema = enriched["components"]["schemas"]["http_loadbalancerCreateRequest"]

        # Verify original properties are preserved
        assert schema["type"] == original_type
        assert schema["properties"] == original_properties

    def test_enrich_empty_spec(self):
        """Test enriching spec with no schemas."""
        enricher = MinimumConfigurationEnricher()
        spec = {"components": {"schemas": {}}}
        enricher.enrich_spec(spec)
        assert enricher.stats.minimum_configs_added == 0

    def test_enrich_no_matching_resources(self):
        """Test enriching spec with no matching resources."""
        enricher = MinimumConfigurationEnricher()
        spec = {
            "components": {
                "schemas": {
                    "UnrelatedSchema": {"type": "object"},
                },
            },
        }
        enricher.enrich_spec(spec)
        assert enricher.stats.minimum_configs_added == 0

    def test_stats_collection(self):
        """Test that statistics are properly collected during enrichment."""
        enricher = MinimumConfigurationEnricher()

        spec = {
            "components": {
                "schemas": {
                    "http_loadbalancerCreateRequest": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                    "origin_poolCreateRequest": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                },
            },
        }

        enricher.enrich_spec(spec)
        stats = enricher.get_stats()

        assert stats["schemas_enriched"] >= 2
        assert stats["minimum_configs_added"] >= 2
        assert stats["cli_domains_added"] >= 2
        assert len(stats["errors"]) == 0


class TestAllFiveResources:
    """Test enrichment for all 5 priority resources."""

    @pytest.mark.parametrize(
        ("resource_type", "schema_name"),
        [
            ("http_loadbalancer", "http_loadbalancerCreateRequest"),
            ("origin_pool", "origin_poolCreateRequest"),
            ("tcp_loadbalancer", "tcp_loadbalancerCreateRequest"),
            ("healthcheck", "healthcheckCreateRequest"),
            ("app_firewall", "app_firewallCreateRequest"),
        ],
    )
    def test_enrich_all_resources(self, resource_type, schema_name):
        """Test enrichment for all 5 resources."""
        enricher = MinimumConfigurationEnricher()

        spec = {
            "components": {
                "schemas": {
                    schema_name: {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                },
            },
        }

        enriched = enricher.enrich_spec(spec)
        schema = enriched["components"]["schemas"][schema_name]

        # Verify minimum configuration was added
        assert X_F5XC_MINIMUM_CONFIGURATION in schema, f"No minimum config for {resource_type}"

        min_config = schema[X_F5XC_MINIMUM_CONFIGURATION]
        assert "required_fields" in min_config
        assert "description" in min_config
        assert "example_yaml" in min_config
        assert "example_json" in min_config

        # Verify CLI metadata was added
        assert X_F5XC_CLI_DOMAIN in schema
        assert schema[X_F5XC_CLI_DOMAIN] is not None


class TestAutoGenerationForUnconfiguredResources:
    """Test auto-generation for resources without explicit configuration."""

    def test_auto_generate_unconfigured_schema(self):
        """Test auto-generation for a schema without explicit configuration."""
        enricher = MinimumConfigurationEnricher()

        # Create a spec with an unconfigured schema
        spec = {
            "components": {
                "schemas": {
                    "RandomUnknownSchema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "status": {"type": "string"},
                        },
                    },
                },
            },
        }

        enriched = enricher.enrich_spec(spec)
        schema = enriched["components"]["schemas"]["RandomUnknownSchema"]

        # Verify auto-generated minimum configuration was added
        assert X_F5XC_MINIMUM_CONFIGURATION in schema
        min_config = schema[X_F5XC_MINIMUM_CONFIGURATION]
        assert "description" in min_config
        assert "Minimum configuration for" in min_config["description"]
        assert min_config["required_fields"] is not None
        assert min_config["example_yaml"] is not None

        # Verify auto-generated CLI domain
        assert X_F5XC_CLI_DOMAIN in schema
        # Should have been auto-categorized or fallback
        assert schema[X_F5XC_CLI_DOMAIN] is not None

    def test_auto_generation_stats_tracked(self):
        """Test that auto-generation statistics are properly tracked."""
        enricher = MinimumConfigurationEnricher()

        spec = {
            "components": {
                "schemas": {
                    "UnknownResource1": {"type": "object"},
                    "UnknownResource2": {"type": "object"},
                    "http_loadbalancerCreateRequest": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                },
            },
        }

        enricher.enrich_spec(spec)
        stats = enricher.get_stats()

        # Should have auto-generated 2 resources
        assert stats["schemas_auto_generated"] >= 2
        # Configured resource should be counted separately
        assert stats["minimum_configs_added"] >= 1

    def test_multiple_unconfigured_resources(self):
        """Test auto-generation for multiple unconfigured resources."""
        enricher = MinimumConfigurationEnricher()

        # Create specs with various unconfigured resource patterns
        spec = {
            "components": {
                "schemas": {
                    "customTypeA": {"type": "object", "properties": {}},
                    "customTypeB": {"type": "string"},
                    "customTypeC": {"type": "object", "properties": {"id": {"type": "string"}}},
                    "customTypeD": {"type": "array"},
                },
            },
        }

        enriched = enricher.enrich_spec(spec)
        schemas = enriched["components"]["schemas"]

        # All should have been enriched
        for schema_name in ["customTypeA", "customTypeB", "customTypeC", "customTypeD"]:
            schema = schemas[schema_name]
            assert X_F5XC_CLI_DOMAIN in schema
            assert schema[X_F5XC_CLI_DOMAIN] is not None


class TestIdempotency:
    """Test idempotent behavior - running enrichment multiple times produces consistent results."""

    def test_preserve_existing_cli_domain(self):
        """Test that existing x-f5xc-cli-domain is preserved (idempotent)."""
        enricher = MinimumConfigurationEnricher()

        # Create a spec with manually-set x-f5xc-cli-domain
        spec = {
            "components": {
                "schemas": {
                    "custom_resource": {
                        "type": "object",
                        X_F5XC_CLI_DOMAIN: "my_custom_domain",
                    },
                },
            },
        }

        enriched = enricher.enrich_spec(spec)
        schema = enriched["components"]["schemas"]["custom_resource"]

        # Should preserve the existing value
        assert schema[X_F5XC_CLI_DOMAIN] == "my_custom_domain"
        # Should have recorded that it was preserved
        assert enricher.stats.cli_domains_preserved > 0

    def test_multiple_enrichment_passes_idempotent(self):
        """Test that running enrichment twice produces identical results."""
        spec_original = {
            "components": {
                "schemas": {
                    "http_loadbalancerCreateRequest": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                },
            },
        }

        # First pass
        enricher1 = MinimumConfigurationEnricher()
        result1 = enricher1.enrich_spec(spec_original.copy())

        # Second pass - use the enriched result as input
        enricher2 = MinimumConfigurationEnricher()
        result2 = enricher2.enrich_spec(result1)

        # Results should be identical
        schema1 = result1["components"]["schemas"]["http_loadbalancerCreateRequest"]
        schema2 = result2["components"]["schemas"]["http_loadbalancerCreateRequest"]

        assert schema1.get(X_F5XC_CLI_DOMAIN) == schema2.get(X_F5XC_CLI_DOMAIN)
        assert schema1.get(X_F5XC_MINIMUM_CONFIGURATION) == schema2.get(
            X_F5XC_MINIMUM_CONFIGURATION,
        )

    def test_idempotent_stats_tracking(self):
        """Test that idempotent behavior is properly tracked in stats."""
        enricher = MinimumConfigurationEnricher()

        spec = {
            "components": {
                "schemas": {
                    "http_loadbalancerCreateRequest": {
                        "type": "object",
                        "properties": {},
                    },
                    "origin_poolCreateRequest": {"type": "object", "properties": {}},
                },
            },
        }

        enricher.enrich_spec(spec)
        stats = enricher.get_stats()

        # Both resources should have been enriched
        assert stats["schemas_enriched"] >= 2
        # At least some CLI domains should have been added
        assert stats["cli_domains_added"] >= 2
        # No domains should have been preserved initially
        assert stats["cli_domains_preserved"] == 0


class TestAllResourcePatterns:
    """Test enrichment across various resource naming patterns."""

    @pytest.mark.parametrize(
        "schema_name",
        [
            "http_loadbalancerCreateRequest",
            "origin_poolGetResponse",
            "tcp_loadbalancerUpdateRequest",
            "healthcheckDeleteResponse",
            "app_firewallCreateSpecType",
            "unknownResourceType",
            "anotherRandomResource",
        ],
    )
    def test_enrich_various_resource_patterns(self, schema_name):
        """Test enrichment for various resource naming patterns."""
        enricher = MinimumConfigurationEnricher()

        spec = {
            "components": {
                "schemas": {
                    schema_name: {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    },
                },
            },
        }

        enriched = enricher.enrich_spec(spec)
        schema = enriched["components"]["schemas"][schema_name]

        # All schemas should be enriched with minimum configuration
        assert X_F5XC_MINIMUM_CONFIGURATION in schema
        min_config = schema[X_F5XC_MINIMUM_CONFIGURATION]
        assert min_config.get("description") is not None
        assert min_config.get("example_yaml") is not None
        assert min_config.get("required_fields") is not None

        # All should have CLI domain
        assert X_F5XC_CLI_DOMAIN in schema
        assert schema[X_F5XC_CLI_DOMAIN] is not None


class TestOneOfAwareRequiredInference:
    """Test that oneOf variant fields are not marked as required via validation rules."""

    def _make_spec_with_schema(self, schema_name, schema):
        return {"components": {"schemas": {schema_name: schema}}}

    def test_oneof_member_with_gte_rule_not_required(self):
        """A field in a x-ves-oneof-field-* group with gte >= 1 should NOT get required_for."""
        enricher = MinimumConfigurationEnricher()
        schema = {
            "type": "object",
            "x-ves-oneof-field-session_type": '["default_caching", "disable_caching", "max_keys"]',
            "properties": {
                "max_keys": {
                    "type": "integer",
                    "x-ves-validation-rules": {
                        "ves.io.schema.rules.uint32.gte": "2",
                    },
                },
                "default_caching": {"$ref": "#/components/schemas/Empty"},
                "disable_caching": {"$ref": "#/components/schemas/Empty"},
            },
        }
        spec = self._make_spec_with_schema("origin_poolUpstreamTlsParameters", schema)
        enriched = enricher.enrich_spec(spec)
        props = enriched["components"]["schemas"]["origin_poolUpstreamTlsParameters"]["properties"]
        max_keys_rf = props["max_keys"].get(X_F5XC_REQUIRED_FOR)
        if max_keys_rf is not None:
            assert max_keys_rf["minimum_config"] is False
            assert max_keys_rf["create"] is False

    def test_non_oneof_field_with_gte_rule_is_required(self):
        """A field NOT in any oneOf group with gte >= 1 SHOULD get required_for: true."""
        enricher = MinimumConfigurationEnricher()
        schema = {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "integer",
                    "x-ves-validation-rules": {
                        "ves.io.schema.rules.uint32.gte": "1",
                    },
                },
            },
        }
        spec = self._make_spec_with_schema("SomeUnknownType", schema)
        enriched = enricher.enrich_spec(spec)
        props = enriched["components"]["schemas"]["SomeUnknownType"]["properties"]
        threshold_rf = props["threshold"].get(X_F5XC_REQUIRED_FOR)
        assert threshold_rf is not None
        assert threshold_rf["minimum_config"] is True
        assert threshold_rf["create"] is True

    def test_oneof_member_in_explicit_required_fields_wins(self):
        """Config-defined required_fields overrides oneOf membership — config always wins."""
        enricher = MinimumConfigurationEnricher()
        schema = {
            "type": "object",
            "x-ves-oneof-field-tls_choice": '["no_tls", "use_tls"]',
            "properties": {
                "no_tls": {"$ref": "#/components/schemas/Empty"},
                "use_tls": {"$ref": "#/components/schemas/TlsParams"},
                "metadata": {"type": "object"},
                "spec": {"type": "object"},
            },
        }
        spec = self._make_spec_with_schema("origin_poolCreateRequest", schema)
        enriched = enricher.enrich_spec(spec)
        props = enriched["components"]["schemas"]["origin_poolCreateRequest"]["properties"]
        metadata_rf = props["metadata"].get(X_F5XC_REQUIRED_FOR)
        assert metadata_rf is not None
        assert metadata_rf["minimum_config"] is True

    def test_collect_oneof_members_valid_json_array(self):
        """_collect_oneof_members parses valid JSON arrays from x-ves-oneof-field-* keys."""
        enricher = MinimumConfigurationEnricher()
        schema = {
            "x-ves-oneof-field-group_a": '["field_1", "field_2"]',
            "x-ves-oneof-field-group_b": '["field_3"]',
        }
        result = enricher._collect_oneof_members(schema)
        assert isinstance(result, frozenset)
        assert result == frozenset({"field_1", "field_2", "field_3"})

    def test_collect_oneof_members_malformed_json(self):
        """Malformed JSON in x-ves-oneof-field-* is silently ignored."""
        enricher = MinimumConfigurationEnricher()
        schema = {
            "x-ves-oneof-field-bad": "not valid json",
            "x-ves-oneof-field-good": '["field_1"]',
        }
        result = enricher._collect_oneof_members(schema)
        assert result == frozenset({"field_1"})

    def test_collect_oneof_members_dict_value_ignored(self):
        """A dict value (valid JSON but not a list) is silently ignored."""
        enricher = MinimumConfigurationEnricher()
        schema = {
            "x-ves-oneof-field-bad": '{"key": "value"}',
            "x-ves-oneof-field-good": '["field_1"]',
        }
        result = enricher._collect_oneof_members(schema)
        assert result == frozenset({"field_1"})

    def test_collect_oneof_members_no_annotations(self):
        """Schema with no x-ves-oneof-field-* returns empty frozenset."""
        enricher = MinimumConfigurationEnricher()
        schema = {"type": "object", "properties": {}}
        result = enricher._collect_oneof_members(schema)
        assert result == frozenset()

    def test_origin_pool_tls_params_max_session_keys_not_required(self):
        """Integration: max_session_keys in real-world origin_pool schema must not be required."""
        enricher = MinimumConfigurationEnricher()
        schema = {
            "type": "object",
            "x-ves-oneof-field-max_session_keys_type": (
                '["default_session_key_caching","disable_session_key_caching","max_session_keys"]'
            ),
            "x-ves-oneof-field-mtls_choice": '["no_mtls","use_mtls","use_mtls_obj"]',
            "x-ves-oneof-field-server_validation_choice": (
                '["skip_server_verification","use_server_verification","volterra_trusted_ca"]'
            ),
            "x-ves-oneof-field-sni_choice": ('["disable_sni","sni","use_host_header_as_sni"]'),
            "properties": {
                "default_session_key_caching": {"$ref": "#/components/schemas/Empty"},
                "disable_session_key_caching": {"$ref": "#/components/schemas/Empty"},
                "max_session_keys": {
                    "type": "integer",
                    "format": "int64",
                    "x-ves-validation-rules": {
                        "ves.io.schema.rules.uint32.gte": "2",
                        "ves.io.schema.rules.uint32.lte": "64",
                    },
                },
                "tls_config": {"$ref": "#/components/schemas/viewsTlsConfig"},
                "no_mtls": {"$ref": "#/components/schemas/Empty"},
                "skip_server_verification": {"$ref": "#/components/schemas/Empty"},
                "sni": {"type": "string"},
                "disable_sni": {"$ref": "#/components/schemas/Empty"},
                "use_host_header_as_sni": {"$ref": "#/components/schemas/Empty"},
                "use_mtls": {"$ref": "#/components/schemas/TlsCerts"},
                "use_mtls_obj": {"$ref": "#/components/schemas/ObjRef"},
                "use_server_verification": {"$ref": "#/components/schemas/TlsValidation"},
                "volterra_trusted_ca": {"$ref": "#/components/schemas/Empty"},
            },
        }
        spec = self._make_spec_with_schema("origin_poolUpstreamTlsParameters", schema)
        enriched = enricher.enrich_spec(spec)
        props = enriched["components"]["schemas"]["origin_poolUpstreamTlsParameters"]["properties"]

        # max_session_keys is in a oneOf group with gte:2 — must NOT be marked required
        max_keys_rf = props["max_session_keys"].get(X_F5XC_REQUIRED_FOR)
        if max_keys_rf is not None:
            assert max_keys_rf["minimum_config"] is False, (
                "max_session_keys should not be required — it's a oneOf variant"
            )
            assert max_keys_rf["create"] is False

        # All other oneOf members should also not be required
        for oneof_field in [
            "default_session_key_caching",
            "disable_session_key_caching",
            "no_mtls",
            "skip_server_verification",
            "volterra_trusted_ca",
            "disable_sni",
            "use_host_header_as_sni",
        ]:
            rf = props[oneof_field].get(X_F5XC_REQUIRED_FOR)
            if rf is not None:
                assert rf["minimum_config"] is False, (
                    f"{oneof_field} is a oneOf variant — should not be required"
                )


class TestSpecValuePlaceholderFix:
    """Test that schemas with a 'spec' property don't produce spec:value collision."""

    def test_spec_property_not_included_in_example_json(self):
        """A schema with 'spec' as a property should not produce {"spec": {"spec": "value"}}."""
        enricher = MinimumConfigurationEnricher()
        spec = {
            "components": {
                "schemas": {
                    "SomeResourceCreateRequest": {
                        "type": "object",
                        "properties": {
                            "metadata": {"type": "object"},
                            "spec": {"$ref": "#/components/schemas/SomeSpec"},
                        },
                    },
                },
            },
        }
        enriched = enricher.enrich_spec(spec)
        schema = enriched["components"]["schemas"]["SomeResourceCreateRequest"]
        min_config = schema.get("x-f5xc-minimum-configuration", {})
        example_json = min_config.get("example_json", "")
        assert '"spec": "value"' not in example_json, (
            "spec property should be excluded from example — it collides with the spec container"
        )

    def test_spec_property_not_included_in_example_yaml(self):
        """A schema with 'spec' as a property should not produce 'spec: value' in YAML."""
        enricher = MinimumConfigurationEnricher()
        spec = {
            "components": {
                "schemas": {
                    "SomeResourceCreateRequest": {
                        "type": "object",
                        "properties": {
                            "metadata": {"type": "object"},
                            "spec": {"$ref": "#/components/schemas/SomeSpec"},
                        },
                    },
                },
            },
        }
        enriched = enricher.enrich_spec(spec)
        schema = enriched["components"]["schemas"]["SomeResourceCreateRequest"]
        min_config = schema.get("x-f5xc-minimum-configuration", {})
        example_yaml = min_config.get("example_yaml", "")
        assert "  spec: value" not in example_yaml, (
            "spec property should be excluded from YAML example — it collides with the spec container"
        )


class TestSubSchemaNotConfigEnriched:
    """Test that sub-schemas matched via partial name don't get parent config applied."""

    def test_sub_schema_gets_auto_generated_not_config(self):
        """A sub-schema like UpstreamTlsParameters should NOT get origin_pool's config."""
        enricher = MinimumConfigurationEnricher()
        spec = {
            "components": {
                "schemas": {
                    "origin_poolUpstreamTlsParameters": {
                        "type": "object",
                        "properties": {
                            "tls_config": {"$ref": "#/components/schemas/TlsConfig"},
                            "max_session_keys": {"type": "integer"},
                        },
                    },
                },
            },
        }
        enriched = enricher.enrich_spec(spec)
        schema = enriched["components"]["schemas"]["origin_poolUpstreamTlsParameters"]
        min_config = schema.get("x-f5xc-minimum-configuration", {})
        # Should NOT have origin_pool's config-defined required_fields
        rf = min_config.get("required_fields", [])
        assert "metadata.name" not in rf, (
            "Sub-schema should not inherit parent resource's required_fields"
        )
        assert "spec.origin_servers" not in rf

    def test_resource_schema_still_gets_config(self):
        """A CreateSpecType schema should still get config-driven enrichment."""
        enricher = MinimumConfigurationEnricher()
        spec = {
            "components": {
                "schemas": {
                    "viewsorigin_poolCreateSpecType": {
                        "type": "object",
                        "properties": {
                            "metadata": {"type": "object"},
                            "spec": {"type": "object"},
                        },
                    },
                },
            },
        }
        enriched = enricher.enrich_spec(spec)
        schema = enriched["components"]["schemas"]["viewsorigin_poolCreateSpecType"]
        min_config = schema.get("x-f5xc-minimum-configuration", {})
        rf = min_config.get("required_fields", [])
        # Should have origin_pool's config required_fields
        assert "metadata.name" in rf

    def test_is_resource_schema_recognizes_suffixes(self):
        """_is_resource_schema correctly identifies resource vs sub schemas."""
        assert (
            MinimumConfigurationEnricher._is_resource_schema("viewsorigin_poolCreateSpecType")
            is True
        )
        assert MinimumConfigurationEnricher._is_resource_schema("app_firewallCreateRequest") is True
        assert (
            MinimumConfigurationEnricher._is_resource_schema("origin_poolReplaceSpecType") is True
        )
        assert (
            MinimumConfigurationEnricher._is_resource_schema("origin_poolUpstreamTlsParameters")
            is False
        )
        assert (
            MinimumConfigurationEnricher._is_resource_schema("app_firewallDetectionSetting")
            is False
        )
        assert (
            MinimumConfigurationEnricher._is_resource_schema("healthcheckHttpHealthCheck") is False
        )

    def test_sub_schema_still_gets_cli_domain(self):
        """Sub-schemas should still get x-f5xc-cli-domain even without config enrichment."""
        enricher = MinimumConfigurationEnricher()
        spec = {
            "components": {
                "schemas": {
                    "origin_poolUpstreamTlsParameters": {
                        "type": "object",
                        "properties": {
                            "tls_config": {"$ref": "#/components/schemas/TlsConfig"},
                        },
                    },
                },
            },
        }
        enriched = enricher.enrich_spec(spec)
        schema = enriched["components"]["schemas"]["origin_poolUpstreamTlsParameters"]
        assert "x-f5xc-cli-domain" in schema


class TestPrefixStrippingFix:
    """Test that resource names starting with 'api_' are not broken by prefix stripping."""

    def test_api_definition_create_request_detected(self):
        """api_definitionCreateRequest should detect as api_definition, not None."""
        enricher = MinimumConfigurationEnricher()
        result = enricher._detect_resource_type("api_definitionCreateRequest")
        assert result == "api_definition"

    def test_api_discovery_create_request_detected(self):
        """api_discoveryCreateRequest should detect as api_discovery."""
        enricher = MinimumConfigurationEnricher()
        result = enricher._detect_resource_type("api_discoveryCreateRequest")
        assert result == "api_discovery"

    def test_views_prefix_still_stripped(self):
        """viewsorigin_poolCreateSpecType should still strip 'views' prefix."""
        enricher = MinimumConfigurationEnricher()
        result = enricher._detect_resource_type("viewsorigin_poolCreateSpecType")
        assert result == "origin_pool"

    def test_schema_prefix_still_stripped(self):
        """schemaservice_policyCreateSpecType should still strip 'schema' prefix."""
        enricher = MinimumConfigurationEnricher()
        result = enricher._detect_resource_type("schemaservice_policyCreateSpecType")
        assert result == "service_policy"


class TestSpecKeyAlwaysPresent:
    """Test that auto-generated min configs always include spec: {} (#344)."""

    def _make_spec_with_schema(self, schema_name, schema):
        return {"components": {"schemas": {schema_name: schema}}}

    def test_json_includes_spec_key_when_no_spec_fields(self):
        """Auto-generated JSON should include 'spec': {} even with no required spec fields."""
        enricher = MinimumConfigurationEnricher()
        schema = {
            "type": "object",
            "properties": {
                "metadata": {"type": "object"},
                "spec": {"$ref": "#/components/schemas/SomeSpec"},
            },
        }
        spec = self._make_spec_with_schema("UnknownResourceCreateRequest", schema)
        enriched = enricher.enrich_spec(spec)
        mc = enriched["components"]["schemas"]["UnknownResourceCreateRequest"]
        example_json = mc.get("x-f5xc-minimum-configuration", {}).get("example_json", "")
        assert '"spec": {}' in example_json or '"spec": {' in example_json, (
            f"spec key must be present in auto-generated JSON, got: {example_json}"
        )

    def test_yaml_includes_spec_key_when_no_spec_fields(self):
        """Auto-generated YAML should include 'spec: {}' even with no required spec fields."""
        enricher = MinimumConfigurationEnricher()
        schema = {
            "type": "object",
            "properties": {
                "metadata": {"type": "object"},
                "spec": {"$ref": "#/components/schemas/SomeSpec"},
            },
        }
        spec = self._make_spec_with_schema("UnknownResourceCreateRequest", schema)
        enriched = enricher.enrich_spec(spec)
        mc = enriched["components"]["schemas"]["UnknownResourceCreateRequest"]
        example_yaml = mc.get("x-f5xc-minimum-configuration", {}).get("example_yaml", "")
        assert "spec: {}" in example_yaml or "spec:" in example_yaml, (
            f"spec key must be present in auto-generated YAML, got: {example_yaml}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
