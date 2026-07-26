# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for the structural contract-diff driver."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.contract_diff import Violation, run_contract_diff

FIXTURES = Path(__file__).parent / "fixtures" / "contract_diff"


def _load(pair: str) -> tuple[dict, dict]:
    return (
        json.loads((FIXTURES / pair / "input.json").read_text()),
        json.loads((FIXTURES / pair / "output.json").read_text()),
    )


def test_pass_additive_extension_and_description() -> None:
    i, o = _load("pass_add_ext")
    violations = run_contract_diff(i, o)
    assert violations == []


def test_fail_when_type_changed() -> None:
    i, o = _load("fail_tighten")
    violations = run_contract_diff(i, o)
    assert len(violations) >= 1
    assert any("type" in v.pointer for v in violations)


def test_fail_when_property_removed() -> None:
    i, o = _load("fail_remove")
    violations = run_contract_diff(i, o)
    assert any("parameters" in v.pointer for v in violations)


def test_violation_structure_has_rule_category() -> None:
    i, o = _load("fail_tighten")
    violations = run_contract_diff(i, o)
    assert isinstance(violations[0], Violation)
    assert violations[0].rule_category in {"removal", "shape-change", "constraint-change", "other"}


def test_run_directory_diff_merges_fan_in_fan_out(tmp_path: Path) -> None:
    """The enrichment pipeline merges ~520 input files into ~40 output
    files grouped by category, so ``run_directory_diff`` must compare
    the aggregate contract on both sides rather than doing a 1:1 file
    match (which would flag every input filename as ``file_removed``).
    """
    from scripts.contract_diff import run_directory_diff

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    # Two upstream specs that land on the same category.
    (input_dir / "docs-cloud.0001.ai_assistant.ves-swagger.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {"/ai/assistant": {"get": {"operationId": "getAssistant"}}},
                "components": {"schemas": {"Assistant": {"type": "object"}}},
            },
        ),
    )
    (input_dir / "docs-cloud.0002.ai_services.ves-swagger.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {"/ai/inference": {"post": {"operationId": "infer"}}},
                "components": {"schemas": {"InferenceRequest": {"type": "object"}}},
            },
        ),
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    # Single merged enriched output — adds a description to getAssistant,
    # adds x-f5xc-cli-help; does NOT remove anything.
    (output_dir / "ai_services.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "paths": {
                    "/ai/assistant": {
                        "get": {
                            "operationId": "getAssistant",
                            "description": "Retrieves assistant info.",
                            "x-f5xc-cli-help": "xcsh ai assistant get",
                        },
                    },
                    "/ai/inference": {"post": {"operationId": "infer"}},
                },
                "components": {
                    "schemas": {
                        "Assistant": {"type": "object"},
                        "InferenceRequest": {"type": "object"},
                    },
                },
            },
        ),
    )
    violations = run_directory_diff(input_dir, output_dir)
    assert violations == []


def test_run_directory_diff_flags_merged_removal(tmp_path: Path) -> None:
    """If the merged output drops a path that exists in any input, flag it."""
    from scripts.contract_diff import run_directory_diff

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "a.ves-swagger.json").write_text(
        json.dumps(
            {
                "paths": {"/a": {"get": {"operationId": "a"}}},
                "components": {"schemas": {}},
            },
        ),
    )
    (input_dir / "b.ves-swagger.json").write_text(
        json.dumps(
            {
                "paths": {"/b": {"get": {"operationId": "b"}}},
                "components": {"schemas": {}},
            },
        ),
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    # Enriched output only keeps /a — /b is dropped.
    (output_dir / "all.json").write_text(
        json.dumps(
            {"paths": {"/a": {"get": {"operationId": "a"}}}, "components": {"schemas": {}}},
        ),
    )
    violations = run_directory_diff(input_dir, output_dir)
    assert any("/b" in v.pointer for v in violations)
    assert all(v.rule_category == "removal" for v in violations)


def test_run_directory_diff_skips_manifest_and_index(tmp_path: Path) -> None:
    """manifest.json (input) and index.json (output) are pipeline metadata,
    not OpenAPI specs; they must not participate in the diff."""
    from scripts.contract_diff import run_directory_diff

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "manifest.json").write_text(json.dumps({"files": ["a.json"]}))
    (input_dir / "a.json").write_text(json.dumps({"paths": {}, "components": {"schemas": {}}}))
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "index.json").write_text(json.dumps({"domains": []}))
    (output_dir / "a.json").write_text(json.dumps({"paths": {}, "components": {"schemas": {}}}))
    violations = run_directory_diff(input_dir, output_dir)
    assert violations == []


def test_run_contract_diff_respects_known_drift():
    """A violation whose fingerprint is in the known_drift set is suppressed."""
    from scripts.contract_diff import (
        _fingerprint_violation,
        run_contract_diff,
    )

    input_spec = {
        "components": {"schemas": {"Foo": {"type": "object"}}},
    }
    output_spec = {
        "components": {
            "schemas": {"Foo": {"type": "string"}}
        },  # type change — normally a violation
    }
    # Compute the fingerprint we want to tolerate. DeepDiff classifies a
    # string→string change at ['type'] as values_changed (not type_changes);
    # the fingerprint must match the change_type actually emitted.
    fp = _fingerprint_violation(
        "values_changed",
        "root['components']['schemas']['Foo']['type']",
        "object",
        "string",
    )
    violations = run_contract_diff(input_spec, output_spec, known_drift={fp})
    assert violations == [], (
        f"Expected the tolerated violation to be suppressed; got {violations!r}"
    )


