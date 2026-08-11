# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for scripts/verify_governance.py.

The verifier blocks PRs that touch files owned by docs-control's
governance template. Unit tests drive a throwaway git repo so the
assertion covers the full `git diff --name-only` path without needing
a network-dependent setup.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from scripts.verify_governance import (
    GovernanceViolationError,
    main,
    verify,
)

LEGACY_THREE_RECEIPT_EXACT_CALLER_BRANCH = (
    "sync/exact-caller-"
    "0123456789abcdef0123456789abcdef01234567"
    "89abcdef0123456789abcdef0123456789abcdef"
    "fedcba9876543210fedcba9876543210fedcba98"
)
FOUR_RECEIPT_EXACT_CALLER_BRANCH = (
    f"{LEGACY_THREE_RECEIPT_EXACT_CALLER_BRANCH}00112233445566778899aabbccddeeff00112233"
)
SKIPPED_AUDIT_EXACT_CALLER_BRANCH = (
    "sync/exact-caller-"
    "0123456789abcdef0123456789abcdef01234567"
    "89abcdef0123456789abcdef0123456789abcdef"
    "skipped"
    "fedcba9876543210fedcba9876543210fedcba98"
)
SKIPPED_LINT_EXACT_CALLER_BRANCH = (
    "sync/exact-caller-"
    "0123456789abcdef0123456789abcdef01234567"
    "skipped"
    "89abcdef0123456789abcdef0123456789abcdef"
    "fedcba9876543210fedcba9876543210fedcba98"
)
BOTH_OPTIONAL_RECEIPTS_SKIPPED_BRANCH = (
    "sync/exact-caller-"
    "0123456789abcdef0123456789abcdef01234567"
    "skipped"
    "skipped"
    "fedcba9876543210fedcba9876543210fedcba98"
)

if TYPE_CHECKING:
    from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


def _set_pull_request_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    head_ref: str,
    head_repository: str = "example/managed",
    base_repository: str = "example/managed",
) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "head": {"repo": {"full_name": head_repository}},
                },
            },
        ),
    )
    monkeypatch.setenv("GITHUB_HEAD_REF", head_ref)
    monkeypatch.setenv("GITHUB_REPOSITORY", base_repository)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))


