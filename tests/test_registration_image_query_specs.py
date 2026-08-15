"""Release-contract tests for the Customer Edge image query."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SPEC_PATH = REPO_ROOT / "docs" / "specifications" / "api" / "ce_management.json"
CATALOG_PATH = REPO_ROOT / "release" / "api-catalog.json"
OPERATION_ID = "ves.io.schema.registration.CustomAPI.GetImageDownloadUrl"


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
    assert response["x-f5xc-terraform-resource"] == "xcsh_site_image"
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
