"""Regression coverage for release version calculation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/release/calculate-version.sh"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _commit(repo: Path, message: str) -> None:
    marker = repo / "marker.txt"
    marker.write_text(f"{marker.read_text() if marker.exists() else ''}{message}\n")
    _git(repo, "add", "marker.txt")
    _git(repo, "commit", "-m", message)


def _run(repo: Path, change_type: str) -> dict[str, str]:
    output = repo / "github-output"
    env = os.environ | {"CHANGE_TYPE": change_type, "GITHUB_OUTPUT": str(output)}
    subprocess.run(
        ["bash", str(SCRIPT)], cwd=repo, env=env, check=True, capture_output=True, text=True
    )
    return dict(line.split("=", maxsplit=1) for line in output.read_text().splitlines())


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _commit(repo, "chore: baseline")
    _git(repo, "tag", "v2.1.225")
    return repo


def test_breaking_signal_survives_intervening_fix(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "feat(api)!: clean break")
    _commit(repo, "fix(validation): fail closed")

    assert _run(repo, "forced") == {
        "current_version": "2.1.225",
        "new_version": "3.0.0",
        "bump_type": "major",
    }


def test_forced_release_without_breaking_signal_is_patch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "fix(validation): fail closed")

    assert _run(repo, "forced") == {
        "current_version": "2.1.225",
        "new_version": "2.1.226",
        "bump_type": "patch",
    }


def test_unknown_change_type_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    output = repo / "github-output"
    env = os.environ | {"CHANGE_TYPE": "unknown", "GITHUB_OUTPUT": str(output)}

    result = subprocess.run(
        ["bash", str(SCRIPT)], cwd=repo, env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode == 1
    assert "Unknown change type" in result.stderr
