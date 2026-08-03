# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for scripts/hooks/pre-commit-pipeline.sh.

Specifically, the STEP 0 input-fingerprint skip that short-circuits the
~13 min enrichment pipeline when no pipeline-input files are staged.
The full pipeline run is NOT exercised here — we only verify the skip
decision against a throwaway git repo.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

_HOOK_RELATIVE = "scripts/hooks/pre-commit-pipeline.sh"
_OWNERSHIP_RELATIVE = "scripts/release/verify-generated-ownership.sh"
_PROJECT_HOOK = Path(__file__).resolve().parents[2] / _HOOK_RELATIVE
_PROJECT_OWNERSHIP = Path(__file__).resolve().parents[2] / _OWNERSHIP_RELATIVE

_GENERATED_PATHS = (
    "CHANGELOG.md",
    "release/api-catalog.json",
    "docs/specifications/api/domain.json",
    "docs/api-reference/domain-api.mdx",
    "docs/openapi-specs-config.json",
)


def _run_cmd(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


def _setup_repo(tmp_path: Path, hook_copy: Path) -> Path:
    """Create a throwaway repo that mimics the project layout the hook sees."""
    _run_cmd(["git", "init", "-q", "-b", "main"], tmp_path)
    _run_cmd(["git", "config", "user.email", "t@t.t"], tmp_path)
    _run_cmd(["git", "config", "user.name", "t"], tmp_path)

    # Mirror the minimum shape the hook fingerprint inspects, and commit
    # the hook itself as part of the base state so `git add .` in a test
    # doesn't re-stage it as a pipeline-input change.
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "specs" / "original").mkdir(parents=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "README.md").write_text("readme\n")
    (tmp_path / "requirements.txt").write_text("pytest\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "uv.lock").write_text("version = 1\n")
    generated = tmp_path / "docs" / "specifications" / "api" / "domain.json"
    generated.parent.mkdir(parents=True)
    generated.write_text('{"version":"2.1.207"}\n')

    hook_target = tmp_path / _HOOK_RELATIVE
    hook_target.parent.mkdir(parents=True, exist_ok=True)
    hook_target.write_text(hook_copy.read_text())
    hook_target.chmod(hook_target.stat().st_mode | stat.S_IEXEC)
    ownership_target = tmp_path / _OWNERSHIP_RELATIVE
    ownership_target.parent.mkdir(parents=True, exist_ok=True)
    ownership_target.write_text(_PROJECT_OWNERSHIP.read_text())
    ownership_target.chmod(ownership_target.stat().st_mode | stat.S_IEXEC)

    _run_cmd(["git", "add", "."], tmp_path)
    _run_cmd(["git", "commit", "-q", "-m", "init"], tmp_path)
    return tmp_path


def _invoke(cwd: Path, *, force: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ}
    if force:
        env["FORCE_PIPELINE"] = "1"
    env["PATH"] = f"{cwd}/_fakebin:{env.get('PATH', '')}"
    env["PYTHON"] = f"{cwd}/_fakebin/python3"
    fakebin = cwd / "_fakebin"
    fakebin.mkdir(exist_ok=True)
    python_stub = fakebin / "python3"
    python_stub.write_text(
        """#!/usr/bin/env bash
echo "[fake python] args: $*" >&2
if [ "$*" = "-m scripts.utils.version_calculator" ]; then
  echo "2.1.208"
fi
if [[ "$*" == *"-m scripts.stamp_release_version"* ]]; then
  target_dir="$1"
  target_ver="2.1.208"
  shift
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "--version" ] || [ "$1" = "--check-version" ]; then
      target_ver="$2"
      break
    fi
    shift
  done
  mkdir -p "$target_dir/docs/specifications/api"
  printf '{"version":"%s"}\n' "$target_ver" > "$target_dir/docs/specifications/api/domain.json"
fi
if [[ "$*" == *"-m scripts.pipeline"* ]]; then
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "--output-dir" ]; then
      mkdir -p "$2"
      printf '%s\n' '{"version":"2.1.208"}' > "$2/domain.json"
      break
    fi
    shift
  done
fi
if [[ "$*" == *"-m scripts.compile_catalog"* ]]; then
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "--output" ]; then
      mkdir -p "$(dirname "$2")"
      printf '%s\n' '{"version":"2.1.208","categories":[]}' > "$2"
      break
    fi
    shift
  done
fi
if [ "${FAIL_VERSION_CHECK:-0}" = "1" ] && [[ "$*" == *"--check-version"* ]]; then
  exit 1
fi
exit 0
"""
    )
    python_stub.chmod(python_stub.stat().st_mode | stat.S_IEXEC)
    spectral_stub = fakebin / "spectral"
    spectral_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    spectral_stub.chmod(spectral_stub.stat().st_mode | stat.S_IEXEC)

    return subprocess.run(
        ["bash", _HOOK_RELATIVE],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )


class TestSkipPath:
    """STEP 0 short-circuits when no pipeline inputs are staged."""

    def test_readme_only_commit_skips_pipeline(self, tmp_path: Path) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)

        (repo / "README.md").write_text("edited\n")
        _run_cmd(["git", "add", "README.md"], repo)

        result = _invoke(repo)
        assert result.returncode == 0, result.stderr
        assert "skipping enrichment + lint" in result.stdout
        assert "[fake python]" not in result.stderr

    def test_test_file_only_skips_pipeline(self, tmp_path: Path) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)

        (repo / "tests").mkdir()
        (repo / "tests" / "test_x.py").write_text("def test_ok():\n    assert True\n")
        _run_cmd(["git", "add", "."], repo)

        result = _invoke(repo)
        assert result.returncode == 0, result.stderr
        assert "skipping enrichment + lint" in result.stdout

    @pytest.mark.parametrize("path", _GENERATED_PATHS)
    def test_output_only_commit_is_rejected_on_ordinary_branch(
        self,
        tmp_path: Path,
        path: str,
    ) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)
        generated = repo / path
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text('{"version":"9.9.9"}\n')
        _run_cmd(["git", "add", path], repo)

        result = _invoke(repo)

        assert result.returncode == 1
        assert "generated release output is staged on non-release branch" in result.stdout
        assert "scripts.pipeline" not in result.stderr


