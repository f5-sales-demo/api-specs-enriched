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

    def test_registration_approval_override(self, config_path):
        """Real config carries the registration_approval override that injects a
        top-level `state` ($ref registrationObjectState) and the schema-level
        x-f5xc-action marker onto registrationApprovalReq (S6-A, #1206)."""
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert "overrides" in config
        entry = config["overrides"]["registration_approval"]
        schema_entry = entry["schemas"][0]
        assert schema_entry["pattern"] == "^registrationApprovalReq$"
        assert schema_entry["inject_properties"]["state"] == {
            "$ref": "#/components/schemas/registrationObjectState",
        }
        assert schema_entry["inject_extensions"]["x-f5xc-action"] == "approve"

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


# ---------------------------------------------------------------------------
# #1142: x-ves-required is F5's own upstream marker (2586 occurrences in
# api-specs) and it is wrong in both directions. Correcting it is the whole
# purpose of this repository, but the enricher could previously only ADD a
# missing property or a missing schema-level extension — it had no way to touch
# an extension on a property that already exists, which is what both corrections
# need.
# ---------------------------------------------------------------------------


@pytest.fixture
def requiredness_config(tmp_path):
    """Override that sets one property extension and removes another."""
    config = {
        "version": "1.0.0",
        "overrides": {
            "upgrade_requests": {
                "upstream_issue": "test#1142",
                "schemas": [
                    {
                        "pattern": "^probeUpgradeRequest$",
                        "remove_property_extensions": {"force": ["x-ves-required"]},
                    },
                ],
            },
            "approval_request": {
                "upstream_issue": "test#1142",
                "schemas": [
                    {
                        "pattern": "^probeApprovalReq$",
                        "set_property_extensions": {"passport": {"x-ves-required": "true"}},
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
def requiredness_spec():
    return {
        "components": {
            "schemas": {
                "probeUpgradeRequest": {
                    "type": "object",
                    "properties": {
                        "force": {"type": "boolean", "x-ves-required": "true"},
                        "version": {"type": "string", "x-ves-required": "true"},
                    },
                },
                "probeApprovalReq": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "x-ves-required": "true"},
                        "passport": {},
                    },
                },
            },
        },
    }


class TestRequirednessOverrides:
    """#1142: correcting x-ves-required in both directions."""

    def test_removes_a_marker_the_api_does_not_enforce(
        self, requiredness_config, requiredness_spec
    ):
        # Verified live 2026-07-30: POST .../upgrade_sw omitting BOTH force and
        # version returns 400 "version empty in the request" — the API names
        # version and never mentions force. Omitting force alone returns 200.
        enricher = SchemaOverrideEnricher(config_path=requiredness_config)
        result = enricher.enrich_spec(requiredness_spec)
        props = result["components"]["schemas"]["probeUpgradeRequest"]["properties"]
        assert "x-ves-required" not in props["force"]
        # The property itself survives; only the marker goes.
        assert props["force"]["type"] == "boolean"
        # A sibling that IS enforced keeps its marker.
        assert props["version"]["x-ves-required"] == "true"

    def test_sets_a_marker_the_api_does_enforce(self, requiredness_config, requiredness_spec):
        # Verified live 2026-07-30: POST .../registration/{name}/approve omitting
        # passport returns 500 "Validation approval: Passport is required"; with a
        # passport present it gets past that check and fails later on the state
        # transition instead.
        enricher = SchemaOverrideEnricher(config_path=requiredness_config)
        result = enricher.enrich_spec(requiredness_spec)
        props = result["components"]["schemas"]["probeApprovalReq"]["properties"]
        assert props["passport"]["x-ves-required"] == "true"
        assert props["name"]["x-ves-required"] == "true"

    def test_counts_what_it_changed(self, requiredness_config, requiredness_spec):
        enricher = SchemaOverrideEnricher(config_path=requiredness_config)
        enricher.enrich_spec(requiredness_spec)
        stats = enricher.get_stats()
        assert stats["property_extensions_set"] == 1
        assert stats["property_extensions_removed"] == 1

    def test_a_miss_is_counted_not_silent(self, tmp_path, requiredness_spec):
        # An override that names a property the schema does not have must be
        # visible. A silently-skipped override is how a correction gets believed
        # without ever having applied — four of eight keys in the provider's
        # sibling data file were wrong that way.
        config = {
            "version": "1.0.0",
            "overrides": {
                "typo": {
                    "upstream_issue": "test#1142",
                    "schemas": [
                        {
                            "pattern": "^probeUpgradeRequest$",
                            # Deliberately absent property names. Not real typos of real
                            # words — codespell rejects those, and the point here is only
                            # that the schema does not have them.
                            "remove_property_extensions": {"frce": ["x-ves-required"]},
                            "set_property_extensions": {"vrsn": {"x-ves-required": "true"}},
                        },
                    ],
                },
            },
        }
        config_file = tmp_path / "schema_overrides.yaml"
        with config_file.open("w") as f:
            yaml.dump(config, f)
        enricher = SchemaOverrideEnricher(config_path=config_file)
        enricher.enrich_spec(requiredness_spec)
        stats = enricher.get_stats()
        assert stats["property_overrides_missed"] == 2
        assert stats["property_extensions_set"] == 0
        assert stats["property_extensions_removed"] == 0

    def test_removing_an_absent_extension_is_not_a_miss(self, tmp_path, requiredness_spec):
        # The property exists but carries no such extension: nothing to do, and
        # not an error — that is the state the override is driving towards, so it
        # must stay idempotent across reruns.
        config = {
            "version": "1.0.0",
            "overrides": {
                "idempotent": {
                    "upstream_issue": "test#1142",
                    "schemas": [
                        {
                            "pattern": "^probeApprovalReq$",
                            "remove_property_extensions": {"passport": ["x-ves-required"]},
                        },
                    ],
                },
            },
        }
        config_file = tmp_path / "schema_overrides.yaml"
        with config_file.open("w") as f:
            yaml.dump(config, f)
        enricher = SchemaOverrideEnricher(config_path=config_file)
        enricher.enrich_spec(requiredness_spec)
        stats = enricher.get_stats()
        assert stats["property_overrides_missed"] == 0
        assert stats["property_extensions_removed"] == 0


class TestShippedRequirednessCorrections:
    """The shipped config, not just the mechanism.

    The mechanism tests above would pass with every pattern in
    schema_overrides.yaml misspelled. These assert the real config against the
    real schema names and property shapes, because a pattern that matches nothing
    fails silently and leaves the correction believed-but-absent.
    """

    @pytest.fixture
    def upstream_shaped_spec(self):
        """The two schemas as upstream actually ships them."""
        return {
            "components": {
                "schemas": {
                    "siteUpgradeSWRequest": {
                        "type": "object",
                        "properties": {
                            "force": {
                                "type": "boolean",
                                "format": "boolean",
                                "x-ves-required": "true",
                            },
                            "name": {"type": "string", "x-ves-required": "true"},
                            "namespace": {"type": "string", "x-ves-required": "true"},
                            "version": {"type": "string", "x-ves-required": "true"},
                        },
                    },
                    "siteUpgradeOSRequest": {
                        "type": "object",
                        "properties": {
                            "force": {
                                "type": "boolean",
                                "format": "boolean",
                                "x-ves-required": "true",
                            },
                            "version": {"type": "string", "x-ves-required": "true"},
                        },
                    },
                    "registrationApprovalReq": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "x-ves-required": "true"},
                            "passport": {},
                            "labels": {"type": "object"},
                        },
                    },
                },
            },
        }

    def test_force_is_negated_on_both_upgrade_requests(self, enricher, upstream_shaped_spec):
        result = enricher.enrich_spec(upstream_shaped_spec, corrections_only=True)
        for schema_name in ("siteUpgradeSWRequest", "siteUpgradeOSRequest"):
            props = result["components"]["schemas"][schema_name]["properties"]
            assert props["force"].get("x-ves-required") == "false", (
                f"{schema_name}.force must be marked NOT required; the API does not "
                "enforce it (400 names only `version` when both are omitted)"
            )
            # Negated, never deleted — a removal fails the contract-diff gate, and it
            # would erase the evidence that upstream asserted the opposite.
            assert "x-ves-required" in props["force"]
            assert props["version"]["x-ves-required"] == "true", (
                f"{schema_name}.version must keep its marker — the API does enforce it"
            )

    def test_upgrade_path_parameters_keep_their_markers(self, enricher, upstream_shaped_spec):
        result = enricher.enrich_spec(upstream_shaped_spec)
        props = result["components"]["schemas"]["siteUpgradeSWRequest"]["properties"]
        for path_param in ("name", "namespace"):
            assert props[path_param]["x-ves-required"] == "true"

    def test_passport_gains_the_marker_the_api_enforces(self, enricher, upstream_shaped_spec):
        result = enricher.enrich_spec(upstream_shaped_spec)
        props = result["components"]["schemas"]["registrationApprovalReq"]["properties"]
        assert props["passport"].get("x-ves-required") == "true", (
            "passport must be marked required: omitting it returns 500 "
            '"Validation approval: Passport is required"'
        )
        # A decorative field must NOT be swept in alongside it. required_fields
        # already lists labels, which is exactly the conflation to avoid.
        assert "x-ves-required" not in props["labels"]

    def test_no_shipped_override_names_a_property_that_does_not_exist(
        self, enricher, upstream_shaped_spec
    ):
        enricher.enrich_spec(upstream_shaped_spec)
        assert enricher.get_stats()["property_overrides_missed"] == 0, (
            "a shipped override named a property these schemas do not have — it "
            "would apply to nothing while looking like a correction"
        )


