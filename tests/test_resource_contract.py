"""Exact downstream resource-contract tests."""

import json
from pathlib import Path

import pytest

from scripts.compile_catalog import generate_operation_name
from scripts.utils.resource_contract import (
    ResourceContractError,
    build_resource_catalog,
    validate_openapi_graph,
)


def _schema(identity: str, kind: str) -> dict:
    return {
        "type": "object",
        "x-ves-proto-message": f"ves.io.schema.{identity}.{kind}",
    }


def _operation(identity: str, action: str, *, request: str | None = None) -> dict:
    operation = {
        "operationId": f"ves.io.schema.{identity}.API.{action}",
        "responses": {},
    }
    if request:
        operation["requestBody"] = {
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{request}"},
                }
            }
        }
    return operation


def _widget_spec() -> dict:
    identity = "prefixed.widget"
    schemas = {
        "prefixedwidgetCreateSpecType": _schema(identity, "CreateSpecType"),
        "prefixedwidgetGetSpecType": _schema(identity, "GetSpecType"),
        "prefixedwidgetReplaceSpecType": _schema(identity, "ReplaceSpecType"),
        "widgetCreateRequest": {
            "properties": {"spec": {"$ref": "#/components/schemas/prefixedwidgetCreateSpecType"}}
        },
        "widgetReplaceRequest": {
            "properties": {"spec": {"$ref": "#/components/schemas/prefixedwidgetReplaceSpecType"}}
        },
        "widgetGetResponse": {
            "properties": {"spec": {"$ref": "#/components/schemas/prefixedwidgetGetSpecType"}}
        },
        "widgetListResponse": {
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/prefixedwidgetGetSpecType"},
                }
            }
        },
    }
    collection = "/api/config/namespaces/{namespace}/widgets"
    item = f"{collection}/{{name}}"
    return {
        "openapi": "3.0.3",
        "components": {"schemas": schemas},
        "paths": {
            collection: {
                "get": {
                    **_operation(identity, "List"),
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/widgetListResponse"}
                                }
                            }
                        }
                    },
                },
                "post": _operation(identity, "Create", request="widgetCreateRequest"),
            },
            item: {
                "get": {
                    **_operation(identity, "Get"),
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/widgetGetResponse"}
                                }
                            }
                        }
                    },
                },
                "put": _operation(identity, "Replace", request="widgetReplaceRequest"),
                "delete": _operation(identity, "Delete"),
            },
        },
    }


def _add_widget_action(
    spec: dict,
    *,
    proto_identity: str = "prefixed.widget",
    proto_leaf: str = "ApprovalReq",
) -> None:
    spec["components"]["schemas"]["widgetApprovalReq"] = {
        "type": "object",
        "x-ves-proto-message": f"ves.io.schema.{proto_identity}.{proto_leaf}",
        "x-f5xc-action": "approve",
    }
    spec["paths"]["/api/config/namespaces/{namespace}/widget/{name}/approve"] = {
        "post": {
            "operationId": "ves.io.schema.prefixed.widget.CustomAPI.WidgetApprove",
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/widgetApprovalReq"}
                    }
                }
            },
            "responses": {},
        }
    }


def _resources(spec: dict) -> list[dict]:
    return build_resource_catalog(spec)["resources"]


def _add_widget_cascade_delete(spec: dict, field_name: str = "name") -> None:
    item = "/api/config/namespaces/{namespace}/widgets/{name}"
    del spec["paths"][item]["delete"]
    spec["components"]["schemas"]["widgetCascadeDeleteRequest"] = {
        "type": "object",
        "properties": {field_name: {"type": "string"}},
    }
    spec["paths"][f"{item}/cascade_delete"] = {
        "post": {
            "operationId": "ves.io.schema.prefixed.widget.CustomAPI.CascadeDelete",
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/widgetCascadeDeleteRequest"}
                    }
                }
            },
            "responses": {},
        }
    }


