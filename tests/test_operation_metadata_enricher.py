# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unit tests for OperationMetadataEnricher."""

from pathlib import Path

import pytest
import yaml

from scripts.utils.operation_metadata_enricher import OperationMetadataEnricher


@pytest.fixture
def enricher():
    """Create enricher with default config."""
    return OperationMetadataEnricher()


@pytest.fixture
def simple_spec():
    """Create a simple OpenAPI spec with operations."""
    return {
        "paths": {
            "/api/config/namespaces/{namespace}/http_loadbalancers": {
                "get": {"operationId": "list_loadbalancers"},
                "post": {
                    "operationId": "create_loadbalancer",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "required": ["metadata"],
                                    "properties": {
                                        "metadata": {
                                            "required": ["name", "namespace"],
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "/api/config/namespaces/{namespace}/http_loadbalancers/{name}": {
                "get": {"operationId": "get_loadbalancer"},
                "delete": {"operationId": "delete_loadbalancer"},
            },
        },
    }


class TestOperationMetadataEnricherBasics:
    """Test basic enricher functionality."""

    def test_initialization(self):
        """Test enricher initializes with default config."""
        enricher = OperationMetadataEnricher()
        assert len(enricher.danger_levels) > 0

    def test_config_loading_missing_file_fails_closed(self):
        """A missing production contract cannot silently select built-in defaults."""
        with pytest.raises(FileNotFoundError, match="operation metadata config not found"):
            OperationMetadataEnricher(config_path=Path("/nonexistent/path.yaml"))

    @pytest.mark.parametrize(
        ("mutation", "message"),
        [
            (lambda config: config.update({"extension_prefix": "x-example"}), "unknown.*keys"),
            (
                lambda config: config["danger_levels"]["escalation_patterns"][0].update(
                    {"pattern": "["},
                ),
                "invalid.*regex",
            ),
            (
                lambda config: config["side_effects"]["method_effects"].update(
                    {"POST": "observes"},
                ),
                "method effects",
            ),
        ],
    )
    def test_invalid_config_fails_closed(self, tmp_path, mutation, message):
        config = yaml.safe_load(Path("config/operation_metadata.yaml").read_text())
        mutation(config)
        path = tmp_path / "operation_metadata.yaml"
        path.write_text(yaml.safe_dump(config))

        with pytest.raises(ValueError, match=message):
            OperationMetadataEnricher(config_path=path)

    def test_stats_initialization(self):
        """Test enrichment stats start at zero."""
        enricher = OperationMetadataEnricher()
        stats = enricher.get_stats()
        assert stats["operations_enriched"] == 0
        assert stats["danger_levels_assigned"] == 0


class TestDangerLevelCalculation:
    """Test danger level classification."""

    def test_get_operation_low_danger(self, enricher):
        """Test GET operations are low danger."""
        danger = enricher._calculate_danger_level("GET", "/api/resources", {})
        assert danger == "low"

    def test_head_operation_low_danger(self, enricher):
        """Test HEAD operations are low danger."""
        danger = enricher._calculate_danger_level("HEAD", "/api/resources", {})
        assert danger == "low"

    def test_post_operation_medium_danger(self, enricher):
        """Test POST operations are medium danger."""
        danger = enricher._calculate_danger_level("POST", "/api/resources", {})
        assert danger == "medium"

    def test_put_operation_medium_danger(self, enricher):
        """Test PUT operations are medium danger."""
        danger = enricher._calculate_danger_level("PUT", "/api/resources/item", {})
        assert danger == "medium"

    def test_delete_operation_high_danger(self, enricher):
        """Test DELETE operations are high danger."""
        danger = enricher._calculate_danger_level("DELETE", "/api/resources/item", {})
        assert danger == "high"

    def test_delete_namespace_escalated_danger(self, enricher):
        """Test DELETE /namespace is escalated to high."""
        danger = enricher._calculate_danger_level(
            "DELETE",
            "/api/config/namespaces/default",
            {},
        )
        assert danger == "high"

    def test_delete_security_escalated_danger(self, enricher):
        """Test DELETE /security is escalated to high."""
        danger = enricher._calculate_danger_level(
            "DELETE",
            "/api/config/security/policies",
            {},
        )
        assert danger == "high"

    def test_post_system_escalated_danger(self, enricher):
        """Test POST /system_ is escalated to medium."""
        danger = enricher._calculate_danger_level(
            "POST",
            "/api/config/system_settings",
            {},
        )
        assert danger == "medium"


class TestRequiredFieldExtraction:
    """Test extracting required fields from operations."""

    def test_extract_from_request_body_schema(self, enricher):
        """Test extracting required fields from requestBody."""
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "required": ["metadata", "spec"],
                        },
                    },
                },
            },
        }

        required = enricher._extract_required_fields(operation, "POST")
        assert "metadata" in required
        assert "spec" in required

    def test_extract_nested_required_fields(self, enricher):
        """Test extracting nested required fields."""
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "properties": {
                                "metadata": {
                                    "required": ["name", "namespace"],
                                },
                            },
                        },
                    },
                },
            },
        }

        required = enricher._extract_required_fields(operation, "POST")
        assert "metadata.name" in required
        assert "metadata.namespace" in required

    def test_post_adds_standard_fields(self, enricher):
        """Test POST operations get standard create fields."""
        operation = {"requestBody": None}

        required = enricher._extract_required_fields(operation, "POST")
        assert "metadata.name" in required
        assert "metadata.namespace" in required

    def test_get_has_no_required_fields(self, enricher):
        """Test GET operations don't get create fields."""
        operation = {}

        required = enricher._extract_required_fields(operation, "GET")
        assert "metadata.name" not in required

    def test_path_parameters_extracted(self, enricher):
        """Test required path parameters are extracted."""
        operation = {
            "parameters": [
                {"name": "namespace", "in": "path", "required": True},
                {"name": "name", "in": "path", "required": True},
            ],
        }

        required = enricher._extract_required_fields(operation, "GET")
        assert "path.namespace" in required
        assert "path.name" in required


