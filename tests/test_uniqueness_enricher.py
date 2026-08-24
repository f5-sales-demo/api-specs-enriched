#!/usr/bin/env python3
# pylint: disable=protected-access  # Tests intentionally verify deterministic internal helpers.
"""
Unit tests for UniquenessEnricher

Tests cover:
- Namespace scope mapping (system → platform, shared → namespace, any → namespace)
- Resource overrides (tenant, certificate, namespace)
- Schema name conversion (PascalCase → snake_case)
- Idempotency (re-enriching preserves existing)
- Statistics tracking
- Edge cases

Target coverage: 80%+
"""

# Import the uniqueness enricher
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.utils.uniqueness_enricher import UniquenessEnricher

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def enricher():
    """Create UniquenessEnricher instance"""
    config_path = Path(__file__).parent.parent / "config" / "constraint_patterns.yaml"
    return UniquenessEnricher(config_path=config_path)


@pytest.fixture
def sample_spec():
    """Create sample OpenAPI spec for testing"""
    return {
        "info": {
            "title": "Test API",
            "x-f5xc-namespace-profile": {
                "constraint": {"allowed": ["system", "shared", "custom"]},
                "recommendation": {"primary": "custom", "rationale": "User namespace resource"},
                "classification": {"multi_tenant_pattern": "tenant-isolated"},
            },
        },
        "components": {
            "schemas": {
                "HTTPLoadBalancer": {"type": "object"},
                "Tenant": {"type": "object"},
                "Certificate": {"type": "object"},
                "Namespace": {"type": "object"},
            },
        },
    }


# =============================================================================
# Namespace Scope Mapping Tests
# =============================================================================


class TestNamespaceScopeMapping:
    """Test namespace scope to uniqueness mapping"""

    def test_namespace_scope_any_maps_to_namespace(self, enricher, sample_spec):
        """Test namespace-scoped uniqueness (any → namespace)"""
        result = enricher.enrich_spec(sample_spec)
        lb_uniqueness = result["components"]["schemas"]["HTTPLoadBalancer"]["x-f5xc-uniqueness"]

        assert lb_uniqueness["scope"] == "namespace"
        assert lb_uniqueness["within"] == ["namespace"]
        assert lb_uniqueness["caseSensitive"] is True
        assert lb_uniqueness["metadata"]["source"] == "inferred"
        assert lb_uniqueness["metadata"]["confidence"] == 0.95

    def test_namespace_scope_system_maps_to_platform(self, enricher):
        """Test platform-scoped uniqueness (system-only profile → platform)"""
        spec = {
            "info": {
                "x-f5xc-namespace-profile": {
                    "constraint": {"allowed": ["system"]},
                    "recommendation": {"primary": "system", "rationale": "System resource"},
                    "classification": {"multi_tenant_pattern": "system-only"},
                },
            },
            "components": {"schemas": {"APICredential": {"type": "object"}}},
        }
        result = enricher.enrich_spec(spec)
        uniqueness = result["components"]["schemas"]["APICredential"]["x-f5xc-uniqueness"]

        assert uniqueness["scope"] == "platform"
        assert uniqueness["within"] == []
        assert uniqueness["metadata"]["confidence"] == 0.99

    def test_namespace_scope_shared_maps_to_namespace(self, enricher):
        """Test shared namespace scope (shared-only profile → namespace)"""
        spec = {
            "info": {
                "x-f5xc-namespace-profile": {
                    "constraint": {"allowed": ["shared"]},
                    "recommendation": {"primary": "shared", "rationale": "Shared resource"},
                    "classification": {"multi_tenant_pattern": "shared-ref"},
                },
            },
            "components": {"schemas": {"SharedResource": {"type": "object"}}},
        }
        result = enricher.enrich_spec(spec)
        uniqueness = result["components"]["schemas"]["SharedResource"]["x-f5xc-uniqueness"]

        assert uniqueness["scope"] == "namespace"
        assert uniqueness["within"] == ["namespace"]
        assert uniqueness["metadata"]["confidence"] == 0.99


# =============================================================================
# Resource Override Tests
# =============================================================================