def test_contract_uses_exact_identity_schema_keys_and_paths():
    contracts = _resources(_widget_spec())

    assert contracts == [
        {
            "name": "widget",
            "kind": "crud",
            "apiIdentity": "ves.io.schema.prefixed.widget",
            "schemaKeys": {
                "create": "prefixedwidgetCreateSpecType",
                "get": "prefixedwidgetGetSpecType",
                "replace": "prefixedwidgetReplaceSpecType",
            },
            "manageability": "managed",
            "operations": {
                "create": {
                    "method": "POST",
                    "path": "/api/config/namespaces/{namespace}/widgets",
                    "operationId": "ves.io.schema.prefixed.widget.API.Create",
                    "surface": "API",
                },
                "read": {
                    "method": "GET",
                    "path": "/api/config/namespaces/{namespace}/widgets/{name}",
                    "operationId": "ves.io.schema.prefixed.widget.API.Get",
                    "surface": "API",
                    "responseSchema": "widgetGetResponse",
                },
                "list": {
                    "method": "GET",
                    "path": "/api/config/namespaces/{namespace}/widgets",
                    "operationId": "ves.io.schema.prefixed.widget.API.List",
                    "surface": "API",
                    "responseSchema": "widgetListResponse",
                },
                "update": {
                    "method": "PUT",
                    "path": "/api/config/namespaces/{namespace}/widgets/{name}",
                    "operationId": "ves.io.schema.prefixed.widget.API.Replace",
                    "surface": "API",
                },
                "delete": {
                    "method": "DELETE",
                    "path": "/api/config/namespaces/{namespace}/widgets/{name}",
                    "operationId": "ves.io.schema.prefixed.widget.API.Delete",
                    "surface": "API",
                },
            },
        }
    ]


def test_custom_collection_looking_endpoint_without_spec_identity_is_not_a_resource():
    spec = _widget_spec()
    spec["paths"]["/api/shape/csd/namespaces/{namespace}/status"] = {
        "get": {
            "operationId": "ves.io.schema.shape.client_side_defense.CustomAPI.GetStatus",
            "responses": {},
        }
    }

    names = {contract["name"] for contract in _resources(spec)}
    assert names == {"widget"}
    assert "status"[:-1] not in names


def test_custom_api_crud_verb_without_spec_identity_is_explicitly_excluded():
    spec = _widget_spec()
    spec["paths"]["/api/config/namespaces/{namespace}/orphan/{name}"] = {
        "get": _operation("prefixed.orphan", "Get")
    }
    spec["paths"]["/api/config/namespaces/{namespace}/orphan/{name}"]["get"]["operationId"] = (
        "ves.io.schema.prefixed.orphan.CustomAPI.Get"
    )

    catalog = build_resource_catalog(spec)
    names = {contract["name"] for contract in catalog["resources"]}

    assert names == {"widget"}
    assert catalog["resourceExclusions"] == [
        {
            "apiIdentity": "ves.io.schema.prefixed.orphan",
            "classification": "custom_api_without_spec_type",
            "reason": ("CustomAPI CRUD-looking operations have no exact SpecType schema identity."),
            "operations": [
                {
                    "method": "GET",
                    "path": "/api/config/namespaces/{namespace}/orphan/{name}",
                    "operationId": "ves.io.schema.prefixed.orphan.CustomAPI.Get",
                    "surface": "CustomAPI",
                }
            ],
        }
    ]


def test_action_contract_uses_annotated_request_and_exact_sibling_read():
    spec = _widget_spec()
    _add_widget_action(spec)

    action = next(contract for contract in _resources(spec) if contract["kind"] == "action")
    assert action == {
        "name": "widget_approval",
        "kind": "action",
        "apiIdentity": "ves.io.schema.prefixed.widget",
        "action": "approve",
        "schemaKeys": {
            "request": "widgetApprovalReq",
            "read": "prefixedwidgetGetSpecType",
        },
        "operations": {
            "action": {
                "method": "POST",
                "path": "/api/config/namespaces/{namespace}/widget/{name}/approve",
                "operationId": "ves.io.schema.prefixed.widget.CustomAPI.WidgetApprove",
                "surface": "CustomAPI",
            },
            "read": {
                "method": "GET",
                "path": "/api/config/namespaces/{namespace}/widgets/{name}",
                "operationId": "ves.io.schema.prefixed.widget.API.Get",
                "surface": "API",
                "responseSchema": "widgetGetResponse",
            },
        },
    }


def test_action_contract_resolves_reusable_request_body_reference():
    spec = _widget_spec()
    _add_widget_action(spec)
    path = "/api/config/namespaces/{namespace}/widget/{name}/approve"
    operation = spec["paths"][path]["post"]
    request_body = operation.pop("requestBody")
    spec["components"]["requestBodies"] = {"WidgetApprovalBody": request_body}
    operation["requestBody"] = {"$ref": "#/components/requestBodies/WidgetApprovalBody"}

    action = next(contract for contract in _resources(spec) if contract["kind"] == "action")

    assert action["name"] == "widget_approval"
    assert action["schemaKeys"]["request"] == "widgetApprovalReq"


