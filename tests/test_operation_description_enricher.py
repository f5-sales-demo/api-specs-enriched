# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unit tests for OperationDescriptionEnricher.

Tests the three-tier matching strategy:
1. Exact resource type match
2. Pattern-based regex match
3. HTTP method fallback

Validates DRY-compliant, noun-first descriptions for operation purpose fields.
"""

import pytest

from scripts.utils.operation_description_enricher import OperationDescriptionEnricher


@pytest.fixture
def enricher():
    """Create enricher with default config."""
    return OperationDescriptionEnricher()


@pytest.fixture
def sample_spec():
    """Create a sample OpenAPI spec with operations."""
    return {
        "paths": {
            "/api/config/namespaces/{namespace}/http_loadbalancers": {
                "get": {
                    "operationId": "listHttpLoadBalancers",
                    "summary": "List HTTP Load Balancers",
                },
                "post": {
                    "operationId": "createHttpLoadBalancer",
                    "summary": "Create HTTP Load Balancer",
                },
            },
            "/api/config/namespaces/{namespace}/http_loadbalancers/{name}": {
                "get": {
                    "operationId": "getHttpLoadBalancer",
                    "summary": "Get HTTP Load Balancer",
                },
                "delete": {
                    "operationId": "deleteHttpLoadBalancer",
                    "summary": "Delete HTTP Load Balancer",
                },
            },
            "/api/config/namespaces/{namespace}/origin_pools": {
                "post": {
                    "operationId": "createOriginPool",
                    "summary": "Create Origin Pool",
                },
            },
        },
    }


class TestOperationDescriptionEnricherBasics:
    """Test basic enricher functionality."""

    def test_initialization(self, enricher):
        """Test enricher initializes correctly."""
        assert enricher.enabled is True
        assert len(enricher.resources) > 0
        assert len(enricher.patterns) > 0
        assert len(enricher.method_fallbacks) > 0

    def test_image_download_query_has_a_read_only_description(self, enricher):
        assert (
            enricher.get_operation_description(
                "ves.io.schema.registration.CustomAPI.GetImageDownloadUrl"
            )
            == "Retrieve signed Customer Edge image download URLs"
        )

    def test_disabled_enricher(self, tmp_path):
        """Test enricher respects enabled flag."""
        config_path = tmp_path / "disabled_config.yaml"
        config_path.write_text("enabled: false\n")

        enricher = OperationDescriptionEnricher(config_path=config_path)
        assert enricher.enabled is False
        assert enricher.get_description("http_loadbalancer", "POST") is None

    def test_stats_initialization(self, enricher):
        """Test enrichment stats start at zero."""
        stats = enricher.get_stats()
        assert stats["operations_processed"] == 0
        assert stats["descriptions_applied"] == 0
        assert stats["exact_matches"] == 0
        assert stats["pattern_matches"] == 0
        assert stats["method_fallbacks"] == 0

    def test_stats_reset(self, enricher):
        """Test stats can be reset."""
        enricher.stats.operations_processed = 5
        enricher.reset_stats()
        assert enricher.stats.operations_processed == 0


class TestResourceTypeExtraction:
    """Test resource type extraction from API paths."""

    def test_extract_http_loadbalancer(self, enricher):
        """Test extraction from http_loadbalancers path."""
        resource = enricher._extract_resource_type(
            "/api/config/namespaces/{namespace}/http_loadbalancers",
        )
        assert resource == "http_loadbalancer"

    def test_extract_origin_pool(self, enricher):
        """Test extraction from origin_pools path."""
        resource = enricher._extract_resource_type(
            "/api/config/namespaces/{namespace}/origin_pools",
        )
        assert resource == "origin_pool"

    def test_extract_with_id_parameter(self, enricher):
        """Test extraction from path with {name} or {id} parameter."""
        resource = enricher._extract_resource_type(
            "/api/config/namespaces/{namespace}/http_loadbalancers/{name}",
        )
        assert resource == "http_loadbalancer"

    def test_extract_from_system_api(self, enricher):
        """Test extraction from /api/system/ paths."""
        resource = enricher._extract_resource_type(
            "/api/system/namespaces/{namespace}/certificates",
        )
        assert resource == "certificate"

    def test_extract_returns_none_for_invalid_path(self, enricher):
        """Test returns None for paths without resource type."""
        resource = enricher._extract_resource_type("/api/config")
        assert resource is None


class TestDescriptionMatching:
    """Test three-tier description matching strategy."""

    def test_exact_match_http_loadbalancer(self, enricher):
        """Test exact resource match for http_loadbalancer."""
        description = enricher.get_description("http_loadbalancer", "POST", "short")
        assert description is not None
        assert len(description) <= 60
        assert description.startswith("HTTP")  # Noun-first
        enricher.get_description("http_loadbalancer", "POST")  # Trigger stat
        stats = enricher.get_stats()
        assert stats["exact_matches"] >= 1

    def test_exact_match_origin_pool(self, enricher):
        """Test exact resource match for origin_pool."""
        description = enricher.get_description("origin_pool", "POST", "medium")
        assert description is not None
        assert len(description) <= 150

    def test_pattern_match_generic_loadbalancer(self, enricher):
        """Test pattern match for generic loadbalancer."""
        # Reset stats first
        enricher.reset_stats()
        description = enricher.get_description("tcp_loadbalancer", "POST", "short")
        # Should match .*loadbalancer.* pattern if not exactly configured
        if description:
            stats = enricher.get_stats()
            # Could be exact match OR pattern match depending on config
            assert (stats["exact_matches"] + stats["pattern_matches"]) >= 1

    def test_pattern_match_generic_pool(self, enricher):
        """Test pattern match for generic pool resources."""
        enricher.reset_stats()
        description = enricher.get_description("server_pool", "POST", "short")
        if description:
            stats = enricher.get_stats()
            assert stats["pattern_matches"] >= 1

    def test_method_fallback_post(self, enricher):
        """Test fallback to HTTP method for unknown resource."""
        enricher.reset_stats()
        description = enricher.get_description("unknown_resource", "POST", "short")
        assert description is not None
        assert "creation" in description.lower() or "create" in description.lower()
        stats = enricher.get_stats()
        assert stats["method_fallbacks"] >= 1

    def test_method_fallback_get(self, enricher):
        """Test fallback to HTTP method GET for unknown resource."""
        enricher.reset_stats()
        description = enricher.get_description("unknown_resource", "GET", "short")
        assert description is not None
        stats = enricher.get_stats()
        assert stats["method_fallbacks"] >= 1

    def test_method_fallback_delete(self, enricher):
        """Test fallback to HTTP method DELETE for unknown resource."""
        enricher.reset_stats()
        description = enricher.get_description("unknown_resource", "DELETE", "short")
        assert description is not None
        assert "deletion" in description.lower() or "delete" in description.lower()
        stats = enricher.get_stats()
        assert stats["method_fallbacks"] >= 1


class TestSpecEnrichment:
    """Test enrichment of OpenAPI specifications."""

    def test_enrich_adds_operation_metadata(self, enricher, sample_spec):
        """Test enrichment adds x-f5xc-operation-metadata to operations."""
        result = enricher.enrich_spec(sample_spec)

        # Check POST operation
        post_op = result["paths"]["/api/config/namespaces/{namespace}/http_loadbalancers"]["post"]
        assert "x-f5xc-operation-metadata" in post_op
        assert "purpose" in post_op["x-f5xc-operation-metadata"]

    def test_enrich_purpose_is_noun_first(self, enricher, sample_spec):
        """Test enrichment provides noun-first descriptions."""
        result = enricher.enrich_spec(sample_spec)

        post_op = result["paths"]["/api/config/namespaces/{namespace}/http_loadbalancers"]["post"]
        purpose = post_op["x-f5xc-operation-metadata"]["purpose"]

        # DRY-compliant: should NOT start with CRUD verbs
        assert not purpose.lower().startswith("create")
        assert not purpose.lower().startswith("list")
        assert not purpose.lower().startswith("get")
        assert not purpose.lower().startswith("delete")

    def test_enrich_respects_existing_metadata(self, enricher):
        """Test enrichment preserves existing x-f5xc-operation-metadata fields."""
        spec = {
            "paths": {
                "/api/config/namespaces/{namespace}/http_loadbalancers": {
                    "post": {
                        "operationId": "createHttpLoadBalancer",
                        "x-f5xc-operation-metadata": {
                            "danger_level": "medium",
                            "existing_field": "preserved",
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        post_op = result["paths"]["/api/config/namespaces/{namespace}/http_loadbalancers"]["post"]
        metadata = post_op["x-f5xc-operation-metadata"]

        assert metadata["existing_field"] == "preserved"
        assert "purpose" in metadata

    def test_enrich_multiple_operations(self, enricher, sample_spec):
        """Test enrichment handles multiple operations."""
        enricher.enrich_spec(sample_spec)
        stats = enricher.get_stats()

        # Should have processed 5 operations (2 GET, 2 POST, 1 DELETE)
        assert stats["operations_processed"] == 5
        assert stats["descriptions_applied"] >= 1

    def test_enrich_short_descriptions_within_limit(self, enricher, sample_spec):
        """Test all short descriptions are ≤60 characters."""
        result = enricher.enrich_spec(sample_spec)

        for path_item in result["paths"].values():
            for method in ["get", "post", "delete"]:
                if method in path_item:
                    operation = path_item[method]
                    if "x-f5xc-operation-metadata" in operation:
                        purpose = operation["x-f5xc-operation-metadata"].get("purpose", "")
                        if purpose:
                            assert len(purpose) <= 60, f"Purpose exceeds 60 chars: {purpose}"


class TestDescriptionTiers:
    """Test description tier lengths."""

    def test_short_tier_length(self, enricher):
        """Test short tier descriptions are ≤60 characters."""
        description = enricher.get_description("http_loadbalancer", "POST", "short")
        if description:
            assert len(description) <= 60

    def test_medium_tier_length(self, enricher):
        """Test medium tier descriptions are ≤150 characters."""
        description = enricher.get_description("http_loadbalancer", "POST", "medium")
        if description:
            assert len(description) <= 150

    def test_long_tier_length(self, enricher):
        """Test long tier descriptions are ≤500 characters."""
        description = enricher.get_description("http_loadbalancer", "POST", "long")
        if description:
            assert len(description) <= 500


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
