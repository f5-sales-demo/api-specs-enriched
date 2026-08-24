# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=protected-access  # Tests intentionally verify deterministic internal helpers.

"""Unit tests for ReadOnlyEnricher.

Tests the readOnly: true annotation added to API-computed fields
for downstream tooling (e.g., xcsh CLI).
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.utils.readonly_enricher import ReadOnlyEnricher, ReadOnlyStats


class TestReadOnlyStats:
    """Test ReadOnlyStats dataclass."""

    def test_default_values(self) -> None:
        """Verify default stat values are zero."""
        stats = ReadOnlyStats()
        assert stats.metadata_fields_marked == 0
        assert stats.object_ref_fields_marked == 0
        assert stats.schemas_processed == 0
        assert stats.schemas_matched == 0

    def test_to_dict_contains_all_fields(self) -> None:
        """Verify to_dict includes all stat fields."""
        stats = ReadOnlyStats()
        result = stats.to_dict()

        assert "metadata_fields_marked" in result
        assert "object_ref_fields_marked" in result
        assert "total_fields_marked" in result
        assert "schemas_processed" in result
        assert "schemas_matched" in result
        assert "fields_by_name" in result

    def test_to_dict_calculates_total(self) -> None:
        """Verify to_dict calculates total_fields_marked correctly."""
        stats = ReadOnlyStats()
        stats.metadata_fields_marked = 5
        stats.object_ref_fields_marked = 3

        result = stats.to_dict()
        assert result["total_fields_marked"] == 8


class TestReadOnlyEnricherConfig:
    """Test ReadOnlyEnricher configuration loading."""

    @pytest.fixture
    def config_path(self) -> Path:
        """Get path to readonly_fields.yaml config file."""
        return Path(__file__).parent.parent / "config" / "readonly_fields.yaml"

    def test_config_file_exists(self, config_path: Path) -> None:
        """Verify configuration file exists."""
        assert config_path.exists(), f"Config file not found: {config_path}"

    def test_config_is_valid_yaml(self, config_path: Path) -> None:
        """Verify configuration file is valid YAML."""
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert config is not None

    def test_config_has_metadata_fields(self, config_path: Path) -> None:
        """Verify config has metadata_fields key."""
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert "metadata_fields" in config

    def test_config_has_object_ref_fields(self, config_path: Path) -> None:
        """Verify config has object_ref_fields key."""
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert "object_ref_fields" in config

    def test_config_has_patterns(self, config_path: Path) -> None:
        """Verify config has pattern definitions."""
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert "metadata_patterns" in config
        assert "object_ref_patterns" in config

    def test_config_metadata_fields_are_complete(self, config_path: Path) -> None:
        """Verify all expected metadata fields are configured."""
        with config_path.open() as f:
            config = yaml.safe_load(f)

        expected_fields = [
            "tenant",
            "uid",
            "kind",
            "creation_timestamp",
            "modification_timestamp",
            "creator_id",
            "creator_class",
            "object_index",
            "owner_view",
        ]

        for field_name in expected_fields:
            assert field_name in config["metadata_fields"], f"Missing field: {field_name}"


class TestReadOnlyEnricherInit:
    """Test ReadOnlyEnricher initialization."""

    def test_init_with_default_config(self) -> None:
        """Verify enricher initializes with default config path."""
        enricher = ReadOnlyEnricher()
        assert enricher.config_path.name == "readonly_fields.yaml"

    def test_init_loads_metadata_fields(self) -> None:
        """Verify enricher loads metadata fields from config."""
        enricher = ReadOnlyEnricher()
        assert "tenant" in enricher.metadata_fields
        assert "uid" in enricher.metadata_fields
        assert "kind" in enricher.metadata_fields

    def test_init_loads_object_ref_fields(self) -> None:
        """Verify enricher loads object ref fields from config."""
        enricher = ReadOnlyEnricher()
        assert "tenant" in enricher.object_ref_fields
        assert "uid" in enricher.object_ref_fields
        assert "kind" in enricher.object_ref_fields

    def test_init_compiles_patterns(self) -> None:
        """Verify enricher compiles regex patterns."""
        enricher = ReadOnlyEnricher()
        assert len(enricher.metadata_patterns) > 0
        assert len(enricher.object_ref_patterns) > 0


class TestReadOnlyEnricherEnrich:
    """Test ReadOnlyEnricher spec enrichment."""

    @pytest.fixture
    def enricher(self) -> ReadOnlyEnricher:
        """Create enricher instance."""
        return ReadOnlyEnricher()

    def test_enrich_empty_spec(self, enricher: ReadOnlyEnricher) -> None:
        """Verify enricher handles empty spec."""
        spec: dict[str, Any] = {}
        result = enricher.enrich_spec(spec)
        assert result == {}

    def test_enrich_spec_without_schemas(self, enricher: ReadOnlyEnricher) -> None:
        """Verify enricher handles spec without schemas."""
        spec = {"info": {"title": "Test API"}}
        result = enricher.enrich_spec(spec)
        assert result == spec

    def test_enrich_metadata_schema(self, enricher: ReadOnlyEnricher) -> None:
        """Verify enricher marks metadata fields as readOnly."""
        spec = {
            "components": {
                "schemas": {
                    "ObjectMetaType": {
                        "type": "object",
                        "properties": {
                            "tenant": {"type": "string"},
                            "uid": {"type": "string"},
                            "kind": {"type": "string"},
                            "name": {"type": "string"},  # Not a computed field
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)

        props = result["components"]["schemas"]["ObjectMetaType"]["properties"]
        assert props["tenant"].get("readOnly") is True
        assert props["uid"].get("readOnly") is True
        assert props["kind"].get("readOnly") is True
        assert "readOnly" not in props["name"]  # Should not be marked

    def test_enrich_object_ref_schema(self, enricher: ReadOnlyEnricher) -> None:
        """Verify enricher marks ObjectRef fields as readOnly."""
        spec = {
            "components": {
                "schemas": {
                    "NetworkObjectRef": {
                        "type": "object",
                        "properties": {
                            "tenant": {"type": "string"},
                            "uid": {"type": "string"},
                            "kind": {"type": "string"},
                            "name": {"type": "string"},
                            "namespace": {"type": "string"},
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)

        props = result["components"]["schemas"]["NetworkObjectRef"]["properties"]
        assert props["tenant"].get("readOnly") is True
        assert props["uid"].get("readOnly") is True
        assert props["kind"].get("readOnly") is True
        assert "readOnly" not in props["name"]  # User-provided
        assert "readOnly" not in props["namespace"]  # User-provided

    def test_preserves_existing_readonly(self, enricher: ReadOnlyEnricher) -> None:
        """Verify enricher does not overwrite existing readOnly values."""
        spec = {
            "components": {
                "schemas": {
                    "ObjectMetaType": {
                        "type": "object",
                        "properties": {
                            "tenant": {"type": "string", "readOnly": False},
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)

        props = result["components"]["schemas"]["ObjectMetaType"]["properties"]
        # Should preserve the existing False value
        assert props["tenant"]["readOnly"] is False

    def test_enrich_non_matching_schema(self, enricher: ReadOnlyEnricher) -> None:
        """Verify enricher does not mark fields in non-matching schemas."""
        spec = {
            "components": {
                "schemas": {
                    "HttpLoadBalancer": {
                        "type": "object",
                        "properties": {
                            "tenant": {"type": "string"},  # Same field name
                            "name": {"type": "string"},
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)

        props = result["components"]["schemas"]["HttpLoadBalancer"]["properties"]
        # Should NOT be marked because schema doesn't match patterns
        assert "readOnly" not in props["tenant"]
        assert "readOnly" not in props["name"]

    def test_stats_tracking(self, enricher: ReadOnlyEnricher) -> None:
        """Verify enricher tracks statistics correctly."""
        spec = {
            "components": {
                "schemas": {
                    "ObjectMetaType": {
                        "type": "object",
                        "properties": {
                            "tenant": {"type": "string"},
                            "uid": {"type": "string"},
                        },
                    },
                    "SiteRef": {
                        "type": "object",
                        "properties": {
                            "tenant": {"type": "string"},
                            "uid": {"type": "string"},
                        },
                    },
                },
            },
        }

        enricher.enrich_spec(spec)
        stats = enricher.get_stats()

        assert stats["schemas_processed"] == 2
        assert stats["schemas_matched"] >= 2
        assert stats["total_fields_marked"] >= 4


class TestReadOnlyEnricherPatterns:
    """Test schema name pattern matching."""

    @pytest.fixture
    def enricher(self) -> ReadOnlyEnricher:
        """Create enricher instance."""
        return ReadOnlyEnricher()

    @pytest.mark.parametrize(
        "schema_name",
        [
            "ObjectMetaType",
            "SomeMetadataType",
            "SystemMetadata",
        ],
    )
    def test_metadata_patterns_match(self, enricher: ReadOnlyEnricher, schema_name: str) -> None:
        """Verify metadata patterns match expected schema names."""
        assert enricher._matches_patterns(schema_name, enricher.metadata_patterns)

    @pytest.mark.parametrize(
        "schema_name",
        [
            "ObjectRefType",
            "NetworkObjectRef",
            "SiteRef",
            "NamespaceRef",
        ],
    )
    def test_object_ref_patterns_match(self, enricher: ReadOnlyEnricher, schema_name: str) -> None:
        """Verify ObjectRef patterns match expected schema names."""
        assert enricher._matches_patterns(schema_name, enricher.object_ref_patterns)

    @pytest.mark.parametrize(
        "schema_name",
        [
            "HttpLoadBalancer",
            "OriginPool",
            "CreateRequest",
            "GetResponse",
        ],
    )
    def test_patterns_dont_match_regular_schemas(
        self,
        enricher: ReadOnlyEnricher,
        schema_name: str,
    ) -> None:
        """Verify patterns don't match regular resource schemas."""
        assert not enricher._matches_patterns(schema_name, enricher.metadata_patterns)
        assert not enricher._matches_patterns(schema_name, enricher.object_ref_patterns)