def test_unbound_annotated_action_schema_fails_closed():
    spec = _widget_spec()
    spec["components"]["schemas"]["UnboundActionReq"] = {
        "type": "object",
        "x-f5xc-action": "unbound",
    }

    with pytest.raises(ResourceContractError, match="annotated action schemas are not bound"):
        build_resource_catalog(spec)


def test_action_request_proto_identity_must_match_sibling_resource():
    spec = _widget_spec()
    _add_widget_action(spec, proto_identity="prefixed.other")

    with pytest.raises(ResourceContractError, match="does not match sibling identity"):
        build_resource_catalog(spec)


def test_action_operation_requires_supported_api_surface():
    spec = _widget_spec()
    _add_widget_action(spec)
    operation = spec["paths"]["/api/config/namespaces/{namespace}/widget/{name}/approve"]["post"]
    operation["operationId"] = "ves.io.schema.prefixed.widget.InternalAPI.WidgetApprove"

    with pytest.raises(ResourceContractError, match="unsupported API surface"):
        build_resource_catalog(spec)


def test_action_annotation_must_match_exact_path_suffix():
    spec = _widget_spec()
    _add_widget_action(spec)
    spec["components"]["schemas"]["widgetApprovalReq"]["x-f5xc-action"] = "deny"

    with pytest.raises(ResourceContractError, match="does not match path suffix"):
        build_resource_catalog(spec)


def test_action_name_cannot_collide_with_crud_resource_name():
    spec = _widget_spec()
    _add_widget_action(spec)
    identity = "prefixed.widget_approval"
    spec["components"]["schemas"]["prefixedwidgetApprovalGetSpecType"] = _schema(
        identity, "GetSpecType"
    )
    spec["paths"]["/api/config/namespaces/{namespace}/widget_approvals/{name}"] = {
        "get": {
            **_operation(identity, "Get"),
            "responses": {
                "200": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": ("#/components/schemas/prefixedwidgetApprovalGetSpecType")
                            }
                        }
                    }
                }
            },
        }
    }

    with pytest.raises(ResourceContractError, match="duplicate resource name 'widget_approval'"):
        build_resource_catalog(spec)


def test_missing_operation_required_schema_fails_closed():
    spec = _widget_spec()
    del spec["components"]["schemas"]["prefixedwidgetCreateSpecType"]

    with pytest.raises(ResourceContractError, match="unresolved local reference"):
        build_resource_catalog(spec)


def test_standard_api_crud_identity_without_any_spec_type_fails_closed():
    spec = _widget_spec()
    schemas = spec["components"]["schemas"]
    for key in (
        "prefixedwidgetCreateSpecType",
        "prefixedwidgetGetSpecType",
        "prefixedwidgetReplaceSpecType",
    ):
        del schemas[key]
    for key in ("widgetCreateRequest", "widgetReplaceRequest", "widgetGetResponse"):
        schemas[key]["properties"] = {}
    schemas["widgetListResponse"]["properties"] = {
        "items": {"type": "array", "items": {"type": "object"}}
    }

    with pytest.raises(ResourceContractError, match=r"no SpecType schemas: prefixed\.widget"):
        build_resource_catalog(spec)


def test_openapi_graph_rejects_missing_request_reference():
    spec = _widget_spec()
    collection = "/api/config/namespaces/{namespace}/widgets"
    request_schema = spec["paths"][collection]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    request_schema["$ref"] = "#/components/schemas/MissingCreateRequest"

    with pytest.raises(ResourceContractError, match="unresolved local reference"):
        validate_openapi_graph(spec)


def test_openapi_graph_rejects_missing_response_reference():
    spec = _widget_spec()
    collection = "/api/config/namespaces/{namespace}/widgets"
    response_schema = spec["paths"][collection]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    response_schema["$ref"] = "#/components/schemas/MissingListResponse"

    with pytest.raises(ResourceContractError, match="unresolved local reference"):
        validate_openapi_graph(spec)


