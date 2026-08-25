"""Tests that documentation projections cannot mutate the canonical master."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from scripts.pipeline import create_master_spec, restore_native_contract_constraints
from scripts.utils.canonical_merge import CanonicalMergeError, canonical_merge_sources
from scripts.utils.schema_constraint_projector import SchemaConstraintProjector


def _source(operation_id: str, description: str = "source") -> dict[str, Any]:
    return {
        "paths": {"/widgets": {"get": {"operationId": operation_id}}},
        "components": {"schemas": {"Widget": {"type": "object", "description": description}}},
    }


def test_domain_only_enrichment_cannot_change_master_bytes() -> None:
    canonical = canonical_merge_sources({"service.json": _source("getWidget")})
    domains = copy.deepcopy(canonical.sources)
    domains["service.json"]["components"]["schemas"]["Widget"]["description"] = "domain-only prose"
    master = create_master_spec(domains, "1.0.0", canonical)
    assert master["components"]["schemas"]["Widget"]["description"] == "source"


def test_domain_constraint_projection_cannot_tighten_canonical_master() -> None:
    source = _source("getWidget")
    widget = source["components"]["schemas"]["Widget"]
    widget["properties"] = {
        "name": {
            "type": "string",
            "x-f5xc-constraints": {
                "category": "naming",
                "minLength": 1,
                "maxLength": 63,
            },
        }
    }
    canonical = canonical_merge_sources({"service.json": source})
    domains = copy.deepcopy(canonical.sources)
    SchemaConstraintProjector().enrich_spec(domains["service.json"])

    projected = domains["service.json"]["components"]["schemas"]["Widget"]["properties"]["name"]
    master = create_master_spec(domains, "1.0.0", canonical)
    canonical_name = master["components"]["schemas"]["Widget"]["properties"]["name"]

    assert projected["maxLength"] == 63
    assert "maxLength" not in canonical_name


def test_canonical_safe_securemesh_overrides_enter_master_contract() -> None:
    source = {
        "components": {
            "schemas": {
                "securemesh_site_v2CreateRequest": {"type": "object", "properties": {}},
                "securemesh_site_v2GetResponse": {"type": "object", "properties": {}},
                "securemesh_site_v2ReplaceRequest": {"type": "object", "properties": {}},
                "securemesh_site_v2Interface": {"type": "object", "properties": {}},
            }
        }
    }
    canonical = canonical_merge_sources({"sites.json": source})

    master = create_master_spec(canonical.sources, "1.0.0", canonical)
    schemas = master["components"]["schemas"]

    assert "resource_version" not in schemas["securemesh_site_v2CreateRequest"]["properties"]
    token = {
        "type": "string",
        "x-f5xc-concurrency-token": {
            "server_assigned": True,
            "echo_on_operations": ["replace"],
        },
    }
    assert schemas["securemesh_site_v2GetResponse"]["properties"]["resource_version"] == token
    assert schemas["securemesh_site_v2ReplaceRequest"]["properties"]["resource_version"] == token
    for field in ("is_management", "is_primary"):
        assert schemas["securemesh_site_v2Interface"]["properties"][field] == {
            "type": "boolean",
            "readOnly": True,
        }


def test_canonical_constraints_are_restored_from_upstream() -> None:
    original: dict[str, Any] = {
        "properties": {
            "name": {"type": "string", "maxLength": 128},
            "labels": {"type": "array"},
        }
    }
    processed = copy.deepcopy(original)
    processed["properties"]["name"].update({"minLength": 1, "maxLength": 63})
    processed["properties"]["labels"]["maxItems"] = 10

    restore_native_contract_constraints(processed, original)

    assert processed["properties"]["name"]["maxLength"] == 128
    assert "minLength" not in processed["properties"]["name"]
    assert "maxItems" not in processed["properties"]["labels"]


def test_constraint_keyword_property_name_keeps_enriched_property() -> None:
    original: dict[str, Any] = {
        "properties": {
            "pattern": {"type": "string", "description": "Required: YES"},
        }
    }
    processed = copy.deepcopy(original)
    processed["properties"]["pattern"]["description"] = "Useful pattern prose."
    processed["properties"]["pattern"]["maxLength"] = 1024

    restore_native_contract_constraints(processed, original)

    pattern_property = processed["properties"]["pattern"]
    assert pattern_property["description"] == "Useful pattern prose."
    assert "maxLength" not in pattern_property


def test_domain_fallback_rejects_unclassified_path_collision() -> None:
    with pytest.raises(CanonicalMergeError, match="unclassified duplicate operation"):
        create_master_spec(
            {"a.json": _source("getWidget"), "b.json": _source("readWidget")},
            "1.0.0",
        )