class TestRunPath:
    """STEP 0 falls through when a pipeline-input file is staged."""

    def test_config_change_triggers_pipeline(self, tmp_path: Path) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)

        (repo / "config" / "thing.yaml").write_text("key: value\n")
        _run_cmd(["git", "add", "config/thing.yaml"], repo)

        result = _invoke(repo)
        # The fake python stub exits 0, so the hook continues past STEP 1.
        assert "Running F5 XC API enrichment pipeline" in result.stdout
        assert "skipping enrichment + lint" not in result.stdout

    def test_upstream_source_receipt_change_triggers_pipeline(self, tmp_path: Path) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)
        receipt = {
            "version": "2026.07.30-1",
            "tag_name": "v2026.07.30-1",
            "published_at": "2026-08-03T15:12:39Z",
            "asset_name": "api-specs-v2026.07.30-1.zip",
            "asset_size": 61_780_014,
            "asset_digest": f"sha256:{'a' * 64}",
        }
        (repo / ".github_release").write_text(json.dumps(receipt) + "\n")
        _run_cmd(["git", "add", ".github_release"], repo)

        result = _invoke(repo)

        assert result.returncode == 0, result.stderr
        assert "Running F5 XC API enrichment pipeline" in result.stdout
        assert "skipping enrichment + lint" not in result.stdout

    def test_scripts_change_triggers_pipeline(self, tmp_path: Path) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)

        (repo / "scripts" / "enrich.py").write_text("print('hi')\n")
        _run_cmd(["git", "add", "scripts/enrich.py"], repo)

        result = _invoke(repo)
        assert "Running F5 XC API enrichment pipeline" in result.stdout

    def test_workflow_change_triggers_pipeline(self, tmp_path: Path) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)

        (repo / ".github" / "workflows" / "sync-and-enrich.yml").write_text("name: x\n")
        _run_cmd(["git", "add", ".github/workflows/sync-and-enrich.yml"], repo)

        result = _invoke(repo)
        assert "Running F5 XC API enrichment pipeline" in result.stdout

    def test_frozen_dependency_lock_change_triggers_pipeline(self, tmp_path: Path) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)

        (repo / "uv.lock").write_text("version = 1\nrevision = 3\n")
        _run_cmd(["git", "add", "uv.lock"], repo)

        result = _invoke(repo)
        assert "Running F5 XC API enrichment pipeline" in result.stdout

    def test_ordinary_branch_validates_without_promoting_candidate(self, tmp_path: Path) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)
        generated = repo / "docs" / "specifications" / "api" / "domain.json"
        before = generated.read_bytes()
        (repo / "config" / "thing.yaml").write_text("key: value\n")
        _run_cmd(["git", "add", "config/thing.yaml"], repo)

        result = _invoke(repo)

        assert result.returncode == 0, result.stderr
        assert "Candidate output and lint verified" in result.stdout
        assert generated.read_bytes() == before
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        assert "docs/specifications/api/domain.json" not in staged

    def test_release_branch_verifies_versions_before_staging(self, tmp_path: Path) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)
        _run_cmd(["git", "switch", "-q", "-c", "release/v2.1.208"], repo)
        (repo / "scripts" / "enrich.py").write_text("print('hi')\n")
        _run_cmd(["git", "add", "scripts/enrich.py"], repo)

        result = _invoke(repo)
        assert result.returncode == 0, result.stderr
        assert "-m scripts.stamp_release_version" in result.stderr
        assert "--check-version 2.1.208" in result.stderr
        assert "-m scripts.pipeline --version 2.1.208" in result.stderr
        assert (
            json.loads((repo / "docs" / "specifications" / "api" / "domain.json").read_text())[
                "version"
            ]
            == "2.1.208"
        )

    def test_untracked_generated_output_is_preserved_and_rejected(self, tmp_path: Path) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)
        scratch = repo / "docs" / "specifications" / "api" / "operator.json"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text("operator work\n")
        (repo / "config" / "thing.yaml").write_text("key: value\n")
        _run_cmd(["git", "add", "config/thing.yaml"], repo)

        result = _invoke(repo)

        assert result.returncode == 1
        assert "generated output already has" in result.stdout
        assert scratch.read_text() == "operator work\n"
        assert "scripts.pipeline" not in result.stderr

    def test_ignored_generated_output_is_preserved_and_rejected(self, tmp_path: Path) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)
        (repo / ".gitignore").write_text("docs/specifications/api/\n")
        _run_cmd(["git", "add", ".gitignore"], repo)
        _run_cmd(["git", "commit", "-q", "-m", "ignore generated tree"], repo)
        scratch = repo / "docs" / "specifications" / "api" / "operator.json"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text("ignored operator work\n")
        (repo / "config" / "thing.yaml").write_text("key: value\n")
        _run_cmd(["git", "add", "config/thing.yaml"], repo)

        result = _invoke(repo)

        assert result.returncode == 1
        assert scratch.read_text() == "ignored operator work\n"
        assert "scripts.pipeline" not in result.stderr

    def test_unstaged_pipeline_input_is_rejected(self, tmp_path: Path) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)
        tracked = repo / "scripts" / "tracked.py"
        tracked.write_text("original\n")
        _run_cmd(["git", "add", "scripts/tracked.py"], repo)
        _run_cmd(["git", "commit", "-q", "-m", "add tracked input"], repo)
        tracked.write_text("unstaged\n")
        (repo / "config" / "thing.yaml").write_text("key: value\n")
        _run_cmd(["git", "add", "config/thing.yaml"], repo)

        result = _invoke(repo)

        assert result.returncode == 1
        assert "pipeline input has unstaged" in result.stdout
        assert "scripts/tracked.py" in result.stdout
        assert "scripts.pipeline" not in result.stderr