def test_openapi_graph_rejects_missing_nested_reference():
    spec = _widget_spec()
    spec["components"]["schemas"]["widgetGetResponse"]["properties"]["spec"]["$ref"] = (
        "#/components/schemas/MissingGetSpecType"
    )

    with pytest.raises(ResourceContractError, match="unresolved local reference"):
        validate_openapi_graph(spec)


def test_openapi_graph_rejects_missing_reference_in_unrelated_schema():
    spec = _widget_spec()
    spec["components"]["schemas"]["UnusedSchema"] = {
        "type": "object",
        "properties": {
            "unused": {"$ref": "#/components/schemas/MissingUnusedSchema"},
        },
    }

    with pytest.raises(ResourceContractError, match="unresolved local reference"):
        validate_openapi_graph(spec)


def test_unique_schema_candidate_must_be_reachable_from_its_exact_role():
    spec = _widget_spec()
    spec["components"]["schemas"]["widgetCreateRequest"]["properties"] = {}

    with pytest.raises(ResourceContractError, match="no unique CreateSpecType schema key"):
        build_resource_catalog(spec)


def test_multiple_role_reachable_schema_candidates_fail_closed():
    spec = _widget_spec()
    spec["components"]["schemas"]["otherwidgetGetSpecType"] = _schema(
        "prefixed.widget", "GetSpecType"
    )
    spec["components"]["schemas"]["widgetGetResponse"]["properties"]["other"] = {
        "$ref": "#/components/schemas/otherwidgetGetSpecType"
    }

    with pytest.raises(ResourceContractError, match="no unique GetSpecType schema key"):
        build_resource_catalog(spec)


def test_delete_only_resource_fails_instead_of_becoming_read_only():
    spec = _widget_spec()
    item = "/api/config/namespaces/{namespace}/widgets/{name}"
    spec["paths"] = {item: {"delete": _operation("prefixed.widget", "Delete")}}

    with pytest.raises(ResourceContractError, match="mutable operations but no item Get"):
        build_resource_catalog(spec)


def test_ambiguous_schema_key_fails_closed_when_neither_is_referenced():
    spec = _widget_spec()
    spec["components"]["schemas"]["otherwidgetGetSpecType"] = _schema(
        "prefixed.widget", "GetSpecType"
    )
    spec["components"]["schemas"]["widgetGetResponse"]["properties"] = {}
    spec["components"]["schemas"]["widgetListResponse"]["properties"] = {}

    with pytest.raises(ResourceContractError, match="no unique GetSpecType schema key"):
        build_resource_catalog(spec)


def test_conflicting_collection_paths_fail_closed():
    spec = _widget_spec()
    spec["paths"]["/api/config/namespaces/{namespace}/widget_inventory"] = {
        "get": _operation("prefixed.widget", "List")
    }

    with pytest.raises(ResourceContractError, match="multiple list operations"):
        build_resource_catalog(spec)


def test_matching_custom_api_crud_verbs_join_the_primary_surface():
    spec = _widget_spec()
    collection = "/api/config/namespaces/{namespace}/widgets"
    item = f"{collection}/{{name}}"
    for path, method in ((collection, "get"), (item, "get"), (item, "delete")):
        operation_id = spec["paths"][path][method]["operationId"]
        spec["paths"][path][method]["operationId"] = operation_id.replace(".API.", ".CustomAPI.")

    catalog = build_resource_catalog(spec)
    contract = catalog["resources"][0]

    assert catalog["resourceExclusions"] == []
    assert contract["operations"]["create"]["surface"] == "API"
    assert contract["operations"]["read"]["surface"] == "CustomAPI"
    assert contract["operations"]["list"]["surface"] == "CustomAPI"
    assert contract["operations"]["delete"]["surface"] == "CustomAPI"


def test_nonstandard_delete_includes_exact_request_body_mapping():
    spec = _widget_spec()
    _add_widget_cascade_delete(spec)

    delete = _resources(spec)[0]["operations"]["delete"]

    assert delete["requestSchema"] == "widgetCascadeDeleteRequest"
    assert delete["requestBody"] == {
        "name": {
            "source": "path",
            "parameter": "name",
        }
    }


def test_nonstandard_delete_rejects_request_field_without_deterministic_source():
    spec = _widget_spec()
    _add_widget_cascade_delete(spec, field_name="other")

    with pytest.raises(ResourceContractError, match="has no deterministic source"):
        build_resource_catalog(spec)


