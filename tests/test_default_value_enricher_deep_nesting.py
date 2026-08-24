# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=protected-access  # Tests intentionally verify deterministic internal helpers.

"""Tests for DefaultValueEnricher deep nesting recursion.

Verifies that _apply_nested_defaults, _apply_nested_recommended, and
_apply_nested_oneof_recommended recurse into nested.X.nested.Y configs.
"""

import pytest

from scripts.utils.default_value_enricher import DefaultValueEnricher
from scripts.utils.extension_constants import (
    X_F5XC_RECOMMENDED_ONEOF_VARIANT,
    X_F5XC_RECOMMENDED_VALUE,
    X_F5XC_SERVER_DEFAULT,
)


@pytest.fixture
def enricher(tmp_path):
    """Create enricher with a minimal config."""
    config_file = tmp_path / "discovered_defaults.yaml"
    config_file.write_text(
        """\
settings:
  use_openapi_default: true
  add_marker_extension: true
resources: {}
"""
    )
    return DefaultValueEnricher(config_path=config_file)


class TestDeepNestedDefaults:
    """Test multi-level nesting in _apply_nested_defaults."""

    def test_two_level_nesting(self, enricher):
        """Level 1 nested.use_tls -> Level 2 nested.custom_security."""
        all_schemas = {
            "TlsConfig": {
                "properties": {
                    "min_version": {"type": "string"},
                    "max_version": {"type": "string"},
                    "custom_security": {
                        "$ref": "#/components/schemas/CustomSecurity",
                    },
                },
            },
            "CustomSecurity": {
                "properties": {
                    "cipher_suites": {"type": "string"},
                    "trust_store": {"type": "string"},
                },
            },
        }
        schema = {
            "properties": {
                "use_tls": {
                    "$ref": "#/components/schemas/TlsConfig",
                },
            },
        }
        nested = {
            "use_tls": {
                "defaults": {"min_version": "TLS_AUTO"},
                "nested": {
                    "custom_security": {
                        "defaults": {"cipher_suites": "DEFAULT"},
                    },
                },
            },
        }

        enricher._apply_nested_defaults(schema, nested, all_schemas)

        # Level 1
        assert all_schemas["TlsConfig"]["properties"]["min_version"]["default"] == "TLS_AUTO"
        assert all_schemas["TlsConfig"]["properties"]["min_version"][X_F5XC_SERVER_DEFAULT] is True

        # Level 2
        assert all_schemas["CustomSecurity"]["properties"]["cipher_suites"]["default"] == "DEFAULT"
        assert (
            all_schemas["CustomSecurity"]["properties"]["cipher_suites"][X_F5XC_SERVER_DEFAULT]
            is True
        )

    def test_three_level_nesting(self, enricher):
        """Three levels deep: A -> B -> C."""
        all_schemas = {
            "SchemaB": {
                "properties": {
                    "b_field": {"type": "integer"},
                    "ref_c": {"$ref": "#/components/schemas/SchemaC"},
                },
            },
            "SchemaC": {
                "properties": {
                    "c_field": {"type": "boolean"},
                },
            },
        }
        schema = {
            "properties": {
                "ref_b": {"$ref": "#/components/schemas/SchemaB"},
            },
        }
        nested = {
            "ref_b": {
                "defaults": {"b_field": 42},
                "nested": {
                    "ref_c": {
                        "defaults": {"c_field": True},
                    },
                },
            },
        }

        enricher._apply_nested_defaults(schema, nested, all_schemas)

        assert all_schemas["SchemaB"]["properties"]["b_field"]["default"] == 42
        assert all_schemas["SchemaC"]["properties"]["c_field"]["default"] is True

    def test_max_depth_prevents_infinite_recursion(self, enricher):
        """Recursion stops at max_depth."""
        all_schemas = {
            "Deep": {
                "properties": {
                    "deeper": {"$ref": "#/components/schemas/Deep"},
                    "val": {"type": "string"},
                },
            },
        }
        schema = {
            "properties": {
                "deeper": {"$ref": "#/components/schemas/Deep"},
            },
        }

        # Build a deeply nested config that would loop
        def build_nested(depth):
            if depth <= 0:
                return {"defaults": {"val": "found"}}
            return {
                "defaults": {"val": f"level{depth}"},
                "nested": {"deeper": build_nested(depth - 1)},
            }

        nested = {"deeper": build_nested(10)}

        # Should not raise; max_depth=5 stops it
        enricher._apply_nested_defaults(schema, nested, all_schemas, max_depth=5)

        # val should be set at level 1 (depth=0 applies level 1)
        assert all_schemas["Deep"]["properties"]["val"].get("default") is not None

    def test_missing_ref_schema_skipped(self, enricher):
        """If nested $ref target doesn't exist in all_schemas, skip gracefully."""
        all_schemas = {}
        schema = {
            "properties": {
                "missing_ref": {"$ref": "#/components/schemas/DoesNotExist"},
            },
        }
        nested = {
            "missing_ref": {
                "defaults": {"field": "value"},
                "nested": {
                    "sub": {"defaults": {"x": 1}},
                },
            },
        }

        # Should not raise
        enricher._apply_nested_defaults(schema, nested, all_schemas)

    def test_legacy_flat_format_still_works(self, enricher):
        """Legacy format without 'defaults' sub-key still works at depth 0."""
        all_schemas = {
            "Inner": {
                "properties": {
                    "timeout": {"type": "integer"},
                },
            },
        }
        schema = {
            "properties": {
                "inner": {"$ref": "#/components/schemas/Inner"},
            },
        }
        # Legacy flat: no 'defaults' key, dict IS the defaults
        nested = {
            "inner": {"timeout": 30},
        }

        enricher._apply_nested_defaults(schema, nested, all_schemas)
        assert all_schemas["Inner"]["properties"]["timeout"]["default"] == 30


