"""Tests for NamespaceProfileEnricher."""

from __future__ import annotations

import copy
from dataclasses import asdict
from typing import Any

import pytest

from scripts.utils.namespace_profile_enricher import (
    NamespaceProfileEnricher,
    NamespaceProfileStats,
)

# -- Fixtures --


@pytest.fixture
def enricher() -> NamespaceProfileEnricher:
    return NamespaceProfileEnricher()


@pytest.fixture
def sample_spec() -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "HTTP Loadbalancer API",
            "version": "1.0.0",
        },
        "paths": {
            "/api/config/namespaces/{namespace}/http_loadbalancers": {
                "get": {"summary": "List"},
            },
        },
    }


@pytest.fixture
def system_spec() -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "AWS VPC Site API",
            "version": "1.0.0",
        },
        "paths": {
            "/api/config/namespaces/system/aws_vpc_sites": {
                "get": {"summary": "List"},
            },
        },
    }


@pytest.fixture
def shared_spec() -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "Namespace Role Binding API",
            "version": "1.0.0",
        },
        "paths": {
            "/api/config/namespaces/shared/namespace_role_bindings": {
                "get": {"summary": "List"},
            },
        },
    }


# -- Stats Tests --


class TestNamespaceProfileStats:
    def test_defaults(self) -> None:
        stats = NamespaceProfileStats()
        assert stats.specs_enriched == 0
        assert stats.system_only == 0
        assert stats.shared_only == 0
        assert stats.tenant == 0
        assert stats.shared_ref == 0
        assert stats.overridden == 0
        assert stats.errors == []

    def test_to_dict(self) -> None:
        stats = NamespaceProfileStats()
        stats.specs_enriched = 5
        d = asdict(stats)
        assert d["specs_enriched"] == 5
        assert isinstance(d["errors"], list)


# -- Config Loading Tests --


class TestConfigLoading:
    def test_loads_config(self, enricher: NamespaceProfileEnricher) -> None:
        assert enricher.config is not None
        assert "default_profile" in enricher.config
        assert "resources" in enricher.config

    def test_default_profile_structure(self, enricher: NamespaceProfileEnricher) -> None:
        default = enricher.config["default_profile"]
        assert "constraint" in default
        assert "recommendation" in default
        assert "classification" in default
        assert "allowed" in default["constraint"]
        assert "primary" in default["recommendation"]
        assert "category" in default["classification"]

    def test_system_resources_loaded(self, enricher: NamespaceProfileEnricher) -> None:
        profile = enricher.get_profile_for_resource("aws_vpc_site")
        assert profile["constraint"]["allowed"] == ["system"]

    def test_shared_resources_loaded(self, enricher: NamespaceProfileEnricher) -> None:
        profile = enricher.get_profile_for_resource("namespace_role_binding")
        assert profile["constraint"]["allowed"] == ["shared"]

    def test_unlisted_resource_gets_default(self, enricher: NamespaceProfileEnricher) -> None:
        profile = enricher.get_profile_for_resource("some_unknown_resource")
        assert profile["constraint"]["allowed"] == ["custom", "default", "shared"]
        assert profile["recommendation"]["primary"] == "custom"

    def test_shared_ref_resource_has_recommendation(
        self, enricher: NamespaceProfileEnricher
    ) -> None:
        profile = enricher.get_profile_for_resource("app_firewall")
        assert profile["recommendation"]["primary"] == "shared"
        assert profile["classification"]["multi_tenant_pattern"] == "shared-ref"


# -- Deep Merge Tests --


class TestDeepMerge:
    def test_partial_override_merges_with_default(self, enricher: NamespaceProfileEnricher) -> None:
        profile = enricher.get_profile_for_resource("http_loadbalancer")
        assert profile["constraint"]["allowed"] == ["custom", "default", "shared"]
        # enforced now reflects verification status, not a hardcoded default:
        # http_loadbalancer is not 'verified', so the constraint is advisory.
        assert profile["constraint"]["enforced"] is False
        assert profile["classification"]["category"] == "networking"
        assert profile["recommendation"]["primary"] == "custom"

    def test_full_override_replaces_default(self, enricher: NamespaceProfileEnricher) -> None:
        profile = enricher.get_profile_for_resource("aws_vpc_site")
        assert profile["constraint"]["allowed"] == ["system"]
        assert profile["recommendation"]["primary"] == "system"
        assert profile["classification"]["category"] == "infrastructure"


