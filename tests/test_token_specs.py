"""Release-contract tests for site-bound JWT registration tokens."""

from __future__ import annotations

import json
from pathlib import Path

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
