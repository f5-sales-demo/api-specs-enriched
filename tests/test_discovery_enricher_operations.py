# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=protected-access  # Tests intentionally verify deterministic internal helpers.

"""Tests for discovery enricher operation-level extensions.

Tests the new operation-level enrichment functionality added in Issue #314:
- x-f5xc-discovered-response-time (percentile format)
- x-f5xc-discovered-rate-limits
- x-f5xc-discovered-error-catalog
"""

# Tests intentionally access private methods to verify internal behavior

from typing import Any

import pytest

from scripts.utils.discovery_enricher import DiscoveryData, DiscoveryEnricher, EnrichmentStats
from scripts.utils.extension_constants import (
    X_F5XC_DISCOVERED_ERROR_CATALOG,
    X_F5XC_DISCOVERED_RATE_LIMITS,
    X_F5XC_DISCOVERED_RESPONSE_TIME,
)


@pytest.fixture
def base_config() -> dict:
    """Provide base configuration for DiscoveryEnricher."""
    return {
        "discovery_enrichment": {
            "enabled": True,
            "extensions": {"prefix": "x-discovered"},
            "performance": {
                "add_response_times": True,
                "add_percentiles": True,
                "add_sample_size": True,
                "latency_estimates_file": "config/latency_estimates.yaml",
            },
            "rate_limits": {
                "enabled": True,
                "include_confidence": True,
                "min_confidence": 0.7,
            },
            "errors": {
                "enabled": True,
                "max_errors_per_operation": 10,
                "min_frequency": 0.01,
            },
            "mutability": {
                "known_read_only": ["uid", "creation_timestamp"],
                "known_write_only": [],
            },
            "examples": {"redact_patterns": []},
        },
    }


@pytest.fixture
def enricher(base_config: dict) -> DiscoveryEnricher:
    """Create DiscoveryEnricher instance with base config."""
    return DiscoveryEnricher(base_config)


class TestEnrichmentStats:
    """Test EnrichmentStats dataclass."""

    def test_stats_include_new_fields(self):
        """Verify EnrichmentStats includes operation-level tracking fields."""
        stats = EnrichmentStats()
        assert hasattr(stats, "response_times_added")
        assert hasattr(stats, "rate_limits_added")
        assert hasattr(stats, "error_catalogs_added")

    def test_stats_to_dict_includes_new_fields(self):
        """Verify to_dict() includes new tracking fields."""
        stats = EnrichmentStats(
            response_times_added=5,
            rate_limits_added=3,
            error_catalogs_added=2,
        )
        result = stats.to_dict()
        assert result["response_times_added"] == 5
        assert result["rate_limits_added"] == 3
        assert result["error_catalogs_added"] == 2