class TestReadOnlyEnricherIntegration:
    """Integration tests for ReadOnlyEnricher."""

    def test_enricher_is_exported(self) -> None:
        """Verify ReadOnlyEnricher is exported from scripts.utils."""
        from scripts.utils import ReadOnlyEnricher as ExportedEnricher

        assert ExportedEnricher is not None
        assert ExportedEnricher.__name__ == "ReadOnlyEnricher"

    def test_enricher_can_be_instantiated(self) -> None:
        """Verify enricher can be instantiated without errors."""
        from scripts.utils import ReadOnlyEnricher as ExportedEnricher

        enricher = ExportedEnricher()
        assert enricher is not None

    def test_enricher_with_complex_spec(self) -> None:
        """Test enricher with a realistic complex spec structure."""
        enricher = ReadOnlyEnricher()

        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test API", "version": "1.0.0"},
            "components": {
                "schemas": {
                    "ObjectMetaType": {
                        "type": "object",
                        "description": "Metadata for all objects",
                        "properties": {
                            "tenant": {"type": "string", "description": "Tenant ID"},
                            "uid": {"type": "string", "description": "Unique identifier"},
                            "kind": {"type": "string", "description": "Object kind"},
                            "creation_timestamp": {"type": "string", "format": "date-time"},
                            "modification_timestamp": {"type": "string", "format": "date-time"},
                            "name": {"type": "string", "description": "Object name"},
                            "labels": {"type": "object"},
                        },
                    },
                    "VirtualHostRef": {
                        "type": "object",
                        "properties": {
                            "tenant": {"type": "string"},
                            "uid": {"type": "string"},
                            "kind": {"type": "string"},
                            "name": {"type": "string"},
                            "namespace": {"type": "string"},
                        },
                    },
                    "HttpLoadBalancer": {
                        "type": "object",
                        "properties": {
                            "metadata": {"$ref": "#/components/schemas/ObjectMetaType"},
                            "spec": {
                                "type": "object",
                                "properties": {
                                    "domains": {"type": "array"},
                                    "http": {"type": "object"},
                                },
                            },
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        stats = enricher.get_stats()

        # Verify ObjectMetaType computed fields are marked
        meta_props = result["components"]["schemas"]["ObjectMetaType"]["properties"]
        assert meta_props["tenant"].get("readOnly") is True
        assert meta_props["uid"].get("readOnly") is True
        assert meta_props["creation_timestamp"].get("readOnly") is True
        assert "readOnly" not in meta_props["name"]  # User-provided
        assert "readOnly" not in meta_props["labels"]  # User-provided

        # Verify VirtualHostRef computed fields are marked
        ref_props = result["components"]["schemas"]["VirtualHostRef"]["properties"]
        assert ref_props["tenant"].get("readOnly") is True
        assert ref_props["uid"].get("readOnly") is True
        assert "readOnly" not in ref_props["name"]  # User-provided

        # Verify HttpLoadBalancer is not affected
        lb_props = result["components"]["schemas"]["HttpLoadBalancer"]["properties"]
        assert "readOnly" not in lb_props.get("metadata", {})
        assert "readOnly" not in lb_props.get("spec", {})

        # Verify stats
        assert stats["schemas_processed"] == 3
        assert stats["total_fields_marked"] > 0
