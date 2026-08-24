"""Tests for wire-safe spelling correction."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import yaml

from scripts.utils.prose_spelling import ProseSpellingError, ProseSpellingTransformer

REQUESTER_TYPO = bytes.fromhex("72 65 71 75 65 73 74 6f 72").decode()
PATHS_TYPO = bytes.fromhex("70 61 74 68 65 73").decode()
EVEN_THOUGH_TYPO = bytes.fromhex("65 76 65 6e 74 68 6f 75 67 68").decode()
APOSTROPHE_TYPO = bytes.fromhex("74 68 61 74 73").decode()
IMPLEMENTERS_TYPO = bytes.fromhex("69 6d 70 6c 65 6d 65 6e 74 6f 72 73").decode()


def test_corrects_only_configured_prose_fields() -> None:
    spec = {
        "paths": {
            f"/{PATHS_TYPO}": {
                "get": {
                    "operationId": f"{REQUESTER_TYPO}{PATHS_TYPO.title()}",
                    "description": (
                        f"The {REQUESTER_TYPO} chooses {PATHS_TYPO} "
                        f"{EVEN_THOUGH_TYPO} {APOSTROPHE_TYPO} unusual."
                    ),
                }
            }
        },
        "components": {
            "schemas": {
                "Example": {
                    "description": f"{IMPLEMENTERS_TYPO.title()} support the {REQUESTER_TYPO}.",
                    "$ref": f"#/components/schemas/{PATHS_TYPO}",
                    "example": f"{REQUESTER_TYPO} {PATHS_TYPO}",
                }
            }
        },
    }
    result = ProseSpellingTransformer().transform_spec(copy.deepcopy(spec))
    operation = result["paths"][f"/{PATHS_TYPO}"]["get"]
    assert operation["description"] == ("The requester chooses paths even though that's unusual.")
    assert operation["operationId"] == f"{REQUESTER_TYPO}{PATHS_TYPO.title()}"
    assert result["components"]["schemas"]["Example"]["$ref"].endswith(f"/{PATHS_TYPO}")
    assert result["components"]["schemas"]["Example"]["example"] == (
        f"{REQUESTER_TYPO} {PATHS_TYPO}"
    )


def test_rejects_search_term_inside_property_or_enum(tmp_path: Path) -> None:
    config = {
        "spelling_corrections": {
            "fields": ["description"],
            "replacements": [{"search": "sevice", "replacement": "service"}],
        }
    }
    config_path = tmp_path / "enrichment.yaml"
    config_path.write_text(yaml.safe_dump(config))
    transformer = ProseSpellingTransformer(config_path)
    for schema in (
        {"properties": {"blocked_sevice": {"type": "string"}}},
        {"enum": ["blocked_sevice"]},
    ):
        with pytest.raises(ProseSpellingError, match="overlaps wire values"):
            transformer.transform_spec({"components": {"schemas": {"Example": schema}}})
