"""Candidate Kubernetes proxy contracts do not qualify Secure Mesh core service health."""

from copy import deepcopy

import pytest

from scripts.utils.gcp_contract import validate_gcp_kubernetes_candidate_openapi

BASE = "/api/data/namespaces/system/site/{site}"
OPERATIONS = (
    ("/api/v1/nodes", "NodeList", "v1NodeList", "corev1Node", False),
    ("/api/v1/namespaces", "NamespaceList", "v1NamespaceList", "v1Namespace", False),
    ("/api/v1/pods", "PodList_get_1", "v1PodList", "v1Pod", True),
    ("/apis/apps/v1/daemonsets", "DaemonSetList_get", "v1DaemonSetList", "v1DaemonSet", True),
)


def fixture():
    schemas = {
        "v1ListMeta": {
            "properties": {
                f: {"type": "string"} for f in ("continue", "resourceVersion", "remainingItemCount")
            }
        },
        "v1ObjectMeta": {
            "properties": {
                f: {"type": "string"} for f in ("name", "namespace", "uid", "resourceVersion")
            }
        },
        "v1PodSpec": {"properties": {"nodeName": {"type": "string"}}},
        "v1NodeSpec": {"properties": {"providerID": {"type": "string"}}},
    }
    paths = {}
    for suffix, operation, list_schema, row_schema, namespace in OPERATIONS:
        schemas[list_schema] = {
            "properties": {
                "items": {"type": "array", "items": {"$ref": f"#/components/schemas/{row_schema}"}},
                "metadata": {"allOf": [{"$ref": "#/components/schemas/v1ListMeta"}]},
            }
        }
        schemas[row_schema] = {
            "properties": {"metadata": {"allOf": [{"$ref": "#/components/schemas/v1ObjectMeta"}]}}
        }
        parameters = [
            {"name": "site", "in": "path", "required": True, "schema": {"type": "string"}}
        ]
        if namespace:
            parameters.append(
                {
                    "name": "namespace",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "string"},
                }
            )
        paths[BASE + suffix] = {
            "get": {
                "operationId": f"ves.io.schema.site.CustomDataK8SAPI.{operation}",
                "parameters": parameters,
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": f"#/components/schemas/{list_schema}"}
                            }
                        }
                    }
                },
            }
        }
    schemas["corev1Node"]["properties"]["spec"] = {
        "allOf": [{"$ref": "#/components/schemas/v1NodeSpec"}]
    }
    schemas["v1Pod"]["properties"]["spec"] = {"allOf": [{"$ref": "#/components/schemas/v1PodSpec"}]}
    return {"paths": paths, "components": {"schemas": schemas}}


def test_exact_candidate_proxy_contract():
    assert validate_gcp_kubernetes_candidate_openapi(fixture()) is None


@pytest.mark.parametrize(
    ("suffix", "operation", "list_schema", "row_schema", "namespace"), OPERATIONS
)
@pytest.mark.parametrize(
    "change",
    ["operation", "method", "duplicate-parameter", "selector", "response", "items", "metadata"],
)
def test_rejects_changed_proxy_contract(
    suffix, operation, list_schema, row_schema, namespace, change
):
    spec = deepcopy(fixture())
    item = spec["paths"][BASE + suffix]
    get = item["get"]
    schemas = spec["components"]["schemas"]
    if change == "operation":
        get["operationId"] += "Changed"
    elif change == "method":
        item["post"] = item.pop("get")
    elif change == "duplicate-parameter":
        get["parameters"].append(deepcopy(get["parameters"][0]))
    elif change == "selector":
        get["parameters"].append(
            {"name": "labelSelector", "in": "query", "schema": {"type": "string"}}
        )
    elif change == "response":
        get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] = (
            "#/components/schemas/unknown"
        )
    elif change == "items":
        schemas[list_schema]["properties"]["items"]["items"]["$ref"] = (
            "#/components/schemas/unknown"
        )
    elif change == "metadata":
        schemas[row_schema]["properties"].pop("metadata")
    with pytest.raises(ValueError, match="Kubernetes candidate"):
        validate_gcp_kubernetes_candidate_openapi(spec)


@pytest.mark.parametrize(
    ("schema", "field"),
    [
        ("v1ObjectMeta", "uid"),
        ("v1ObjectMeta", "name"),
        ("v1ListMeta", "continue"),
        ("v1ListMeta", "resourceVersion"),
        ("v1PodSpec", "nodeName"),
        ("v1NodeSpec", "providerID"),
    ],
)
def test_rejects_changed_proxy_identity_fields(schema, field):
    spec = fixture()
    spec["components"]["schemas"][schema]["properties"][field]["type"] = "integer"
    with pytest.raises(ValueError, match="Kubernetes candidate"):
        validate_gcp_kubernetes_candidate_openapi(spec)
