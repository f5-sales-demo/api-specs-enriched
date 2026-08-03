# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for MinimalDefaultsExporter.

Verifies that the exporter walks each covered resource's SpecType schema
(resolving allOf/$ref, building spec.-prefixed dot-paths) and emits the flat
{serverDefaultFields, fieldDefaults, minimumConfigFields, fieldConflicts}
artifact consumed by @f5-sales-demo/pi-resource-management.
"""

import pytest

from scripts.utils.minimal_defaults_exporter import MinimalDefaultsExporter


@pytest.fixture
def exporter(tmp_path):
    """Exporter with a one-resource config matching origin_pool SpecTypes."""
    config_file = tmp_path / "discovered_defaults.yaml"
    config_file.write_text(
        """\
version: "1.0.0"
description: "Focused test configuration"
settings:
  use_openapi_default: true
  add_marker_extension: true
resources:
  origin_pool:
    description: "Origin pool"
    schema_pattern: "origin_pool.*SpecType"
"""
    )
    return MinimalDefaultsExporter(config_path=config_file)


@pytest.fixture
def schemas():
    """A minimal enriched component-schema set for origin_pool."""
    return {
        "viewsorigin_poolGlobalSpecType": {
            "properties": {
                "endpoint_selection": {
                    "type": "string",
                    "default": "DISTRIBUTED",
                    "x-f5xc-server-default": True,
                },
                "round_robin": {
                    "allOf": [{"$ref": "#/components/schemas/ioschemaEmpty"}],
                    "default": {},
                    "x-f5xc-server-default": True,
                    "x-f5xc-conflicts-with": ["least_active", "random"],
                },
                "origin_servers": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/OriginServer"},
                    "x-f5xc-required-for": {"minimum_config": True, "create": True},
                },
                "advanced_options": {
                    "allOf": [{"$ref": "#/components/schemas/originPoolAdvancedOptions"}],
                },
            },
        },
        "ioschemaEmpty": {"type": "object", "properties": {}},
        "originPoolAdvancedOptions": {
            "properties": {
                "connection_timeout": {
                    "type": "integer",
                    "default": 0,
                    "x-f5xc-server-default": True,
                },
            },
        },
        "OriginServer": {
            "properties": {
                "server_null": {
                    "type": "string",
                    "x-f5xc-server-default": True,
                    "x-f5xc-server-default-value": {"type": "null", "value": None},
                },
            },
        },
        "viewsorigin_poolReplaceSpecType": {
            "properties": {
                "replace_only": {
                    "type": "boolean",
                    "default": False,
                    "x-f5xc-server-default": True,
                },
                "round_robin": {
                    "type": "object",
                    "default": {},
                    "x-f5xc-server-default": True,
                    "x-f5xc-conflicts-with": ["source_ip"],
                },
            },
        },
    }


class TestBuild:
    def test_collects_server_default_fields_with_spec_prefix(self, exporter, schemas):
        result = exporter.build(schemas)
        op = result["resources"]["origin_pool"]
        assert sorted(op["serverDefaultFields"]) == [
            "spec.advanced_options.connection_timeout",
            "spec.endpoint_selection",
            "spec.origin_servers.server_null",
            "spec.replace_only",
            "spec.round_robin",
        ]

    def test_records_known_default_values(self, exporter, schemas):
        op = exporter.build(schemas)["resources"]["origin_pool"]
        assert op["fieldDefaults"] == {
            "spec.endpoint_selection": "DISTRIBUTED",
            "spec.round_robin": {},
            "spec.advanced_options.connection_timeout": 0,
            "spec.origin_servers.server_null": None,
            "spec.replace_only": False,
        }

    def test_collects_minimum_config_fields(self, exporter, schemas):
        op = exporter.build(schemas)["resources"]["origin_pool"]
        assert op["minimumConfigFields"] == ["spec.origin_servers"]

    def test_collects_field_conflicts(self, exporter, schemas):
        op = exporter.build(schemas)["resources"]["origin_pool"]
        assert op["fieldConflicts"] == {"spec.round_robin": ["least_active", "random", "source_ip"]}

    def test_includes_version(self, exporter, schemas):
        result = exporter.build(schemas, version="2.1.145")
        assert result["version"] == "2.1.145"
        assert "resources" in result

    def test_export_is_byte_deterministic_across_schema_order(self, exporter, schemas, tmp_path):
        forward_path = tmp_path / "forward.json"
        reverse_path = tmp_path / "reverse.json"

        forward = exporter.export(schemas, forward_path, version="2.1.145")
        reverse = exporter.export(
            dict(reversed(list(schemas.items()))),
            reverse_path,
            version="2.1.145",
        )

        assert forward == reverse
        assert forward_path.read_bytes() == reverse_path.read_bytes()

    def test_resource_with_no_markers_is_omitted(self, exporter):
        # SpecType present but no markers anywhere -> no resource entry.
        schemas = {"viewsorigin_poolGlobalSpecType": {"properties": {"name": {"type": "string"}}}}
        result = exporter.build(schemas)
        assert "origin_pool" not in result["resources"]

    def test_unmatched_resource_is_rejected(self, exporter):
        with pytest.raises(ValueError, match="matches no published schema"):
            exporter.build({"somethingElse": {"properties": {}}})

    def test_conflicting_defaults_across_spec_types_are_rejected(self, exporter):
        schemas = {
            "origin_poolCreateSpecType": {
                "properties": {
                    "mode": {
                        "default": "first",
                        "x-f5xc-server-default": True,
                    },
                },
            },
            "origin_poolReplaceSpecType": {
                "properties": {
                    "mode": {
                        "default": "second",
                        "x-f5xc-server-default": True,
                    },
                },
            },
        }

        with pytest.raises(ValueError, match="conflicting fieldDefaults"):
            exporter.build(schemas)

    def test_boolean_and_integer_variant_defaults_conflict(self, exporter):
        schemas = {
            "origin_poolCreateSpecType": {
                "properties": {
                    "mode": {
                        "default": True,
                        "x-f5xc-server-default": True,
                    },
                },
            },
            "origin_poolReplaceSpecType": {
                "properties": {
                    "mode": {
                        "default": 1,
                        "x-f5xc-server-default": True,
                    },
                },
            },
        }

        with pytest.raises(ValueError, match="conflicting fieldDefaults"):
            exporter.build(schemas)

    def test_ambiguous_allof_reference_is_rejected(self, exporter):
        schemas = {
            "origin_poolCreateSpecType": {
                "properties": {
                    "choice": {
                        "allOf": [
                            {"$ref": "#/components/schemas/First"},
                            {"$ref": "#/components/schemas/Second"},
                        ],
                    },
                },
            },
            "First": {"properties": {}},
            "Second": {"properties": {}},
        }

        with pytest.raises(ValueError, match="ambiguous reference targets"):
            exporter.build(schemas)

    def test_schema_collection_is_order_independent_and_rejects_conflicts(self):
        first = {"components": {"schemas": {"Shared": {"type": "boolean"}}}}
        identical = {"components": {"schemas": {"Shared": {"type": "boolean"}}}}
        conflicting = {"components": {"schemas": {"Shared": {"type": "integer"}}}}

        forward = MinimalDefaultsExporter.collect_schemas([first, identical])
        reverse = MinimalDefaultsExporter.collect_schemas([identical, first])

        assert forward == reverse == {"Shared": {"type": "boolean"}}
        forward["Shared"]["type"] = "mutated"
        assert first["components"]["schemas"]["Shared"]["type"] == "boolean"
        with pytest.raises(ValueError, match="conflicting collected schema values"):
            MinimalDefaultsExporter.collect_schemas([first, conflicting])
