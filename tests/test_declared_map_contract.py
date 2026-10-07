"""Exact issue-linked map schemas preserve contract enforcement."""

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.contract_diff import load_declared_maps, run_contract_diff


def declaration(tmp_path: Path, issue: bool = True):
    config = tmp_path / "maps.yaml"
    provenance = "    upstream_issue: f5-sales-demo/api-specs-enriched#1805\n" if issue else ""
    config.write_text(
        "overrides:\n  map_shape:\n"
        + provenance
        + "    schemas:\n      - pattern: ^Response$\n"
        + "        set_property_extensions:\n          images:\n"
        + "            additionalProperties:\n              type: object\n"
        + "              properties:\n                url:\n                  type: string\n"
    )
    return load_declared_maps(config)


def pair():
    before = {
        "components": {"schemas": {"Response": {"properties": {"images": {"type": "object"}}}}}
    }
    after = deepcopy(before)
    after["components"]["schemas"]["Response"]["properties"]["images"]["additionalProperties"] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Image URL", "x-f5xc-sensitive": True}
        },
    }
    return before, after


def test_exact_declared_map_and_additive_annotations_pass(tmp_path):
    before, after = pair()
    assert run_contract_diff(before, after, declared_maps=declaration(tmp_path)) == []


def test_declared_map_type_change_remains_rejected(tmp_path):
    before, after = pair()
    after["components"]["schemas"]["Response"]["properties"]["images"]["additionalProperties"][
        "properties"
    ]["url"]["type"] = "integer"
    assert run_contract_diff(before, after, declared_maps=declaration(tmp_path))


def test_unrelated_map_is_not_normalized(tmp_path):
    before, after = pair()
    declarations = declaration(tmp_path)
    assert run_contract_diff(before, after)
    existing = deepcopy(before)
    existing["components"]["schemas"]["Response"]["properties"]["images"][
        "additionalProperties"
    ] = {"type": "string"}
    assert run_contract_diff(existing, after, declared_maps=declarations)


def test_missing_issue_provenance_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="issue-linked"):
        declaration(tmp_path, issue=False)
