# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for release-version stamping."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from scripts.stamp_release_version import stamp_directory, stamp_document

if TYPE_CHECKING:
    from pathlib import Path


def test_stamp_document_uses_openapi_info_version() -> None:
    document = {"openapi": "3.0.3", "info": {"title": "Example", "version": "1.0.0"}}

    assert stamp_document(document, "2.0.0") is True
    assert document == {
        "openapi": "3.0.3",
        "info": {"title": "Example", "version": "2.0.0"},
    }


def test_stamp_document_uses_top_level_artifact_version() -> None:
    document = {"version": "1.0.0", "resources": {}}

    assert stamp_document(document, "2.0.0") is True
    assert document == {"version": "2.0.0", "resources": {}}
    assert "info" not in document


def test_stamp_document_preserves_self_describing_schema() -> None:
    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "version": "2.1.0",
    }

    assert stamp_document(document, "9.9.9") is False
    assert document["version"] == "2.1.0"
    assert "info" not in document


def test_stamp_document_does_not_treat_arbitrary_info_as_openapi() -> None:
    document = {"info": {"version": "format-v1"}, "resources": {}}

    assert stamp_document(document, "2.0.0") is False
    assert document["info"] == {"version": "format-v1"}


def test_stamp_directory_updates_only_build_version_artifacts(tmp_path: Path) -> None:
    fixtures = {
        "domain.json": {"openapi": "3.0.3", "info": {"version": "1.0.0"}},
        "index.json": {"version": "1.0.0"},
        "validation.json": {"$schema": "example", "version": "2.1.0"},
        "unversioned.json": {"resources": {}},
    }
    for name, document in fixtures.items():
        (tmp_path / name).write_text(json.dumps(document))

    assert stamp_directory(tmp_path, "2.0.0") == 2
    assert json.loads((tmp_path / "domain.json").read_text())["info"]["version"] == "2.0.0"
    assert json.loads((tmp_path / "index.json").read_text())["version"] == "2.0.0"
    assert json.loads((tmp_path / "validation.json").read_text()) == fixtures["validation.json"]
    assert json.loads((tmp_path / "unversioned.json").read_text()) == fixtures["unversioned.json"]
