"""Release-contract tests for the Customer Edge image query."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SPEC_PATH = REPO_ROOT / "docs" / "specifications" / "api" / "ce_management.json"
TOKEN_SPEC_PATH = REPO_ROOT / "docs" / "specifications" / "api" / "users.json"
CATALOG_PATH = REPO_ROOT / "release" / "api-catalog.json"
OPERATION_ID = "ves.io.schema.registration.CustomAPI.GetImageDownloadUrl"
CLOUD_INIT_OPERATION_ID = "ves.io.schema.token.CustomAPI.GetCloudInitConfig"


def test_image_download_operation_is_a_read_only_query() -> None:
    spec = json.loads(SPEC_PATH.read_text())
    operation = spec["paths"]["/api/register/namespaces/system/get-image-download-url"]["post"]

    assert operation["operationId"] == OPERATION_ID
    assert operation["x-f5xc-operation-role"] == "query"
    assert operation["x-f5xc-danger-level"] == "low"
    assert "x-f5xc-side-effects" not in operation
    assert operation["x-f5xc-required-fields"] == ["provider"]


def test_image_download_schema_marks_signed_urls_sensitive() -> None:
    spec = json.loads(SPEC_PATH.read_text())
    schemas = spec["components"]["schemas"]
    request = schemas["registrationGetImageDownloadUrlReq"]
    response = schemas["registrationGetImageDownloadUrlResp"]

    assert request["properties"]["provider"]["x-ves-required"] == "true"
    assert request["properties"]["provider"]["x-f5xc-recommended-value"] == "KVM"
    assert request["properties"]["provider"]["x-f5xc-description-medium"] == (
        "Deployment platform identifier for the requested Customer Edge image."
    )
    assert response["x-f5xc-terraform-resource"] == "xcsh_site_image"
    assert response["x-f5xc-category"] == "Sites"
    assert response["x-f5xc-description-medium"] == (
        "Signed Customer Edge image and checksum URLs for a requested deployment platform."
    )
    for field in ("image_download_url", "image_md5_download_url"):
        assert response["properties"][field]["x-f5xc-sensitive"] is True


def test_release_catalog_publishes_the_query_schema_pair() -> None:
    catalog = json.loads(CATALOG_PATH.read_text())
    registration = next(
        identity
        for identity in catalog["apiOperations"]
        if identity["apiIdentity"] == "ves.io.schema.registration"
    )
    query = next(
        operation
        for operation in registration["operations"]
        if operation["operationId"] == OPERATION_ID
    )

    assert query == {
        "method": "POST",
        "path": "/api/register/namespaces/system/get-image-download-url",
        "operationId": OPERATION_ID,
        "surface": "register",
        "requestSchema": "registrationGetImageDownloadUrlReq",
        "responseSchema": "registrationGetImageDownloadUrlResp",
        "role": "query",
    }


def test_cloud_init_operation_is_a_site_scoped_sensitive_issuance() -> None:
    spec = json.loads(TOKEN_SPEC_PATH.read_text())
    operation = spec["paths"]["/api/register/namespaces/system/get-cloud-init-config"]["get"]
    response = spec["components"]["schemas"]["tokenGetCloudInitConfigResp"]

    assert operation["operationId"] == CLOUD_INIT_OPERATION_ID
    assert operation["x-f5xc-operation-role"] == "issuance"
    assert operation["x-f5xc-required-fields"] == ["provider", "site_name"]
    assert operation["x-f5xc-danger-level"] == "medium"
    assert operation["x-f5xc-side-effects"] == {"creates": ["site_node_token"]}
    assert response["x-f5xc-terraform-resource"] == "xcsh_site_cloud_init"
    assert response["x-f5xc-category"] == "Sites"
    assert response["properties"]["cloud_init_config"]["x-f5xc-sensitive"] is True
    assert response["properties"]["cloud_init_config"]["x-f5xc-description-medium"] == (
        "Complete cloud-init containing the site-scoped one-time node token."
    )


def test_release_catalog_publishes_the_cloud_init_issuance() -> None:
    catalog = json.loads(CATALOG_PATH.read_text())
    token = next(
        identity
        for identity in catalog["apiOperations"]
        if identity["apiIdentity"] == "ves.io.schema.token"
    )
    query = next(
        operation
        for operation in token["operations"]
        if operation["operationId"] == CLOUD_INIT_OPERATION_ID
    )

    assert query == {
        "method": "GET",
        "path": "/api/register/namespaces/system/get-cloud-init-config",
        "operationId": CLOUD_INIT_OPERATION_ID,
        "surface": "register",
        "responseSchema": "tokenGetCloudInitConfigResp",
        "role": "issuance",
    }
