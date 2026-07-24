"""Tests for SchemaOverrideEnricher."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from scripts.utils.schema_override_enricher import SchemaOverrideEnricher


@pytest.fixture
def config_path():
    return Path(__file__).parent.parent / "config" / "schema_overrides.yaml"


@pytest.fixture
def enricher(config_path):
    return SchemaOverrideEnricher(config_path=config_path)


@pytest.fixture
def synthetic_config(tmp_path):
    """Config with a synthetic override for testing enricher behavior."""
    config = {
        "version": "1.0.0",
        "overrides": {
            "test_resource": {
                "upstream_issue": "test#1",
                "schemas": [
                    {
                        "pattern": "testResource(Create|Get)SpecType",
                        "oneof_group": "variant_choice",
                        "complete_variants": ["variant_a", "variant_b", "variant_c"],
                        "inject_properties": {
                            "variant_c": {"$ref": "#/components/schemas/emptySchema"},
                        },
                    },
                ],
            },
        },
    }
    config_file = tmp_path / "schema_overrides.yaml"
    with config_file.open("w") as f:
        yaml.dump(config, f)
    return config_file


@pytest.fixture
def synthetic_enricher(synthetic_config):
    return SchemaOverrideEnricher(config_path=synthetic_config)


@pytest.fixture
def test_spec():
    """Spec with 2-variant schemas for synthetic override testing."""
    base_props = {
        "variant_a": {"$ref": "#/components/schemas/typeA"},
        "variant_b": {"$ref": "#/components/schemas/typeB"},
        "other_field": {"type": "string"},
    }
    base_ext = {
        "x-ves-oneof-field-variant_choice": ["variant_a", "variant_b"],
    }

    def make_schema():
        return {
            "type": "object",
            "properties": dict(base_props),
            **{k: list(v) for k, v in base_ext.items()},
        }

    return {
        "components": {
            "schemas": {
                "testResourceCreateSpecType": make_schema(),
                "testResourceGetSpecType": make_schema(),
                "emptySchema": {"type": "object"},
            },
        },
    }


class TestSchemaOverrideEnricher:
    """Core enricher behavior with synthetic overrides."""

    def test_injects_missing_properties(self, synthetic_enricher, test_spec):
        result = synthetic_enricher.enrich_spec(test_spec)
        schema = result["components"]["schemas"]["testResourceCreateSpecType"]
        assert "variant_c" in schema["properties"]
        assert schema["properties"]["variant_c"] == {"$ref": "#/components/schemas/emptySchema"}

    def test_updates_oneof_extension_array(self, synthetic_enricher, test_spec):
        result = synthetic_enricher.enrich_spec(test_spec)
        for schema_name in ["testResourceCreateSpecType", "testResourceGetSpecType"]:
            schema = result["components"]["schemas"][schema_name]
            variants = schema["x-ves-oneof-field-variant_choice"]
            assert len(variants) == 3
            assert "variant_c" in variants

    def test_preserves_existing_properties(self, synthetic_enricher, test_spec):
        result = synthetic_enricher.enrich_spec(test_spec)
        schema = result["components"]["schemas"]["testResourceCreateSpecType"]
        assert "variant_a" in schema["properties"]
        assert "other_field" in schema["properties"]

    def test_preserves_existing_variants_in_extension(self, synthetic_enricher, test_spec):
        result = synthetic_enricher.enrich_spec(test_spec)
        schema = result["components"]["schemas"]["testResourceCreateSpecType"]
        variants = schema["x-ves-oneof-field-variant_choice"]
        for existing in ["variant_a", "variant_b"]:
            assert existing in variants

    def test_does_not_duplicate_existing_variants(self, synthetic_enricher, test_spec):
        result = synthetic_enricher.enrich_spec(test_spec)
        schema = result["components"]["schemas"]["testResourceCreateSpecType"]
        variants = schema["x-ves-oneof-field-variant_choice"]
        assert len(variants) == len(set(variants))

    def test_skips_non_matching_schemas(self, synthetic_enricher, test_spec):
        result = synthetic_enricher.enrich_spec(test_spec)
        empty = result["components"]["schemas"]["emptySchema"]
        assert "x-ves-oneof-field-variant_choice" not in empty

    def test_stats_tracking(self, synthetic_enricher, test_spec):
        synthetic_enricher.enrich_spec(test_spec)
        stats = synthetic_enricher.get_stats()
        assert stats["schemas_processed"] > 0
        assert stats["properties_injected"] == 2  # 1 variant x 2 schema types
        assert stats["oneof_arrays_updated"] == 2

    def test_reset_stats(self, synthetic_enricher, test_spec):
        synthetic_enricher.enrich_spec(test_spec)
        synthetic_enricher.reset_stats()
        stats = synthetic_enricher.get_stats()
        assert stats["schemas_processed"] == 0
        assert stats["properties_injected"] == 0


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_empty_spec(self, enricher):
        result = enricher.enrich_spec({})
        assert result == {}

    def test_spec_without_schemas(self, enricher):
        result = enricher.enrich_spec({"components": {}})
        assert result == {"components": {}}

    def test_no_matching_schemas(self, synthetic_enricher):
        spec = {
            "components": {
                "schemas": {
                    "unrelatedSchema": {
                        "type": "object",
                        "properties": {"foo": {"type": "string"}},
                    },
                },
            },
        }
        result = synthetic_enricher.enrich_spec(spec)
        schema = result["components"]["schemas"]["unrelatedSchema"]
        assert "x-ves-oneof-field-variant_choice" not in schema

    def test_already_complete_spec(self, synthetic_enricher, test_spec):
        """If all variants already present, enricher should be a no-op for that variant."""
        schema = test_spec["components"]["schemas"]["testResourceCreateSpecType"]
        schema["properties"]["variant_c"] = {"$ref": "#/components/schemas/emptySchema"}
        schema["x-ves-oneof-field-variant_choice"].append("variant_c")

        result = synthetic_enricher.enrich_spec(test_spec)
        create_schema = result["components"]["schemas"]["testResourceCreateSpecType"]
        assert len(create_schema["x-ves-oneof-field-variant_choice"]) == 3

    def test_preserves_json_string_encoding(self, synthetic_enricher):
        """When x-ves-oneof-field is a JSON string, output must also be a JSON string."""
        spec = {
            "components": {
                "schemas": {
                    "testResourceCreateSpecType": {
                        "type": "object",
                        "properties": {
                            "variant_a": {"$ref": "#/components/schemas/typeA"},
                        },
                        "x-ves-oneof-field-variant_choice": json.dumps(["variant_a", "variant_b"]),
                    },
                },
            },
        }
        result = synthetic_enricher.enrich_spec(spec)
        schema = result["components"]["schemas"]["testResourceCreateSpecType"]
        ext_value = schema["x-ves-oneof-field-variant_choice"]
        assert isinstance(ext_value, str), f"Expected JSON string, got {type(ext_value)}"
        parsed = json.loads(ext_value)
        assert len(parsed) == 3
        assert "variant_c" in parsed

    def test_empty_overrides_is_noop(self, enricher, test_spec):
        """Real config has no overrides — enricher should not modify any schema."""
        import copy

        original = copy.deepcopy(test_spec)
        result = enricher.enrich_spec(test_spec)
        for schema_name in ["testResourceCreateSpecType", "testResourceGetSpecType"]:
            assert (
                result["components"]["schemas"][schema_name]["properties"]
                == original["components"]["schemas"][schema_name]["properties"]
            )


class TestConfigLoading:
    """Config file loading and validation."""

    def test_loads_real_config(self, config_path):
        enricher = SchemaOverrideEnricher(config_path=config_path)
        assert enricher.overrides is not None

    def test_empty_overrides_config(self, config_path):
        """Real config should have empty overrides dict."""
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert "overrides" in config
        assert config["overrides"] == {} or config["overrides"] is None

    def test_synthetic_config_structure(self, synthetic_config):
        with synthetic_config.open() as f:
            config = yaml.safe_load(f)
        assert "overrides" in config
        tr = config["overrides"]["test_resource"]
        assert "schemas" in tr
        for schema_entry in tr["schemas"]:
            assert "pattern" in schema_entry
            assert "oneof_group" in schema_entry
            assert "complete_variants" in schema_entry
            assert "inject_properties" in schema_entry
            re.compile(schema_entry["pattern"])


def test_oneof_free_injection_with_extension():
    """Override without oneof_group injects a plain property + schema extension,
    and must NOT create any x-ves-oneof-field-* array."""
    enricher = SchemaOverrideEnricher.__new__(SchemaOverrideEnricher)
    enricher.overrides = {
        "reg": {
            "schemas": [
                {
                    "pattern": "^fooReq$",
                    "inject_properties": {"state": {"$ref": "#/components/schemas/barState"}},
                    "inject_extensions": {"x-f5xc-action": "approve"},
                }
            ]
        }
    }
    enricher._compiled = enricher._compile_overrides()
    enricher._stats = enricher._empty_stats()
    spec = {
        "components": {
            "schemas": {"fooReq": {"type": "object", "properties": {"name": {"type": "string"}}}}
        }
    }
    out = enricher.enrich_spec(spec)
    schema = out["components"]["schemas"]["fooReq"]
    assert schema["properties"]["state"] == {"$ref": "#/components/schemas/barState"}
    assert schema["x-f5xc-action"] == "approve"
    assert not any(k.startswith("x-ves-oneof-field-") for k in schema)
