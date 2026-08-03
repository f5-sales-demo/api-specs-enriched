# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Strictness and atomicity tests for server-default enrichment."""

import copy
import json
import re
from pathlib import Path

import pytest
import yaml
from openapi_spec_validator import validate

from scripts.utils.default_value_enricher import (
    DefaultValueConfigError,
    DefaultValueEnricher,
)
from scripts.utils.extension_constants import (
    X_F5XC_SERVER_DEFAULT,
    X_F5XC_SERVER_DEFAULT_VALUE,
)
from scripts.utils.minimal_defaults_exporter import MinimalDefaultsExporter


def _config(*, resources=None, settings=None):
    return {
        "version": "1.0.0",
        "description": "Focused test configuration",
        "settings": settings
        or {
            "use_openapi_default": True,
            "add_marker_extension": True,
        },
        "resources": resources or {},
    }


def _write_config(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "discovered_defaults.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(extra=True), "unknown=['extra']"),
        (lambda value: value["settings"].pop("use_openapi_default"), "missing"),
        (
            lambda value: value["settings"].update(use_openapi_default="true"),
            "must be a boolean",
        ),
        (
            lambda value: value["resources"].update(
                item={
                    "description": "Item",
                    "schema_pattern": "item.*SpecType",
                    "unexpected": {},
                },
            ),
            "unknown=['unexpected']",
        ),
        (
            lambda value: value["resources"].update(
                item={
                    "description": "Item",
                    "schema_pattern": "item.*SpecType",
                    "nested": {"child": {"legacy_field": None}},
                },
            ),
            "unknown=['legacy_field']",
        ),
        (
            lambda value: value["resources"].update(
                item={"description": "Item", "schema_pattern": "[invalid"},
            ),
            "invalid regex",
        ),
    ],
)
def test_config_schema_is_strict_and_regexes_must_compile(tmp_path, mutate, message):
    config = _config()
    mutate(config)

    with pytest.raises(DefaultValueConfigError, match=re.escape(message)):
        DefaultValueEnricher(config_path=_write_config(tmp_path, config))


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("resources: [", "malformed YAML"),
        (
            """\
version: "1.0.0"
description: test
settings:
  use_openapi_default: true
  use_openapi_default: false
  add_marker_extension: true
resources: {}
""",
            "duplicate key 'use_openapi_default'",
        ),
        ("- not\n- a\n- mapping\n", "must contain a YAML mapping"),
    ],
)
def test_malformed_duplicate_and_nonmapping_yaml_are_rejected(tmp_path, contents, message):
    path = tmp_path / "discovered_defaults.yaml"
    path.write_text(contents)

    with pytest.raises(DefaultValueConfigError, match=re.escape(message)):
        DefaultValueEnricher(config_path=path)


def test_config_file_is_required(tmp_path):
    path = tmp_path / "missing.yaml"

    with pytest.raises(DefaultValueConfigError, match="cannot be read"):
        DefaultValueEnricher(config_path=path)


def test_failed_reload_leaves_previous_configuration_unchanged(tmp_path):
    path = _write_config(
        tmp_path,
        _config(
            resources={
                "item": {"description": "Item", "schema_pattern": "item.*SpecType"},
            },
        ),
    )
    enricher = DefaultValueEnricher(config_path=path)
    original_config = copy.deepcopy(enricher.config)
    original_resources = copy.deepcopy(enricher.resources)
    original_settings = copy.deepcopy(enricher.settings)
    original_patterns = dict(enricher._compiled_patterns)
    _write_config(
        tmp_path,
        _config(
            resources={
                "item": {"description": "Item", "schema_pattern": "[invalid"},
            },
        ),
    )

    with pytest.raises(DefaultValueConfigError, match="invalid regex"):
        enricher._load_config()

    assert enricher.config == original_config
    assert enricher.resources == original_resources
    assert enricher.settings == original_settings
    assert enricher._compiled_patterns == original_patterns


