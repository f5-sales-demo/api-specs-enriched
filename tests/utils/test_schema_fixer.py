# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=protected-access  # Tests intentionally verify deterministic internal helpers.

"""Tests for scripts/utils/schema_fixer.py.

Covers the two-phase fixer that landed via PR #138. The critical
invariant: ``inject_max_items`` must run AFTER ``ConstraintEnricher``
so the synthetic 65535 bound does not leak into
``x-f5xc-constraints.maxItems``. A direct regression of Codex P1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import yaml

from scripts.utils.schema_fixer import SchemaFixer

if TYPE_CHECKING:
    from pathlib import Path


class TestNeedsMaxItems:
    """Guards on which array schemas are eligible for maxItems injection."""

    @pytest.fixture
    def fixer(self) -> SchemaFixer:
        return SchemaFixer()

    @pytest.mark.parametrize(
        ("schema", "expected"),
        [
            ({"type": "array"}, True),
            ({"type": "array", "items": {"type": "string"}}, True),
            ({"type": "array", "maxItems": 10}, False),
            ({"type": "array", "$ref": "#/components/schemas/X"}, False),
            ({"type": "array", "allOf": []}, False),
            ({"type": "array", "oneOf": []}, False),
            ({"type": "array", "anyOf": []}, False),
            ({"type": "string"}, False),
            ({"type": "object"}, False),
            ({"type": "integer"}, False),
            ({}, False),
        ],
        ids=[
            "plain_array",
            "array_with_items",
            "array_with_maxitems",
            "array_with_ref",
            "array_with_allof",
            "array_with_oneof",
            "array_with_anyof",
            "string",
            "object",
            "integer",
            "empty",
        ],
    )
    def test_matrix(
        self,
        fixer: SchemaFixer,
        schema: dict[str, Any],
        expected: bool,
    ) -> None:
        assert fixer._needs_max_items(schema) is expected


class TestNeedsTypeFix:
    """Guards on which schemas are eligible for the format-without-type fix."""

    @pytest.fixture
    def fixer(self) -> SchemaFixer:
        return SchemaFixer()

    @pytest.mark.parametrize(
        ("schema", "expected"),
        [
            ({"format": "date-time"}, True),
            ({"format": "int32"}, True),
            ({"format": "date-time", "type": "string"}, False),
            ({"format": "date-time", "$ref": "#/components/schemas/X"}, False),
            ({"format": "date-time", "allOf": []}, False),
            ({"format": "date-time", "oneOf": []}, False),
            ({"format": "date-time", "anyOf": []}, False),
            ({"type": "string"}, False),
            ({}, False),
        ],
        ids=[
            "format_string",
            "format_int",
            "format_plus_type",
            "format_plus_ref",
            "format_plus_allof",
            "format_plus_oneof",
            "format_plus_anyof",
            "type_without_format",
            "empty",
        ],
    )
    def test_matrix(
        self,
        fixer: SchemaFixer,
        schema: dict[str, Any],
        expected: bool,
    ) -> None:
        assert fixer._needs_type_fix(schema) is expected


class TestInjectMaxItems:
    """inject_max_items respects the configured bound, never clobbers.

    Default is ``None`` (disabled) per design spec 2026-04-22 section 3.3 —
    stamping the legacy 65535 sentinel adds zero information. Tests that
    need injection enabled pass an explicit positive bound via config.
    """

    def test_disabled_by_default_no_op(self) -> None:
        """With DEFAULT_ARRAY_MAX_ITEMS = None, injection is a no-op."""
        spec = {"components": {"schemas": {"L": {"type": "array"}}}}
        result = SchemaFixer().inject_max_items(spec)
        assert "maxItems" not in result["components"]["schemas"]["L"]

    def test_stamps_on_unbounded_array_when_configured(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {"default_max_items": 1024})
        spec = {"components": {"schemas": {"L": {"type": "array"}}}}
        result = SchemaFixer(config_path=config).inject_max_items(spec)
        assert result["components"]["schemas"]["L"]["maxItems"] == 1024

    def test_does_not_overwrite_existing_max_items(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {"default_max_items": 1024})
        spec = {"components": {"schemas": {"L": {"type": "array", "maxItems": 42}}}}
        result = SchemaFixer(config_path=config).inject_max_items(spec)
        assert result["components"]["schemas"]["L"]["maxItems"] == 42

    def test_stats_count_injections(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {"default_max_items": 1024})
        spec = {
            "components": {
                "schemas": {
                    "A": {"type": "array"},
                    "B": {"type": "array"},
                    "C": {"type": "array", "maxItems": 5},  # untouched
                    "D": {"type": "string"},  # not an array
                },
            },
        }
        fixer = SchemaFixer(config_path=config)
        fixer.inject_max_items(spec)
        assert fixer.get_stats()["max_items_added"] == 2

    def test_does_not_touch_non_array_schemas(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {"default_max_items": 1024})
        spec = {"components": {"schemas": {"S": {"type": "string"}}}}
        result = SchemaFixer(config_path=config).inject_max_items(spec)
        assert "maxItems" not in result["components"]["schemas"]["S"]

    def test_isolation_invariant_does_not_leak_into_xf5xc_constraints(
        self,
        tmp_path: Path,
    ) -> None:
        """Codex P1 regression guard.

        When an array already has pattern-inferred ``x-f5xc-constraints.maxItems``,
        ``inject_max_items`` must stamp the schema-level bound on the array but
        MUST NOT touch the ``x-f5xc-constraints`` subtree. Uses an explicit
        config bound because the default injection is disabled.
        """
        config = _write_config(tmp_path, {"default_max_items": 1024})
        spec = {
            "components": {
                "schemas": {
                    "tags": {
                        "type": "array",
                        "x-f5xc-constraints": {"maxItems": 512},
                    },
                },
            },
        }
        result = SchemaFixer(config_path=config).inject_max_items(spec)
        tags = result["components"]["schemas"]["tags"]
        assert tags["maxItems"] == 1024
        assert tags["x-f5xc-constraints"]["maxItems"] == 512


class TestFixSpec:
    """fix_spec applies the format-without-type fix only."""

    def test_adds_type_when_format_present(self) -> None:
        spec = {"components": {"schemas": {"T": {"format": "date-time"}}}}
        result = SchemaFixer().fix_spec(spec)
        assert result["components"]["schemas"]["T"]["type"] == "string"

    @pytest.mark.parametrize(
        ("format_value", "expected_type"),
        [
            ("date-time", "string"),
            ("uuid", "string"),
            ("int32", "integer"),
            ("int64", "integer"),
            ("float", "number"),
            ("double", "number"),
            ("ipv4", "string"),
        ],
    )
    def test_format_type_mapping(self, format_value: str, expected_type: str) -> None:
        spec = {"components": {"schemas": {"T": {"format": format_value}}}}
        result = SchemaFixer().fix_spec(spec)
        assert result["components"]["schemas"]["T"]["type"] == expected_type

    def test_unknown_format_falls_back_to_string(self) -> None:
        spec = {"components": {"schemas": {"T": {"format": "quuxly"}}}}
        result = SchemaFixer().fix_spec(spec)
        assert result["components"]["schemas"]["T"]["type"] == "string"

    def test_fix_spec_does_not_inject_max_items(self) -> None:
        """fix_spec is the early-phase pass; maxItems must come from inject_max_items."""
        spec = {"components": {"schemas": {"L": {"type": "array"}}}}
        result = SchemaFixer().fix_spec(spec)
        assert "maxItems" not in result["components"]["schemas"]["L"]


def _write_config(tmp_path: Path, schema_fixes: dict[str, Any]) -> Path:
    config = {"schema_fixes": schema_fixes}
    path = tmp_path / "enrichment.yaml"
    with path.open("w") as f:
        yaml.safe_dump(config, f)
    return path


class TestConfigLoading:
    """SchemaFixer respects enrichment.yaml schema_fixes block."""

    def test_disabling_max_items_makes_inject_noop(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {"fix_missing_max_items": False})
        spec = {"components": {"schemas": {"L": {"type": "array"}}}}
        result = SchemaFixer(config_path=config).inject_max_items(spec)
        assert "maxItems" not in result["components"]["schemas"]["L"]

    def test_custom_default_max_items_respected(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {"default_max_items": 1024})
        spec = {"components": {"schemas": {"L": {"type": "array"}}}}
        result = SchemaFixer(config_path=config).inject_max_items(spec)
        assert result["components"]["schemas"]["L"]["maxItems"] == 1024

    def test_custom_format_type_mapping_overrides_default(self, tmp_path: Path) -> None:
        # Override "date-time" → "integer" (unrealistic, but tests the hook).
        config = _write_config(
            tmp_path,
            {"format_type_mapping": {"date-time": "integer"}},
        )
        spec = {"components": {"schemas": {"T": {"format": "date-time"}}}}
        result = SchemaFixer(config_path=config).fix_spec(spec)
        assert result["components"]["schemas"]["T"]["type"] == "integer"

    def test_missing_config_file_uses_defaults(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.yaml"
        fixer = SchemaFixer(config_path=missing)
        assert fixer._default_max_items == SchemaFixer.DEFAULT_ARRAY_MAX_ITEMS
        assert fixer._fix_missing_max_items is True
        assert fixer._fix_format_without_type is True


class TestGetStats:
    """get_stats reports the expected shape after each phase."""

    def test_stats_after_fix_spec(self) -> None:
        spec = {"components": {"schemas": {"T": {"format": "date-time"}}}}
        fixer = SchemaFixer()
        fixer.fix_spec(spec)
        stats = fixer.get_stats()
        assert stats["fixes_applied"] == 1
        assert stats["max_items_added"] == 0

    def test_stats_after_inject_max_items(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, {"default_max_items": 1024})
        spec = {"components": {"schemas": {"L": {"type": "array"}}}}
        fixer = SchemaFixer(config_path=config)
        fixer.inject_max_items(spec)
        stats = fixer.get_stats()
        assert stats["max_items_added"] == 1
        assert stats["fixes_applied"] == 0

    def test_stats_exposes_config_values(self) -> None:
        stats = SchemaFixer().get_stats()
        assert stats["fix_missing_max_items"] is True
        assert stats["fix_format_without_type"] is True
        assert stats["default_max_items"] == SchemaFixer.DEFAULT_ARRAY_MAX_ITEMS
