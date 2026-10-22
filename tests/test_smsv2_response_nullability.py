"""Response-only null qualification must preserve strict request/identity checks."""

import copy

import pytest
from jsonschema.exceptions import ValidationError
from openapi_schema_validator import OAS30ReadValidator

from scripts.utils.smsv2_response_nullability import (
    PATHS,
    PREFIX,
    ROOT,
    qualify_smsv2_get_nullability,
)


def ref(name):
    return {"allOf": [{"$ref": PREFIX + name}]}


def fixture():
    optional = {"type": "object", "properties": {"enabled": {"type": "boolean"}}}
    spec_fields = {
        path[1]: ref("Optional") for path in PATHS if path[0] == "spec" and len(path) == 2
    }
    spec_fields["software_settings"] = ref("Software")
    spec_fields["azure"] = {"type": "object"}
    return {
        "openapi": "3.0.3",
        "paths": {
            "/site": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": ref("Request")}}}
                },
                "get": {
                    "operationId": "ves.io.schema.views.securemesh_site_v2.API.Get",
                    "responses": {"200": {"content": {"application/json": {"schema": ref(ROOT)}}}},
                },
            }
        },
        "components": {
            "schemas": {
                ROOT: {
                    "type": "object",
                    "properties": {
                        "create_form": ref("Request"),
                        "replace_form": ref("Request"),
                        "spec": ref("Spec"),
                        "system_metadata": ref("Meta"),
                    },
                    "required": ["system_metadata"],
                },
                "Request": {
                    "type": "object",
                    "properties": {"setting": ref("Optional")},
                },
                "Optional": optional,
                "Spec": {"type": "object", "properties": spec_fields},
                "Software": {"type": "object", "properties": {"waf_signatures": ref("Optional")}},
                "Meta": {
                    "type": "object",
                    "properties": {
                        "uid": {"type": "string", "minLength": 1},
                        "deletion_timestamp": {"type": "string", "format": "date-time"},
                        "initializers": ref("Optional"),
                        "owner_view": ref("Optional"),
                    },
                    "required": ["uid"],
                },
            }
        },
    }


def response():
    value = {"spec": {"software_settings": {}}, "system_metadata": {"uid": "owned-site-uid"}}
    for path in PATHS:
        target = value
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = None
    return value


def validator(document, name=ROOT):
    return OAS30ReadValidator({"components": document["components"], "$ref": PREFIX + name})


def test_all_observed_optional_null_paths_accept_without_changing_requests_or_shared_shapes():
    document = fixture()
    original = copy.deepcopy(document)
    raw = response()
    before = list(validator(document).iter_errors(raw))
    assert {tuple(error.absolute_path) for error in before} == set(PATHS)
    qualify_smsv2_get_nullability(document)
    validator(document).validate(raw)
    assert raw == response()
    for name, schema in original["components"]["schemas"].items():
        if name != ROOT:
            assert document["components"]["schemas"][name] == schema
    assert document["paths"] == original["paths"]
    with pytest.raises(ValidationError):
        validator(document, "Request").validate({"setting": None})
    validator(document, "Request").validate({"setting": {"enabled": True}})


@pytest.mark.parametrize("path", PATHS)
def test_qualified_fields_keep_original_nonnull_types_and_reject_malformed_values(path):
    document = fixture()
    qualify_smsv2_get_nullability(document)
    value = response()
    target = value
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = (
        "2026-10-22T15:39:57Z" if path[-1] == "deletion_timestamp" else {"enabled": True}
    )
    validator(document).validate(value)
    target[path[-1]] = 123
    assert not validator(document).is_valid(value)


@pytest.mark.parametrize("uid", [None, "", 17])
def test_required_owned_identity_remains_strict(uid):
    document = fixture()
    qualify_smsv2_get_nullability(document)
    value = response()
    value["system_metadata"]["uid"] = uid
    assert not validator(document).is_valid(value)


def test_idempotence_and_failed_declaration_leave_graph_unchanged():
    document = fixture()
    qualify_smsv2_get_nullability(document)
    once = copy.deepcopy(document)
    qualify_smsv2_get_nullability(document)
    assert document == once
    missing = fixture()
    del missing["components"]["schemas"][ROOT]["properties"]["create_form"]
    original = copy.deepcopy(missing)
    with pytest.raises(ValueError, match="absent or ambiguous"):
        qualify_smsv2_get_nullability(missing)
    assert missing == original


def test_rejects_direct_or_indirect_request_reachability():
    for request in (ROOT, "Wrapper"):
        document = fixture()
        document["components"]["schemas"]["Wrapper"] = {
            "type": "object",
            "properties": {"site": ref(ROOT)},
        }
        document["paths"]["/site"]["post"]["requestBody"]["content"]["application/json"][
            "schema"
        ] = ref(request)
        original = copy.deepcopy(document)
        with pytest.raises(ValueError, match="request schema"):
            qualify_smsv2_get_nullability(document)
        assert document == original


def test_owning_enrichment_pass_applies_only_to_the_qualified_get_operation():
    from scripts.utils.schema_override_enricher import SchemaOverrideEnricher

    document = fixture()
    enricher = SchemaOverrideEnricher()
    enricher.enrich_spec(document, corrections_only=True)
    assert not validator(document).is_valid(response())
    enricher.enrich_spec(document, canonical_only=True)
    validator(document).validate(response())
    unrelated = fixture()
    unrelated["paths"]["/site"]["get"]["operationId"] = "unrelated.API.Get"
    original = copy.deepcopy(unrelated)
    qualify_smsv2_get_nullability(unrelated)
    assert unrelated == original


def test_contract_diff_accepts_exact_response_qualification_without_mutating_source():
    from scripts.contract_diff import run_contract_diff

    source = fixture()
    original = copy.deepcopy(source)
    enriched = copy.deepcopy(source)
    qualify_smsv2_get_nullability(enriched)
    assert not run_contract_diff(source, enriched)
    assert source == original


@pytest.mark.parametrize("target", ["request", "identity", "unqualified-response"])
def test_contract_diff_still_rejects_changes_outside_exact_null_qualification(target):
    from scripts.contract_diff import run_contract_diff

    source = fixture()
    enriched = copy.deepcopy(source)
    qualify_smsv2_get_nullability(enriched)
    schemas = enriched["components"]["schemas"]
    if target == "request":
        schemas["Request"]["properties"]["setting"] = {"type": "string"}
    elif target == "identity":
        clone = next(name for name in schemas if name.startswith("Meta__smsv2_read_"))
        schemas[clone]["properties"]["uid"]["type"] = "integer"
    else:
        clone = next(name for name in schemas if name.startswith("Spec__smsv2_read_"))
        schemas[clone]["properties"]["azure"]["type"] = "string"
    assert run_contract_diff(source, enriched)
