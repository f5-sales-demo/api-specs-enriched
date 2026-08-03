# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Contracts for release-branch ownership of generated publication files."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_CHECK = _ROOT / "scripts" / "release" / "verify-generated-ownership.sh"

_GENERATED_PATHS = (
    "CHANGELOG.md",
    "release/api-catalog.json",
    "docs/specifications/api/domain.json",
    "docs/api-reference/domain-api.mdx",
    "docs/openapi-specs-config.json",
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "source.py").write_text("before\n")
    _git(tmp_path, "add", "source.py")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_CHECK), *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("path", _GENERATED_PATHS)
def test_cached_generated_path_is_rejected_on_source_branch(tmp_path: Path, path: str) -> None:
    repo = _repo(tmp_path)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("generated\n")
    _git(repo, "add", path)

    result = _run(repo, "--cached", "--branch", "fix/source-change")

    assert result.returncode == 1
    assert path in result.stderr
    assert "release/v*" in result.stderr


def test_non_generated_source_change_is_allowed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "source.py").write_text("after\n")
    _git(repo, "add", "source.py")

    result = _run(repo, "--cached", "--branch", "fix/source-change")

    assert result.returncode == 0, result.stderr


def test_upstream_source_receipt_change_is_allowed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".github_release").write_text('{"asset_digest":"sha256:source"}\n')
    _git(repo, "add", ".github_release")

    result = _run(repo, "--cached", "--branch", "fix/source-change")

    assert result.returncode == 0, result.stderr


def test_intent_to_add_generated_path_is_rejected_on_source_branch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = repo / "docs" / "specifications" / "api" / "intent.json"
    target.parent.mkdir(parents=True)
    target.write_text("generated\n")
    _git(repo, "add", "--intent-to-add", str(target.relative_to(repo)))

    result = _run(repo, "--cached", "--branch", "fix/source-change")

    assert result.returncode == 1
    assert "docs/specifications/api/intent.json" in result.stderr


def test_release_branch_may_own_generated_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = repo / "release" / "api-catalog.json"
    target.parent.mkdir(parents=True)
    target.write_text("generated\n")
    _git(repo, "add", "release/api-catalog.json")

    result = _run(repo, "--cached", "--branch", "release/v2.1.209")

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("branch", ["release/foo", "release/vnext", "fix/release/v2.1.209"])
def test_only_semantic_release_branch_is_exempt(tmp_path: Path, branch: str) -> None:
    repo = _repo(tmp_path)
    target = repo / "CHANGELOG.md"
    target.write_text("generated\n")
    _git(repo, "add", "CHANGELOG.md")

    result = _run(repo, "--cached", "--branch", branch)

    assert result.returncode == 1


def test_commit_range_is_checked_for_required_ci_gate(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    target = repo / "docs" / "api-reference" / "domain-api.mdx"
    target.parent.mkdir(parents=True)
    target.write_text("generated\n")
    _git(repo, "add", str(target.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "generated output on source branch")

    result = _run(
        repo,
        "--base",
        base,
        "--head",
        "HEAD",
        "--branch",
        "fix/source-change",
    )

    assert result.returncode == 1
    assert "docs/api-reference/domain-api.mdx" in result.stderr


def test_rename_out_of_generated_tree_is_still_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    original = repo / "docs" / "specifications" / "api" / "domain.json"
    original.parent.mkdir(parents=True)
    original.write_text("generated\n")
    _git(repo, "add", str(original.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "release output")
    (repo / "archive").mkdir()
    _git(repo, "mv", str(original.relative_to(repo)), "archive/domain.json")

    result = _run(repo, "--cached", "--branch", "fix/source-change")

    assert result.returncode == 1
    assert "docs/specifications/api/domain.json" in result.stderr