def test_all_matching_resource_configs_are_applied(tmp_path):
    path = _write_config(
        tmp_path,
        _config(
            resources={
                "first": {
                    "description": "First",
                    "schema_pattern": "item.*SpecType",
                    "defaults": {"first_value": "first"},
                },
                "second": {
                    "description": "Second",
                    "schema_pattern": "item.*SpecType",
                    "defaults": {"second_value": "second"},
                },
            },
        ),
    )
    original = {
        "components": {
            "schemas": {
                "itemCreateSpecType": {
                    "type": "object",
                    "properties": {
                        "first_value": {"type": "string"},
                        "second_value": {"type": "string"},
                    },
                },
            },
        },
    }

    enriched = DefaultValueEnricher(config_path=path).enrich_spec(original)

    properties = enriched["components"]["schemas"]["itemCreateSpecType"]["properties"]
    assert properties["first_value"]["default"] == "first"
    assert properties["second_value"]["default"] == "second"
    assert (
        "default"
        not in original["components"]["schemas"]["itemCreateSpecType"]["properties"]["first_value"]
    )


def test_conflicting_overlapping_resource_configs_are_rejected(tmp_path):
    path = _write_config(
        tmp_path,
        _config(
            resources={
                "first": {
                    "description": "First",
                    "schema_pattern": "item.*SpecType",
                    "defaults": {"value": "first"},
                },
                "second": {
                    "description": "Second",
                    "schema_pattern": "item.*SpecType",
                    "defaults": {"value": "second"},
                },
            },
        ),
    )
    spec = {
        "components": {
            "schemas": {
                "itemCreateSpecType": {"properties": {"value": {"type": "string"}}},
            },
        },
    }

    with pytest.raises(
        DefaultValueConfigError,
        match=re.escape("has ambiguous 'defaults.value'"),
    ):
        DefaultValueEnricher(config_path=path).enrich_spec(spec)


def test_boolean_and_integer_defaults_are_distinct_conflicting_contracts(tmp_path):
    path = _write_config(
        tmp_path,
        _config(
            resources={
                "boolean": {
                    "description": "Boolean",
                    "schema_pattern": "item.*SpecType",
                    "defaults": {"value": True},
                },
                "integer": {
                    "description": "Integer",
                    "schema_pattern": "item.*SpecType",
                    "defaults": {"value": 1},
                },
            },
        ),
    )
    spec = {
        "components": {
            "schemas": {
                "itemCreateSpecType": {"properties": {"value": {"type": "boolean"}}},
            },
        },
    }

    with pytest.raises(
        DefaultValueConfigError,
        match=re.escape("ambiguous 'defaults.value'"),
    ):
        DefaultValueEnricher(config_path=path).enrich_spec(spec)


def test_distinct_resource_matches_cannot_overwrite_one_shared_target(tmp_path):
    path = _write_config(
        tmp_path,
        _config(
            resources={
                "first": {
                    "description": "First",
                    "schema_pattern": "^FirstSpecType$",
                    "nested": {"shared": {"defaults": {"value": True}}},
                },
                "second": {
                    "description": "Second",
                    "schema_pattern": "^SecondSpecType$",
                    "nested": {"shared": {"defaults": {"value": 1}}},
                },
            },
        ),
    )
    spec = {
        "components": {
            "schemas": {
                "FirstSpecType": {
                    "properties": {
                        "shared": {"$ref": "#/components/schemas/Shared"},
                    },
                },
                "SecondSpecType": {
                    "properties": {
                        "shared": {"$ref": "#/components/schemas/Shared"},
                    },
                },
                "Shared": {"properties": {"value": {"type": "boolean"}}},
            },
        },
    }
    snapshot = copy.deepcopy(spec)

    with pytest.raises(DefaultValueConfigError, match="existing OpenAPI default conflicts"):
        DefaultValueEnricher(config_path=path).enrich_spec(spec)

    assert spec == snapshot


def test_configured_noop_is_rejected_without_mutation(tmp_path):
    path = _write_config(
        tmp_path,
        _config(
            resources={
                "item": {
                    "description": "Item",
                    "schema_pattern": "item.*SpecType",
                    "defaults": {"missing": "server"},
                },
            },
        ),
    )
    spec = {
        "components": {
            "schemas": {
                "itemCreateSpecType": {"properties": {"present": {"type": "string"}}},
            },
        },
    }
    snapshot = copy.deepcopy(spec)

    with pytest.raises(DefaultValueConfigError, match="1 configured enrichment entries"):
        DefaultValueEnricher(config_path=path).enrich_spec(spec)

    assert spec == snapshot


