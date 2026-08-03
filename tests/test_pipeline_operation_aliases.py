"""Explicit operation-alias accounting and fail-closed merge tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts import pipeline
from scripts.utils.component_canonicalization import canonicalize_source_components

PATH = "/api/discovery/namespaces/{namespace}/suggest-values"
CANONICAL_ID = "ves.io.schema.discovery_cloud.CustomAPI.SuggestValues"
ALTERNATE_ID = "ves.io.schema.discovered_service.CustomAPI.SuggestValues"


def _operation(service: str) -> dict:
    return {
        "operationId": f"ves.io.schema.{service}.CustomAPI.SuggestValues",
        "x-ves-proto-rpc": f"ves.io.schema.{service}.CustomAPI.SuggestValues",
        "tags": ["discovery"],
        "parameters": [
            {
                "in": "path",
                "name": "namespace",
                "required": True,
                "schema": {"type": "string"},
            }
        ],
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{service}SuggestValuesReq"}
                }
            }
        },
        "responses": {
            "200": {
                "description": "OK",
                "content": {
                    "application/json": {
                        "schema": {"$ref": f"#/components/schemas/{service}SuggestValuesResp"}
                    }
                },
            }
        },
    }


def _spec(service: str) -> dict:
    display_name = (
        "EXAMPLE_DISCOVERY_SERVICE" if service == "discovery_cloud" else "Discovered Services."
    )
    return {
        "openapi": "3.0.0",
        "info": {
            "title": f"F5 Distributed Cloud Services API for ves.io.schema.{service}",
            "version": "1.0.0",
        },
        "paths": {
            PATH: {
                "x-displayname": display_name,
                "x-ves-proto-service": f"ves.io.schema.{service}.CustomAPI",
                "post": _operation(service),
            }
        },
        "components": {
            "schemas": {
                f"{service}SuggestValuesReq": {
                    "type": "object",
                    "properties": {"field": {"type": "string"}},
                },
                f"{service}SuggestValuesResp": {
                    "type": "object",
                    "properties": {"items": {"type": "array", "items": {"type": "string"}}},
                },
            }
        },
        "tags": [{"name": "discovery"}],
    }


def _source_specs() -> dict[str, dict]:
    return {
        path.name: json.loads(path.read_text())
        for path in sorted(Path("specs/original").glob("*.json"))
        if path.name != "manifest.json"
    }


def _canonicalize(specs: dict[str, dict]):
    sources = {}
    for position, spec in enumerate(specs.values(), start=1):
        owner = spec["info"]["title"].removeprefix("F5 Distributed Cloud Services API for ")
        sources[f"docs-cloud-f5-com.{position}.public.{owner}.ves-swagger.json"] = spec
    return canonicalize_source_components(sources)


def test_current_source_identity_accounting_is_exact():
    accounting = pipeline.validate_operation_alias_accounting(
        _source_specs(),
        pipeline.load_operation_aliases(),
    )

    assert accounting == {
        "source_operations": 1852,
        "unique_operation_ids": 1852,
        "canonical_operations": 1851,
        "explicit_aliases": 1,
        "identical_duplicates": 0,
    }


@pytest.mark.parametrize("domain_order", [("alternate", "canonical"), ("canonical", "alternate")])
def test_master_uses_configured_canonical_identity_and_preserves_alias(domain_order):
    specs = {
        "canonical": _spec("discovery_cloud"),
        "alternate": _spec("discovered_service"),
    }
    original_specs = copy.deepcopy(specs)
    master = pipeline._create_master_graph(
        _canonicalize({domain: specs[domain] for domain in domain_order}),
        "1.2.3",
    )

    operation = master["paths"][PATH]["post"]
    assert operation["operationId"] == CANONICAL_ID
    assert operation["x-f5xc-operation-aliases"] == [
        {"operationId": ALTERNATE_ID, "relationship": "wire-equivalent"}
    ]
    assert master["paths"][PATH]["x-displayname"] == "EXAMPLE_DISCOVERY_SERVICE"
    assert master["paths"][PATH]["x-ves-proto-service"] == (
        "ves.io.schema.discovery_cloud.CustomAPI"
    )
    assert specs == original_specs


def test_configured_alias_rejects_resolved_wire_shape_divergence():
    canonical = _spec("discovery_cloud")
    alternate = _spec("discovered_service")
    alternate["components"]["schemas"]["discovered_serviceSuggestValuesResp"]["properties"][
        "items"
    ]["items"]["type"] = "integer"

    with pytest.raises(pipeline.PathMemberConflictError, match="divergent wire shapes"):
        pipeline.validate_operation_alias_accounting(
            {"canonical.json": canonical, "alternate.json": alternate},
            pipeline.load_operation_aliases(),
        )


@pytest.mark.parametrize("wire_location", ["property-name", "enum-value"])
def test_configured_alias_never_normalizes_transmitted_wire_values(wire_location):
    canonical = _spec("discovery_cloud")
    alternate = _spec("discovered_service")
    canonical_request = canonical["components"]["schemas"]["discovery_cloudSuggestValuesReq"]
    alternate_request = alternate["components"]["schemas"]["discovered_serviceSuggestValuesReq"]
    if wire_location == "property-name":
        canonical_request["properties"] = {"tenant_discovery_cloud_mode": {"type": "string"}}
        alternate_request["properties"] = {"tenant_discovered_service_mode": {"type": "string"}}
    else:
        canonical_request["properties"]["field"]["enum"] = ["tenant-discovery_cloud-mode"]
        alternate_request["properties"]["field"]["enum"] = ["tenant-discovered_service-mode"]

    with pytest.raises(pipeline.PathMemberConflictError, match="divergent wire shapes"):
        pipeline.validate_operation_alias_accounting(
            {"canonical.json": canonical, "alternate.json": alternate},
            pipeline.load_operation_aliases(),
        )


def test_unconfigured_duplicate_path_method_rejected():
    canonical = _spec("discovery_cloud")
    unexpected = _spec("discovered_service")
    unexpected["paths"][PATH]["post"]["operationId"] = (
        "ves.io.schema.unexpected.CustomAPI.SuggestValues"
    )

    with pytest.raises(pipeline.PathMemberConflictError, match="unconfigured POST"):
        pipeline.validate_operation_alias_accounting(
            {"canonical.json": canonical, "unexpected.json": unexpected},
            pipeline.load_operation_aliases(),
        )
    with pytest.raises(pipeline.PathMemberConflictError, match="unconfigured POST"):
        pipeline._create_master_graph(
            _canonicalize({"canonical": canonical, "unexpected": unexpected}),
            "1.2.3",
        )


def test_exact_duplicate_operation_and_wire_graph_is_accepted():
    spec = _spec("unrelated")
    path = "/api/example"
    operation = spec["paths"].pop(PATH)
    spec["paths"][path] = operation
    duplicate = copy.deepcopy(spec)

    accounting = pipeline.validate_operation_alias_accounting(
        {"one.json": spec, "two.json": duplicate},
        aliases={},
        require_all_aliases=False,
    )

    assert accounting["source_operations"] == 2
    assert accounting["canonical_operations"] == 1
    assert accounting["identical_duplicates"] == 1


def test_exact_operation_with_divergent_resolved_component_rejected():
    spec = _spec("unrelated")
    path = "/api/example"
    operation = spec["paths"].pop(PATH)
    spec["paths"][path] = operation
    divergent = copy.deepcopy(spec)
    divergent["components"]["schemas"]["unrelatedSuggestValuesReq"]["properties"]["field"][
        "type"
    ] = "integer"

    with pytest.raises(pipeline.PathMemberConflictError, match="divergent wire shapes"):
        pipeline.validate_operation_alias_accounting(
            {"one.json": spec, "two.json": divergent},
            aliases={},
            require_all_aliases=False,
        )
