"""Map contract regressions using synthetic values only."""

import base64
import copy

import pytest
from jsonschema import Draft4Validator

from scripts.utils.constraint_enricher import ConstraintEnricher

PREFIX = "ves.io.schema.rules.map."


def map_schema():
    return {
        "type": "object",
        "x-ves-validation-rules": {
            PREFIX + "max_pairs": "16",
            PREFIX + "keys.uint32.ranges": "3,4,5,300-599",
            PREFIX + "values.string.max_len": "65536",
            PREFIX + "values.string.uri_ref": "true",
        },
    }


def test_recursive_map_constraints_and_idempotence():
    node = map_schema()
    spec = {
        "components": {
            "schemas": {
                "Root": {"properties": {"nested": {"type": "array", "items": {"anyOf": [node]}}}}
            }
        }
    }
    enricher = ConstraintEnricher(config_path="config/constraint_patterns.yaml")
    enricher.enrich_spec(spec)
    constraints = node["x-f5xc-constraints"]
    assert constraints["constraintType"] == "map"
    assert constraints["cardinality"]["maxProperties"] == 16
    assert constraints["keys"]["ranges"] == [[3, 3], [4, 4], [5, 5], [300, 599]]
    assert constraints["values"]["maxLength"] == 65536
    assert constraints["values"]["format"] == "uri-reference"
    before = copy.deepcopy(spec)
    enricher.enrich_spec(spec)
    assert spec == before


def test_alias_conflicts_fail():
    node = map_schema()
    node["x-validation-rules"] = {PREFIX + "max_pairs": "17"}
    with pytest.raises(ValueError, match="alias"):
        ConstraintEnricher(config_path="config/constraint_patterns.yaml").enrich_spec(
            {"components": {"schemas": {"Root": {"properties": {"map": node}}}}}
        )


def test_base64_native_value_budget():
    schema = {
        "type": "object",
        "maxProperties": 16,
        "additionalProperties": {"type": "string", "maxLength": 65536},
    }
    validator = Draft4Validator(schema)
    for size, accepted in [(49143, True), (49144, False)]:
        uri = "string:///" + base64.b64encode(b"a" * size).decode()
        assert validator.is_valid({"300": uri}) is accepted
    assert validator.is_valid({"300": "a" * 65536})
    assert not validator.is_valid({"300": "a" * 65537})
