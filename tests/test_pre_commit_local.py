"""Regression tests for the repository-local pre-commit hook (Issue #1235)."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
HOOK = REPO_ROOT / "scripts" / "pre-commit-local.sh"


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def hook_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "pre-commit-local.sh").write_text(HOOK.read_text())
    (repo / "scripts" / "pre-commit-local.sh").chmod(0o755)
    (repo / "scripts" / "changed.py").write_text("value: int = 1\n")
    _run(["git", "init"], repo)
    _run(["git", "config", "user.email", "tests@example.com"], repo)
    _run(["git", "config", "user.name", "Tests"], repo)
    _run(["git", "add", "scripts/changed.py"], repo)
    return repo


def _environment(repo: Path) -> dict[str, str]:
    fake_bin = repo / "fake-bin"
    _write_executable(fake_bin / "ruff", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "mypy",
        '#!/usr/bin/env bash\nprintf global >> "$MYPY_MARKER"\nexit 97\n',
    )
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["MYPY_MARKER"] = str(repo / "mypy-marker")
    return environment


def _write_project_mypy(repo: Path, exit_code: int) -> None:
    _write_executable(
        repo / ".venv" / "bin" / "mypy",
        '#!/usr/bin/env bash\nprintf project >> "$MYPY_MARKER"\nexit ' + str(exit_code) + "\n",
    )


def test_hook_prefers_project_venv_mypy(hook_repo: Path) -> None:
    _write_project_mypy(hook_repo, 0)
    result = subprocess.run(
        ["bash", "scripts/pre-commit-local.sh"],
        cwd=hook_repo,
        env=_environment(hook_repo),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (hook_repo / "mypy-marker").read_text() == "project"
    assert "All repo-specific checks passed" in result.stdout


def test_hook_blocks_a_project_mypy_failure(hook_repo: Path) -> None:
    _write_project_mypy(hook_repo, 7)
    result = subprocess.run(
        ["bash", "scripts/pre-commit-local.sh"],
        cwd=hook_repo,
        env=_environment(hook_repo),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert (hook_repo / "mypy-marker").read_text() == "project"