@pytest.fixture(autouse=True)
def _clear_sync_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from GitHub's pull-request identity environment.

    Authorized automation tests install a complete synthetic event receipt.
    Every other test must exercise the verifier without CI state leaking in.
    """
    for variable in ("GITHUB_EVENT_PATH", "GITHUB_HEAD_REF", "GITHUB_REPOSITORY"):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal git repo with one commit on main and a branch with two changes.

    Also writes a governance.json stub so tests can point --governance-json at it.
    """
    _run(["git", "init", "-q", "-b", "main"], tmp_path)
    _run(["git", "config", "user.email", "t@t.t"], tmp_path)
    _run(["git", "config", "user.name", "t"], tmp_path)
    (tmp_path / "README.md").write_text("root\n")
    (tmp_path / "biome.json").write_text("{}\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "build.py").write_text("print('hi')\n")
    _run(["git", "add", "."], tmp_path)
    _run(["git", "commit", "-q", "-m", "init"], tmp_path)
    _run(["git", "checkout", "-q", "-b", "feature"], tmp_path)

    governance = tmp_path / "governance.json"
    governance.write_text(
        json.dumps({"protected_files": ["biome.json", "README.md"]}) + "\n",
    )
    return tmp_path


class TestVerify:
    """Direct calls to verify(); no argparse layer."""

    def test_clean_diff_returns_empty(self, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (repo / "scripts" / "build.py").write_text("print('ok')\n")
        _run(["git", "add", "."], repo)
        _run(["git", "commit", "-q", "-m", "touch untracked area"], repo)

        monkeypatch.chdir(repo)
        violations = verify("main", "feature", governance_path=repo / "governance.json")
        assert violations == []

    def test_touching_protected_is_flagged(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (repo / "biome.json").write_text('{"formatter": false}\n')
        _run(["git", "add", "."], repo)
        _run(["git", "commit", "-q", "-m", "edit biome"], repo)

        monkeypatch.chdir(repo)
        violations = verify("main", "feature", governance_path=repo / "governance.json")
        assert violations == ["biome.json"]

    def test_multiple_violations_are_sorted(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (repo / "biome.json").write_text('{"new": true}\n')
        (repo / "README.md").write_text("changed\n")
        _run(["git", "add", "."], repo)
        _run(["git", "commit", "-q", "-m", "edit two protected files"], repo)

        monkeypatch.chdir(repo)
        violations = verify("main", "feature", governance_path=repo / "governance.json")
        assert violations == ["README.md", "biome.json"]

    def test_missing_governance_raises(self, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(repo)
        with pytest.raises(GovernanceViolationError, match="not found"):
            verify("main", "feature", governance_path=repo / "does-not-exist.json")

    def test_malformed_governance_raises(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bad = repo / "bad.json"
        bad.write_text('{"protected_files": "not-a-list"}\n')
        monkeypatch.chdir(repo)
        with pytest.raises(GovernanceViolationError, match="must be a list"):
            verify("main", "feature", governance_path=bad)


class TestMain:
    """CLI entrypoint — exit-code contract."""

    def test_exit_zero_when_clean(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (repo / "scripts" / "build.py").write_text("print('v2')\n")
        _run(["git", "add", "."], repo)
        _run(["git", "commit", "-q", "-m", "non-governed change"], repo)

        monkeypatch.chdir(repo)
        rc = main(
            [
                "--base",
                "main",
                "--head",
                "feature",
                "--governance-json",
                str(repo / "governance.json"),
            ],
        )
        assert rc == 0
        captured = capsys.readouterr()
        assert "ok:" in captured.out

    def test_exit_one_when_violation(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (repo / "biome.json").write_text('{"x": 1}\n')
        _run(["git", "add", "."], repo)
        _run(["git", "commit", "-q", "-m", "edit"], repo)

        monkeypatch.chdir(repo)
        rc = main(
            [
                "--base",
                "main",
                "--head",
                "feature",
                "--governance-json",
                str(repo / "governance.json"),
            ],
        )
        assert rc == 1
        captured = capsys.readouterr()
        assert "biome.json" in captured.err
        assert "docs-control" in captured.err

    def test_exit_two_on_missing_manifest(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(repo)
        rc = main(
            [
                "--base",
                "main",
                "--head",
                "feature",
                "--governance-json",
                str(repo / "absent.json"),
            ],
        )
        assert rc == 2
        captured = capsys.readouterr()
        assert "error:" in captured.err

    @pytest.mark.parametrize(
        "head_ref",
        [
            "governance/sync-managed-files",
            "governance/sync-managed-files-05b3aea603b7-30784318975-1",
            FOUR_RECEIPT_EXACT_CALLER_BRANCH,
            SKIPPED_AUDIT_EXACT_CALLER_BRANCH,
            SKIPPED_LINT_EXACT_CALLER_BRANCH,
            BOTH_OPTIONAL_RECEIPTS_SKIPPED_BRANCH,
        ],
    )
    def test_same_repository_automation_branch_is_authorized(
        self,
        repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        head_ref: str,
    ) -> None:
        (repo / "biome.json").write_text('{"x": 1}\n')
        _run(["git", "add", "."], repo)
        _run(["git", "commit", "-q", "-m", "authorized governance update"], repo)
        _set_pull_request_context(monkeypatch, tmp_path, head_ref=head_ref)
        monkeypatch.chdir(repo)

        rc = main(
            [
                "--base",
                "main",
                "--head",
                "feature",
                "--governance-json",
                str(repo / "governance.json"),
            ],
        )

        assert rc == 0

    @pytest.mark.parametrize(
        "head_ref",
        [
            "governance/sync-managed-files",
            FOUR_RECEIPT_EXACT_CALLER_BRANCH,
        ],
    )
    def test_same_ref_fork_is_not_authorized(
        self,
        repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        head_ref: str,
    ) -> None:
        (repo / "biome.json").write_text('{"x": 1}\n')
        _run(["git", "add", "."], repo)
        _run(["git", "commit", "-q", "-m", "hostile fork update"], repo)
        _set_pull_request_context(
            monkeypatch,
            tmp_path,
            head_ref=head_ref,
            head_repository="example/fork",
        )
        monkeypatch.chdir(repo)

        rc = main(
            [
                "--base",
                "main",
                "--head",
                "feature",
                "--governance-json",
                str(repo / "governance.json"),
            ],
        )

        assert rc == 1

    @pytest.mark.parametrize(
        "head_ref",
        [
            "governance/sync-managed-files-lookalike",
            "sync/exact-caller-0123456789ab-123-4",
            LEGACY_THREE_RECEIPT_EXACT_CALLER_BRANCH,
            FOUR_RECEIPT_EXACT_CALLER_BRANCH[:-1],
            f"{FOUR_RECEIPT_EXACT_CALLER_BRANCH[:-1]}g",
            f"{FOUR_RECEIPT_EXACT_CALLER_BRANCH}0",
            SKIPPED_AUDIT_EXACT_CALLER_BRANCH.replace("skipped", "skip", 1),
            SKIPPED_LINT_EXACT_CALLER_BRANCH.replace("skipped", "SKIPPED", 1),
        ],
    )
    def test_lookalike_automation_branch_is_not_authorized(
        self,
        repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        head_ref: str,
    ) -> None:
        (repo / "biome.json").write_text('{"x": 1}\n')
        _run(["git", "add", "."], repo)
        _run(["git", "commit", "-q", "-m", "lookalike update"], repo)
        _set_pull_request_context(monkeypatch, tmp_path, head_ref=head_ref)
        monkeypatch.chdir(repo)

        rc = main(
            [
                "--base",
                "main",
                "--head",
                "feature",
                "--governance-json",
                str(repo / "governance.json"),
            ],
        )

        assert rc == 1

    def test_missing_event_receipt_is_not_authorized(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (repo / "biome.json").write_text('{"x": 1}\n')
        _run(["git", "add", "."], repo)
        _run(["git", "commit", "-q", "-m", "unmeasured update"], repo)
        monkeypatch.setenv("GITHUB_HEAD_REF", "governance/sync-managed-files")
        monkeypatch.setenv("GITHUB_REPOSITORY", "example/managed")
        monkeypatch.chdir(repo)

        rc = main(
            [
                "--base",
                "main",
                "--head",
                "feature",
                "--governance-json",
                str(repo / "governance.json"),
            ],
        )

        assert rc == 1

    @pytest.mark.parametrize("missing_variable", ["GITHUB_HEAD_REF", "GITHUB_REPOSITORY"])
    def test_incomplete_environment_is_not_authorized(
        self,
        repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        missing_variable: str,
    ) -> None:
        (repo / "biome.json").write_text('{"x": 1}\n')
        _run(["git", "add", "."], repo)
        _run(["git", "commit", "-q", "-m", "incomplete identity"], repo)
        _set_pull_request_context(
            monkeypatch,
            tmp_path,
            head_ref="governance/sync-managed-files",
        )
        monkeypatch.delenv(missing_variable)
        monkeypatch.chdir(repo)

        rc = main(
            [
                "--base",
                "main",
                "--head",
                "feature",
                "--governance-json",
                str(repo / "governance.json"),
            ],
        )

        assert rc == 1

    @pytest.mark.parametrize(
        "event_content",
        [
            "not JSON",
            "[]",
            "{}",
            '{"pull_request": []}',
            '{"pull_request": {"head": []}}',
            '{"pull_request": {"head": {"repo": []}}}',
            '{"pull_request": {"head": {"repo": {"full_name": 7}}}}',
        ],
    )
    def test_malformed_event_receipt_is_not_authorized(
        self,
        repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        event_content: str,
    ) -> None:
        (repo / "biome.json").write_text('{"x": 1}\n')
        _run(["git", "add", "."], repo)
        _run(["git", "commit", "-q", "-m", "malformed identity"], repo)
        event_path = tmp_path / "event.json"
        event_path.write_text(event_content)
        monkeypatch.setenv("GITHUB_HEAD_REF", "governance/sync-managed-files")
        monkeypatch.setenv("GITHUB_REPOSITORY", "example/managed")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
        monkeypatch.chdir(repo)

        rc = main(
            [
                "--base",
                "main",
                "--head",
                "feature",
                "--governance-json",
                str(repo / "governance.json"),
            ],
        )

        assert rc == 1
