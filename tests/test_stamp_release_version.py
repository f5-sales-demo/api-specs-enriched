# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for release-version stamping."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from scripts.stamp_release_version import (
    stamp_directory,
    stamp_document,
    version_mismatches,
)

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


def test_stamp_document_rejects_nonsemantic_build_version() -> None:
    document = {"version": "1.0.0", "resources": {}}

    with pytest.raises(ValueError, match="not semantic"):
        stamp_document(document, "release-latest")

    assert document["version"] == "1.0.0"


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


def test_version_mismatches_reports_only_build_identity_artifacts(tmp_path: Path) -> None:
    fixtures = {
        "domain.json": {"openapi": "3.0.3", "info": {"version": "2.1.207"}},
        "index.json": {
            "version": "2.1.208",
            "specifications": [{"file": "domain.json"}],
        },
        "openapi.json": {"openapi": "3.0.3", "info": {"version": "2.1.208"}},
        "minimal-export-defaults.json": {"version": "2.1.208", "resources": {}},
        "namespace_profiles.json": {"version": "2.1.208", "resources": {}},
        "validation.json": {"$schema": "example", "version": "2.1.0"},
    }
    for name, document in fixtures.items():
        (tmp_path / name).write_text(json.dumps(document))

    assert version_mismatches(tmp_path, "2.1.208") == [
        (tmp_path / "domain.json", "2.1.207"),
    ]


def test_version_verification_rejects_empty_output(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        version_mismatches(tmp_path, "2.1.208")


def test_version_verification_rejects_unversioned_auxiliary_json(tmp_path: Path) -> None:
    fixtures = {
        "domain.json": {"openapi": "3.0.3", "info": {"version": "2.1.208"}},
        "index.json": {
            "version": "2.1.208",
            "specifications": [{"file": "domain.json"}],
        },
        "openapi.json": {"openapi": "3.0.3", "info": {"version": "2.1.208"}},
        "minimal-export-defaults.json": {"version": "2.1.208", "resources": {}},
        "namespace_profiles.json": {"version": "2.1.208", "resources": {}},
        "validation.json": {"$schema": "example", "version": "2.1.0"},
        "unknown.json": {"resources": {}},
    }
    for name, document in fixtures.items():
        (tmp_path / name).write_text(json.dumps(document))

    with pytest.raises(ValueError, match="no build version"):
        version_mismatches(tmp_path, "2.1.208")


def test_version_verification_rejects_missing_index_domain(tmp_path: Path) -> None:
    fixtures = {
        "index.json": {
            "version": "2.1.208",
            "specifications": [{"file": "missing.json"}],
        },
        "openapi.json": {"openapi": "3.0.3", "info": {"version": "2.1.208"}},
        "minimal-export-defaults.json": {"version": "2.1.208", "resources": {}},
        "namespace_profiles.json": {"version": "2.1.208", "resources": {}},
        "validation.json": {"$schema": "example", "version": "2.1.0"},
    }
    for name, document in fixtures.items():
        (tmp_path / name).write_text(json.dumps(document))

    with pytest.raises(ValueError, match="missing domain"):
        version_mismatches(tmp_path, "2.1.208")


def test_version_verification_rejects_duplicate_index_domain(tmp_path: Path) -> None:
    fixtures = {
        "domain.json": {"openapi": "3.0.3", "info": {"version": "2.1.208"}},
        "index.json": {
            "version": "2.1.208",
            "specifications": [{"file": "domain.json"}, {"file": "domain.json"}],
        },
        "openapi.json": {"openapi": "3.0.3", "info": {"version": "2.1.208"}},
        "minimal-export-defaults.json": {"version": "2.1.208", "resources": {}},
        "namespace_profiles.json": {"version": "2.1.208", "resources": {}},
        "validation.json": {"$schema": "example", "version": "2.1.0"},
    }
    for name, document in fixtures.items():
        (tmp_path / name).write_text(json.dumps(document))

    with pytest.raises(ValueError, match="duplicate domain"):
        version_mismatches(tmp_path, "2.1.208")


def test_version_verification_rejects_unindexed_json(tmp_path: Path) -> None:
    fixtures = {
        "domain.json": {"openapi": "3.0.3", "info": {"version": "2.1.208"}},
        "extra.json": {"version": "2.1.208"},
        "index.json": {
            "version": "2.1.208",
            "specifications": [{"file": "domain.json"}],
        },
        "openapi.json": {"openapi": "3.0.3", "info": {"version": "2.1.208"}},
        "minimal-export-defaults.json": {"version": "2.1.208", "resources": {}},
        "namespace_profiles.json": {"version": "2.1.208", "resources": {}},
        "validation.json": {"$schema": "example", "version": "2.1.0"},
    }
    for name, document in fixtures.items():
        (tmp_path / name).write_text(json.dumps(document))

    with pytest.raises(ValueError, match=r"unexpected=.*extra\.json"):
        version_mismatches(tmp_path, "2.1.208")
