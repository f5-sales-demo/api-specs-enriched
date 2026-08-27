"""Release contracts for Terraform response operations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
OPENAPI_PATH = REPO_ROOT / "docs" / "specifications" / "api" / "openapi.json"
CATALOG_PATH = REPO_ROOT / "release" / "api-catalog.json"

EXPECTED_OPERATIONS = {
    "ves.io.schema.registration.CustomAPI.GetImageDownloadUrl": {
        "role": "query",
        "terraformName": "site_image",
        "method": "POST",
        "path": "/api/register/namespaces/system/get-image-download-url",
        "required": ["provider"],
        "requestSchema": "registrationGetImageDownloadUrlReq",
        "responseSchema": "registrationGetImageDownloadUrlResp",
    },
    "ves.io.schema.token.CustomAPI.GetCloudInitConfig": {
        "role": "issuance",
        "terraformName": "site_cloud_init",
        "method": "GET",
        "path": "/api/register/namespaces/system/get-cloud-init-config",
        "required": ["provider", "site_name"],
        "responseSchema": "tokenGetCloudInitConfigResp",
    },
    "ves.io.schema.registration.CustomAPI.List": {
        "role": "collection",
        "terraformName": "site_registrations",
        "method": "GET",
        "path": "/api/register/namespaces/{namespace}/registrations",
        "required": ["namespace"],
        "responseSchema": "registrationListResponse",
    },
    "ves.io.schema.registration.CustomAPI.ListRegistrationsBySite": {
        "role": "collection",
        "terraformName": "site_registrations_by_site",
        "method": "GET",
        "path": "/api/register/namespaces/{namespace}/registrations_by_site/{site_name}",
        "required": ["namespace", "site_name"],
        "responseSchema": "registrationListResponse",
    },
    "ves.io.schema.registration.CustomAPI.ListRegistrationsByState": {
        "role": "collection",
        "terraformName": "site_registrations_by_state",
        "method": "POST",
        "path": "/api/register/namespaces/{namespace}/listregistrationsbystate",
        "required": ["namespace", "state"],
        "requestSchema": "registrationListStateReq",
        "responseSchema": "registrationListResponse",
    },
    "ves.io.schema.site.UpgradeAPI.UpgradeSW": {
        "role": "action",
        "terraformName": "site_upgrade_sw",
        "method": "POST",
        "path": "/api/config/namespaces/{namespace}/sites/{name}/upgrade_sw",
        "required": ["namespace", "name", "version"],
        "requestSchema": "siteUpgradeSWRequest",
        "responseSchema": "siteUpgradeSWResponse",
    },
    "ves.io.schema.site.UpgradeAPI.UpgradeOS": {
        "role": "action",
        "terraformName": "site_upgrade_os",
        "method": "POST",
        "path": "/api/config/namespaces/{namespace}/sites/{name}/upgrade_os",
        "required": ["namespace", "name", "version"],
        "requestSchema": "siteUpgradeOSRequest",
        "responseSchema": "siteUpgradeOSResponse",
    },
}


def _openapi_operations(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    operations: dict[str, dict[str, Any]] = {}
    for path, path_item in spec["paths"].items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            if isinstance(operation, dict) and operation.get("operationId"):
                operations[operation["operationId"]] = {
                    "method": method.upper(),
                    "path": path,
                    **operation,
                }
    return operations


def test_release_publishes_all_terraform_response_operations() -> None:
    spec = json.loads(OPENAPI_PATH.read_text())
    operations = _openapi_operations(spec)
    catalog = json.loads(CATALOG_PATH.read_text())
    catalog_operations = {
        operation["operationId"]: operation
        for identity in catalog["apiOperations"]
        for operation in identity["operations"]
        if operation.get("role")
    }

    assert set(catalog_operations) == set(EXPECTED_OPERATIONS)
    for operation_id, expected in EXPECTED_OPERATIONS.items():
        operation = operations[operation_id]
        assert operation["method"] == expected["method"]
        assert operation["path"] == expected["path"]
        assert operation["x-f5xc-operation-role"] == expected["role"]
        assert operation["x-f5xc-terraform-name"] == expected["terraformName"]
        assert operation["x-f5xc-required-fields"] == expected["required"]

        catalog_operation = catalog_operations[operation_id]
        for field in (
            "role",
            "terraformName",
            "method",
            "path",
            "requestSchema",
            "responseSchema",
        ):
            if field in expected:
                assert catalog_operation[field] == expected[field]
            else:
                assert field not in catalog_operation


def test_release_roles_preserve_side_effect_semantics() -> None:
    operations = _openapi_operations(json.loads(OPENAPI_PATH.read_text()))

    for operation_id, expected in EXPECTED_OPERATIONS.items():
        operation = operations[operation_id]
        if expected["role"] in {"query", "collection"}:
            assert operation["x-f5xc-danger-level"] == "low"
            assert "x-f5xc-side-effects" not in operation
        elif expected["role"] == "issuance":
            assert operation["x-f5xc-danger-level"] == "medium"
            assert operation["x-f5xc-side-effects"] == {"creates": ["site_node_token"]}
        else:
            assert operation["x-f5xc-danger-level"] == "medium"
            assert operation["x-f5xc-side-effects"] == {"modifies": ["site"]}
            assert operation["x-f5xc-operation-metadata"]["conditions"]["postconditions"] == [
                "Action request accepted by the API",
                "Asynchronous convergence not implied",
            ]


def test_registration_collection_response_remains_typed() -> None:
    spec = json.loads(OPENAPI_PATH.read_text())
    response = spec["components"]["schemas"]["registrationListResponse"]

    assert response["properties"]["items"]["items"]["$ref"] == (
        "#/components/schemas/registrationListResponseItem"
    )
    assert response["properties"]["errors"]["items"]["$ref"]


def test_upgrade_force_remains_optional() -> None:
    spec = json.loads(OPENAPI_PATH.read_text())
    for schema_name in ("siteUpgradeSWRequest", "siteUpgradeOSRequest"):
        request = spec["components"]["schemas"][schema_name]
        assert request["properties"]["force"]["type"] == "boolean"
        assert request["properties"]["force"]["x-ves-required"] == "false"