def test_domain_partition_reports_but_defers_cross_domain_noop(tmp_path):
    path = _write_config(
        tmp_path,
        _config(
            resources={
                "item": {
                    "description": "Item",
                    "schema_pattern": "item.*SpecType",
                    "defaults": {"field_from_another_partition": "server"},
                },
            },
        ),
    )
    spec = {
        "info": {"x-f5xc-cli-domain": "partition"},
        "components": {
            "schemas": {
                "itemCreateSpecType": {"properties": {"local": {"type": "string"}}},
            },
        },
    }
    enricher = DefaultValueEnricher(config_path=path)

    enricher.enrich_spec(spec, allow_partition_residuals=True)

    assert enricher.get_stats()["unapplied_config_entries"] == [
        "item.defaults.field_from_another_partition"
    ]
    assert enricher.get_stats()["partition_residual_config_entries"] == [
        "item.defaults.field_from_another_partition"
    ]


def test_document_domain_marker_does_not_bypass_master_reachability(tmp_path):
    path = _write_config(
        tmp_path,
        _config(
            resources={
                "item": {
                    "description": "Item",
                    "schema_pattern": "item.*SpecType",
                    "defaults": {"missing": "server"},
                },
            },
        ),
    )
    spec = {
        "info": {"x-f5xc-cli-domain": "plausible_but_unauthorised"},
        "components": {
            "schemas": {"itemCreateSpecType": {"properties": {"present": {}}}},
        },
    }

    with pytest.raises(DefaultValueConfigError, match="do not reach"):
        DefaultValueEnricher(config_path=path).enrich_spec(spec)


@pytest.mark.parametrize("domain", [True, 1, "", "Not_A_Domain"])
def test_partition_residual_authority_requires_exact_domain_identity(tmp_path, domain):
    path = _write_config(tmp_path, _config())
    spec = {
        "info": {"x-f5xc-cli-domain": domain},
        "components": {"schemas": {}},
    }

    with pytest.raises(DefaultValueConfigError, match="exact x-f5xc-cli-domain"):
        DefaultValueEnricher(config_path=path).enrich_spec(
            spec,
            allow_partition_residuals=True,
        )


def test_unbound_oneof_recommendation_is_not_counted_as_reachable(tmp_path):
    path = _write_config(
        tmp_path,
        _config(
            resources={
                "item": {
                    "description": "Item",
                    "schema_pattern": "^ItemSpecType$",
                    "oneof_recommended": {"missing_group": "missing_variant"},
                },
            },
        ),
    )
    spec = {
        "components": {
            "schemas": {"ItemSpecType": {"type": "object", "properties": {}}},
        },
    }

    with pytest.raises(DefaultValueConfigError, match="do not reach"):
        DefaultValueEnricher(config_path=path).enrich_spec(spec)


def test_oneof_recommendation_must_name_a_declared_variant(tmp_path):
    path = _write_config(
        tmp_path,
        _config(
            resources={
                "item": {
                    "description": "Item",
                    "schema_pattern": "^ItemSpecType$",
                    "oneof_recommended": {"choice": "missing"},
                },
            },
        ),
    )
    spec = {
        "components": {
            "schemas": {
                "ItemSpecType": {
                    "type": "object",
                    "x-ves-oneof-field-choice": '["first", "second"]',
                    "properties": {},
                },
            },
        },
    }

    with pytest.raises(DefaultValueConfigError, match="is not declared"):
        DefaultValueEnricher(config_path=path).enrich_spec(spec)


def test_array_allof_target_is_reachable_and_enrichment_is_idempotent(tmp_path):
    path = _write_config(
        tmp_path,
        _config(
            resources={
                "item": {
                    "description": "Item",
                    "schema_pattern": "^ItemSpecType$",
                    "nested": {"items": {"defaults": {"measured": None}}},
                },
            },
        ),
    )
    spec = {
        "components": {
            "schemas": {
                "ItemSpecType": {
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "allOf": [{"$ref": "#/components/schemas/Item"}],
                            },
                        },
                    },
                },
                "Item": {"properties": {"measured": {"type": "string"}}},
            },
        },
    }
    enricher = DefaultValueEnricher(config_path=path)

    first = enricher.enrich_spec(spec)
    first_stats = enricher.get_stats()
    second = enricher.enrich_spec(first)

    assert second == first
    assert enricher.get_stats() == first_stats
    measured = second["components"]["schemas"]["Item"]["properties"]["measured"]
    assert measured[X_F5XC_SERVER_DEFAULT_VALUE] == {"type": "null", "value": None}
    assert "default" not in measured
    assert first_stats["configured_entries"] == first_stats["applied_config_entries"] == 1


