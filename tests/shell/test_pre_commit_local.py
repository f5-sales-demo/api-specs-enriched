# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Regression tests for repository-specific pre-commit enforcement."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

_PROJECT_HOOK = Path(__file__).resolve().parents[2] / "scripts/pre-commit-local.sh"


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _fixture(tmp_path: Path, *, mypy_exit: int) -> tuple[Path, Path]:
    """Create a repository with both venv and global mypy candidates."""
    _run(tmp_path, "git", "init", "-q", "-b", "main")
    _run(tmp_path, "git", "config", "user.email", "ci@example.com")
    _run(tmp_path, "git", "config", "user.name", "test")

    hook = tmp_path / "scripts/pre-commit-local.sh"
    hook.parent.mkdir(parents=True)
    hook.write_text(_PROJECT_HOOK.read_text())
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    _executable(tmp_path / "scripts/hooks/pre-commit-pipeline.sh", "#!/bin/sh\nexit 0\n")
    source = tmp_path / "scripts/example.py"
    source.write_text("value = 1\n")
    _run(tmp_path, "git", "add", ".")
    _run(tmp_path, "git", "commit", "-q", "-m", "fixture")
    source.write_text("value = 'changed'\n")
    _run(tmp_path, "git", "add", "scripts/example.py")

    marker = tmp_path / "venv-mypy-ran"
    _executable(tmp_path / ".venv/bin/python3", "#!/bin/sh\nexit 0\n")
    _executable(tmp_path / ".venv/bin/ruff", "#!/bin/sh\nexit 0\n")
    _executable(
        tmp_path / ".venv/bin/mypy",
        f"#!/bin/sh\nprintf ran > {marker.as_posix()!r}\nexit {mypy_exit}\n",
    )
    # The project venv must win even when a different global candidate exists.
    _executable(tmp_path / "fakebin/mypy", "#!/bin/sh\nexit 0\n")
    _executable(tmp_path / "fakebin/ruff", "#!/bin/sh\nexit 0\n")
    return tmp_path, marker


def _invoke(repo: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": f"{repo / 'fakebin'}:{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", "scripts/pre-commit-local.sh"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_project_venv_mypy_failure_blocks_commit(tmp_path: Path) -> None:
    repo, marker = _fixture(tmp_path, mypy_exit=1)

    result = _invoke(repo)

    assert result.returncode != 0
    assert marker.read_text() == "ran"
    assert "Running mypy type checking" in result.stdout
    assert "All repo-specific checks passed" not in result.stdout


def test_project_venv_mypy_success_allows_commit(tmp_path: Path) -> None:
    repo, marker = _fixture(tmp_path, mypy_exit=0)

    result = _invoke(repo)

    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "ran"
    assert "All repo-specific checks passed" in result.stdout


def test_missing_project_ruff_blocks_commit(tmp_path: Path) -> None:
    repo, _ = _fixture(tmp_path, mypy_exit=0)
    (repo / ".venv/bin/ruff").unlink()

    result = _invoke(repo)

    assert result.returncode != 0
    assert "project ruff is required" in result.stderr
    assert "All repo-specific checks passed" not in result.stdout


def test_missing_project_mypy_blocks_commit(tmp_path: Path) -> None:
    repo, _ = _fixture(tmp_path, mypy_exit=0)
    (repo / ".venv/bin/mypy").unlink()

    result = _invoke(repo)

    assert result.returncode != 0
    assert "project mypy is required" in result.stderr
    assert "All repo-specific checks passed" not in result.stdout


def test_missing_project_python_blocks_config_validation(tmp_path: Path) -> None:
    repo, _ = _fixture(tmp_path, mypy_exit=0)
    config = repo / "config/example.yaml"
    config.parent.mkdir()
    config.write_text("key: value\n")
    _run(repo, "git", "add", "config/example.yaml")
    (repo / ".venv/bin/python3").unlink()

    result = _invoke(repo)

    assert result.returncode != 0
    assert "project Python environment is required" in result.stderr


def test_unimportable_config_validator_blocks_commit(tmp_path: Path) -> None:
    repo, _ = _fixture(tmp_path, mypy_exit=0)
    config = repo / "config/example.yaml"
    config.parent.mkdir()
    config.write_text("key: value\n")
    _run(repo, "git", "add", "config/example.yaml")
    _executable(repo / ".venv/bin/python3", "#!/bin/sh\nexit 1\n")

    result = _invoke(repo)

    assert result.returncode != 0
    assert "scripts.validate_configs is not importable" in result.stderr


def test_missing_pipeline_hook_blocks_commit(tmp_path: Path) -> None:
    repo, _ = _fixture(tmp_path, mypy_exit=0)
    (repo / "scripts/hooks/pre-commit-pipeline.sh").unlink()

    result = _invoke(repo)

    assert result.returncode != 0
    assert "required enrichment pipeline hook" in result.stderr