# -- Profile Validation Tests --


class TestProfileValidation:
    def test_primary_must_be_in_allowed(self, enricher: NamespaceProfileEnricher) -> None:
        profile = enricher.get_profile_for_resource("aws_vpc_site")
        assert profile["recommendation"]["primary"] in profile["constraint"]["allowed"]

    def test_all_resources_have_valid_categories(self, enricher: NamespaceProfileEnricher) -> None:
        valid = enricher.config["valid_categories"]
        for name in enricher.config.get("resources", {}):
            profile = enricher.get_profile_for_resource(name)
            assert profile["classification"]["category"] in valid, f"{name} has invalid category"

    def test_all_resources_have_valid_patterns(self, enricher: NamespaceProfileEnricher) -> None:
        valid = enricher.config["valid_multi_tenant_patterns"]
        for name in enricher.config.get("resources", {}):
            profile = enricher.get_profile_for_resource(name)
            assert profile["classification"]["multi_tenant_pattern"] in valid, (
                f"{name} has invalid pattern"
            )

    def test_all_resources_have_valid_namespace_types(
        self, enricher: NamespaceProfileEnricher
    ) -> None:
        valid = enricher.config["valid_namespace_types"]
        for name in enricher.config.get("resources", {}):
            profile = enricher.get_profile_for_resource(name)
            for ns_type in profile["constraint"]["allowed"]:
                assert ns_type in valid, f"{name} has invalid namespace type: {ns_type}"


# -- Resource Type Extraction Tests --


class TestVerificationGate:
    """constraint.enforced must reflect verification: only 'verified' resources are
    enforced; assumed/restricted/unverified/default-fallback are advisory."""

    def test_verified_resource_is_enforced(self, enricher: NamespaceProfileEnricher) -> None:
        # dns_load_balancer is _verification.status == 'verified' (live CRUD probe).
        profile = enricher.get_profile_for_resource("dns_load_balancer")
        assert profile["constraint"]["allowed"] == ["system"]
        assert profile["constraint"]["enforced"] is True

    def test_unverified_single_allowed_is_advisory(
        self, enricher: NamespaceProfileEnricher
    ) -> None:
        # A single-allowed resource that is not 'verified' must be advisory, so the
        # provider keeps namespace Required rather than over-restricting.
        res = enricher.config["resources"]
        target = next(
            r
            for r, p in res.items()
            if len((p.get("constraint") or {}).get("allowed", [])) == 1
            and (p.get("_verification") or {}).get("status") != "verified"
        )
        profile = enricher.get_profile_for_resource(target)
        assert profile["constraint"]["enforced"] is False, target

    def test_default_fallback_is_advisory(self, enricher: NamespaceProfileEnricher) -> None:
        profile = enricher.get_profile_for_resource("some_unknown_resource_xyz")
        assert profile["constraint"]["enforced"] is False


class TestResourceTypeExtraction:
    def test_from_title(self, enricher: NamespaceProfileEnricher) -> None:
        spec = {"info": {"title": "Alert Policy API"}}
        assert enricher._detect_resource_type(spec) == "alert_policy"

    def test_from_multi_word_title(self, enricher: NamespaceProfileEnricher) -> None:
        spec = {"info": {"title": "AWS VPC Site API"}}
        assert enricher._detect_resource_type(spec) == "aws_vpc_site"

    def test_from_paths(self, enricher: NamespaceProfileEnricher) -> None:
        spec: dict[str, Any] = {
            "info": {"title": "Unknown"},
            "paths": {"/api/config/namespaces/{namespace}/http_loadbalancers": {}},
        }
        assert enricher._detect_resource_type(spec) == "http_loadbalancer"

    def test_from_cli_domain(self, enricher: NamespaceProfileEnricher) -> None:
        spec: dict[str, Any] = {
            "info": {"title": "Unknown", "x-f5xc-cli-domain": "dns_zone"},
            "paths": {},
        }
        assert enricher._detect_resource_type(spec) == "dns_zone"

    def test_views_prefix_handled(self, enricher: NamespaceProfileEnricher) -> None:
        profile = enricher.get_profile_for_resource("views_aws_vpc_site")
        assert profile["constraint"]["allowed"] == ["system"]

    def test_schema_prefix_stripped(self, enricher: NamespaceProfileEnricher) -> None:
        # Some spec schema keys are 'schema'-prefixed (e.g.
        # schemadns_load_balancerCreateSpecType). They must map back to their
        # resource, not fall through to the domain default.
        assert (
            enricher._schema_name_to_resource_type("schemadns_load_balancerCreateSpecType")
            == "dns_load_balancer"
        )
        assert (
            enricher._match_schema_to_resource(
                "schemadns_load_balancerCreateSpecType", enricher.config.get("resources", {})
            )
            == "dns_load_balancer"
        )


