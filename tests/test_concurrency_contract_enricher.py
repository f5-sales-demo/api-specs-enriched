"""Provider-wide optimistic-concurrency contract tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.utils.concurrency_contract_enricher import (
    ConcurrencyContractEnricher,
    ConcurrencyContractError,
)


def _operation(operation_id: str, schema: str, *, response: bool = False) -> dict:
    if response:
        return {
            "operationId": operation_id,
            "responses": {
                "200": {
                    "content": {
                        "application/json": {"schema": {"$ref": f"#/components/schemas/{schema}"}}
                    }
                }
            },
        }
    return {
        "operationId": operation_id,
        "requestBody": {
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema}"}}}
        },
        "responses": {},
    }


def _spec() -> dict:
    prefix = "ves.io.schema.example.API"
    return {
        "openapi": "3.0.3",
        "info": {"version": "2.1.224"},
        "paths": {
            "/api/config/namespaces/{metadata.namespace}/examples": {
                "post": _operation(f"{prefix}.Create", "exampleCreateRequest")
            },
            "/api/config/namespaces/{namespace}/examples/{name}": {
                "get": _operation(f"{prefix}.Get", "exampleGetResponse", response=True)
            },
            "/api/config/namespaces/{metadata.namespace}/examples/{metadata.name}": {
                "put": _operation(f"{prefix}.Replace", "exampleReplaceRequest")
            },
        },
        "components": {
            "schemas": {
                "exampleCreateRequest": {"type": "object", "properties": {}},
                "exampleGetResponse": {"type": "object", "properties": {}},
                "exampleReplaceRequest": {"type": "object", "properties": {}},
            }
        },
    }


TOKEN = {
    "type": "string",
    "x-f5xc-concurrency-token": {
        "server_assigned": True,
        "echo_on_operations": ["replace"],
    },
}


def test_discovers_and_enriches_every_standard_config_object_pair() -> None:
    spec = _spec()
    inventory = ConcurrencyContractEnricher().enrich_spec(spec)

    assert inventory["eligible_count"] == 1
    assert inventory["resources"][0]["api_identity"] == "ves.io.schema.example.API"
    schemas = spec["components"]["schemas"]
    assert schemas["exampleGetResponse"]["properties"]["resource_version"] == TOKEN
    assert schemas["exampleReplaceRequest"]["properties"]["resource_version"] == TOKEN
    assert "resource_version" not in schemas["exampleCreateRequest"]["properties"]


@pytest.mark.parametrize(
    ("schema_name", "mutation", "message"),
    [
        ("exampleGetResponse", {"type": "integer"}, "type"),
        ("exampleGetResponse", {"type": "string"}, "metadata"),
        (
            "exampleReplaceRequest",
            {
                "type": "string",
                "x-f5xc-concurrency-token": {
                    "server_assigned": False,
                    "echo_on_operations": ["replace"],
                },
            },
            "server_assigned",
        ),
        (
            "exampleReplaceRequest",
            {
                "type": "string",
                "x-f5xc-concurrency-token": {
                    "server_assigned": True,
                    "echo_on_operations": [],
                },
            },
            "echo_on_operations",
        ),
    ],
)
def test_rejects_every_malformed_existing_token(
    schema_name: str, mutation: dict, message: str
) -> None:
    spec = _spec()
    spec["components"]["schemas"][schema_name]["properties"]["resource_version"] = mutation
    with pytest.raises(ConcurrencyContractError, match=message):
        ConcurrencyContractEnricher().enrich_spec(spec)


def test_rejects_token_in_create_schema() -> None:
    spec = _spec()
    spec["components"]["schemas"]["exampleCreateRequest"]["properties"]["resource_version"] = (
        copy.deepcopy(TOKEN)
    )
    with pytest.raises(ConcurrencyContractError, match="create"):
        ConcurrencyContractEnricher().enrich_spec(spec)


def test_canonical_master_has_complete_deterministic_inventory() -> None:
    spec = json.loads(Path("docs/specifications/api/openapi.json").read_text())
    inventory = ConcurrencyContractEnricher().enrich_spec(spec)

    assert inventory["eligible_count"] >= 161
    assert inventory["covered_count"] == inventory["eligible_count"]
    assert inventory["excluded_count"] == 2
    assert inventory["exclusions"] == [
        {
            "api_identity": "ves.io.schema.registration.API",
            "operation": "Replace",
            "reason": "registration is an enrollment command without a standard Get operation",
        },
        {
            "api_identity": "ves.io.schema.user_group.CustomAPI",
            "operation": "Replace",
            "reason": "custom user-group membership endpoint is not a provider config-object resource",
        },
    ]
    assert inventory["resources"] == sorted(
        inventory["resources"], key=lambda item: item["api_identity"]
    )


def test_provider_bundle_domains_cover_the_master_inventory() -> None:
    """The archived domains consumed by provider codegen carry every token contract."""
    docs = Path("docs/specifications/api")
    master = json.loads((docs / "openapi.json").read_text())
    expected = {
        item["api_identity"]
        for item in ConcurrencyContractEnricher().enrich_spec(master)["resources"]
    }
    covered: set[str] = set()
    for path in sorted(docs.glob("*.json")):
        document = json.loads(path.read_text())
        if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
            continue
        inventory = ConcurrencyContractEnricher().enrich_spec(document)
        covered.update(item["api_identity"] for item in inventory["resources"])

    assert covered == expected