class TestNestedExtensionKeyRemoval:
    """#1142: requiredness is asserted in a nested key too.

    `x-ves-required` is not the only upstream signal. F5 also ships
    `ves.io.schema.rules.message.required` inside `x-ves-validation-rules`, and the
    two travel together on 3613 properties but disagree on 43 — so the marker alone
    is not the whole story, and the pipeline's own derivation reads both. Correcting
    a field therefore has to reach the nested key, and it must not take unrelated
    sibling rules with it.
    """

    @pytest.fixture
    def nested_config(self, tmp_path):
        config = {
            "version": "1.0.0",
            "overrides": {
                "nested": {
                    "upstream_issue": "test#1142",
                    "schemas": [
                        {
                            "pattern": "^probeRules$",
                            "remove_property_extension_keys": {
                                "force": {
                                    "x-ves-validation-rules": [
                                        "ves.io.schema.rules.message.required",
                                    ],
                                },
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
    def nested_spec(self):
        return {
            "components": {
                "schemas": {
                    "probeRules": {
                        "type": "object",
                        "properties": {
                            "force": {
                                "type": "boolean",
                                "x-ves-validation-rules": {
                                    "ves.io.schema.rules.message.required": "true",
                                    "ves.io.schema.rules.some.other": "keep-me",
                                },
                            },
                        },
                    },
                },
            },
        }

    def test_removes_the_nested_key(self, nested_config, nested_spec):
        enricher = SchemaOverrideEnricher(config_path=nested_config)
        result = enricher.enrich_spec(nested_spec)
        rules = result["components"]["schemas"]["probeRules"]["properties"]["force"][
            "x-ves-validation-rules"
        ]
        assert "ves.io.schema.rules.message.required" not in rules

    def test_keeps_unrelated_sibling_rules(self, nested_config, nested_spec):
        enricher = SchemaOverrideEnricher(config_path=nested_config)
        result = enricher.enrich_spec(nested_spec)
        rules = result["components"]["schemas"]["probeRules"]["properties"]["force"][
            "x-ves-validation-rules"
        ]
        assert rules["ves.io.schema.rules.some.other"] == "keep-me", (
            "removing the required rule must not discard the whole extension"
        )

    def test_drops_the_extension_when_it_empties(self, nested_config, nested_spec):
        # An extension left as {} is noise a consumer may still read as "there are
        # rules". If the last key goes, the extension goes.
        del nested_spec["components"]["schemas"]["probeRules"]["properties"]["force"][
            "x-ves-validation-rules"
        ]["ves.io.schema.rules.some.other"]
        enricher = SchemaOverrideEnricher(config_path=nested_config)
        result = enricher.enrich_spec(nested_spec)
        force = result["components"]["schemas"]["probeRules"]["properties"]["force"]
        assert "x-ves-validation-rules" not in force

    def test_counts_the_removal(self, nested_config, nested_spec):
        enricher = SchemaOverrideEnricher(config_path=nested_config)
        enricher.enrich_spec(nested_spec)
        assert enricher.get_stats()["property_extension_keys_removed"] == 1

    def test_a_missing_property_is_counted(self, tmp_path, nested_spec):
        config = {
            "version": "1.0.0",
            "overrides": {
                "typo": {
                    "upstream_issue": "test#1142",
                    "schemas": [
                        {
                            "pattern": "^probeRules$",
                            "remove_property_extension_keys": {
                                "frce": {"x-ves-validation-rules": ["anything"]},
                            },
                        },
                    ],
                },
            },
        }
        config_file = tmp_path / "schema_overrides.yaml"
        with config_file.open("w") as f:
            yaml.dump(config, f)
        enricher = SchemaOverrideEnricher(config_path=config_file)
        enricher.enrich_spec(nested_spec)
        assert enricher.get_stats()["property_overrides_missed"] == 1


class TestCorrectionsOnlyPass:
    """The enrich-phase pass must not inject, only correct.

    Requiredness has to be corrected before the pipeline derives
    x-f5xc-required-for from it, so the enricher runs twice: once early with
    corrections_only, once in the merge phase as before. Injecting that early ran
    the injected property through the whole enrichment chain and came back with a
    bare $ref wrapped in allOf plus derived extensions — a shape downstream codegen
    special-cases. This pins the split.
    """

    @pytest.fixture
    def split_spec(self):
        return {
            "components": {
                "schemas": {
                    "registrationApprovalReq": {
                        "type": "object",
                        "properties": {"passport": {}, "name": {"type": "string"}},
                    },
                },
            },
        }

    def test_corrections_apply(self, enricher, split_spec):
        result = enricher.enrich_spec(split_spec, corrections_only=True)
        props = result["components"]["schemas"]["registrationApprovalReq"]["properties"]
        assert props["passport"].get("x-ves-required") == "true"

    def test_injection_does_not(self, enricher, split_spec):
        result = enricher.enrich_spec(split_spec, corrections_only=True)
        schema = result["components"]["schemas"]["registrationApprovalReq"]
        assert "state" not in schema["properties"], (
            "corrections_only must not inject properties: doing it this early puts "
            "them through the enrichment chain and changes their shape"
        )
        assert "x-f5xc-action" not in schema, (
            "corrections_only must not inject schema-level extensions either"
        )

    def test_the_full_pass_still_injects(self, enricher, split_spec):
        result = enricher.enrich_spec(split_spec)
        schema = result["components"]["schemas"]["registrationApprovalReq"]
        assert schema["properties"]["state"] == {
            "$ref": "#/components/schemas/registrationObjectState",
        }
        assert schema["x-f5xc-action"] == "approve"


class TestNestedExtensionKeySet:
    """#1142 follow-up: correct a nested requiredness rule by NEGATING it.

    The contract-diff gate rejects removing a key upstream provides — it exists to
    stop the enriched specs quietly dropping contract data. Removing
    `x-ves-required` therefore failed it (4 violations), while changing the value is
    classified additive: relaxing a requirement cannot break a caller who already
    sends the field.

    So requiredness corrections negate rather than delete. That also leaves the
    disagreement with F5 visible in the artifact — upstream asserts "true", we assert
    "false" — instead of erasing the evidence that upstream ever said it.
    """

    @pytest.fixture
    def negate_config(self, tmp_path):
        config = {
            "version": "1.0.0",
            "overrides": {
                "negate": {
                    "upstream_issue": "test#1142",
                    "schemas": [
                        {
                            "pattern": "^probeRules$",
                            "set_property_extension_keys": {
                                "force": {
                                    "x-ves-validation-rules": {
                                        "ves.io.schema.rules.message.required": "false",
                                    },
                                },
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
    def negate_spec(self):
        return {
            "components": {
                "schemas": {
                    "probeRules": {
                        "type": "object",
                        "properties": {
                            "force": {
                                "type": "boolean",
                                "x-ves-validation-rules": {
                                    "ves.io.schema.rules.message.required": "true",
                                    "ves.io.schema.rules.some.other": "keep-me",
                                },
                            },
                        },
                    },
                },
            },
        }

    def test_negates_the_nested_rule(self, negate_config, negate_spec):
        enricher = SchemaOverrideEnricher(config_path=negate_config)
        result = enricher.enrich_spec(negate_spec, corrections_only=True)
        rules = result["components"]["schemas"]["probeRules"]["properties"]["force"][
            "x-ves-validation-rules"
        ]
        assert rules["ves.io.schema.rules.message.required"] == "false"

    def test_the_key_survives_so_the_diff_is_a_change_not_a_removal(
        self, negate_config, negate_spec
    ):
        enricher = SchemaOverrideEnricher(config_path=negate_config)
        result = enricher.enrich_spec(negate_spec, corrections_only=True)
        force = result["components"]["schemas"]["probeRules"]["properties"]["force"]
        assert "ves.io.schema.rules.message.required" in force["x-ves-validation-rules"], (
            "the key must remain present: a removal fails the contract-diff gate"
        )

    def test_keeps_unrelated_sibling_rules(self, negate_config, negate_spec):
        enricher = SchemaOverrideEnricher(config_path=negate_config)
        result = enricher.enrich_spec(negate_spec, corrections_only=True)
        rules = result["components"]["schemas"]["probeRules"]["properties"]["force"][
            "x-ves-validation-rules"
        ]
        assert rules["ves.io.schema.rules.some.other"] == "keep-me"

    def test_creates_the_container_when_absent(self, negate_config, negate_spec):
        del negate_spec["components"]["schemas"]["probeRules"]["properties"]["force"][
            "x-ves-validation-rules"
        ]
        enricher = SchemaOverrideEnricher(config_path=negate_config)
        result = enricher.enrich_spec(negate_spec, corrections_only=True)
        rules = result["components"]["schemas"]["probeRules"]["properties"]["force"][
            "x-ves-validation-rules"
        ]
        assert rules["ves.io.schema.rules.message.required"] == "false"

    def test_counts_the_change(self, negate_config, negate_spec):
        enricher = SchemaOverrideEnricher(config_path=negate_config)
        enricher.enrich_spec(negate_spec, corrections_only=True)
        assert enricher.get_stats()["property_extension_keys_set"] == 1

    def test_a_missing_property_is_counted(self, tmp_path, negate_spec):
        config = {
            "version": "1.0.0",
            "overrides": {
                "typo": {
                    "upstream_issue": "test#1142",
                    "schemas": [
                        {
                            "pattern": "^probeRules$",
                            "set_property_extension_keys": {
                                "frce": {"x-ves-validation-rules": {"a": "b"}},
                            },
                        },
                    ],
                },
            },
        }
        config_file = tmp_path / "schema_overrides.yaml"
        with config_file.open("w") as f:
            yaml.dump(config, f)
        enricher = SchemaOverrideEnricher(config_path=config_file)
        enricher.enrich_spec(negate_spec, corrections_only=True)
        assert enricher.get_stats()["property_overrides_missed"] == 1


class TestMapOverridesRegression:
    """Regression tests for nested map overrides from production schema_overrides.yaml."""

    def test_nested_map_overrides_applied_correctly(self, enricher):
        spec = {
            "components": {
                "schemas": {
                    "schemaObjectMetaType": {
                        "type": "object",
                        "properties": {
                            "labels": {"type": "object"},
                            "annotations": {"type": "object"},
                        },
                    },
                    "network_interfaceStaticIpParametersClusterType": {
                        "type": "object",
                        "properties": {
                            "interface_ip_map": {"type": "object"},
                        },
                    },
                    "schemasegmentEdgeData": {
                        "type": "object",
                        "properties": {
                            "dst_labels": {"type": "object"},
                            "src_labels": {"type": "object"},
                        },
                    },
                    "tenantLastLoginMap": {
                        "type": "object",
                        "properties": {
                            "last_login_map": {"type": "object"},
                        },
                    },
                    "tenantLoginEventsMap": {
                        "type": "object",
                        "properties": {
                            "login_events_map": {"type": "object"},
                        },
                    },
                },
            },
        }

        # Run enrichment
        result = enricher.enrich_spec(spec)
        schemas = result["components"]["schemas"]

        # Validate that string additionalProperties are injected correctly
        assert schemas["schemaObjectMetaType"]["properties"]["labels"]["additionalProperties"] == {"type": "string"}
        assert schemas["schemaObjectMetaType"]["properties"]["annotations"]["additionalProperties"] == {"type": "string"}
        assert schemas["network_interfaceStaticIpParametersClusterType"]["properties"]["interface_ip_map"]["additionalProperties"] == {"type": "string"}
        assert schemas["schemasegmentEdgeData"]["properties"]["dst_labels"]["additionalProperties"] == {"type": "string"}
        assert schemas["schemasegmentEdgeData"]["properties"]["src_labels"]["additionalProperties"] == {"type": "string"}
        assert schemas["tenantLastLoginMap"]["properties"]["last_login_map"]["additionalProperties"] == {"type": "string"}

        # Validate that heterogeneous/unoverridden maps remain untouched
        assert "additionalProperties" not in schemas["tenantLoginEventsMap"]["properties"]["login_events_map"]