# -- Spec Enrichment Tests --


class TestSpecEnrichment:
    def test_adds_profile_to_info(
        self, enricher: NamespaceProfileEnricher, sample_spec: dict
    ) -> None:
        result = enricher.enrich_spec(sample_spec)
        assert "x-f5xc-namespace-profile" in result["info"]
        profile = result["info"]["x-f5xc-namespace-profile"]
        assert "constraint" in profile
        assert "recommendation" in profile
        assert "classification" in profile

    def test_system_resource_gets_system_profile(
        self, enricher: NamespaceProfileEnricher, system_spec: dict
    ) -> None:
        result = enricher.enrich_spec(system_spec)
        profile = result["info"]["x-f5xc-namespace-profile"]
        assert profile["constraint"]["allowed"] == ["system"]
        assert profile["recommendation"]["primary"] == "system"

    def test_shared_resource_gets_shared_profile(
        self, enricher: NamespaceProfileEnricher, shared_spec: dict
    ) -> None:
        result = enricher.enrich_spec(shared_spec)
        profile = result["info"]["x-f5xc-namespace-profile"]
        assert profile["constraint"]["allowed"] == ["shared"]

    def test_schema_prefixed_createspec_gets_resource_profile(
        self, enricher: NamespaceProfileEnricher
    ) -> None:
        # A 'schema'-prefixed CreateSpecType must receive its resource's profile
        # (system for dns_load_balancer), not the domain default — even when the
        # domain-level detection falls back to the default.
        spec: dict[str, Any] = {
            "info": {"title": "DNS API"},
            "paths": {},
            "components": {
                "schemas": {
                    "schemadns_load_balancerCreateSpecType": {"type": "object"},
                    "schemadns_load_balancerGetSpecType": {"type": "object"},
                }
            },
        }
        result = enricher.enrich_spec(spec)
        schemas = result["components"]["schemas"]
        for name in ("schemadns_load_balancerCreateSpecType", "schemadns_load_balancerGetSpecType"):
            profile = schemas[name]["x-f5xc-namespace-profile"]
            assert profile["constraint"]["allowed"] == ["system"], name

    def test_idempotent(self, enricher: NamespaceProfileEnricher, sample_spec: dict) -> None:
        result1 = enricher.enrich_spec(sample_spec)
        result2 = enricher.enrich_spec(copy.deepcopy(result1))
        assert (
            result1["info"]["x-f5xc-namespace-profile"]
            == result2["info"]["x-f5xc-namespace-profile"]
        )

    def test_removes_old_namespace_scope(self, enricher: NamespaceProfileEnricher) -> None:
        spec: dict[str, Any] = {
            "info": {
                "title": "HTTP Loadbalancer API",
                "x-f5xc-namespace-scope": "any",
            },
            "paths": {"/api/config/namespaces/{namespace}/http_loadbalancers": {}},
        }
        result = enricher.enrich_spec(spec)
        assert "x-f5xc-namespace-scope" not in result["info"]
        assert "x-f5xc-namespace-profile" in result["info"]

    def test_preserves_existing_info_fields(self, enricher: NamespaceProfileEnricher) -> None:
        spec: dict[str, Any] = {
            "info": {
                "title": "HTTP Loadbalancer API",
                "description": "Existing description",
                "x-f5xc-cli-domain": "loadbalancer",
            },
            "paths": {"/api/config/namespaces/{namespace}/http_loadbalancers": {}},
        }
        result = enricher.enrich_spec(spec)
        assert result["info"]["description"] == "Existing description"
        assert result["info"]["x-f5xc-cli-domain"] == "loadbalancer"

    def test_creates_info_if_missing(self, enricher: NamespaceProfileEnricher) -> None:
        spec: dict[str, Any] = {
            "paths": {"/api/config/namespaces/{namespace}/http_loadbalancers": {}}
        }
        result = enricher.enrich_spec(spec)
        assert "info" in result
        assert "x-f5xc-namespace-profile" in result["info"]

    def test_stats_updated(
        self, enricher: NamespaceProfileEnricher, sample_spec: dict, system_spec: dict
    ) -> None:
        enricher.enrich_spec(sample_spec)
        enricher.enrich_spec(system_spec)
        stats = enricher.get_stats()
        assert stats["specs_enriched"] == 2
        assert stats["system_only"] >= 1
        assert stats["tenant"] >= 1


