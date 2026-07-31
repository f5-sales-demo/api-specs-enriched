# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for the resource_version enricher.

The F5 XC API implements optimistic concurrency and the specs do not describe it.
Measured against the live tenant:

    resource_version omitted   -> 200, unconditional replace  (what every caller does)
    resource_version current   -> 200, version increments
    resource_version stale     -> 409 RESOURCE_VERSION_MISMATCH, write rejected

The field is returned at the TOP LEVEL of every GET response — confirmed in 12 of 12
captured responses in terraform-provider-xcsh's tools/api-defaults.json — and read from
the top level of the replace request body. It appears zero times in the enriched specs
and zero times upstream, so a generated client cannot send it and every update is an
unconditional overwrite (api-specs-enriched#1159, terraform-provider-xcsh#1399).

It belongs to neither metadata schema: schemaObjectGetMetaType carries
annotations/description/disable/labels/name/namespace, and schemaSystemObjectGetMetaType
carries creation_timestamp/uid/tenant/etc. It is a sibling of metadata and spec.
"""

from __future__ import annotations

from typing import Any

from scripts.utils.resource_version_enricher import ResourceVersionEnricher


def _spec(schemas: dict[str, Any]) -> dict[str, Any]:
    return {"components": {"schemas": schemas}}


def _schemas(spec: dict[str, Any]) -> dict[str, Any]:
    return spec["components"]["schemas"]


def test_adds_resource_version_to_get_response():
    spec = _spec(
        {"siteGetResponse": {"type": "object", "properties": {"metadata": {}, "spec": {}}}}
    )
    out = ResourceVersionEnricher().enrich_spec(spec)
    prop = _schemas(out)["siteGetResponse"]["properties"]["resource_version"]
    assert prop["type"] == "string"


def test_adds_resource_version_to_replace_request():
    spec = _spec(
        {"siteReplaceRequest": {"type": "object", "properties": {"metadata": {}, "spec": {}}}}
    )
    out = ResourceVersionEnricher().enrich_spec(spec)
    assert "resource_version" in _schemas(out)["siteReplaceRequest"]["properties"]


def test_leaves_unrelated_schemas_alone():
    """Only the two shapes whose behaviour was measured are touched.

    CreateRequest has no prior version to guard against, and ReplaceResponse was not
    probed. Adding the field on a guess would document behaviour nobody verified.
    """
    spec = _spec(
        {
            "siteCreateRequest": {"type": "object", "properties": {"spec": {}}},
            "siteReplaceResponse": {"type": "object", "properties": {}},
            "schemaObjectGetMetaType": {"type": "object", "properties": {"name": {}}},
            "siteStatusObject": {"type": "object", "properties": {}},
        },
    )
    out = ResourceVersionEnricher().enrich_spec(spec)
    for name in spec["components"]["schemas"]:
        assert "resource_version" not in (_schemas(out)[name].get("properties") or {}), name


def test_never_marks_the_field_required():
    """Omitting it must keep working — that is what every existing caller does."""
    spec = _spec(
        {"siteReplaceRequest": {"type": "object", "properties": {"spec": {}}, "required": ["spec"]}}
    )
    out = ResourceVersionEnricher().enrich_spec(spec)
    schema = _schemas(out)["siteReplaceRequest"]
    assert "resource_version" not in schema.get("required", [])
    prop = schema["properties"]["resource_version"]
    assert prop.get("x-ves-required") != "true"


def test_does_not_overwrite_an_existing_declaration():
    """If upstream ever ships it, upstream wins — the enricher must not clobber it."""
    upstream = {"type": "string", "description": "upstream text", "x-displayname": "Upstream"}
    spec = _spec(
        {"siteGetResponse": {"type": "object", "properties": {"resource_version": dict(upstream)}}}
    )
    out = ResourceVersionEnricher().enrich_spec(spec)
    assert _schemas(out)["siteGetResponse"]["properties"]["resource_version"] == upstream


def test_is_idempotent():
    spec = _spec({"siteGetResponse": {"type": "object", "properties": {"spec": {}}}})
    enricher = ResourceVersionEnricher()
    once = enricher.enrich_spec(spec)
    twice = ResourceVersionEnricher().enrich_spec(once)
    assert once == twice


def test_creates_properties_when_the_schema_has_none():
    spec = _spec({"siteGetResponse": {"type": "object"}})
    out = ResourceVersionEnricher().enrich_spec(spec)
    assert "resource_version" in _schemas(out)["siteGetResponse"]["properties"]


def test_handles_swagger_two_definitions_layout():
    spec = {"definitions": {"siteGetResponse": {"type": "object", "properties": {}}}}
    out = ResourceVersionEnricher().enrich_spec(spec)
    assert "resource_version" in out["definitions"]["siteGetResponse"]["properties"]


def test_stats_count_what_changed():
    spec = _spec(
        {
            "aGetResponse": {"type": "object", "properties": {}},
            "bReplaceRequest": {"type": "object", "properties": {}},
            "cGetResponse": {
                "type": "object",
                "properties": {"resource_version": {"type": "string"}},
            },
            "dCreateRequest": {"type": "object", "properties": {}},
        },
    )
    enricher = ResourceVersionEnricher()
    enricher.enrich_spec(spec)
    stats = enricher.get_stats()
    assert stats["schemas_stamped"] == 2
    assert stats["schemas_already_declared"] == 1
    assert stats["error_count"] == 0


def test_survives_a_malformed_schema_entry():
    """A non-dict entry must not abort the whole spec."""
    spec = _spec(
        {
            "siteGetResponse": "not-a-schema",
            "otherGetResponse": {"type": "object", "properties": {}},
        }
    )
    out = ResourceVersionEnricher().enrich_spec(spec)
    assert "resource_version" in _schemas(out)["otherGetResponse"]["properties"]
