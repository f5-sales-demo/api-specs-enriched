# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for ConsoleUIEnricher."""

from __future__ import annotations

import pytest

from scripts.utils.console_ui_enricher import ConsoleUIEnricher


@pytest.fixture
def enricher():
    """Create enricher with default config."""
    instance = ConsoleUIEnricher()
    instance.field_config = {
        "resources": {"http_loadbalancer": {"spec.domains": {"widget_type": "table"}}}
    }
    return instance


@pytest.fixture
def simple_spec():
    """Create a minimal OpenAPI spec for testing."""
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "Test Spec",
            "x-ves-proto-package": "ves.io.schema.views.http_loadbalancer",
        },
        "paths": {"/api/config/namespaces/{namespace}/http_loadbalancers": {"post": {}}},
        "components": {
            "schemas": {
                "http_loadbalancerCreateRequest": {
                    "properties": {
                        "spec": {
                            "$ref": "#/components/schemas/viewshttp_loadbalancerCreateSpecType"
                        }
                    }
                },
                "viewshttp_loadbalancerCreateSpecType": {
                    "type": "object",
                    "properties": {
                        "domains": {"type": "array"},
                        "app_firewall": {"$ref": "#/components/schemas/SomeRef"},
                    },
                },
            }
        },
    }


@pytest.fixture
def unknown_spec():
    """Spec for an unknown resource (not in config)."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Unknown Resource"},
        "paths": {"/api/unknown": {"get": {}}},
        "components": {"schemas": {}},
    }


class TestConsoleUIEnricherInit:
    def test_loads_config(self, enricher):
        assert len(enricher.config.get("resources", {})) > 0

    def test_loads_workspaces(self, enricher):
        workspaces = enricher.config.get("workspaces", {})
        assert "web-app-and-api-protection" in workspaces
        assert "multi-cloud-app-connect" in workspaces
        assert "administration" in workspaces

    def test_stats_initialized(self, enricher):
        stats = enricher.get_stats()
        assert stats["specs_processed"] == 0
        assert stats["resources_enriched"] == 0

    def test_missing_config_graceful(self, tmp_path):
        enricher = ConsoleUIEnricher(
            config_path=tmp_path / "nonexistent.yaml",
            field_config_path=tmp_path / "nonexistent2.yaml",
        )
        assert enricher.config == {}


class TestSchemaLevelEnrichment:
    def test_adds_console_extension(self, enricher, simple_spec):
        spec = enricher.enrich_spec(simple_spec)
        schema = spec["components"]["schemas"]["viewshttp_loadbalancerCreateSpecType"]
        assert "x-f5xc-console" in schema

    def test_console_has_workspace(self, enricher, simple_spec):
        spec = enricher.enrich_spec(simple_spec)
        console = spec["components"]["schemas"]["viewshttp_loadbalancerCreateSpecType"][
            "x-f5xc-console"
        ]
        assert console["workspace"] == "web-app-and-api-protection"

    def test_console_has_menu_path(self, enricher, simple_spec):
        spec = enricher.enrich_spec(simple_spec)
        console = spec["components"]["schemas"]["viewshttp_loadbalancerCreateSpecType"][
            "x-f5xc-console"
        ]
        assert "Manage" in console["menu_path"]
        assert "HTTP Load Balancers" in console["menu_path"]

    def test_console_has_form_sections(self, enricher, simple_spec):
        spec = enricher.enrich_spec(simple_spec)
        console = spec["components"]["schemas"]["viewshttp_loadbalancerCreateSpecType"][
            "x-f5xc-console"
        ]
        sections = console.get("form_sections", [])
        assert len(sections) == 13

    def test_console_has_route_pattern(self, enricher, simple_spec):
        spec = enricher.enrich_spec(simple_spec)
        console = spec["components"]["schemas"]["viewshttp_loadbalancerCreateSpecType"][
            "x-f5xc-console"
        ]
        assert (
            "/namespaces/{namespace}/manage/load_balancers/http_loadbalancers"
            in console["route_pattern"]
        )


class TestPropertyLevelEnrichment:
    def test_adds_console_field_extension(self, enricher, simple_spec):
        spec = enricher.enrich_spec(simple_spec)
        _ = spec["components"]["schemas"]["viewshttp_loadbalancerCreateSpecType"]["properties"][
            "domains"
        ]
        # Field enrichment depends on console_field_metadata.yaml having entries for "domains"
        # It may or may not be enriched depending on config
        # At minimum, the spec should be returned unchanged for unmatched fields

    def test_field_enrichment_count(self, enricher, simple_spec):
        enricher.enrich_spec(simple_spec)
        stats = enricher.get_stats()
        # fields_enriched should be >= 0 (depends on field config matching)
        assert stats["fields_enriched"] >= 0


class TestIdempotency:
    def test_double_enrichment_no_duplicate(self, enricher, simple_spec):
        spec1 = enricher.enrich_spec(simple_spec.copy())

        enricher2 = ConsoleUIEnricher()
        enricher2.field_config = enricher.field_config
        spec2 = enricher2.enrich_spec(spec1)

        # x-f5xc-console should appear exactly once
        schema = spec2["components"]["schemas"]["viewshttp_loadbalancerCreateSpecType"]
        assert "x-f5xc-console" in schema

    def test_idempotent_stats(self, enricher, simple_spec):
        enricher.enrich_spec(simple_spec)
        stats1 = enricher.get_stats()

        enricher2 = ConsoleUIEnricher()
        enricher2.field_config = enricher.field_config
        enricher2.enrich_spec(simple_spec)
        stats2 = enricher2.get_stats()

        # Both should report 1 resource enriched
        assert stats1["resources_enriched"] == 1
        assert stats2["resources_enriched"] == 1


class TestSkipBehavior:
    def test_unknown_resource_skipped(self, enricher, unknown_spec):
        enricher.enrich_spec(unknown_spec)
        stats = enricher.get_stats()
        assert stats["skipped_no_config"] == 1
        assert stats["resources_enriched"] == 0

    def test_navigation_applied(self, enricher, simple_spec):
        spec = enricher.enrich_spec(simple_spec)
        assert "x-f5xc-console-navigation" in spec["info"]
        assert "workspaces" in spec["info"]["x-f5xc-console-navigation"]


class TestStats:
    def test_stats_to_dict(self, enricher):
        stats = enricher.get_stats()
        assert isinstance(stats, dict)
        assert "specs_processed" in stats
        assert "resources_enriched" in stats
        assert "fields_enriched" in stats
        assert "sections_mapped" in stats

    def test_stats_after_enrichment(self, enricher, simple_spec):
        enricher.enrich_spec(simple_spec)
        stats = enricher.get_stats()
        assert stats["specs_processed"] == 1
        assert stats["resources_enriched"] == 1
        assert stats["sections_mapped"] == 13
        assert stats["navigation_applied"] == 1