class TestResponseTimeEnrichment:
    """Test x-f5xc-discovered-response-time enrichment."""

    def test_response_time_with_discovery_data(self, enricher: DiscoveryEnricher):
        """Test response time enrichment when discovery data is available."""
        operation: dict[str, Any] = {}
        discovered_op = {"x-response-time-ms": 150.5}
        enricher.discovery_data = DiscoveryData(discovered_at="2025-01-01T00:00:00Z")

        enricher._enrich_operation_with_response_time(
            operation,
            discovered_op,
            "GET",
            "/api/test",
        )

        assert X_F5XC_DISCOVERED_RESPONSE_TIME in operation
        rt_data = operation[X_F5XC_DISCOVERED_RESPONSE_TIME]
        assert rt_data["p50_ms"] == 150.5
        assert rt_data["source"] == "discovery"
        assert rt_data["sample_count"] == 1
        assert "last_measured" in rt_data

    def test_response_time_fallback_to_estimates(self, enricher: DiscoveryEnricher):
        """Test response time falls back to latency estimates when no discovery data."""
        operation: dict[str, Any] = {}
        enricher._enrich_operation_with_response_time(
            operation,
            None,
            "GET",
            "/api/test",
        )

        assert X_F5XC_DISCOVERED_RESPONSE_TIME in operation
        rt_data = operation[X_F5XC_DISCOVERED_RESPONSE_TIME]
        assert rt_data["source"] == "estimate"
        assert rt_data["sample_count"] == 0
        assert "p50_ms" in rt_data
        assert "p95_ms" in rt_data
        assert "p99_ms" in rt_data

    def test_response_time_method_based_estimates(self, enricher: DiscoveryEnricher):
        """Test latency estimates vary by HTTP method."""
        get_op: dict[str, Any] = {}
        post_op: dict[str, Any] = {}

        enricher._enrich_operation_with_response_time(get_op, None, "GET", "/api/test")
        enricher._enrich_operation_with_response_time(
            post_op,
            None,
            "POST",
            "/api/test",
        )

        # POST operations typically have higher latency estimates than GET
        get_p50 = get_op[X_F5XC_DISCOVERED_RESPONSE_TIME]["p50_ms"]
        post_p50 = post_op[X_F5XC_DISCOVERED_RESPONSE_TIME]["p50_ms"]
        assert post_p50 > get_p50

    def test_response_time_disabled(self, base_config: dict):
        """Test response time enrichment when disabled."""
        base_config["discovery_enrichment"]["performance"]["add_percentiles"] = False
        enricher = DiscoveryEnricher(base_config)
        operation: dict[str, Any] = {}

        enricher._enrich_operation_with_response_time(
            operation,
            {"x-response-time-ms": 100},
            "GET",
            "/api/test",
        )

        assert X_F5XC_DISCOVERED_RESPONSE_TIME not in operation

    def test_response_time_stats_tracking(self, enricher: DiscoveryEnricher):
        """Test that response time enrichment updates stats."""
        operation: dict[str, Any] = {}
        enricher._enrich_operation_with_response_time(
            operation,
            {"x-response-time-ms": 100},
            "GET",
            "/api/test",
        )

        assert enricher.stats.response_times_added == 1


class TestRateLimitEnrichment:
    """Test x-f5xc-discovered-rate-limits enrichment."""

    def test_rate_limits_from_discovery_data(self, enricher: DiscoveryEnricher):
        """Test rate limit enrichment with discovery data."""
        operation: dict[str, Any] = {}
        discovered_op = {
            "x-rate-limit": {
                "requests_per_minute": 1000,
                "burst_limit": 100,
                "retry_after_header": True,
                "confidence": 0.9,
            },
        }

        enricher._enrich_operation_with_rate_limits(operation, discovered_op)

        assert X_F5XC_DISCOVERED_RATE_LIMITS in operation
        rl_data = operation[X_F5XC_DISCOVERED_RATE_LIMITS]
        assert rl_data["requests_per_minute"] == 1000
        assert rl_data["burst_limit"] == 100
        assert rl_data["retry_after_header"] is True
        assert rl_data["confidence"] == 0.9

    def test_rate_limits_low_confidence_filtered(self, enricher: DiscoveryEnricher):
        """Test rate limits below confidence threshold are not added."""
        operation: dict[str, Any] = {}
        discovered_op = {
            "x-rate-limit": {
                "requests_per_minute": 1000,
                "confidence": 0.5,  # Below 0.7 threshold
            },
        }

        enricher._enrich_operation_with_rate_limits(operation, discovered_op)

        assert X_F5XC_DISCOVERED_RATE_LIMITS not in operation

    def test_rate_limits_from_response_header(self, enricher: DiscoveryEnricher):
        """Test rate limits extracted from response headers."""
        operation: dict[str, Any] = {}
        discovered_op = {"x-ratelimit-limit": 500}

        enricher._enrich_operation_with_rate_limits(operation, discovered_op)

        assert X_F5XC_DISCOVERED_RATE_LIMITS in operation
        rl_data = operation[X_F5XC_DISCOVERED_RATE_LIMITS]
        assert rl_data["requests_per_minute"] == 500
        assert rl_data["source"] == "response_header"

    def test_rate_limits_no_discovery_data(self, enricher: DiscoveryEnricher):
        """Test rate limits not added when no discovery data."""
        operation: dict[str, Any] = {}
        enricher._enrich_operation_with_rate_limits(operation, None)

        assert X_F5XC_DISCOVERED_RATE_LIMITS not in operation

    def test_rate_limits_disabled(self, base_config: dict):
        """Test rate limit enrichment when disabled."""
        base_config["discovery_enrichment"]["rate_limits"]["enabled"] = False
        enricher = DiscoveryEnricher(base_config)
        operation: dict[str, Any] = {}

        enricher._enrich_operation_with_rate_limits(
            operation,
            {"x-rate-limit": {"requests_per_minute": 1000, "confidence": 0.9}},
        )

        assert X_F5XC_DISCOVERED_RATE_LIMITS not in operation

    def test_rate_limits_stats_tracking(self, enricher: DiscoveryEnricher):
        """Test that rate limit enrichment updates stats."""
        operation: dict[str, Any] = {}
        enricher._enrich_operation_with_rate_limits(
            operation,
            {"x-rate-limit": {"requests_per_minute": 1000, "confidence": 0.9}},
        )

        assert enricher.stats.rate_limits_added == 1


