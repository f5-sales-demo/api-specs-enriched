"""Catalog null qualification preserves shared requests and original types."""

import copy

import pytest
from openapi_schema_validator import OAS30ReadValidator

from scripts.utils.permission_catalog_nullability import qualify_permission_catalog_nullability

KINDS = ("api_group", "api_group_element", "role")
PREFIX = "#/components/schemas/"


def fixture():
    schemas = {
        "Optional": {"type": "object", "properties": {"enabled": {"type": "boolean"}}},
        "Meta": {
            "type": "object",
            "properties": {
                "uid": {"type": "string", "minLength": 1},
                "deletion_timestamp": {"type": "string"},
                "modification_timestamp": {"type": "string"},
                "initializers": {"$ref": PREFIX + "Optional"},
                "owner_view": {"$ref": PREFIX + "Optional"},
            },
            "required": ["uid"],
        },
        "Request": {"type": "object", "properties": {"metadata": {"$ref": PREFIX + "Meta"}}},
    }
    paths = {
        "/request": {
            "post": {
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": PREFIX + "Request"}}}
                }
            }
        }
    }
    for kind in KINDS:
        item = kind + "ListResponseItem"
        schemas[item] = {
            "type": "object",
            "required": ["uid"],
            "properties": {
                "uid": {"type": "string", "minLength": 1},
                "get_spec": {"$ref": PREFIX + "Optional"},
                "metadata": {"$ref": PREFIX + "Optional"},
                "owner_view": {"$ref": PREFIX + "Optional"},
                "system_metadata": {"allOf": [{"$ref": PREFIX + "Meta"}]},
            },
        }
        schemas[kind + "ListResponse"] = {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"$ref": PREFIX + item}}},
        }
        paths["/" + kind] = {
            "get": {
                "operationId": "ves.io.schema." + kind + ".API.List",
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {"schema": {"$ref": PREFIX + kind + "ListResponse"}}
                        }
                    }
                },
            }
        }
    return {"openapi": "3.0.3", "paths": paths, "components": {"schemas": schemas}}


def validate(document, kind, item):
    return OAS30ReadValidator(
        {"components": document["components"], "$ref": PREFIX + kind + "ListResponse"}
    ).is_valid({"items": [item]})


@pytest.mark.parametrize("kind", KINDS)
def test_observed_default_and_expanded_nulls_preserve_shared_schema(kind):
    doc = fixture()
    original = copy.deepcopy(doc)
    raw = {
        "uid": "owned",
        "get_spec": None,
        "metadata": None,
        "owner_view": None,
        "system_metadata": None,
    }
    assert not validate(doc, kind, raw)
    qualify_permission_catalog_nullability(doc)
    assert validate(doc, kind, raw)
    raw["system_metadata"] = {
        "uid": "owned",
        "deletion_timestamp": None,
        "modification_timestamp": None,
        "initializers": None,
        "owner_view": None,
    }
    assert validate(doc, kind, raw) is (kind != "role")
    for name in ["Optional", "Meta", "Request"]:
        assert doc["components"]["schemas"][name] == original["components"]["schemas"][name]
    assert doc["paths"] == original["paths"]


@pytest.mark.parametrize("field", ["uid", "get_spec", "system_metadata"])
def test_malformed_nonnull_and_identity_rejected(field):
    doc = fixture()
    qualify_permission_catalog_nullability(doc)
    raw = {"uid": "owned", "get_spec": None, "system_metadata": None}
    raw[field] = 17
    assert not validate(doc, "role", raw)


def test_idempotent_and_transactional_on_missing_field():
    doc = fixture()
    qualify_permission_catalog_nullability(doc)
    once = copy.deepcopy(doc)
    qualify_permission_catalog_nullability(doc)
    assert doc == once
    doc = fixture()
    del doc["components"]["schemas"]["roleListResponseItem"]["properties"]["owner_view"]
    original = copy.deepcopy(doc)
    with pytest.raises(ValueError, match="absent"):
        qualify_permission_catalog_nullability(doc)
    assert doc == original


def test_unrelated_operation_and_requests_unchanged():
    doc = fixture()
    doc["paths"]["/role"]["get"]["operationId"] = "unrelated.API.List"
    original = copy.deepcopy(doc["components"]["schemas"]["roleListResponse"])
    qualify_permission_catalog_nullability(doc)
    assert doc["components"]["schemas"]["roleListResponse"] == original


def test_request_reachability_rejected():
    doc = fixture()
    doc["components"]["schemas"]["Request"]["properties"]["catalog"] = {
        "$ref": PREFIX + "roleListResponse"
    }
    original = copy.deepcopy(doc)
    with pytest.raises(ValueError, match="request schema"):
        qualify_permission_catalog_nullability(doc)
    assert doc == original


def test_owning_enrichment_and_contract_diff_preserve_boundary():
    from scripts.contract_diff import run_contract_diff
    from scripts.utils.schema_override_enricher import SchemaOverrideEnricher

    source = fixture()
    enriched = copy.deepcopy(source)
    SchemaOverrideEnricher().enrich_spec(enriched, canonical_only=True)
    assert validate(enriched, "role", {"uid": "owned", "metadata": None})
    assert not run_contract_diff(source, enriched)
    enriched["components"]["schemas"]["Request"]["properties"]["metadata"] = {"type": "string"}
    assert run_contract_diff(source, enriched)