def test_run_contract_diff_without_drift_flags_violation():
    """Without known_drift, the same change fires."""
    from scripts.contract_diff import run_contract_diff

    input_spec = {"components": {"schemas": {"Foo": {"type": "object"}}}}
    output_spec = {"components": {"schemas": {"Foo": {"type": "string"}}}}
    violations = run_contract_diff(input_spec, output_spec)
    assert len(violations) == 1


def test_fingerprint_is_deterministic():
    """Same inputs produce same fingerprint across calls."""
    from scripts.contract_diff import _fingerprint_violation

    fp1 = _fingerprint_violation(
        "values_changed",
        "root['a']['b']",
        {"x": 1, "y": 2},
        {"y": 2, "x": 1},  # same dict, different insertion order
    )
    fp2 = _fingerprint_violation(
        "values_changed",
        "root['a']['b']",
        {"y": 2, "x": 1},
        {"x": 1, "y": 2},
    )
    assert fp1 == fp2, "Fingerprint must be order-independent for dict values"


def test_normalize_pointer_collapses_array_indices():
    """`parameters[2]` and `parameters[3]` fingerprint identically."""
    from scripts.contract_diff import _normalize_pointer

    p1 = "root['paths']['/x']['get']['parameters'][2]['name']"
    p2 = "root['paths']['/x']['get']['parameters'][3]['name']"
    assert _normalize_pointer(p1) == _normalize_pointer(p2)
    # Keep dict-key segments distinct.
    p3 = "root['paths']['/x']['get']['parameters'][2]['in']"
    assert _normalize_pointer(p1) != _normalize_pointer(p3)


def test_load_known_drift_missing_file_returns_empty_set(tmp_path: Path):
    """A missing drift file is not an error — absence means "no tolerance"."""
    from scripts.contract_diff import load_known_drift

    assert load_known_drift(tmp_path / "does-not-exist.json") == set()


def test_load_known_drift_none_path_returns_empty_set():
    """Passing None short-circuits to the empty set."""
    from scripts.contract_diff import load_known_drift

    assert load_known_drift(None) == set()


def test_load_known_drift_malformed_entry_raises_value_error(tmp_path: Path):
    """Entries missing 'fingerprint' should raise ValueError with context."""
    from scripts.contract_diff import load_known_drift

    drift = tmp_path / "bad.json"
    drift.write_text(json.dumps({"version": 1, "entries": [{"foo": "bar"}]}))
    with pytest.raises(ValueError, match=r"entries\[0\] missing or non-string 'fingerprint'"):
        load_known_drift(drift)


def test_allof_wrapped_ref_is_not_a_violation() -> None:
    """allOf-wrapped $ref with vendor extensions is semantically equivalent."""
    input_spec = {
        "components": {
            "schemas": {
                "Foo": {
                    "properties": {
                        "bar": {"$ref": "#/components/schemas/Bar"},
                    },
                },
            },
        },
    }
    output_spec = {
        "components": {
            "schemas": {
                "Foo": {
                    "properties": {
                        "bar": {
                            "allOf": [{"$ref": "#/components/schemas/Bar"}],
                            "x-f5xc-example": "test",
                        },
                    },
                },
            },
        },
    }
    violations = run_contract_diff(input_spec, output_spec)
    assert violations == []


def test_upstream_allof_wrapped_ref_is_not_a_violation() -> None:
    """Reverse direction: upstream wraps the $ref, enrichment adds a sibling.

    A handful of upstream properties are emitted as
    ``{"allOf": [{"$ref": X}]}`` rather than ``{"$ref": X}``. Enrichment keeps
    the wrapper and attaches a description. Normalising only the output side
    turned those sites into a bogus ``allOf`` removal plus ``$ref`` add.
    """
    input_spec = {
        "components": {
            "schemas": {
                "originPoolAdvancedOptions": {
                    "properties": {
                        "disable_lb_source_ip_persistence": {
                            "allOf": [{"$ref": "#/components/schemas/ioschemaEmpty"}],
                        },
                    },
                },
            },
        },
    }
    output_spec = {
        "components": {
            "schemas": {
                "originPoolAdvancedOptions": {
                    "properties": {
                        "disable_lb_source_ip_persistence": {
                            "allOf": [{"$ref": "#/components/schemas/ioschemaEmpty"}],
                            "description": "IP address configuration",
                        },
                    },
                },
            },
        },
    }
    assert run_contract_diff(input_spec, output_spec) == []


def test_dropped_ref_under_upstream_allof_is_still_a_violation() -> None:
    """Normalising both sides must not hide a reference the output lost."""
    input_spec = {
        "components": {
            "schemas": {
                "Foo": {
                    "properties": {
                        "bar": {"allOf": [{"$ref": "#/components/schemas/Bar"}]},
                    },
                },
            },
        },
    }
    output_spec = {
        "components": {
            "schemas": {
                "Foo": {
                    "properties": {
                        "bar": {"description": "no longer references Bar"},
                    },
                },
            },
        },
    }
    violations = run_contract_diff(input_spec, output_spec)
    assert [v.rule_category for v in violations] == ["removal"]


def test_multi_member_allof_is_not_flattened() -> None:
    """Only a single-member allOf is a $ref wrapper; dropping a member breaks."""
    input_spec = {
        "components": {
            "schemas": {
                "Foo": {
                    "properties": {
                        "bar": {
                            "allOf": [
                                {"$ref": "#/components/schemas/Bar"},
                                {"$ref": "#/components/schemas/Baz"},
                            ],
                        },
                    },
                },
            },
        },
    }
    output_spec = {
        "components": {
            "schemas": {
                "Foo": {
                    "properties": {
                        "bar": {"allOf": [{"$ref": "#/components/schemas/Bar"}]},
                    },
                },
            },
        },
    }
    assert run_contract_diff(input_spec, output_spec) != []
