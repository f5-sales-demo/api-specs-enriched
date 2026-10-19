"""Candidate HA schema checks cannot grant runtime admission."""

from copy import deepcopy

import pytest

from scripts.utils.gcp_contract import validate_gcp_ha_candidate_openapi


def fixture():
    schemas = {
        name: {
            "properties": {"enable_ha": {"allOf": [{"$ref": "#/components/schemas/ioschemaEmpty"}]}}
        }
        for name in (
            "viewssecuremesh_site_v2CreateSpecType",
            "viewssecuremesh_site_v2GetSpecType",
            "viewssecuremesh_site_v2ReplaceSpecType",
        )
    }
    schemas["ioschemaEmpty"] = {"type": "object"}
    schemas["debugStatusResponse"] = {
        "properties": {
            "status": {"type": "array", "items": {"$ref": "#/components/schemas/debugStatus"}}
        }
    }
    schemas["debugStatus"] = {
        "properties": {name: {"type": "string"} for name in ("node", "name", "status", "message")}
    }
    path = "/api/operate/namespaces/{namespace}/sites/{site}/vpm/debug/global/{vesnamespace}/status"
    return {
        "components": {"schemas": schemas},
        "paths": {
            path: {
                "get": {
                    "operationId": "ves.io.schema.operate.debug.CustomPublicAPI.Status",
                    "parameters": [
                        {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
                        for name in ("namespace", "site", "vesnamespace")
                    ]
                    + [
                        {
                            "name": "cached",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "boolean"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/debugStatusResponse"}
                                }
                            }
                        }
                    },
                }
            }
        },
    }


def test_exact_ha_candidate_shape():
    spec = fixture()
    before = deepcopy(spec)
    assert validate_gcp_ha_candidate_openapi(spec) is None
    assert spec == before


@pytest.mark.parametrize("field", ["node", "name", "status", "message"])
def test_rejects_missing_or_malformed_component_fields(field):
    spec = fixture()
    spec["components"]["schemas"]["debugStatus"]["properties"][field] = {"type": "integer"}
    with pytest.raises(ValueError, match="component"):
        validate_gcp_ha_candidate_openapi(spec)


@pytest.mark.parametrize("change", ["selector", "cached", "response", "method", "ha"])
def test_rejects_inferred_or_changed_wire_contract(change):
    spec = fixture()
    item = next(iter(spec["paths"].values()))
    op = item["get"]
    if change == "selector":
        op["parameters"].append({"name": "node", "in": "query", "schema": {"type": "string"}})
    elif change == "cached":
        op["parameters"][-1]["schema"]["type"] = "string"
    elif change == "response":
        op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] = (
            "#/components/schemas/debugHealthResponse"
        )
    elif change == "method":
        item["post"] = item.pop("get")
    else:
        del spec["components"]["schemas"]["viewssecuremesh_site_v2GetSpecType"]["properties"][
            "enable_ha"
        ]
    with pytest.raises(ValueError, match="HA candidate"):
        validate_gcp_ha_candidate_openapi(spec)


def test_api_only_evidence_cannot_promote_runtime():
    import json
    from pathlib import Path

    from scripts.utils.gcp_contract import GCP_SCHEMA_PROFILE

    receipt = json.loads(
        (Path(__file__).parents[1] / "config/evidence/gcp-ha-api-only-20261019.json").read_text()
    )
    assert receipt["sanitized"] is True
    assert receipt["facts"]["enable_ha_retained"] is True
    assert receipt["facts"]["distinct_original_token_identities"] is True
    assert receipt["facts"]["node_label_and_site_binding"] is True
    assert receipt["cleanup"]["all_absent"] is True
    assert receipt["cleanup"]["independent_absence_count"] == 4
    assert not any(receipt["acceptance"].values())
    assert GCP_SCHEMA_PROFILE["single_node_lifecycle"]["ha"] == "unavailable"
    assert GCP_SCHEMA_PROFILE["global_health_observation"]["ha_health_acceptance"] is False
