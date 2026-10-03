"""Production map contract and coverage regressions."""

import copy
import json
from pathlib import Path

import pytest

from scripts.compile_catalog import _extract_field_metadata, validate_payload_against_schema
from scripts.enrichment_coverage import inventory
from scripts.utils.console_ui_enricher import ConsoleUIEnricher
from scripts.utils.constraint_enricher import ConstraintEnricher
from scripts.utils.map_constraints import FORMATS, PREFIX, RULES, normalize_map, project_map


@pytest.mark.parametrize("family", sorted(RULES))
def test_every_map_family_has_explicit_semantics(family):
    scope, key = RULES[family]
    value = (
        "1"
        if key in {"minLength", "maxLength", "minimum", "maximum", "minProperties", "maxProperties"}
        else "3,4,5,300-599"
        if key == "ranges"
        else "true"
        if key in {"format", "uniqueValues"}
        else "^[a-z]+$"
    )
    result = normalize_map({"x-ves-validation-rules": {PREFIX + family: value}})
    assert result["originalRules"] == {PREFIX + family: value}
    assert key in result[scope]
    if key == "format":
        assert result[scope][key] == FORMATS[family.rsplit(".", 1)[-1]]


@pytest.mark.parametrize("composition", ["allOf", "oneOf", "anyOf"])
def test_map_value_composition_is_native_and_idempotent(composition):
    schemas = {"Text": {"type": "string"}}
    node = {
        "type": "object",
        "additionalProperties": {composition: [{"$ref": "#/components/schemas/Text"}]},
        "x-ves-validation-rules": {PREFIX + "values.string.max_len": "10"},
    }
    project_map(node, schemas)
    before = copy.deepcopy(node)
    project_map(node, schemas)
    assert node == before
    assert "allOf" in node["additionalProperties"]


def test_cycle_does_not_recurse_forever():
    schemas = {"Cycle": {"$ref": "#/components/schemas/Cycle"}}
    node = {
        "type": "object",
        "additionalProperties": {"$ref": "#/components/schemas/Cycle"},
        "x-ves-validation-rules": {PREFIX + "values.string.max_len": "10"},
    }
    assert project_map(node, schemas)


def test_map_catalog_keeps_complete_metadata_and_accepts_typed_keys():
    node = {
        "type": "object",
        "additionalProperties": {"type": "string"},
        "x-ves-validation-rules": {
            PREFIX + "max_pairs": "16",
            PREFIX + "values.string.max_len": "65536",
        },
    }
    spec = {"components": {"schemas": {"Root": {"properties": {"errors": node}}}}}
    ConstraintEnricher(Path("config/constraint_patterns.yaml")).enrich_spec(spec)
    root = spec["components"]["schemas"]["Root"]
    assert (
        _extract_field_metadata(root, spec["components"])["errors"]["constraints"]
        == node["x-f5xc-constraints"]
    )
    assert not validate_payload_against_schema(
        {"errors": {"300": "string:///text"}}, root, spec["components"]
    )
    assert validate_payload_against_schema({"errors": {"300": 3}}, root, spec["components"])


def test_pinned_baseline_reproduces():
    # Until regenerated artifacts are committed, this is the immutable source baseline.
    import subprocess

    source = json.loads(
        subprocess.check_output(
            [
                "git",
                "show",
                "158db014109f2a838b95bccd8eb1870a39f8ca71:docs/specifications/api/openapi.json",
            ]
        )
    )
    catalog = json.loads(
        subprocess.check_output(
            ["git", "show", "158db014109f2a838b95bccd8eb1870a39f8ca71:release/api-catalog.json"]
        )
    )
    report = inventory(source, catalog)
    expected = {
        "mapSites": 99,
        "mapRuleOccurrences": 319,
        "mapRuleFamilies": 19,
        "catalogApiIdentities": 283,
        "catalogMethodPaths": 1826,
        "catalogCategories": 896,
        "catalogFieldEntries": 48983,
        "requestMapFields": 661,
        "requestKeyValueFields": 633,
        "requestMapMethods": 331,
        "requestKeyValueMethods": 327,
        "requestApiIdentities": 171,
        "requestOrResponseMethods": 758,
        "requestOrResponseIdentities": 200,
    }
    assert {key: report["counts"][key] for key in expected} == expected
    assert len(report["retainedBodyExceptions"]) == 4


def test_console_production_targets_resolve_or_have_evidence():
    spec = json.loads(Path("docs/specifications/api/openapi.json").read_text())
    enricher = ConsoleUIEnricher()
    enricher.enrich_spec(spec)
    assert enricher.stats.fields_enriched > 400
    for fields in enricher.field_config["exclusions"].values():
        for disposition in fields.values():
            assert disposition["owner"]
            assert disposition["reason"]
            assert disposition["evidence"]
