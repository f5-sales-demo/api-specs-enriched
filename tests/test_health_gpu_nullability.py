"""Only the observed optional GPU field may become nullable in health reads."""

import copy

import pytest
from openapi_schema_validator import OAS30ReadValidator

from scripts.utils.permission_catalog_nullability import qualify_health_gpu_nullability


def fixture():
    return {
        "openapi": "3.0.3",
        "paths": {
            "/health": {
                "get": {
                    "operationId": "ves.io.schema.operate.debug.CustomPublicAPI.HealthPublic",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/debugHealthResponse"}
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "debugHealthResponse": {
                    "type": "object",
                    "required": ["hostname", "state"],
                    "properties": {
                        "hostname": {"type": "string"},
                        "state": {"type": "string", "enum": ["PROVISIONING", "PROVISIONED"]},
                        "os_info": {"$ref": "#/components/schemas/OS"},
                    },
                },
                "OS": {
                    "type": "object",
                    "properties": {
                        "gpu": {"$ref": "#/components/schemas/GPU"},
                        "cpu": {"type": "string"},
                    },
                },
                "GPU": {"type": "object", "properties": {"vendor": {"type": "string"}}},
            }
        },
    }


def valid(doc, raw):
    return OAS30ReadValidator(
        {"components": doc["components"], "$ref": "#/components/schemas/debugHealthResponse"}
    ).is_valid(raw)


def test_null_gpu_and_original_nonnull_types_preserved():
    doc = fixture()
    original = copy.deepcopy(doc)
    raw = {"hostname": "owned", "state": "PROVISIONING", "os_info": {"gpu": None}}
    assert not valid(doc, raw)
    qualify_health_gpu_nullability(doc)
    assert valid(doc, raw)
    assert doc["components"]["schemas"]["OS"] == original["components"]["schemas"]["OS"]
    assert doc["components"]["schemas"]["GPU"] == original["components"]["schemas"]["GPU"]
    raw["os_info"]["gpu"] = {"vendor": "example"}
    assert valid(doc, raw)
    raw["os_info"]["gpu"] = 17
    assert not valid(doc, raw)


@pytest.mark.parametrize(
    ("field", "value"), [("hostname", None), ("state", "FAILED_UNKNOWN"), ("os_info", None)]
)
def test_unqualified_states_and_fields_remain_strict(field, value):
    doc = fixture()
    qualify_health_gpu_nullability(doc)
    raw = {"hostname": "owned", "state": "PROVISIONING", "os_info": {"gpu": None}}
    raw[field] = value
    assert not valid(doc, raw)


def test_missing_gpu_transactional_and_repeat_idempotent():
    doc = fixture()
    qualify_health_gpu_nullability(doc)
    once = copy.deepcopy(doc)
    qualify_health_gpu_nullability(doc)
    assert doc == once
    doc = fixture()
    del doc["components"]["schemas"]["OS"]["properties"]["gpu"]
    original = copy.deepcopy(doc)
    with pytest.raises(ValueError, match="absent"):
        qualify_health_gpu_nullability(doc)
    assert doc == original


def test_owning_enrichment_and_contract_diff_preserve_health_boundary():
    from scripts.contract_diff import run_contract_diff
    from scripts.utils.schema_override_enricher import SchemaOverrideEnricher

    source = fixture()
    enriched = copy.deepcopy(source)
    SchemaOverrideEnricher().enrich_spec(enriched, canonical_only=True)
    assert valid(enriched, {"hostname": "owned", "state": "PROVISIONING", "os_info": {"gpu": None}})
    assert not run_contract_diff(source, enriched)
    enriched["components"]["schemas"]["OS"]["properties"]["cpu"] = {"type": "integer"}
    assert run_contract_diff(source, enriched)