class TestResourceOverrides:
    """Test resource-specific uniqueness overrides"""

    def test_resource_override_tenant(self, enricher, sample_spec):
        """Test resource override for tenant (platform uniqueness)"""
        result = enricher.enrich_spec(sample_spec)
        tenant_uniqueness = result["components"]["schemas"]["Tenant"]["x-f5xc-uniqueness"]

        assert tenant_uniqueness["scope"] == "platform"
        assert tenant_uniqueness["within"] == []
        assert tenant_uniqueness["metadata"]["confidence"] == 0.99
        # Verify override was applied (counter incremented)
        assert enricher.stats.overrides_applied > 0

    def test_resource_override_certificate(self, enricher, sample_spec):
        """Test resource override for certificate (tenant uniqueness)"""
        result = enricher.enrich_spec(sample_spec)
        cert_uniqueness = result["components"]["schemas"]["Certificate"]["x-f5xc-uniqueness"]

        assert cert_uniqueness["scope"] == "tenant"
        assert cert_uniqueness["within"] == ["tenant"]
        assert cert_uniqueness["metadata"]["confidence"] == 0.95

    def test_resource_override_namespace(self, enricher, sample_spec):
        """Test resource override for namespace (tenant uniqueness)"""
        result = enricher.enrich_spec(sample_spec)
        ns_uniqueness = result["components"]["schemas"]["Namespace"]["x-f5xc-uniqueness"]

        assert ns_uniqueness["scope"] == "tenant"
        assert ns_uniqueness["within"] == ["tenant"]
        assert ns_uniqueness["metadata"]["confidence"] == 0.95


# =============================================================================
# Schema Name Conversion Tests
# =============================================================================


class TestSchemaNameConversion:
    """Test PascalCase to snake_case conversion"""

    def test_conversion_http_load_balancer(self, enricher):
        """Test HTTPLoadBalancer → http_load_balancer"""
        result = enricher._schema_name_to_resource_type("HTTPLoadBalancer")
        # The converter splits on each Upper→lower transition:
        # H-T-T-P → 'http', LoadBalancer → 'load_balancer'.
        assert result == "http_load_balancer"

    def test_conversion_certificate(self, enricher):
        """Test Certificate → certificate"""
        result = enricher._schema_name_to_resource_type("Certificate")
        assert result == "certificate"

    def test_conversion_aws_vpc_site(self, enricher):
        """Test AWSVPCSite → awsvpc_site"""
        result = enricher._schema_name_to_resource_type("AWSVPCSite")
        # Consecutive uppercase runs are kept together: AWSVPC → 'awsvpc'
        # (the converter only inserts underscores at Upper→lower
        # transitions, not inside all-upper spans).
        assert result == "awsvpc_site"

    def test_conversion_origin_pool(self, enricher):
        """Test OriginPool → origin_pool"""
        result = enricher._schema_name_to_resource_type("OriginPool")
        assert result == "origin_pool"

    def test_conversion_app_firewall(self, enricher):
        """Test AppFirewall → app_firewall"""
        result = enricher._schema_name_to_resource_type("AppFirewall")
        assert result == "app_firewall"


# =============================================================================
# Idempotency Tests
# =============================================================================