class TestErrorCatalogEnrichment:
    """Test x-f5xc-discovered-error-catalog enrichment."""

    def test_error_catalog_from_discovered_errors(self, enricher: DiscoveryEnricher):
        """Test error catalog populated from discovered errors."""
        operation: dict[str, Any] = {}
        discovered_op = {
            "x-discovered-errors": [
                {
                    "status_code": 400,
                    "error_type": "validation_error",
                    "message_pattern": "Invalid input",
                    "frequency": 0.1,
                },
                {
                    "status_code": 404,
                    "error_type": "not_found_error",
                    "message_pattern": "Resource not found",
                    "resolution": "Check resource ID",
                    "frequency": 0.05,
                },
            ],
        }

        enricher._enrich_operation_with_error_catalog(operation, discovered_op)

        assert X_F5XC_DISCOVERED_ERROR_CATALOG in operation
        errors = operation[X_F5XC_DISCOVERED_ERROR_CATALOG]
        assert len(errors) == 2
        assert errors[0]["status_code"] == 400
        assert errors[1]["resolution"] == "Check resource ID"

    def test_error_catalog_from_responses(self, enricher: DiscoveryEnricher):
        """Test error catalog extracted from response definitions."""
        operation: dict[str, Any] = {}
        discovered_op = {
            "responses": {
                "200": {"description": "Success"},
                "401": {"description": "Unauthorized - invalid token"},
                "404": {"description": "Not found"},
            },
        }

        enricher._enrich_operation_with_error_catalog(operation, discovered_op)

        assert X_F5XC_DISCOVERED_ERROR_CATALOG in operation
        errors = operation[X_F5XC_DISCOVERED_ERROR_CATALOG]
        assert len(errors) == 2  # Only 4xx/5xx responses
        status_codes = [e["status_code"] for e in errors]
        assert 401 in status_codes
        assert 404 in status_codes

    def test_error_catalog_frequency_filtering(self, enricher: DiscoveryEnricher):
        """Test errors below min_frequency are filtered."""
        operation: dict[str, Any] = {}
        discovered_op = {
            "x-discovered-errors": [
                {"status_code": 400, "frequency": 0.1},
                {"status_code": 500, "frequency": 0.001},  # Below 0.01 threshold
            ],
        }

        enricher._enrich_operation_with_error_catalog(operation, discovered_op)

        errors = operation[X_F5XC_DISCOVERED_ERROR_CATALOG]
        assert len(errors) == 1
        assert errors[0]["status_code"] == 400

    def test_error_catalog_max_errors_limit(self, base_config: dict):
        """Test error catalog respects max_errors_per_operation."""
        base_config["discovery_enrichment"]["errors"]["max_errors_per_operation"] = 2
        enricher = DiscoveryEnricher(base_config)
        operation: dict[str, Any] = {}
        discovered_op = {
            "x-discovered-errors": [
                {"status_code": 400, "frequency": 0.1},
                {"status_code": 401, "frequency": 0.1},
                {"status_code": 404, "frequency": 0.1},
                {"status_code": 500, "frequency": 0.1},
            ],
        }

        enricher._enrich_operation_with_error_catalog(operation, discovered_op)

        errors = operation[X_F5XC_DISCOVERED_ERROR_CATALOG]
        assert len(errors) <= 2

    def test_error_catalog_no_discovery_data(self, enricher: DiscoveryEnricher):
        """Test error catalog not added when no discovery data."""
        operation: dict[str, Any] = {}
        enricher._enrich_operation_with_error_catalog(operation, None)

        assert X_F5XC_DISCOVERED_ERROR_CATALOG not in operation

    def test_error_catalog_disabled(self, base_config: dict):
        """Test error catalog enrichment when disabled."""
        base_config["discovery_enrichment"]["errors"]["enabled"] = False
        enricher = DiscoveryEnricher(base_config)
        operation: dict[str, Any] = {}

        enricher._enrich_operation_with_error_catalog(
            operation,
            {"x-discovered-errors": [{"status_code": 400, "frequency": 0.1}]},
        )

        assert X_F5XC_DISCOVERED_ERROR_CATALOG not in operation

    def test_error_catalog_stats_tracking(self, enricher: DiscoveryEnricher):
        """Test that error catalog enrichment updates stats."""
        operation: dict[str, Any] = {}
        enricher._enrich_operation_with_error_catalog(
            operation,
            {"x-discovered-errors": [{"status_code": 400, "frequency": 0.1}]},
        )

        assert enricher.stats.error_catalogs_added == 1


