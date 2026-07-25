# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for the ``x-f5xc-wire-name`` property-level extension.

``x-f5xc-wire-name`` is produced by the buffer-zone repo
(``f5-sales-demo/api-specs#686``), not by this repo. Upstream corrects F5's
misspelled property *names* while preserving the wire contract: the property is
presented under the corrected name and the original (misspelled) key is
recorded on it::

    "blocked_service": {"x-f5xc-wire-name": "blocked_sevice", ...}

Downstream code generators build API requests from the wire name, because F5's
server only accepts the misspelling. If this repo were to drop the annotation
during enrichment, the generated provider would emit the corrected key, which
F5 silently discards — exactly
``f5-sales-demo/terraform-provider-xcsh#1257``.

These tests therefore cover two things:

1. **Registration** — the extension is a known, documented member of the
   ``x-f5xc-*`` namespace (constant, valid set, catalog, registry).
2. **Preservation** — a property carrying the annotation still carries it,
   byte-identical, after the normalization + enrichment pipeline runs.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.pipeline import (
    _remove_ref_siblings,
    enrich_spec,
    load_config,
    normalize_spec,
)
from scripts.utils.extension_constants import (
    VALID_X_F5XC_EXTENSIONS,
    X_F5XC_WIRE_NAME,
    is_valid_extension,
    validate_no_invalid_extensions,
)

REPO_ROOT = Path(__file__).parent.parent
CATALOG = REPO_ROOT / "docs" / "en" / "extensions" / "catalog.md"
REGISTRY = REPO_ROOT / "config" / "extension_registry.yaml"
API_DIR = REPO_ROOT / "docs" / "specifications" / "api"

# The real upstream case: F5 accepts only the misspelling on the wire.
PRESENTED_NAME = "blocked_service"
WIRE_NAME = "blocked_sevice"  # codespell:ignore sevice


# =============================================================================
# REGISTRATION
# =============================================================================


class TestRegistration:
    """``x-f5xc-wire-name`` must be a known member of the namespace."""

    def test_constant_value(self) -> None:
        assert X_F5XC_WIRE_NAME == "x-f5xc-wire-name"

    def test_in_valid_extensions(self) -> None:
        assert X_F5XC_WIRE_NAME in VALID_X_F5XC_EXTENSIONS

    def test_is_valid_extension(self) -> None:
        assert is_valid_extension(X_F5XC_WIRE_NAME)

    def test_not_reported_as_invalid_extension(self) -> None:
        """A spec carrying the annotation must not be flagged by the validator."""
        spec = {
            "components": {
                "schemas": {
                    "fooSpec": {
                        "type": "object",
                        "properties": {
                            PRESENTED_NAME: {
                                "type": "boolean",
                                X_F5XC_WIRE_NAME: WIRE_NAME,
                            },
                        },
                    },
                },
            },
        }
        errors = validate_no_invalid_extensions(spec)
        assert len(errors) == 0, errors

    def test_has_catalog_entry(self) -> None:
        headers = set(re.findall(r"^### (x-[a-z0-9-]+)\s*$", CATALOG.read_text(), re.MULTILINE))
        assert X_F5XC_WIRE_NAME in headers

    def test_registered_in_extension_registry(self) -> None:
        with REGISTRY.open() as handle:
            registry = yaml.safe_load(handle)
        entry = registry["property_level"][X_F5XC_WIRE_NAME]
        assert entry["type"] == "string"
        assert entry["purpose"]
        # Consumed by the code generators that build API requests.
        assert "terraform" in entry["consumers"]


# =============================================================================
# PRESERVATION THROUGH THE PIPELINE
# =============================================================================


def _spec_with_wire_name() -> dict[str, Any]:
    """Synthetic spec exercising both property shapes that carry the annotation.

    ``blocked_service`` is an inline scalar property; ``origin_service`` is a
    ``$ref`` property, which normalization rewrites into an ``allOf`` wrapper
    (see :func:`scripts.pipeline._remove_ref_siblings`) and is therefore the
    shape most at risk of losing sibling keys.
    """
    return {
        "openapi": "3.0.3",
        "info": {"title": "Wire Name Fixture", "version": "1.0.0"},
        "paths": {
            "/api/config/namespaces/{namespace}/widgets": {
                "post": {
                    "operationId": "createWidget",
                    "summary": "Create widget",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/widgetCreateSpecType",
                                },
                            },
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
        "components": {
            "schemas": {
                "widgetCreateSpecType": {
                    "type": "object",
                    "description": "Widget create specification.",
                    "x-ves-proto-message": "ves.io.schema.widget.CreateSpecType",
                    "properties": {
                        "name": {"type": "string", "description": "Widget name."},
                        PRESENTED_NAME: {
                            "type": "boolean",
                            "description": "Whether the service is blocked.",
                            X_F5XC_WIRE_NAME: WIRE_NAME,
                        },
                        "origin_service": {
                            "$ref": "#/components/schemas/serviceRefType",
                            "description": "Origin service reference.",
                            X_F5XC_WIRE_NAME: "origin_sevice",
                        },
                    },
                },
                "serviceRefType": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            },
        },
    }


def _collect_wire_names(obj: Any, acc: dict[str, str], path: str = "") -> None:
    """Collect every ``x-f5xc-wire-name`` value keyed by its JSON path."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else key
            if key == X_F5XC_WIRE_NAME:
                acc[child] = value
            else:
                _collect_wire_names(value, acc, child)
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            _collect_wire_names(item, acc, f"{path}[{index}]")


@pytest.fixture(scope="module")
def pipeline_config() -> dict:
    return load_config()


class TestSurvivesPipeline:
    """The annotation must reach downstream byte-identical."""

    def test_survives_normalize_and_enrich(self, pipeline_config: dict) -> None:
        spec = _spec_with_wire_name()
        expected = {}
        _collect_wire_names(copy.deepcopy(spec), expected)
        assert len(expected) == 2, "fixture must carry two annotations"

        normalized, _ = normalize_spec(copy.deepcopy(spec), pipeline_config)
        enriched, _ = enrich_spec(normalized, pipeline_config)

        actual: dict[str, str] = {}
        _collect_wire_names(enriched, actual)

        assert sorted(actual.values()) == sorted(expected.values()), (
            "x-f5xc-wire-name values were dropped or rewritten by the enrichment "
            f"pipeline: expected {sorted(expected.values())}, got {sorted(actual.values())}"
        )

    def test_inline_property_annotation_is_byte_identical(self, pipeline_config: dict) -> None:
        normalized, _ = normalize_spec(_spec_with_wire_name(), pipeline_config)
        enriched, _ = enrich_spec(normalized, pipeline_config)
        prop = enriched["components"]["schemas"]["widgetCreateSpecType"]["properties"][
            PRESENTED_NAME
        ]
        assert prop[X_F5XC_WIRE_NAME] == WIRE_NAME

    def test_ref_sibling_normalization_keeps_annotation(self) -> None:
        """``$ref`` siblings are filtered down to ``x-*`` keys plus ``default``.

        This is the one place in the pipeline that rebuilds a property dict from
        an allowlist rather than copying every key, so it is asserted directly.
        """
        spec = {
            "components": {
                "schemas": {
                    "widgetCreateSpecType": {
                        "properties": {
                            "origin_service": {
                                "$ref": "#/components/schemas/serviceRefType",
                                "description": "dropped by OAS3 $ref-sibling rules",
                                X_F5XC_WIRE_NAME: "origin_sevice",
                            },
                        },
                    },
                },
            },
        }
        normalized, wrapped = _remove_ref_siblings(spec)
        prop = normalized["components"]["schemas"]["widgetCreateSpecType"]["properties"][
            "origin_service"
        ]
        assert wrapped == 1
        assert prop[X_F5XC_WIRE_NAME] == "origin_sevice"
        assert prop["allOf"] == [{"$ref": "#/components/schemas/serviceRefType"}]

    def test_schema_fixer_property_rename_carries_annotation(self) -> None:
        """This repo's own misspelling rename must move the annotation with the property.

        ``SchemaFixer`` renames wire-safe misspellings (``config/enrichment.yaml``
        ``schema_fixes.rename_properties``). If a renamed property already carries an
        upstream ``x-f5xc-wire-name``, the annotation must travel with it.
        """
        from scripts.utils.schema_fixer import SchemaFixer

        fixer = SchemaFixer()
        fixer._property_renames = {"WidgetSpec": {"lables": "labels"}}  # codespell:ignore lables
        spec = {
            "components": {
                "schemas": {
                    "widgetSpec": {
                        "x-ves-proto-message": "ves.io.schema.widget.WidgetSpec",
                        "properties": {
                            "lables": {  # codespell:ignore lables
                                "type": "object",
                                X_F5XC_WIRE_NAME: "lables",  # codespell:ignore lables
                            },
                        },
                    },
                },
            },
        }
        out = fixer.fix_spec(spec)
        props = out["components"]["schemas"]["widgetSpec"]["properties"]
        assert "labels" in props
        assert props["labels"][X_F5XC_WIRE_NAME] == "lables"  # codespell:ignore lables


# =============================================================================
# DERIVED-ARTIFACT PROJECTION
# =============================================================================


class TestCatalogProjection:
    """``scripts/compile_catalog.py`` projects a narrow allowlist of ``x-f5xc-*``
    keys into ``release/api-catalog.json``.

    That catalog feeds ``xcsh``, which builds API requests from it, so the wire
    name has to survive the projection for the same reason the provider needs
    it: sending the presented name makes F5 silently discard the field.
    """

    def test_wire_name_is_projected_from_an_inline_property(self) -> None:
        from scripts.compile_catalog import _extract_field_metadata

        schema = {
            "type": "object",
            "properties": {
                PRESENTED_NAME: {"type": "boolean", X_F5XC_WIRE_NAME: WIRE_NAME},
            },
        }
        result = _extract_field_metadata(schema, {"schemas": {}}, prefix="", depth=0, max_depth=3)
        assert PRESENTED_NAME in result, (
            "a property whose only enrichment is x-f5xc-wire-name must still reach the catalog"
        )
        assert result[PRESENTED_NAME]["wireName"] == WIRE_NAME

    def test_wire_name_is_projected_from_a_ref_wrapper(self) -> None:
        from scripts.compile_catalog import _extract_field_metadata

        components = {
            "schemas": {
                "serviceRefType": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            },
        }
        schema = {
            "type": "object",
            "properties": {
                "origin_service": {
                    "$ref": "#/components/schemas/serviceRefType",
                    X_F5XC_WIRE_NAME: "origin_sevice",
                },
            },
        }
        result = _extract_field_metadata(schema, components, prefix="", depth=0, max_depth=3)
        assert result["origin_service"]["wireName"] == "origin_sevice"


# =============================================================================
# CROSS-SPEC INVARIANT OVER THE GENERATED SPECS
# =============================================================================


def _generated_wire_name_sites() -> list[tuple[str, str, str]]:
    """Return ``(spec_file, json_path, wire_name)`` for every annotation found."""
    sites: list[tuple[str, str, str]] = []
    for path in sorted(API_DIR.glob("*.json")):
        found: dict[str, str] = {}
        _collect_wire_names(json.loads(path.read_text()), found)
        sites.extend((path.name, json_path, value) for json_path, value in found.items())
    return sites


_SPECS_MISSING = not API_DIR.exists() or not any(API_DIR.glob("*.json"))
_SITES: list[tuple[str, str, str]] = [] if _SPECS_MISSING else _generated_wire_name_sites()


@pytest.mark.skipif(_SPECS_MISSING, reason="specs not generated; run `make pipeline` first")
@pytest.mark.skipif(
    not _SITES,
    reason=(
        "upstream api-specs does not carry x-f5xc-wire-name yet "
        "(f5-sales-demo/api-specs#689 still draft) — 0 occurrences in "
        "docs/specifications/api/. Skipped rather than passed vacuously; this "
        "test becomes an enforced invariant the moment the annotation ships."
    ),
)
class TestGeneratedSpecInvariants:
    """Shape invariants over every ``x-f5xc-wire-name`` in the generated specs."""

    def test_every_wire_name_is_a_non_empty_string(self) -> None:
        bad = [site for site in _SITES if not isinstance(site[2], str) or not site[2]]
        assert not bad, f"non-string / empty x-f5xc-wire-name values: {bad}"

    def test_every_wire_name_sits_on_a_schema_property(self) -> None:
        """The annotation is property-level: its parent must be a ``properties`` member."""
        bad = [
            site
            for site in _SITES
            if not re.search(r"\.properties\.[^.]+\.x-f5xc-wire-name$", site[1])
        ]
        assert not bad, f"x-f5xc-wire-name found outside a schema property: {bad}"

    def test_wire_name_differs_from_presented_property_key(self) -> None:
        """A wire name equal to its property key carries no information."""
        redundant = [
            site
            for site in _SITES
            if site[1].split(".")[-2] == site[2]  # parent property key
        ]
        assert not redundant, f"x-f5xc-wire-name identical to the property key: {redundant}"