class TestIdempotency:
    """Test that re-enriching doesn't modify existing metadata"""

    def test_idempotency_preserves_original(self, enricher, sample_spec):
        """Test that re-enriching preserves original timestamp"""
        # First enrichment
        result1 = enricher.enrich_spec(sample_spec)
        first_timestamp = result1["components"]["schemas"]["HTTPLoadBalancer"]["x-f5xc-uniqueness"][
            "metadata"
        ]["validatedAt"]

        # Reset stats and re-enrich
        enricher.reset_stats()
        result2 = enricher.enrich_spec(result1)

        # Should preserve original timestamp
        second_timestamp = result2["components"]["schemas"]["HTTPLoadBalancer"][
            "x-f5xc-uniqueness"
        ]["metadata"]["validatedAt"]
        assert first_timestamp == second_timestamp
        assert enricher.stats.already_had_uniqueness == 4  # All 4 schemas

    def test_idempotency_skips_enrichment(self, enricher):
        """Test that existing uniqueness metadata is not overwritten"""
        spec = {
            "info": {"title": "Test"},
            "components": {
                "schemas": {
                    "Resource": {
                        "type": "object",
                        "x-f5xc-uniqueness": {
                            "scope": "custom",  # Custom value
                            "within": ["custom_field"],
                            "metadata": {"source": "existing"},
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        uniqueness = result["components"]["schemas"]["Resource"]["x-f5xc-uniqueness"]

        # Original values preserved
        assert uniqueness["scope"] == "custom"
        assert uniqueness["within"] == ["custom_field"]
        assert uniqueness["metadata"]["source"] == "existing"


# =============================================================================
# Statistics Tests
# =============================================================================


class TestStatistics:
    """Test statistics tracking accuracy"""

    def test_stats_tracking(self, enricher, sample_spec):
        """Test statistics tracking accuracy"""
        enricher.enrich_spec(sample_spec)
        stats = enricher.get_stats()

        assert stats["schemas_enriched"] == 4
        assert stats["namespace_scoped"] == 1  # HTTPLoadBalancer
        assert stats["platform_scoped"] == 1  # Tenant
        assert stats["tenant_scoped"] == 2  # Certificate + Namespace
        assert stats["overrides_applied"] == 3  # Tenant + Certificate + Namespace
        assert stats["error_count"] == 0

    def test_stats_to_dict_conversion(self, enricher):
        """Test stats dataclass to_dict conversion"""
        stats_dict = enricher.stats.to_dict()

        assert isinstance(stats_dict, dict)
        assert "schemas_enriched" in stats_dict
        assert "platform_scoped" in stats_dict
        assert "error_count" in stats_dict
        assert "errors" in stats_dict

    def test_stats_reset(self, enricher, sample_spec):
        """Test stats can be reset"""
        enricher.enrich_spec(sample_spec)
        assert enricher.stats.schemas_enriched == 4

        enricher.reset_stats()
        assert enricher.stats.schemas_enriched == 0
        assert enricher.stats.platform_scoped == 0


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================


class TestEdgeCases:
    """Test edge cases and error handling"""

    def test_missing_namespace_profile_defaults_to_any(self, enricher):
        """Test behavior when namespace profile is missing (defaults to 'any')"""
        spec = {
            "info": {"title": "Test"},  # No x-f5xc-namespace-profile
            "components": {"schemas": {"Resource": {"type": "object"}}},
        }
        result = enricher.enrich_spec(spec)
        uniqueness = result["components"]["schemas"]["Resource"]["x-f5xc-uniqueness"]

        # Should default to 'any' → namespace scope
        assert uniqueness["scope"] == "namespace"
        assert uniqueness["within"] == ["namespace"]

    def test_empty_spec(self, enricher):
        """Test enriching an empty spec"""
        spec = {}
        result = enricher.enrich_spec(spec)
        assert result == {}
        assert enricher.stats.schemas_enriched == 0

    def test_spec_without_schemas(self, enricher):
        """Test spec without components.schemas"""
        spec = {
            "info": {
                "title": "Test",
                "x-f5xc-namespace-profile": {
                    "constraint": {"allowed": ["system", "shared", "custom"]},
                    "recommendation": {"primary": "custom", "rationale": "User namespace resource"},
                    "classification": {"multi_tenant_pattern": "tenant-isolated"},
                },
            },
        }
        enricher.enrich_spec(spec)
        assert enricher.stats.schemas_enriched == 0

    def test_schema_without_components(self, enricher):
        """Test spec without components section"""
        spec = {
            "info": {
                "title": "Test",
                "x-f5xc-namespace-profile": {
                    "constraint": {"allowed": ["system", "shared", "custom"]},
                    "recommendation": {"primary": "custom", "rationale": "User namespace resource"},
                    "classification": {"multi_tenant_pattern": "tenant-isolated"},
                },
            },
        }
        enricher.enrich_spec(spec)
        assert enricher.stats.schemas_enriched == 0


# =============================================================================
# Default Fields Tests
# =============================================================================


class TestDefaultFields:
    """Test that default fields are applied to all resources"""

    def test_default_fields_application(self, enricher, sample_spec):
        """Test that default fields are applied to all resources"""
        result = enricher.enrich_spec(sample_spec)

        for schema in result["components"]["schemas"].values():
            uniqueness = schema["x-f5xc-uniqueness"]
            assert "metadata.name" in uniqueness["fields"]
            assert "name" in uniqueness["fields"]

    def test_case_sensitivity_applied(self, enricher, sample_spec):
        """Test that case sensitivity is applied to all resources"""
        result = enricher.enrich_spec(sample_spec)

        for schema in result["components"]["schemas"].values():
            uniqueness = schema["x-f5xc-uniqueness"]
            assert uniqueness["caseSensitive"] is True


# =============================================================================
# Constraint Explanation Tests
# =============================================================================


class TestConstraintExplanations:
    """Test that constraint explanations are added"""

    def test_namespace_scope_explanation(self, enricher, sample_spec):
        """Test namespace scope has explanation"""
        result = enricher.enrich_spec(sample_spec)
        lb_uniqueness = result["components"]["schemas"]["HTTPLoadBalancer"]["x-f5xc-uniqueness"]

        assert "constraintExplanation" in lb_uniqueness
        assert len(lb_uniqueness["constraintExplanation"]) > 0
        assert "namespace" in lb_uniqueness["constraintExplanation"].lower()

    def test_platform_scope_explanation(self, enricher, sample_spec):
        """Test platform scope has explanation"""
        result = enricher.enrich_spec(sample_spec)
        tenant_uniqueness = result["components"]["schemas"]["Tenant"]["x-f5xc-uniqueness"]

        assert "constraintExplanation" in tenant_uniqueness
        assert len(tenant_uniqueness["constraintExplanation"]) > 0
        # The platform-scope explanation describes the uniqueness as
        # global across F5 XC tenants; the exact word "platform" is not
        # required. Assert the globally-unique phrasing instead.
        assert "globally unique" in tenant_uniqueness["constraintExplanation"].lower()

    def test_tenant_scope_explanation(self, enricher, sample_spec):
        """Test tenant scope has explanation"""
        result = enricher.enrich_spec(sample_spec)
        cert_uniqueness = result["components"]["schemas"]["Certificate"]["x-f5xc-uniqueness"]

        assert "constraintExplanation" in cert_uniqueness
        assert len(cert_uniqueness["constraintExplanation"]) > 0
        assert "tenant" in cert_uniqueness["constraintExplanation"].lower()


# =============================================================================
# Metadata Tests
# =============================================================================


class TestMetadata:
    """Test metadata structure and content"""

    def test_metadata_structure(self, enricher, sample_spec):
        """Test metadata has required fields"""
        result = enricher.enrich_spec(sample_spec)
        lb_uniqueness = result["components"]["schemas"]["HTTPLoadBalancer"]["x-f5xc-uniqueness"]

        assert "metadata" in lb_uniqueness
        metadata = lb_uniqueness["metadata"]
        assert "source" in metadata
        assert "confidence" in metadata
        assert "validatedAt" in metadata

    def test_metadata_source_inferred(self, enricher, sample_spec):
        """Test metadata source is 'inferred' for pattern-based"""
        result = enricher.enrich_spec(sample_spec)
        lb_uniqueness = result["components"]["schemas"]["HTTPLoadBalancer"]["x-f5xc-uniqueness"]

        assert lb_uniqueness["metadata"]["source"] == "inferred"

    def test_metadata_timestamp_iso8601(self, enricher, sample_spec):
        """Test metadata timestamp is in ISO 8601 format"""
        result = enricher.enrich_spec(sample_spec)
        lb_uniqueness = result["components"]["schemas"]["HTTPLoadBalancer"]["x-f5xc-uniqueness"]

        timestamp = lb_uniqueness["metadata"]["validatedAt"]
        # Basic ISO 8601 format check
        assert "T" in timestamp
        assert timestamp.endswith("Z") or "+" in timestamp or "-" in timestamp[-6:]


if __name__ == "__main__":
    pytest.main(
        [__file__, "-v", "--cov=scripts.utils.uniqueness_enricher", "--cov-report=term-missing"],
    )
