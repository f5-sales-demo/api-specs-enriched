# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Fail-closed optimistic-concurrency contract declaration tests."""

from __future__ import annotations

import copy

import pytest

from scripts.utils.resource_version_enricher import (
    ResourceVersionContractError,
    declare_resource_versions,
)


def _document(schemas: dict) -> dict:
    return {
        "openapi": "3.0.0",
        "components": {"schemas": schemas},
    }


def test_declares_only_measured_read_and_replace_shapes_without_mutating_input():
    documents = {
        "alpha.json": _document(
            {
                "alphaGetResponse": {"type": "object", "properties": {"spec": {}}},
                "alphaReplaceRequest": {
                    "type": "object",
                    "properties": {"spec": {}},
                    "required": ["spec"],
                },
                "alphaCreateRequest": {"type": "object", "properties": {}},
                "alphaReplaceResponse": {"type": "object", "properties": {}},
            }
        )
    }
    original = copy.deepcopy(documents)

    declared, accounting = declare_resource_versions(documents)

    assert documents == original
    assert declared is not documents
    schemas = declared["alpha.json"]["components"]["schemas"]
    expected = {
        "type": "string",
        "title": "resource_version",
        "description": (
            "Opaque token identifying this version of the object. Send the token returned "
            "by a read on a replace to make the write conditional. If the object changed "
            "after it was read, the API rejects the replace with 409 "
            "RESOURCE_VERSION_MISMATCH instead of overwriting the newer object. Omit the "
            "token only when an unconditional replace is intentional."
        ),
        "x-displayname": "Resource Version",
        "x-f5xc-description-short": "Optimistic-concurrency token for conditional replace.",
        "x-f5xc-example": "opaque-version-token",
    }
    assert schemas["alphaGetResponse"]["properties"]["resource_version"] == expected
    assert schemas["alphaReplaceRequest"]["properties"]["resource_version"] == expected
    assert "resource_version" not in schemas["alphaReplaceRequest"]["required"]
    assert "resource_version" not in schemas["alphaCreateRequest"]["properties"]
    assert "resource_version" not in schemas["alphaReplaceResponse"]["properties"]
    assert accounting.to_dict() == {
        "get_responses": 1,
        "replace_requests": 1,
        "total": 2,
    }


@pytest.mark.parametrize(
    ("schemas", "message"),
    [
        ({"alphaGetResponse": "invalid"}, "alphaGetResponse.*schema object"),
        (
            {"alphaGetResponse": {"type": "string", "properties": {}}},
            "alphaGetResponse.*type.*object",
        ),
        (
            {"alphaGetResponse": {"type": "object"}},
            "alphaGetResponse.*properties.*object",
        ),
        (
            {"alphaGetResponse": {"type": "object", "properties": []}},
            "alphaGetResponse.*properties.*object",
        ),
        (
            {
                "alphaGetResponse": {
                    "type": "object",
                    "properties": {"resource_version": {"type": "string"}},
                }
            },
            "already declares resource_version",
        ),
        (
            {
                "alphaGetResponse": {
                    "type": "object",
                    "properties": {},
                    "required": "resource_version",
                }
            },
            "required must be an array",
        ),
    ],
)
def test_malformed_or_predeclared_target_contract_fails_atomically(schemas, message):
    documents = {
        "valid.json": _document({"validGetResponse": {"type": "object", "properties": {}}}),
        "invalid.json": _document(schemas),
    }
    original = copy.deepcopy(documents)

    with pytest.raises(ResourceVersionContractError, match=message):
        declare_resource_versions(documents)

    assert documents == original


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"openapi": "3.0.0"},
        {"openapi": "3.0.0", "components": []},
        {"openapi": "3.0.0", "components": {"schemas": []}},
        {"swagger": "2.0", "definitions": {}},
    ],
)
def test_requires_openapi_three_components_schemas(document):
    with pytest.raises(ResourceVersionContractError, match=r"components\.schemas"):
        declare_resource_versions({"invalid.json": document})


def test_current_raw_source_accounting_is_exact():
    import json
    from pathlib import Path

    from scripts.utils.source_graph_validator import source_spec_files

    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in source_spec_files(Path("specs/original"))
    }

    declared, accounting = declare_resource_versions(documents)

    assert accounting.to_dict() == {
        "get_responses": 202,
        "replace_requests": 167,
        "total": 369,
    }
    assert (
        sum(
            "resource_version" in schema["properties"]
            for document in declared.values()
            for name, schema in document["components"]["schemas"].items()
            if name.endswith(("GetResponse", "ReplaceRequest"))
        )
        == 369
    )