class TestErrorTypeClassification:
    """Test _classify_error_type method."""

    def test_classify_authentication_error(self, enricher: DiscoveryEnricher):
        """Test authentication error classification."""
        assert enricher._classify_error_type(401, "Unauthorized") == "authentication_error"
        assert enricher._classify_error_type(401, "Invalid auth token") == "authentication_error"

    def test_classify_authorization_error(self, enricher: DiscoveryEnricher):
        """Test authorization error classification."""
        assert enricher._classify_error_type(403, "Forbidden") == "authorization_error"
        assert enricher._classify_error_type(403, "Permission denied") == "authorization_error"

    def test_classify_not_found_error(self, enricher: DiscoveryEnricher):
        """Test not found error classification."""
        assert enricher._classify_error_type(404, "Resource not found") == "not_found_error"

    def test_classify_validation_error(self, enricher: DiscoveryEnricher):
        """Test validation error classification."""
        assert enricher._classify_error_type(400, "Invalid input") == "validation_error"
        assert enricher._classify_error_type(422, "Validation failed") == "validation_error"

    def test_classify_rate_limit_error(self, enricher: DiscoveryEnricher):
        """Test rate limit error classification."""
        assert enricher._classify_error_type(429, "Rate limit exceeded") == "rate_limit_error"
        assert enricher._classify_error_type(429, "Too many requests") == "rate_limit_error"

    def test_classify_server_error(self, enricher: DiscoveryEnricher):
        """Test server error classification."""
        assert enricher._classify_error_type(500, "Internal server error") == "server_error"

    def test_classify_by_status_code_fallback(self, enricher: DiscoveryEnricher):
        """Test classification falls back to status code when description unclear."""
        assert enricher._classify_error_type(400, "Something went wrong") == "bad_request"
        assert enricher._classify_error_type(502, "Gateway issue") == "gateway_error"


