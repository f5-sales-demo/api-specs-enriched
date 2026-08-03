# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for deterministic enriched-build version authority."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.utils.version_calculator import get_build_version, highest_version

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _repository(tmp_path: Path, version: str | None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    if version is not None:
        index = repo / "docs" / "specifications" / "api" / "index.json"
        index.parent.mkdir(parents=True)
        index.write_text(json.dumps({"version": version}) + "\n")
    (repo / "README.md").write_text("fixture\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def test_committed_index_is_the_build_version_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, "2.1.208")
    _git(repo, "tag", "v2.1.207")
    monkeypatch.chdir(repo)

    assert get_build_version() == "2.1.208"


def test_matching_release_branch_preserves_its_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, "2.1.208")
    _git(repo, "switch", "-q", "-c", "release/v2.1.208")
    monkeypatch.chdir(repo)

    assert get_build_version() == "2.1.208"


def test_release_branch_and_committed_artifact_must_agree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repository(tmp_path, "2.1.207")
    _git(repo, "switch", "-q", "-c", "release/v2.1.208")
    monkeypatch.chdir(repo)

    with pytest.raises(RuntimeError, match=r"release branch.*committed artifact"):
        get_build_version()


@pytest.mark.parametrize("version", [None, "0.0.0", "2.1", "not-semver"])
def test_missing_or_invalid_committed_version_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str | None,
) -> None:
    repo = _repository(tmp_path, version)
    monkeypatch.chdir(repo)

    with pytest.raises(RuntimeError, match="committed build version"):
        get_build_version()


def test_committed_version_wins_when_publication_tag_lags() -> None:
    assert highest_version("2.1.208", "2.1.207") == "2.1.208"


def test_published_tag_wins_when_it_is_newer() -> None:
    assert highest_version("2.1.207", "2.1.208") == "2.1.208"


def test_module_cli_emits_only_semantic_version(tmp_path: Path) -> None:
    repo = _repository(tmp_path, "2.1.208")
    env = {**os.environ, "PYTHONPATH": str(_PROJECT_ROOT)}

    result = subprocess.run(
        [sys.executable, "-m", "scripts.utils.version_calculator"],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == "2.1.208\n"
    assert result.stderr == ""