class TestSideEffectDetermination:
    """Test side effect determination."""

    def test_post_creates_resource(self, enricher):
        """Test POST determines creates side effect."""
        side_effects = enricher._determine_side_effects(
            "POST",
            "/api/config/namespaces/default/http_loadbalancers",
            {},
        )

        assert "creates" in side_effects
        assert len(side_effects["creates"]) > 0

    def test_put_modifies_resource(self, enricher):
        """Test PUT determines modifies side effect."""
        side_effects = enricher._determine_side_effects(
            "PUT",
            "/api/config/namespaces/default/http_loadbalancers/lb1",
            {},
        )

        assert "modifies" in side_effects
        assert len(side_effects["modifies"]) > 0

    def test_delete_deletes_resource(self, enricher):
        """Test DELETE determines deletes side effect."""
        side_effects = enricher._determine_side_effects(
            "DELETE",
            "/api/config/namespaces/default/http_loadbalancers/lb1",
            {},
        )

        assert "deletes" in side_effects
        assert len(side_effects["deletes"]) > 0

    def test_delete_namespace_affects_contained(self, enricher):
        """Test DELETE /namespace affects contained resources."""
        side_effects = enricher._determine_side_effects(
            "DELETE",
            "/api/config/namespaces/default",
            {},
        )

        assert "deletes" in side_effects
        assert "contained_resources" in side_effects["deletes"]


class TestResourceTypeExtraction:
    """Test resource type extraction from paths."""

    def test_extract_http_loadbalancer(self, enricher):
        """Test extracting http_loadbalancer resource type."""
        resource = enricher._extract_resource_type(
            "/api/config/namespaces/{namespace}/http_loadbalancers",
        )

        assert resource == "http-loadbalancer"

    def test_extract_origin_pool(self, enricher):
        """Test extracting origin_pool resource type."""
        resource = enricher._extract_resource_type(
            "/api/config/namespaces/{namespace}/origin_pools",
        )

        assert resource == "origin-pool"

    def test_extract_with_parameters(self, enricher):
        """Test extracting resource type with path parameters."""
        resource = enricher._extract_resource_type(
            "/api/config/namespaces/{namespace}/items/{id}",
        )

        assert resource == "item"

    def test_simple_path_extraction(self, enricher):
        """Test extracting from simple path."""
        resource = enricher._extract_resource_type("/api/resources")

        assert resource == "resource"


class TestDomainExtraction:
    """Test domain extraction from paths."""

    def test_extract_virtual_domain(self, enricher):
        """Test extracting virtual domain."""
        domain = enricher._extract_domain("/api/virtual/loadbalancers")
        assert domain == "virtual"

    def test_extract_from_config_path(self, enricher):
        """Test extracting from /api/config path."""
        domain = enricher._extract_domain("/api/config/namespaces/{ns}/items")
        assert domain == "config"

    def test_fallback_to_default(self, enricher):
        """Test fallback to default domain."""
        domain = enricher._extract_domain("/some/other/path")
        assert domain == "default"


class TestSpecEnrichment:
    """Test full specification enrichment."""

    def test_enrich_simple_spec(self, enricher, simple_spec):
        """Test enriching a simple OpenAPI spec."""
        result = enricher.enrich_spec(simple_spec)

        # Check paths exist
        assert "paths" in result

        # Check GET operation enriched
        list_op = result["paths"]["/api/config/namespaces/{namespace}/http_loadbalancers"]["get"]
        assert list_op["x-f5xc-operation-metadata"]["danger_level"] == "low"

        # Check DELETE operation enriched
        delete_op = result["paths"]["/api/config/namespaces/{namespace}/http_loadbalancers/{name}"][
            "delete"
        ]
        delete_metadata = delete_op["x-f5xc-operation-metadata"]
        assert delete_metadata["danger_level"] == "high"
        assert delete_metadata["confirmation_required"] is True

        # Check POST operation has required fields
        create_op = result["paths"]["/api/config/namespaces/{namespace}/http_loadbalancers"]["post"]
        assert create_op["x-f5xc-operation-metadata"]["required_fields"]

    def test_stats_updated(self, enricher, simple_spec):
        """Test stats are updated after enrichment."""
        enricher.enrich_spec(simple_spec)
        stats = enricher.get_stats()

        assert stats["operations_enriched"] > 0
        assert stats["danger_levels_assigned"] > 0

    def test_no_paths_handled(self, enricher):
        """Test spec without paths is handled."""
        spec = {"components": {}}
        result = enricher.enrich_spec(spec)
        assert result == spec


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_spec(self, enricher):
        """Test enriching empty spec."""
        result = enricher.enrich_spec({})
        assert result == {}

    def test_non_operation_items_skipped(self, enricher):
        """Test that non-operation items are skipped."""
        spec = {
            "paths": {
                "/api/items": {
                    "parameters": [{"name": "id"}],  # Not an operation
                    "get": {"operationId": "list"},
                },
            },
        }

        result = enricher.enrich_spec(spec)
        # Should not raise, parameters should be unchanged
        assert result["paths"]["/api/items"]["parameters"] == [{"name": "id"}]

    def test_missing_request_body(self, enricher):
        """Test operation without requestBody."""
        operation = {}
        required = enricher._extract_required_fields(operation, "GET")
        assert required == []

    def test_empty_paths(self, enricher):
        """Test spec with empty paths."""
        spec = {"paths": {}}
        result = enricher.enrich_spec(spec)
        assert result == spec


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
