# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Reject discovered nulls that are invalid request defaults."""

from copy import deepcopy

import pytest
import yaml
from openapi_spec_validator import validate

from scripts.utils.default_value_enricher import DefaultValueEnricher


def _enricher(tmp_path, *, nested=False):
    resource = {"schema_pattern": "^Fixture$", "defaults": {"value": None}}
    if nested:
        resource = {
            "schema_pattern": "^Fixture$",
            "nested": {"child": {"defaults": {"value": None}}},
        }
    config = tmp_path / "defaults.yaml"
    config.write_text(yaml.safe_dump({"resources": {"fixture": resource}}))
    return DefaultValueEnricher(config)


def _spec(value_schema, *, version="3.0.3", nested=False):
    properties = {"value": deepcopy(value_schema)}
    if nested:
        properties = {"child": {"type": "object", "properties": properties}}
    return {
        "openapi": version,
        "info": {"title": "Fixture API", "version": "1.0.0"},
        "paths": {},
        "components": {
            "schemas": {
                "Fixture": {"type": "object", "properties": properties},
                "ObjectValue": {"type": "object"},
            },
        },
    }


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "value_schema",
    [
        {"type": "object"},
        {"allOf": [{"$ref": "#/components/schemas/ObjectValue"}]},
        {"$ref": "#/components/schemas/ObjectValue"},
    ],
)
def test_invalid_null_is_not_promoted(tmp_path, value_schema, nested):
    """Null response evidence must not become an invalid request default."""
    spec = _spec(value_schema, nested=nested)
    before = deepcopy(spec)
    validate(spec)
    enricher = _enricher(tmp_path, nested=nested)
    enricher.enrich_spec(spec)
    assert spec == before
    assert enricher.stats.null_defaults_rejected == 1
    assert len(enricher.stats.rejected_null_defaults) == 1
    validate(spec)


@pytest.mark.parametrize(
    ("version", "value_schema"),
    [
        ("3.0.3", {"type": "object", "nullable": True}),
        ("3.1.0", {"type": ["object", "null"]}),
    ],
)
def test_valid_nullable_default_preserved(tmp_path, version, value_schema):
    """Explicit nullable request schemas retain supported null defaults."""
    spec = _spec(value_schema, version=version)
    enricher = _enricher(tmp_path)
    enricher.enrich_spec(spec)
    prop = spec["components"]["schemas"]["Fixture"]["properties"]["value"]
    assert "default" in prop
    assert prop["default"] is None
    assert prop["x-f5xc-server-default"] is True
    validate(spec)


def test_invalid_discovery_does_not_replace_valid_existing_default(tmp_path):
    """Rejecting discovery preserves an existing valid source annotation."""
    spec = _spec({"type": "object", "default": {}})
    before = deepcopy(spec)
    _enricher(tmp_path).enrich_spec(spec)
    assert spec == before
    validate(spec)


@pytest.mark.parametrize("ref", ["#/components/schemas/Missing", "https://example.invalid/schema"])
def test_unresolvable_reference_rejected_without_network(tmp_path, monkeypatch, ref):
    """Incomplete or foreign schema references cannot trigger remote retrieval."""
    connections = []

    def record_connection(_socket, address):
        connections.append(address)
        raise AssertionError("Unexpected network retrieval")

    monkeypatch.setattr("socket.socket.connect", record_connection)
    spec = _spec({"allOf": [{"$ref": ref}]})
    before = deepcopy(spec)
    enricher = _enricher(tmp_path)
    enricher.enrich_spec(spec)
    assert spec == before
    assert not connections
    assert enricher.stats.null_defaults_rejected == 1
