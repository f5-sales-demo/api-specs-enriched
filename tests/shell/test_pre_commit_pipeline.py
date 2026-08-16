# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for scripts/hooks/pre-commit-pipeline.sh.

Specifically, the STEP 0 input-fingerprint skip that short-circuits the
~13 min enrichment pipeline when no pipeline-input files are staged.
The full pipeline run is NOT exercised here — we only verify the skip
decision against a throwaway git repo.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

_HOOK_RELATIVE = "scripts/hooks/pre-commit-pipeline.sh"
_PROJECT_HOOK = Path(__file__).resolve().parents[2] / _HOOK_RELATIVE


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
    generated = tmp_path / "docs" / "specifications" / "api"
    generated.mkdir(parents=True)
    (generated / "index.json").write_text('{"version":"2.1.208"}\n')

    hook_target = tmp_path / _HOOK_RELATIVE
    hook_target.parent.mkdir(parents=True, exist_ok=True)
    hook_target.write_text(hook_copy.read_text())
    hook_target.chmod(hook_target.stat().st_mode | stat.S_IEXEC)

    _run_cmd(["git", "add", "."], tmp_path)
    _run_cmd(["git", "commit", "-q", "-m", "init"], tmp_path)
    return tmp_path


def _invoke(
    cwd: Path,
    *,
    force: bool = False,
    python_script: str = '#!/usr/bin/env bash\necho "[fake python] args: $*" >&2\nexit 0\n',
    spectral_script: str = "#!/usr/bin/env bash\nexit 0\n",
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ}
    if force:
        env["FORCE_PIPELINE"] = "1"
    # We point PYTHON at `true` so that if the hook DOES fall through to
    # STEP 1 (pipeline run) it exits 0 without doing anything. Our assertions
    # then key on stdout to distinguish skip vs. fall-through.
    env["PATH"] = f"{cwd}/_fakebin:{env.get('PATH', '')}"
    fakebin = cwd / "_fakebin"
    fakebin.mkdir(exist_ok=True)
    python_stub = fakebin / "python3"
    python_stub.write_text(python_script)
    python_stub.chmod(python_stub.stat().st_mode | stat.S_IEXEC)
    spectral_stub = fakebin / "spectral"
    spectral_stub.write_text(spectral_script)
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


class TestGeneratedOutputRecovery:
    """Pipeline failures cannot leave generated specs modified or staged."""

    @staticmethod
    def _stage_pipeline_input(repo: Path) -> None:
        (repo / "config" / "thing.yaml").write_text("key: value\n")
        _run_cmd(["git", "add", "config/thing.yaml"], repo)

    def test_pipeline_failure_restores_generated_output(self, tmp_path: Path) -> None:
        repo = _setup_repo(tmp_path, _project_hook())
        self._stage_pipeline_input(repo)

        result = _invoke(
            repo,
            python_script="""#!/usr/bin/env bash
if [ "$1" = "-m" ]; then
  printf '{"version":"2.1.207"}\\n' > docs/specifications/api/index.json
  exit 1
fi
exit 0
""",
        )

        assert result.returncode == 1
        assert (repo / "docs/specifications/api/index.json").read_text() == '{"version":"2.1.208"}\n'
        assert _git_diff_is_empty(repo, "docs/specifications/api")
        assert _git_diff_is_empty(repo, "--cached", "docs/specifications/api")

    def test_lint_failure_restores_generated_output_before_staging(self, tmp_path: Path) -> None:
        repo = _setup_repo(tmp_path, _project_hook())
        self._stage_pipeline_input(repo)

        result = _invoke(
            repo,
            python_script="""#!/usr/bin/env bash
if [ "$1" = "-m" ]; then
  printf '{"version":"2.1.207"}\\n' > docs/specifications/api/index.json
  exit 0
fi
if [ "$1" = "scripts/lint.py" ]; then
  exit 1
fi
exit 0
""",
        )

        assert result.returncode == 1
        assert (repo / "docs/specifications/api/index.json").read_text() == '{"version":"2.1.208"}\n'
        assert _git_diff_is_empty(repo, "docs/specifications/api")
        assert _git_diff_is_empty(repo, "--cached", "docs/specifications/api")

    def test_release_branch_rejects_and_restores_version_downgrade(self, tmp_path: Path) -> None:
        repo = _setup_repo(tmp_path, _project_hook())
        _run_cmd(["git", "checkout", "-q", "-b", "release/v2.1.208"], repo)
        self._stage_pipeline_input(repo)

        result = _invoke(
            repo,
            python_script="""#!/usr/bin/env bash
if [ "$1" = "-m" ]; then
  printf '{"version":"2.1.207"}\\n' > docs/specifications/api/index.json
  exit 0
fi
exit 0
""",
        )

        assert result.returncode == 1
        assert "does not match release branch" in result.stdout
        assert (repo / "docs/specifications/api/index.json").read_text() == '{"version":"2.1.208"}\n'
        assert _git_diff_is_empty(repo, "--cached", "docs/specifications/api")


def _git_diff_is_empty(repo: Path, *args: str) -> bool:
    """Return whether one git-diff invocation has no output."""

    result = subprocess.run(
        ["git", "diff", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout == ""
def _project_hook() -> Path:
    """Path to the repo's own copy of the hook."""
    return _PROJECT_HOOK
