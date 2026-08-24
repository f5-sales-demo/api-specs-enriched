"""Contract-integrity tests for the shared canonical merger."""

from __future__ import annotations

import copy

import pytest

from scripts.compile_catalog import compile_catalog
from scripts.utils.canonical_merge import (
    CanonicalMergeError,
    canonical_merge_sources,
    source_service_slug,
)


def test_release_filename_uses_full_stable_service_slug() -> None:
    assert (
        source_service_slug(
            "docs-cloud-f5-com.0123.public.ves.io.schema.discovery_cloud.ves-swagger.json"
        )
        == "ves_io_schema_discovery_cloud"
    )


def _spec(schema: dict, operation_id: str | None = None) -> dict:
    paths = {}
    if operation_id:
        paths = {
            "/api/widgets": {
                "post": {
                    "operationId": operation_id,
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Widget"}}
                        }
                    },
                    "responses": {},
                }
            }
        }
    return {"paths": paths, "components": {"schemas": {"Widget": schema}}}


def test_conflicts_are_qualified_refs_rewritten_and_order_independent() -> None:
    alpha = _spec({"type": "object", "properties": {"value": {"type": "string"}}})
    beta = _spec({"type": "object", "properties": {"value": {"type": "integer"}}})
    alpha["components"]["schemas"]["Holder"] = {"$ref": "#/components/schemas/Widget"}
    beta["components"]["schemas"]["Holder"] = {"$ref": "#/components/schemas/Widget"}
    forward = canonical_merge_sources({"svc.alpha.json": alpha, "svc.beta.json": beta})
    reverse = canonical_merge_sources({"svc.beta.json": beta, "svc.alpha.json": alpha})
    assert forward.merged == reverse.merged
    assert compile_catalog(forward.merged) == compile_catalog(reverse.merged)
    assert forward.accounting.schema_occurrences == 4
    assert forward.accounting.schema_assignments == 4
    assert set(forward.merged["components"]["schemas"]) == {
        "Widget__svc_alpha",
        "Widget__svc_beta",
        "Holder__svc_alpha",
        "Holder__svc_beta",
    }
    assert forward.merged["components"]["schemas"]["Holder__svc_alpha"]["$ref"].endswith(
        "/Widget__svc_alpha"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda schema: schema.update({"description": "different"}),
        lambda schema: schema.update({"type": "integer"}),
        lambda schema: schema.update({"$ref": "#/components/schemas/Other"}),
        lambda schema: schema.update({"enum": ["a", "b"]}),
    ],
)
def test_scalar_type_ref_and_list_conflicts_never_union(mutation) -> None:
    first = _spec({"type": "string", "enum": ["a"]})
    second = copy.deepcopy(first)
    if "$ref" in mutation.__code__.co_consts:
        second["components"]["schemas"]["Other"] = {"type": "string"}
    mutation(second["components"]["schemas"]["Widget"])
    result = canonical_merge_sources({"first.json": first, "second.json": second})
    assert "Widget" not in result.merged["components"]["schemas"]


def test_unclassified_duplicate_operation_fails() -> None:
    first = _spec({"type": "string"}, "ves.io.schema.first.API.Create")
    second = _spec({"type": "string"}, "ves.io.schema.second.API.Create")
    with pytest.raises(CanonicalMergeError, match="unclassified duplicate operation"):
        canonical_merge_sources({"first.json": first, "second.json": second})


def _alias_spec(identity: str, schema_type: str = "string") -> dict:
    return {
        "paths": {
            "/api/discovery/namespaces/{namespace}/suggest-values": {
                "x-displayname": "Discovery Cloud"
                if identity == "discovery_cloud"
                else "Discovered Services",
                "x-ves-proto-service": f"ves.io.schema.{identity}.CustomAPI",
                "post": {
                    "operationId": f"ves.io.schema.{identity}.CustomAPI.SuggestValues",
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Request"}}
                        }
                    },
                    "responses": {},
                },
            }
        },
        "components": {"schemas": {"Request": {"type": schema_type}}},
    }


def test_classified_alias_is_preserved_and_accounted() -> None:
    result = canonical_merge_sources(
        {
            "discovered.json": _alias_spec("discovered_service"),
            "cloud.json": _alias_spec("discovery_cloud"),
        }
    )
    operation = result.merged["paths"]["/api/discovery/namespaces/{namespace}/suggest-values"][
        "post"
    ]
    assert operation["operationId"] == "ves.io.schema.discovery_cloud.CustomAPI.SuggestValues"
    assert operation["x-f5xc-operation-aliases"] == [
        "ves.io.schema.discovered_service.CustomAPI.SuggestValues"
    ]
    assert result.accounting.operation_occurrences == 2
    assert result.accounting.canonical_operations == 1
    assert result.accounting.operation_aliases == 1


def test_classified_alias_divergence_fails() -> None:
    with pytest.raises(CanonicalMergeError, match="aliases diverged"):
        canonical_merge_sources(
            {
                "discovered.json": _alias_spec("discovered_service", "integer"),
                "cloud.json": _alias_spec("discovery_cloud", "string"),
            }
        )
