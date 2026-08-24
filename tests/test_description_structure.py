"""Tests for prose-only structure normalization."""

from scripts.utils.description_structure import DescriptionStructureTransformer


def test_standalone_required_assertions_are_removed_without_losing_prose() -> None:
    spec = {
        "components": {
            "schemas": {
                "Example": {
                    "properties": {
                        "force": {"description": "Force upgrade.\nRequired: YES\nUse with care."},
                        "optional": {
                            "description": "Optional setting.\n  Required: NO.\nStill useful."
                        },
                    }
                }
            }
        }
    }
    result = DescriptionStructureTransformer().transform_spec(spec)
    properties = result["components"]["schemas"]["Example"]["properties"]
    assert properties["force"]["description"] == "Force upgrade.\nUse with care."
    assert properties["optional"]["description"] == "Optional setting.\nStill useful."
    assert "x-required" not in properties["force"]
    assert "x-required" not in properties["optional"]
