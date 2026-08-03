# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for strict enriched-release source provenance."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from scripts.release import source_provenance

if TYPE_CHECKING:
    from pathlib import Path


def _receipt(**overrides: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "version": "2026.07.30-19",
        "tag_name": "v2026.07.30-19",
        "published_at": "2026-08-02T08:25:35Z",
        "asset_name": "api-specs-v2026.07.30-19.zip",
        "asset_size": 5_988_559,
        "asset_digest": f"sha256:{'a' * 64}",
    }
    receipt.update(overrides)
    return receipt


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_source_provenance_reads_exact_receipt_from_release_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / ".github_release").write_text(json.dumps(_receipt(), indent=2) + "\n")
    _git(repo, "add", ".github_release")
    _git(repo, "commit", "-q", "-m", "source receipt")
    commit = _git(repo, "rev-parse", "HEAD")
    monkeypatch.chdir(repo)

    assert source_provenance.source_provenance_at(commit) == _receipt()


def test_exact_marker_round_trips_and_is_idempotent() -> None:
    marker = source_provenance.source_provenance_marker(_receipt())
    body = f"Release notes\n\n{marker}\n"

    assert source_provenance.require_source_provenance(body, _receipt()) == _receipt()
    assert source_provenance.ensure_source_provenance(body, _receipt()) == body
    assert body.count(source_provenance.SOURCE_PROVENANCE_PREFIX) == 1


def test_missing_marker_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="no source provenance"):
        source_provenance.require_source_provenance("Release notes\n", _receipt())


def test_duplicate_markers_fail_closed() -> None:
    marker = source_provenance.source_provenance_marker(_receipt())
    with pytest.raises(RuntimeError, match="multiple source provenance"):
        source_provenance.source_provenance_from_body(f"{marker}\n{marker}\n")


@pytest.mark.parametrize(
    "marker",
    [
        '<!-- api-specs-source-provenance:{"bad":true} -->',
        "<!-- api-specs-source-provenance:not-json -->",
        '<!-- api-specs-source-provenance: {"asset_digest":"sha256:' + "a" * 64 + '"} -->',
        '<p><!-- api-specs-source-provenance:{"bad":true} --></p>',
    ],
)
def test_malformed_marker_fails_closed(marker: str) -> None:
    with pytest.raises(RuntimeError, match="source provenance"):
        source_provenance.source_provenance_from_body(marker)


def test_mismatched_marker_fails_closed_with_exact_field() -> None:
    marker = source_provenance.source_provenance_marker(_receipt(asset_size=123))
    with pytest.raises(RuntimeError, match="asset_size"):
        source_provenance.require_source_provenance(marker, _receipt())


def test_missing_marker_is_added_once() -> None:
    body = source_provenance.ensure_source_provenance("Release notes\n", _receipt())

    assert source_provenance.require_source_provenance(body, _receipt()) == _receipt()
    assert body.count(source_provenance.SOURCE_PROVENANCE_PREFIX) == 1