def test_list_only_read_contract_is_explicit_and_not_manageable():
    spec = _widget_spec()
    collection = "/api/config/namespaces/{namespace}/widgets"
    spec["paths"] = {collection: {"get": spec["paths"][collection]["get"]}}

    contract = _resources(spec)[0]

    assert contract["manageability"] == "read_only"
    assert contract["schemaKeys"] == {
        "create": None,
        "get": "prefixedwidgetGetSpecType",
        "replace": None,
    }
    assert contract["operations"]["list"] == {
        "method": "GET",
        "path": "/api/config/namespaces/{namespace}/widgets",
        "operationId": "ves.io.schema.prefixed.widget.API.List",
        "surface": "API",
        "responseSchema": "widgetListResponse",
        "collectionField": "items",
        "itemSchema": "prefixedwidgetGetSpecType",
    }
    assert contract["operations"]["read"] is None


def test_list_only_response_requires_a_direct_schema_ref():
    spec = _widget_spec()
    collection = "/api/config/namespaces/{namespace}/widgets"
    spec["paths"] = {collection: {"get": spec["paths"][collection]["get"]}}
    spec["paths"][collection]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] = spec["components"]["schemas"]["widgetListResponse"]

    with pytest.raises(ResourceContractError, match="has no exact response schema"):
        build_resource_catalog(spec)


def test_list_only_collection_requires_a_direct_item_schema_ref():
    spec = _widget_spec()
    collection = "/api/config/namespaces/{namespace}/widgets"
    spec["paths"] = {collection: {"get": spec["paths"][collection]["get"]}}
    spec["components"]["schemas"]["widgetListResponse"]["properties"]["items"]["items"] = {
        "allOf": [{"$ref": "#/components/schemas/prefixedwidgetGetSpecType"}]
    }

    with pytest.raises(ResourceContractError, match="has no unique collection field"):
        build_resource_catalog(spec)


def test_list_only_collection_rejects_ambiguous_item_fields():
    spec = _widget_spec()
    collection = "/api/config/namespaces/{namespace}/widgets"
    spec["paths"] = {collection: {"get": spec["paths"][collection]["get"]}}
    spec["components"]["schemas"]["widgetListResponse"]["properties"]["duplicates"] = {
        "type": "array",
        "items": {"$ref": "#/components/schemas/prefixedwidgetGetSpecType"},
    }

    with pytest.raises(ResourceContractError, match="has no unique collection field"):
        build_resource_catalog(spec)


def test_item_only_read_contract_is_explicit_and_not_manageable():
    spec = _widget_spec()
    item = "/api/config/namespaces/{namespace}/widgets/{name}"
    spec["paths"] = {item: {"get": spec["paths"][item]["get"]}}

    contract = _resources(spec)[0]

    assert contract["manageability"] == "read_only"
    assert contract["operations"]["read"] is not None
    assert contract["operations"]["list"] is None


def test_resource_contract_is_deterministic():
    assert build_resource_catalog(_widget_spec()) == build_resource_catalog(_widget_spec())


