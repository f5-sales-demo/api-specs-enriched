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
    schemas["debugListServicesResponse"] = {
        "properties": {
            "service": {"type": "array", "items": {"$ref": "#/components/schemas/debugService"}}
        }
    }
    schemas["debugService"] = {
        "properties": {name: {"type": "string"} for name in ("node", "name", "value")}
    }
    schemas["debugDiagnosisResponse"] = {
        "properties": {
            name: {"type": "object"}
            for name in ("responses", "curl_response", "registration_status")
        }
    }
    inventory = "/api/operate/namespaces/{namespace}/sites/{site}/vpm/debug/global/list-service"
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
            },
            "/api/operate/namespaces/system/sites/{site}/vpm/debug/global/diagnosis": {
                "get": {
                    "operationId": "ves.io.schema.operate.debug.CustomPublicAPI.DiagnosisPublic",
                    "parameters": [
                        {
                            "name": "site",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "console_user",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/debugDiagnosisResponse"
                                    }
                                }
                            }
                        }
                    },
                }
            },
            inventory: {
                "get": {
                    "operationId": "ves.io.schema.operate.debug.CustomPublicAPI.ListVolterraServices",
                    "parameters": [
                        {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
                        for name in ("namespace", "site")
                    ],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/debugListServicesResponse"
                                    }
                                }
                            }
                        }
                    },
                }
            },
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


@pytest.mark.parametrize("field", ["node", "name", "value"])
@pytest.mark.parametrize("change", ["missing", "malformed"])
def test_rejects_missing_or_malformed_inventory_identity(field, change):
    spec = fixture()
    properties = spec["components"]["schemas"]["debugService"]["properties"]
    if change == "missing":
        del properties[field]
    else:
        properties[field] = {"type": "integer"}
    with pytest.raises(ValueError, match="HA candidate service inventory"):
        validate_gcp_ha_candidate_openapi(spec)


@pytest.mark.parametrize(
    "change", ["method", "operation", "selector", "duplicate", "response", "rows"]
)
def test_rejects_changed_inventory_wire_contract(change):
    spec = fixture()
    item = spec["paths"][
        "/api/operate/namespaces/{namespace}/sites/{site}/vpm/debug/global/list-service"
    ]
    op = item["get"]
    if change == "method":
        item["post"] = item.pop("get")
    elif change == "operation":
        op["operationId"] = "ves.io.schema.operate.debug.CustomPublicAPI.Status"
    elif change == "selector":
        op["parameters"].append({"name": "node", "in": "query", "schema": {"type": "string"}})
    elif change == "duplicate":
        op["parameters"].append(deepcopy(op["parameters"][0]))
    elif change == "response":
        op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] = (
            "#/components/schemas/debugStatusResponse"
        )
    else:
        spec["components"]["schemas"]["debugListServicesResponse"]["properties"]["service"] = {
            "type": "object"
        }
    with pytest.raises(ValueError, match="HA candidate service inventory"):
        validate_gcp_ha_candidate_openapi(spec)


@pytest.mark.parametrize(
    "change", ["method", "operation", "selector", "duplicate", "response", "rows"]
)
def test_rejects_changed_diagnosis_wire_contract(change):
    spec = fixture()
    item = spec["paths"]["/api/operate/namespaces/system/sites/{site}/vpm/debug/global/diagnosis"]
    op = item["get"]
    if change == "method":
        item["post"] = item.pop("get")
    elif change == "operation":
        op["operationId"] = "ves.io.schema.operate.debug.CustomPublicAPI.Status"
    elif change == "selector":
        op["parameters"].append({"name": "node", "in": "query", "schema": {"type": "string"}})
    elif change == "duplicate":
        op["parameters"].append(deepcopy(op["parameters"][0]))
    elif change == "response":
        op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] = (
            "#/components/schemas/debugStatusResponse"
        )
    else:
        spec["components"]["schemas"]["debugDiagnosisResponse"]["properties"]["responses"] = {
            "type": "array"
        }
    with pytest.raises(ValueError, match="HA candidate diagnosis"):
        validate_gcp_ha_candidate_openapi(spec)


@pytest.mark.parametrize("field", ["responses", "curl_response", "registration_status"])
def test_rejects_missing_diagnosis_maps(field):
    spec = fixture()
    del spec["components"]["schemas"]["debugDiagnosisResponse"]["properties"][field]
    with pytest.raises(ValueError, match="HA candidate diagnosis"):
        validate_gcp_ha_candidate_openapi(spec)


def test_securemesh_live_diagnosis_cannot_promote_required_service_health():
    import json
    from pathlib import Path

    from scripts.utils.gcp_contract import GCP_SCHEMA_PROFILE

    receipt = json.loads(
        (
            Path(__file__).parents[1] / "config/evidence/gcp-securemesh-ha-discovery-20261019.json"
        ).read_text()
    )
    assert receipt["sanitized"] is True
    assert receipt["siteType"] == "securemesh_site_v2"
    assert receipt["diagnosis"]["exactOriginalNodeMap"] is True
    assert receipt["diagnosis"]["requiredServiceHealthMapping"] is False
    assert receipt["inventoryCompleteness"] == "unqualified"
    assert receipt["observations"]["missingServiceStatus"] == [
        "coredns",
        "kube-proxy",
        "vector-agent",
        "voucher",
    ]
    assert receipt["cleanup"]["objectsAbsent"] == 18
    assert receipt["cleanup"]["emptyDestroy"] is True
    assert receipt["cleanup"]["repeatedResumeSubmitted"] is False
    assert not any(receipt["acceptance"].values())
    assert GCP_SCHEMA_PROFILE["single_node_lifecycle"]["ha"] == "unavailable"
