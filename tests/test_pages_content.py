from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest

from scripts.release.pages_content import (
    PagesContentError,
    stage_content,
    write_publication_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path


def _source_tree(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    (docs / "en").mkdir(parents=True)
    (docs / "en" / "index.mdx").write_text("# English\n", encoding="utf-8")
    (docs / "api-reference").mkdir()
    (docs / "api-reference" / "widget.mdx").write_text("# Widget\n", encoding="utf-8")
    (docs / "specifications" / "api").mkdir(parents=True)
    (docs / "specifications" / "api" / "openapi.json").write_text(
        '{"openapi":"3.0.3"}\n',
        encoding="utf-8",
    )
    (docs / "openapi-specs-config.json").write_text("[]\n", encoding="utf-8")

    (docs / "fr").mkdir()
    (docs / "fr" / "index.mdx").write_text("# French\n", encoding="utf-8")
    (docs / "superpowers").mkdir()
    (docs / "superpowers" / "internal.md").write_text("internal\n", encoding="utf-8")
    (docs / "llms-config.json").write_text("{}\n", encoding="utf-8")
    return docs


def test_stage_content_copies_only_the_publication_allowlist(tmp_path: Path) -> None:
    source = _source_tree(tmp_path)
    output = tmp_path / "staged"

    stage_content(source, output)

    files = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    assert files == {
        "api-reference/widget.mdx",
        "en/index.mdx",
        "openapi-specs-config.json",
        "specifications/api/openapi.json",
    }
    assert not (output / "fr").exists()
    assert not (output / "superpowers").exists()
    assert not (output / "llms-config.json").exists()


def test_stage_content_rejects_duplicate_english_api_reference(tmp_path: Path) -> None:
    source = _source_tree(tmp_path)
    duplicate = source / "en" / "api-reference"
    duplicate.mkdir()
    (duplicate / "widget.mdx").write_text("# Stale widget\n", encoding="utf-8")
    output = tmp_path / "staged"

    with pytest.raises(PagesContentError, match="duplicate English API reference"):
        stage_content(source, output)

    assert not output.exists()


def test_stage_content_rejects_symlinks_without_publishing_partial_output(
    tmp_path: Path,
) -> None:
    source = _source_tree(tmp_path)
    (source / "en" / "linked.mdx").symlink_to(source / "en" / "index.mdx")
    output = tmp_path / "staged"

    with pytest.raises(PagesContentError, match="symlink"):
        stage_content(source, output)

    assert not output.exists()


def test_stage_content_rejects_missing_input_and_existing_output(tmp_path: Path) -> None:
    source = _source_tree(tmp_path)
    missing_output = tmp_path / "missing-output"
    (source / "api-reference" / "widget.mdx").unlink()
    (source / "api-reference").rmdir()

    with pytest.raises(PagesContentError, match="unreadable"):
        stage_content(source, missing_output)
    assert not missing_output.exists()

    existing_output = tmp_path / "existing-output"
    existing_output.mkdir()
    with pytest.raises(PagesContentError, match="already exists"):
        stage_content(_source_tree(tmp_path / "second"), existing_output)


def test_publication_manifest_is_deterministic_and_covers_every_existing_file(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    (output / "en").mkdir(parents=True)
    (output / "en" / "index.html").write_bytes(b"English")
    (output / ".nojekyll").write_bytes(b"")

    manifest_path = write_publication_manifest(output)

    payload = manifest_path.read_bytes()
    assert payload.endswith(b"\n")
    assert json.loads(payload) == {
        "files": {
            ".nojekyll": hashlib.sha256(b"").hexdigest(),
            "en/index.html": hashlib.sha256(b"English").hexdigest(),
        },
        "version": 1,
    }
    assert b'"files":{".nojekyll"' in payload
    assert "api/publication-manifest.json" not in json.loads(payload)["files"]


def test_publication_manifest_rejects_symlinks(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "index.html").write_bytes(b"English")
    (output / "linked.html").symlink_to(output / "index.html")

    with pytest.raises(PagesContentError, match="symlink"):
        write_publication_manifest(output)

    assert not (output / "api" / "publication-manifest.json").exists()