# -- Parametrized Known Resource Tests --


SYSTEM_RESOURCES = [
    "aws_vpc_site",
    "azure_vnet_site",
    "gcp_vpc_site",
    "fleet",
    "namespace",
    "role",
    "user",
    "global_network",
    "virtual_network",
    "k8s_cluster",
    "cloud_credentials",
    "token",
    "views_aws_vpc_site",
    "views_azure_vnet_site",
]

SHARED_ONLY_RESOURCES = ["namespace_role_binding"]

SHARED_REF_RESOURCES = [
    "app_firewall",
    "service_policy",
    "rate_limiter",
    "ip_prefix_set",
    "waf_exclusion_policy",
]

TENANT_RESOURCES = ["http_loadbalancer", "origin_pool", "tcp_loadbalancer", "healthcheck"]


@pytest.mark.parametrize("resource_name", SYSTEM_RESOURCES)
def test_system_resource_scope(enricher: NamespaceProfileEnricher, resource_name: str) -> None:
    profile = enricher.get_profile_for_resource(resource_name)
    assert profile["constraint"]["allowed"] == ["system"], f"{resource_name} should be system-only"


@pytest.mark.parametrize("resource_name", SHARED_ONLY_RESOURCES)
def test_shared_only_resource_scope(enricher: NamespaceProfileEnricher, resource_name: str) -> None:
    profile = enricher.get_profile_for_resource(resource_name)
    assert profile["constraint"]["allowed"] == ["shared"], f"{resource_name} should be shared-only"


@pytest.mark.parametrize("resource_name", SHARED_REF_RESOURCES)
def test_shared_ref_recommendation(enricher: NamespaceProfileEnricher, resource_name: str) -> None:
    profile = enricher.get_profile_for_resource(resource_name)
    assert profile["recommendation"]["primary"] == "shared", (
        f"{resource_name} should recommend shared"
    )
    assert (
        "custom" in profile["constraint"]["allowed"] or "shared" in profile["constraint"]["allowed"]
    )


@pytest.mark.parametrize("resource_name", TENANT_RESOURCES)
def test_tenant_resource_scope(enricher: NamespaceProfileEnricher, resource_name: str) -> None:
    profile = enricher.get_profile_for_resource(resource_name)
    assert "custom" in profile["constraint"]["allowed"], f"{resource_name} should allow custom"
    assert "system" not in profile["constraint"]["allowed"], (
        f"{resource_name} should not allow system"
    )


# -- Completeness Gate --


def test_all_primary_resources_have_explicit_namespace_profile() -> None:
    """Every primary resource in the spec index must have an explicit entry
    in namespace_profile.yaml. Silent default inheritance is not acceptable
    for primary resources — it leads to misclassified resources in the tree."""
    import json
    from pathlib import Path

    index_path = Path("docs/specifications/api/index.json")
    if not index_path.exists():
        pytest.skip("index.json not available")

    with index_path.open() as f:
        index = json.load(f)

    primary_names: set[str] = set()
    specs = index.get("specifications", [])
    if isinstance(specs, dict):
        specs = list(specs.values())
    for domain_info in specs:
        for resource in domain_info.get("x-f5xc-primary-resources", []):
            primary_names.add(resource["name"])

    enricher = NamespaceProfileEnricher()
    missing = sorted(name for name in primary_names if not enricher.is_resource_explicit(name))

    assert missing == [], (
        f"{len(missing)} primary resources lack explicit namespace profile entries: {missing}"
    )
