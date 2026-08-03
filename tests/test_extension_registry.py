# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for strict extension-registry and generated-candidate parity."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import TYPE_CHECKING

import pytest
import yaml

from scripts.utils.extension_registry import (
    REGISTRY_SECTIONS,
    ExtensionRegistryError,
    assert_candidate_registry_parity,
    collect_candidate_extensions,
    load_extension_registry,
)

if TYPE_CHECKING:
    from pathlib import Path


def _registry(*extensions: str) -> dict:
    document = {
        "version": "test",
        "extension_prefix": "x-f5xc-",
        "preserved_native": {
            "description": "Upstream fields",
            "fields": ["x-ves-proto-message"],
        },
    }
    document.update({section: {} for section in REGISTRY_SECTIONS})
    document["spec_level"] = {
        extension: {"type": "string", "purpose": "Test extension"} for extension in extensions
    }
    return document


def _write_registry(path: Path, *extensions: str) -> Path:
    path.write_text(yaml.safe_dump(_registry(*extensions)), encoding="utf-8")
    return path


def test_collect_candidate_extensions_walks_every_generated_document(tmp_path: Path) -> None:
    (tmp_path / "first.json").write_text(
        json.dumps({"info": {"x-f5xc-first": True}}), encoding="utf-8"
    )
    (tmp_path / "second.json").write_text(
        json.dumps({"items": [{"x-f5xc-second": {}}]}), encoding="utf-8"
    )

    assert collect_candidate_extensions(tmp_path) == {
        "x-f5xc-first",
        "x-f5xc-second",
    }


def test_candidate_registry_parity_is_exact_in_both_directions(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "openapi.json").write_text(
        json.dumps({"x-f5xc-emitted-only": True}), encoding="utf-8"
    )
    registry = _write_registry(tmp_path / "registry.yaml", "x-f5xc-declared-only")

    with pytest.raises(ExtensionRegistryError) as error:
        assert_candidate_registry_parity(candidate, registry)

    message = str(error.value)
    assert "undeclared=['x-f5xc-emitted-only']" in message
    assert "unemitted=['x-f5xc-declared-only']" in message


def test_registry_rejects_historical_top_level_side_channels(tmp_path: Path) -> None:
    document = _registry("x-f5xc-current")
    document["wrapper_fields"] = {"fields": []}
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ExtensionRegistryError, match=r"unexpected=\['wrapper_fields'\]"):
        load_extension_registry(path)


def test_authoritative_registry_is_a_packaged_resource() -> None:
    assert files("config").joinpath("extension_registry.yaml").is_file()
