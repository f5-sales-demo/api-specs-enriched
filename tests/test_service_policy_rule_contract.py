"""Regression coverage for the inline service-policy rule contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
SPEC_DIR = ROOT / "docs" / "specifications" / "api"
RULE_SCHEMAS = (
    "schemaservice_policy_ruleCreateSpecType",
    "schemaservice_policy_ruleGlobalSpecType",
    "schemaservice_policy_ruleReplaceSpecType",
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


@pytest.mark.parametrize("artifact", ["network_security.json", "virtual.json", "openapi.json"])
def test_service_policy_rule_actions_are_required_in_published_specs(artifact: str) -> None:
    schemas = _load_json(SPEC_DIR / artifact)["components"]["schemas"]
    present = set(RULE_SCHEMAS) & schemas.keys()
    assert present
    for schema_name in present:
        properties = schemas[schema_name]["properties"]
        for field in ("action", "waf_action"):
            assert properties[field]["x-ves-required"] == "true"
            assert properties[field]["x-f5xc-required-for"]["create"] is True


def test_service_policy_dependencies_are_not_modeled_on_the_parent() -> None:
    config = yaml.safe_load((ROOT / "config" / "minimum_configs.yaml").read_text())
    dependencies = config["resources"]["service_policy"].get("dependencies", [])
    fields = {dependency["field"] for dependency in dependencies}
    assert "spec.rule_list.rules[].spec.action" not in fields
    assert "spec.rule_list.rules[].spec.waf_action" not in fields


def test_create_service_policy_catalog_exposes_nested_rule_contract() -> None:
    catalog = _load_json(ROOT / "release" / "api-catalog.json")
    operation = next(
        operation
        for category in catalog["categories"]
        for operation in category["operations"]
        if operation["name"] == "create_service_policy"
    )
    metadata = operation["fieldMetadata"]
    action_path = "spec.rule_list.rules[].spec.action"
    waf_path = "spec.rule_list.rules[].spec.waf_action"
    assert metadata[action_path]["required_for"]["create"] is True
    assert metadata[waf_path]["required_for"]["create"] is True
    assert set(operation["oneOfVariants"][f"{waf_path}.action_type"]) == {
        "app_firewall_detection_control",
        "none",
        "waf_skip_processing",
    }


def test_catalog_walker_follows_arrays_allof_and_stops_cycles() -> None:
    from scripts.compile_catalog import _collect_oneof_variants, _extract_field_metadata

    components = {
        "schemas": {
            "Node": {
                "type": "object",
                "properties": {
                    "children": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}},
                    "choice": {
                        "allOf": [{"$ref": "#/components/schemas/Choice"}],
                        "x-f5xc-required-for": {"create": True},
                    },
                },
            },
            "Choice": {
                "type": "object",
                "x-ves-oneof-field-kind": '["none","enabled"]',
            },
        }
    }
    root = {"type": "array", "items": {"$ref": "#/components/schemas/Node"}}
    assert _extract_field_metadata(root, components)["[].choice"]["required_for"]["create"]
    assert _collect_oneof_variants(root, components) == {"[].choice.kind": ["none", "enabled"]}