class TestLatencyEstimates:
    """Test latency estimate loading and application."""

    def test_load_latency_estimates(self, enricher: DiscoveryEnricher):
        """Test latency estimates are loaded."""
        assert enricher.latency_estimates is not None
        assert "defaults" in enricher.latency_estimates

    def test_get_latency_level_get_operation(self, enricher: DiscoveryEnricher):
        """Test latency level detection for GET operations."""
        level = enricher._get_latency_level("GET", "/api/resources")
        assert level == "get_operations"

    def test_get_latency_level_list_operation(self, enricher: DiscoveryEnricher):
        """Test latency level detection for list operations."""
        level = enricher._get_latency_level("GET", "/api/resources/list")
        assert level == "list_operations"

    def test_get_latency_level_create_operation(self, enricher: DiscoveryEnricher):
        """Test latency level detection for POST operations."""
        level = enricher._get_latency_level("POST", "/api/resources")
        assert level == "create_operations"

    def test_get_latency_level_update_operation(self, enricher: DiscoveryEnricher):
        """Test latency level detection for PUT/PATCH operations."""
        assert enricher._get_latency_level("PUT", "/api/resources/1") == "update_operations"
        assert enricher._get_latency_level("PATCH", "/api/resources/1") == "update_operations"

    def test_get_latency_level_delete_operation(self, enricher: DiscoveryEnricher):
        """Test latency level detection for DELETE operations."""
        level = enricher._get_latency_level("DELETE", "/api/resources/1")
        assert level == "delete_operations"

    def test_get_default_latency_estimates(self, enricher: DiscoveryEnricher):
        """Test default latency estimates include percentiles."""
        estimates = enricher._get_default_latency_estimates("GET", "/api/test")
        assert "p50" in estimates
        assert "p95" in estimates
        assert "p99" in estimates


class TestPathsEnrichment:
    """Test _enrich_paths integration with operation-level enrichment."""

    def test_enrich_paths_calls_all_enrichment_methods(
        self,
        enricher: DiscoveryEnricher,
    ):
        """Test _enrich_paths calls all operation-level enrichment methods."""
        spec = {
            "paths": {
                "/api/test": {
                    "get": {
                        "operationId": "getTest",
                        "responses": {"200": {"description": "Success"}},
                    },
                },
            },
        }
        discoveries = DiscoveryData(
            paths={
                "/api/test": {
                    "get": {
                        "x-response-time-ms": 150,
                        "x-rate-limit": {
                            "requests_per_minute": 1000,
                            "confidence": 0.9,
                        },
                        "responses": {
                            "200": {"description": "Success"},
                            "404": {"description": "Not found"},
                        },
                    },
                },
            },
        )

        enricher._enrich_paths(spec, discoveries)

        operation = spec["paths"]["/api/test"]["get"]
        # Check all three extension types are present
        assert X_F5XC_DISCOVERED_RESPONSE_TIME in operation
        assert X_F5XC_DISCOVERED_RATE_LIMITS in operation
        assert X_F5XC_DISCOVERED_ERROR_CATALOG in operation

    def test_enrich_paths_with_no_discovered_operation(
        self,
        enricher: DiscoveryEnricher,
    ):
        """Test _enrich_paths handles missing discovered operations gracefully."""
        spec: dict[str, Any] = {
            "paths": {
                "/api/unknown": {
                    "get": {
                        "operationId": "getUnknown",
                        "responses": {"200": {"description": "Success"}},
                    },
                },
            },
        }
        discoveries = DiscoveryData(paths={})

        enricher._enrich_paths(spec, discoveries)

        operation = spec["paths"]["/api/unknown"]["get"]
        # Response time should still be added via fallback estimates
        assert X_F5XC_DISCOVERED_RESPONSE_TIME in operation
        rt_data = operation[X_F5XC_DISCOVERED_RESPONSE_TIME]
        assert rt_data["source"] == "estimate"