def test_release_verification_precedes_generated_output_staging() -> None:
    hook = _project_hook().read_text()

    assert hook.index("--check-version") < hook.index("git add -u")


def test_only_release_branches_promote_generated_output() -> None:
    hook = _project_hook().read_text()

    assert '[[ "$CURRENT_BRANCH" != release/v* ]]' in hook
    assert hook.index('[[ "$CURRENT_BRANCH" != release/v* ]]') < hook.index("rsync -a --delete")
    assert '--report-dir "$TEMP_REPORT"' in hook


def test_hook_has_no_catalog_migration_exception() -> None:
    hook = _project_hook().read_text()

    assert "CATALOG_MIGRATION" not in hook
    assert "MIGRATION_CATALOG" not in hook
    assert _OWNERSHIP_RELATIVE in hook


def test_hook_checks_the_catalog_file_without_treating_it_as_a_complete_spec_tree() -> None:
    hook = _project_hook().read_text()

    assert 'scripts.stamp_release_version "$TEMP_ROOT"' not in hook
    assert 'jq -e --arg version "$EXPECTED_VERSION"' in hook
    assert '"$TEMP_CATALOG"' in hook


class TestForceOverride:
    """FORCE_PIPELINE=1 runs the pipeline even when no inputs are staged."""

    def test_force_overrides_skip(self, tmp_path: Path) -> None:
        hook_src = _project_hook()
        repo = _setup_repo(tmp_path, hook_src)

        (repo / "README.md").write_text("edited\n")
        _run_cmd(["git", "add", "README.md"], repo)

        result = _invoke(repo, force=True)
        assert "Running F5 XC API enrichment pipeline" in result.stdout
        assert "skipping enrichment + lint" not in result.stdout


def _project_hook() -> Path:
    """Path to the repo's own copy of the hook."""
    return _PROJECT_HOOK