def test_published_corpus_contract_has_no_fuzzy_false_resources():
    openapi = json.loads(Path("docs/specifications/api/openapi.json").read_text(encoding="utf-8"))
    catalog = build_resource_catalog(openapi)
    contracts = catalog["resources"]
    names = {contract["name"] for contract in contracts}

    assert len(contracts) == 194
    assert {"domain", "status"[:-1], "nginx_dataplane_server"}.isdisjoint(names)
    assert {
        "data_group",
        "nginx_csg",
        "nginx_server",
        "alert_gen_policy",
        "alert_template",
        "protected_application",
        "bot_detection_rule",
        "bot_endpoint_policy",
        "registration_approval",
    } <= names

    data_group = next(contract for contract in contracts if contract["name"] == "data_group")
    assert data_group["schemaKeys"]["create"] == "bigcnedata_groupCreateSpecType"
    discovered = next(contract for contract in contracts if contract["name"] == "discovered")
    assert discovered == {
        "name": "discovered",
        "kind": "crud",
        "apiIdentity": "ves.io.schema.uztna.application.discovered",
        "schemaKeys": {
            "create": None,
            "get": "discoveredGetSpecType",
            "replace": None,
        },
        "manageability": "read_only",
        "operations": {
            "create": None,
            "read": None,
            "list": {
                "method": "GET",
                "path": (
                    "/api/bigipconnector/namespaces/{namespace}/uztna/applications/discovered"
                ),
                "operationId": "ves.io.schema.uztna.application.discovered.CustomAPI.List",
                "surface": "CustomAPI",
                "responseSchema": "discoveredListResponse",
                "collectionField": "items",
                "itemSchema": "discoveredGetSpecType",
            },
            "update": None,
            "delete": None,
        },
    }
    action = next(contract for contract in contracts if contract["name"] == "registration_approval")
    assert action["operations"]["read"]["path"] == (
        "/api/register/namespaces/{namespace}/registrations/{name}"
    )
    exclusions = catalog["resourceExclusions"]
    assert len(exclusions) == 17
    assert {
        classification: sum(
            exclusion["classification"] == classification for exclusion in exclusions
        )
        for classification in (
            "custom_api_without_spec_type",
            "spec_type_without_crud_operations",
            "alternate_custom_api_surface",
        )
    } == {
        "custom_api_without_spec_type": 15,
        "spec_type_without_crud_operations": 1,
        "alternate_custom_api_surface": 1,
    }
    no_spec = [
        exclusion
        for exclusion in exclusions
        if exclusion["classification"] == "custom_api_without_spec_type"
    ]
    assert sum(len(exclusion["operations"]) for exclusion in no_spec) == 31
    alternate = next(
        exclusion
        for exclusion in exclusions
        if exclusion["classification"] == "alternate_custom_api_surface"
    )
    assert alternate["apiIdentity"] == "ves.io.schema.user_group"
    assert len(alternate["operations"]) == 5
    schema_only = next(
        exclusion
        for exclusion in exclusions
        if exclusion["classification"] == "spec_type_without_crud_operations"
    )
    assert schema_only["apiIdentity"] == "ves.io.schema.dos_mitigation"
    assert schema_only["schemaKeys"] == {
        "create": [],
        "get": ["schemados_mitigationGetSpecType"],
        "replace": [],
    }


def test_published_corpus_lifecycle_classifications_and_namespace_delete_are_exact():
    openapi = json.loads(Path("docs/specifications/api/openapi.json").read_text(encoding="utf-8"))
    resources = build_resource_catalog(openapi)["resources"]
    crud = [resource for resource in resources if resource["kind"] == "crud"]

    assert {
        classification: sum(resource["manageability"] == classification for resource in crud)
        for classification in ("managed", "non_deletable", "replace_only", "read_only")
    } == {
        "managed": 154,
        "non_deletable": 5,
        "replace_only": 10,
        "read_only": 24,
    }
    assert {
        resource["name"] for resource in crud if resource["manageability"] == "non_deletable"
    } == {
        "bot_infrastructure",
        "customer_support",
        "tpm_api_key",
        "tpm_category",
        "tpm_manager",
    }
    namespace = next(resource for resource in crud if resource["name"] == "namespace")
    assert namespace["manageability"] == "managed"
    assert namespace["operations"]["delete"] == {
        "method": "POST",
        "path": "/api/web/namespaces/{name}/cascade_delete",
        "operationId": "ves.io.schema.namespace.CustomAPI.CascadeDelete",
        "surface": "CustomAPI",
        "responseSchema": "namespaceCascadeDeleteResponse",
        "requestSchema": "namespaceCascadeDeleteRequest",
        "requestBody": {
            "name": {
                "source": "path",
                "parameter": "name",
            }
        },
    }


@pytest.mark.parametrize(
    ("operation_id", "expected"),
    [
        (
            "ves.io.schema.shape.client_side_defense.CustomAPI.GetStatus",
            "get_status_shape_client_side_defense_custom_api",
        ),
        (
            "ves.io.schema.shape.client_side_defense.CustomAPI.BulkDeleteDomains",
            "bulk_delete_domains_shape_client_side_defense_custom_api",
        ),
        (
            "ves.io.schema.nginx.one.nginx_server.CustomAPI.GetDataplaneServers",
            "get_dataplane_servers_nginx_one_nginx_server_custom_api",
        ),
    ],
)
def test_catalog_operation_names_come_from_operation_id(operation_id: str, expected: str):
    assert generate_operation_name({"operationId": operation_id}) == expected


def test_resource_contract_serializes_canonically():
    first = json.dumps(build_resource_catalog(_widget_spec()), sort_keys=True)
    second = json.dumps(build_resource_catalog(_widget_spec()), sort_keys=True)
    assert first == second