class TestDeepNestedRecommended:
    """Test multi-level nesting in _apply_nested_recommended."""

    def test_two_level_nesting(self, enricher):
        """Recommended values propagate through two nesting levels."""
        all_schemas = {
            "Outer": {
                "properties": {
                    "mode": {"type": "string"},
                    "inner": {"$ref": "#/components/schemas/Inner"},
                },
            },
            "Inner": {
                "properties": {
                    "strategy": {"type": "string"},
                },
            },
        }
        schema = {
            "properties": {
                "outer": {"$ref": "#/components/schemas/Outer"},
            },
        }
        nested = {
            "outer": {
                "recommended": {"mode": "ACTIVE"},
                "nested": {
                    "inner": {
                        "recommended": {"strategy": "ROUND_ROBIN"},
                    },
                },
            },
        }

        enricher._apply_nested_recommended(schema, nested, all_schemas)

        assert all_schemas["Outer"]["properties"]["mode"][X_F5XC_RECOMMENDED_VALUE] == "ACTIVE"
        assert (
            all_schemas["Inner"]["properties"]["strategy"][X_F5XC_RECOMMENDED_VALUE]
            == "ROUND_ROBIN"
        )


class TestDeepNestedOneofRecommended:
    """Test multi-level nesting in _apply_nested_oneof_recommended."""

    def test_two_level_nesting(self, enricher):
        """OneOf recommended variants propagate through two nesting levels."""
        all_schemas = {
            "Outer": {
                "properties": {
                    "inner": {"$ref": "#/components/schemas/Inner"},
                },
            },
            "Inner": {
                "properties": {
                    "variant_a": {"type": "object"},
                    "variant_b": {"type": "object"},
                },
            },
        }
        schema = {
            "properties": {
                "outer": {"$ref": "#/components/schemas/Outer"},
            },
        }
        nested = {
            "outer": {
                "oneof_recommended": {"choice_group": "variant_a"},
                "nested": {
                    "inner": {
                        "oneof_recommended": {"sub_choice": "variant_b"},
                    },
                },
            },
        }

        enricher._apply_nested_oneof_recommended(schema, nested, all_schemas)

        assert all_schemas["Outer"][X_F5XC_RECOMMENDED_ONEOF_VARIANT]["choice_group"] == "variant_a"
        assert all_schemas["Inner"][X_F5XC_RECOMMENDED_ONEOF_VARIANT]["sub_choice"] == "variant_b"
