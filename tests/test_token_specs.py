"""Release-contract tests for site-bound JWT registration tokens."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.utils.smsv2_bootstrap_contract import AWS_BOOTSTRAP

REPO_ROOT = Path(__file__).parent.parent
SPEC_PATHS = (
    REPO_ROOT / "docs" / "specifications" / "api" / "users.json",
    REPO_ROOT / "docs" / "specifications" / "api" / "openapi.json",
)


def test_release_specs_publish_site_bound_jwt_token_fields() -> None:
    expected_type = {
        "type": "integer",
        "format": "int32",
        "description": "Token type, where 0 is NORMAL and 1 is JWT.",
        "enum": [0, 1],
        "default": 0,
        "x-field-mutability": "immutable",
    }
    expected_site_name = {
        "type": "string",
        "description": "Secure Mesh Site v2 name bound into a JWT token.",
        "x-field-mutability": "immutable",
    }
    expected_content = {
        "type": "string",
        "description": "Server-issued JWT registration credential.",
        "readOnly": True,
        "x-f5xc-sensitive": True,
    }

    for spec_path in SPEC_PATHS:
        schemas = json.loads(spec_path.read_text())["components"]["schemas"]
        assert schemas["tokenCreateSpecType"]["properties"] == {
            "type": expected_type,
            "content": expected_content,
            "site_name": expected_site_name,
        }

        for schema_name in ("tokenGetSpecType", "tokenGlobalSpecType"):
            properties = schemas[schema_name]["properties"]
            assert properties["type"] == {
                **{
                    key: value
                    for key, value in expected_type.items()
                    if key != "x-field-mutability"
                },
                "readOnly": True,
            }
            assert properties["site_name"] == {
                **{
                    key: value
                    for key, value in expected_site_name.items()
                    if key != "x-field-mutability"
                },
                "readOnly": True,
            }
            assert properties["content"] == expected_content


def test_jwt_content_is_never_accepted_in_token_create_requests() -> None:
    for spec_path in SPEC_PATHS:
        schemas = json.loads(spec_path.read_text())["components"]["schemas"]
        content = schemas["tokenCreateSpecType"]["properties"]["content"]
        assert content["readOnly"] is True
        assert content["x-f5xc-sensitive"] is True


def test_bootstrap_contract_matches_published_token_and_cloud_init_wire_shapes() -> None:
    spec = json.loads(SPEC_PATHS[0].read_text())
    schemas = spec["components"]["schemas"]
    fields = schemas["tokenCreateSpecType"]["properties"]
    assert AWS_BOOTSTRAP["token"]["request_fields"]["spec.type"] in fields["type"]["enum"]
    assert fields["site_name"]["type"] == "string"
    assert schemas["tokenGetSpecType"]["properties"]["content"]["x-f5xc-sensitive"] is True
    token_path = AWS_BOOTSTRAP["token"]["path"].replace("/system/", "/{metadata.namespace}/")
    assert "post" in spec["paths"][token_path]
    cloud_init = AWS_BOOTSTRAP["cloud_init"]
    operation = spec["paths"][cloud_init["path"]][cloud_init["method"].lower()]
    assert operation["operationId"] == cloud_init["operation_id"]
    assert operation["x-f5xc-operation-role"] == cloud_init["operation_role"]
    assert set(cloud_init["query_fields"]) == {item["name"] for item in operation["parameters"]}
    response = schemas["tokenGetCloudInitConfigResp"]["properties"][cloud_init["response_path"]]
    assert response["x-f5xc-sensitive"] is True