def test_ambiguous_allof_targets_are_rejected_atomically(tmp_path):
    path = _write_config(
        tmp_path,
        _config(
            resources={
                "item": {
                    "description": "Item",
                    "schema_pattern": "^ItemSpecType$",
                    "nested": {"choice": {"defaults": {"value": "measured"}}},
                },
            },
        ),
    )
    spec = {
        "components": {
            "schemas": {
                "ItemSpecType": {
                    "properties": {
                        "choice": {
                            "allOf": [
                                {"$ref": "#/components/schemas/First"},
                                {"$ref": "#/components/schemas/Second"},
                            ],
                        },
                    },
                },
                "First": {"properties": {"value": {"type": "string"}}},
                "Second": {"properties": {"value": {"type": "string"}}},
            },
        },
    }
    snapshot = copy.deepcopy(spec)

    with pytest.raises(DefaultValueConfigError, match="ambiguous reference targets"):
        DefaultValueEnricher(config_path=path).enrich_spec(spec)

    assert spec == snapshot


def test_enrichment_failure_is_atomic_and_propagates(tmp_path, monkeypatch):
    path = _write_config(
        tmp_path,
        _config(
            resources={
                "item": {
                    "description": "Item",
                    "schema_pattern": "item.*SpecType",
                    "defaults": {"defaulted": "server"},
                    "recommended": {"required": "client"},
                },
            },
        ),
    )
    enricher = DefaultValueEnricher(config_path=path)
    original = {
        "components": {
            "schemas": {
                "itemCreateSpecType": {
                    "properties": {
                        "defaulted": {"type": "string"},
                        "required": {"type": "string"},
                    },
                },
            },
        },
    }
    original_snapshot = copy.deepcopy(original)
    stats_snapshot = copy.deepcopy(enricher.get_stats())

    def fail_after_defaults(*_args, **_kwargs):
        raise RuntimeError("injected enrichment failure")

    monkeypatch.setattr(enricher, "_apply_recommended_to_properties", fail_after_defaults)

    with pytest.raises(RuntimeError, match="injected enrichment failure"):
        enricher.enrich_spec(original)

    assert original == original_snapshot
    assert enricher.get_stats() == stats_snapshot


def _enrich_published_spec():
    repository = Path(__file__).parents[1]
    enricher = DefaultValueEnricher(config_path=repository / "config" / "discovered_defaults.yaml")
    spec = json.loads((repository / "docs/specifications/api/openapi.json").read_text())
    return enricher, enricher.enrich_spec(spec)


def test_all_current_null_measurements_are_validator_safe():
    enricher, enriched = _enrich_published_spec()

    validate(enriched)
    properties = [
        prop
        for schema in enriched["components"]["schemas"].values()
        for prop in schema.get("properties", {}).values()
        if isinstance(prop, dict)
    ]
    typed_nulls = [prop for prop in properties if X_F5XC_SERVER_DEFAULT_VALUE in prop]
    assert len(typed_nulls) == 26
    for owner in typed_nulls:
        assert "default" not in owner
        assert owner[X_F5XC_SERVER_DEFAULT] is True
        assert owner[X_F5XC_SERVER_DEFAULT_VALUE] == {"type": "null", "value": None}
    assert all(prop.get("default", object()) is not None for prop in properties)
    stats = enricher.get_stats()
    assert stats["configured_entries"] == stats["applied_config_entries"]


def test_every_configured_enrichment_reaches_the_published_spec():
    """Keep measured defaults and recommendations from becoming silent no-ops."""
    enricher, enriched = _enrich_published_spec()

    stats = enricher.get_stats()
    assert stats["configured_entries"] == stats["applied_config_entries"] == 352
    assert stats["unapplied_config_entries"] == []
    artifact = MinimalDefaultsExporter(
        Path(__file__).parents[1] / "config" / "discovered_defaults.yaml"
    ).build(enriched["components"]["schemas"], version="verification")
    assert set(artifact["resources"]) == set(enricher.compiled_patterns)
