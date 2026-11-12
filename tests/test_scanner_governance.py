# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Regress exact canonical scanner ownership, scope, and checkout evidence."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from scripts.utils import scanner_governance as guard

if TYPE_CHECKING:
    from pathlib import Path


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _content(data: dict) -> dict:
    raw = json.dumps(data).encode()
    return {
        "encoding": "base64",
        "content": base64.b64encode(raw).decode(),
        "sha": hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest(),  # noqa: S324
    }


@pytest.fixture
def candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "Example")
    _git(tmp_path, "config", "user.email", "example@example.com")
    for path in guard.SCANNER_PATHS:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("original\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "baseline")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "checkout", "-qb", "candidate")
    (tmp_path / "scripts/check_pii.py").write_text("canonical\n")
    source, producer = "1" * 40, "2" * 40
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", f"chore: reconcile canonical scanners @ {source}")
    revision = _git(tmp_path, "rev-parse", "HEAD")
    files, blobs, policies = [], [], []
    for path in guard.SCANNER_PATHS:
        blob = _git(tmp_path, "rev-parse", f"HEAD:{path}")
        files.append({"path": path, "sha": blob, "mode": "100644", "src": path})
        blobs.append({"path": path, "sha": blob, "mode": "100644", "type": "blob"})
        policies.append({"src": path, "dest": path})
    desired = {"files": files, "deletes": []}
    repo = "f5-sales-demo/api-specs-enriched"
    # pylint: disable-next=protected-access
    scope = guard._hash(guard.SCANNER_PATHS)
    branch = (
        # pylint: disable-next=protected-access
        f"governance/scanner-{source[:12]}-{scope[:12]}-{guard._hash('api-specs-enriched')[:12]}"
    )
    # pylint: disable-next=protected-access
    note = f"<!-- scanner-reconciler v=1 source={source} scope={scope} desired-tree={guard._hash(desired)} base={base} -->"
    pr = {
        "number": 2,
        "body": f"{note}\n\nCloses #1",
        "title": f"Reconcile canonical scanners @ {source[:12]}",
        "head": {"sha": revision, "ref": branch, "repo": {"full_name": repo}},
        "base": {"sha": base, "ref": "main", "repo": {"full_name": repo}},
        "user": {"login": "example"},
    }
    manifest = {"source_commit": source, "files": files, "absent_paths": []}
    config = {"managed_files": {"source_repo": guard.OWNER, "skip_files": {}, "files": policies}}
    issue = {
        "number": 1,
        # pylint: disable-next=protected-access
        "body": guard._issue_body(note),
        "title": pr["title"],
        "user": {"login": "example"},
    }
    replies = {
        f"{repo}/pulls/2": pr,
        f"{guard.OWNER}/commits/main": {"sha": producer},
        f"{guard.OWNER}/git/trees/{source}?recursive=1": {"truncated": False, "tree": blobs},
        f"{repo}/issues/1": issue,
    }

    def api(endpoint: str) -> dict:
        if "/contents/.github/config/managed-files-manifest.json?" in endpoint:
            assert endpoint.endswith(f"ref={producer}")
            return _content(manifest)
        if "/contents/.github/config/repo-settings.json?" in endpoint:
            assert endpoint.endswith(f"ref={producer}")
            return _content(config)
        return replies[endpoint]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(guard, "_api", api)
    return {
        "event": {"pull_request": copy.deepcopy(pr)},
        "pr": pr,
        "branch": branch,
        "repo": repo,
        "manifest": manifest,
        "config": config,
        "issue": issue,
        "replies": replies,
        "root": tmp_path,
        "base": base,
        "revision": revision,
    }


def _authorize(candidate: dict) -> set[str]:
    return guard.authorized_scanner_paths(
        candidate["event"], candidate["branch"], candidate["repo"], candidate["base"], "HEAD"
    )


def test_exact_canonical_scanner_is_authorized(candidate: dict) -> None:
    assert _authorize(candidate) == {"scripts/check_pii.py"}


@pytest.mark.parametrize(
    "case",
    [
        "fork",
        "lookalike",
        "stale_event",
        "wrong_scope",
        "wrong_tree",
        "superseded_source",
        "wrong_title",
        "advanced_base",
        "wrong_parent",
        "extra_commit",
        "out_of_scope",
        "rename",
        "partial_tree",
        "wrong_mode",
        "source_tree",
        "truncated",
        "missing_manifest",
        "opt_out",
        "foreign_producer",
        "issue_owner",
        "issue_body",
        "issue_is_pr",
        "wrong_commit_message",
    ],
)
def test_incomplete_or_forged_scanner_is_rejected(candidate: dict, case: str) -> None:
    pr, manifest = candidate["pr"], candidate["manifest"]
    if case == "fork":
        candidate["event"]["pull_request"]["head"]["repo"]["full_name"] = "example/fork"
    elif case == "lookalike":
        candidate["branch"] += "-lookalike"
    elif case == "stale_event":
        candidate["event"]["pull_request"]["head"]["sha"] = "3" * 40
    elif case in {"wrong_scope", "wrong_tree"}:
        key = "scope" if case == "wrong_scope" else "desired-tree"
        pr["body"] = pr["body"].replace(f"{key}=", f"{key}=0")
        candidate["event"]["pull_request"]["body"] = pr["body"]
    elif case == "superseded_source":
        manifest["source_commit"] = "3" * 40
    elif case == "wrong_title":
        pr["title"] = "foreign"
    elif case == "advanced_base":
        pr["base"]["sha"] = "3" * 40
    elif case == "wrong_parent":
        candidate["event"]["pull_request"]["body"] = pr["body"] = pr["body"].replace(
            candidate["base"], "3" * 40
        )
    elif case in {"extra_commit", "out_of_scope", "rename", "partial_tree", "wrong_commit_message"}:
        root = candidate["root"]
        if case == "extra_commit":
            _git(root, "commit", "--allow-empty", "-qm", "extra")
        else:
            if case == "out_of_scope":
                (root / "README.md").write_text("extra\n")
            elif case == "rename":
                (root / ".gitleaks.toml").rename(root / "other.toml")
            elif case == "partial_tree":
                (root / "scripts/check-pii.sh").write_text("foreign\n")
            _git(root, "add", ".")
            message = (
                "foreign"
                if case == "wrong_commit_message"
                else f"chore: reconcile canonical scanners @ {'1' * 40}"
            )
            _git(root, "commit", "--amend", "-qm", message)
        pr["head"]["sha"] = candidate["event"]["pull_request"]["head"]["sha"] = _git(
            root, "rev-parse", "HEAD"
        )
    elif case == "wrong_mode":
        manifest["files"][0]["mode"] = "120000"
    elif case in {"source_tree", "truncated"}:
        tree = candidate["replies"][f"{guard.OWNER}/git/trees/{'1' * 40}?recursive=1"]
        if case == "truncated":
            tree["truncated"] = True
        else:
            tree["tree"][0]["sha"] = "3" * 40
    elif case == "missing_manifest":
        manifest["files"].pop()
    elif case == "opt_out":
        candidate["config"]["managed_files"]["skip_files"]["api-specs-enriched"] = [
            "scripts/check_pii.py"
        ]
    elif case == "foreign_producer":
        candidate["config"]["managed_files"]["source_repo"] = "example/foreign"
    elif case == "issue_owner":
        candidate["issue"]["user"]["login"] = "foreign"
    elif case == "issue_body":
        candidate["issue"]["body"] += "\nextra"
    elif case == "issue_is_pr":
        candidate["issue"]["pull_request"] = {}
    with pytest.raises(ValueError, match=r"scanner|producer|canonical|fork|repository"):
        _authorize(candidate)


def test_exact_checkout_merge_is_authorized(candidate: dict) -> None:
    root = candidate["root"]
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "--no-ff", "-qm", "checkout merge", "candidate")
    assert _authorize(candidate) == {"scripts/check_pii.py"}


def test_checkout_merge_with_added_content_is_rejected(candidate: dict) -> None:
    test_exact_checkout_merge_is_authorized(candidate)
    (candidate["root"] / "README.md").write_text("foreign\n")
    _git(candidate["root"], "add", ".")
    _git(candidate["root"], "commit", "--amend", "--no-edit", "-q")
    with pytest.raises(ValueError, match="checkout merge changes"):
        _authorize(candidate)


def test_api_failure_is_visible(candidate: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    def failed(_endpoint: str) -> dict:
        raise OSError("temporary transport failure")

    monkeypatch.setattr(guard, "_api", failed)
    with pytest.raises(OSError, match="transport"):
        _authorize(candidate)


def test_cli_authorizes_only_proven_scanner_paths(
    candidate: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.verify_governance import main

    root = candidate["root"]
    event_path = root / "event.json"
    event_path.write_text(json.dumps(candidate["event"]))
    governance = root / "governance.json"
    governance.write_text(json.dumps({"protected_files": [*guard.SCANNER_PATHS, "README.md"]}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_HEAD_REF", candidate["branch"])
    monkeypatch.setenv("GITHUB_REPOSITORY", candidate["repo"])
    assert (
        main(["--base", candidate["base"], "--head", "HEAD", "--governance-json", str(governance)])
        == 0
    )


def test_cli_returns_operational_failure_for_missing_evidence(
    candidate: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.verify_governance import main

    root = candidate["root"]
    governance = root / "governance.json"
    governance.write_text(json.dumps({"protected_files": guard.SCANNER_PATHS}))
    monkeypatch.setenv("GITHUB_HEAD_REF", candidate["branch"])
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert (
        main(["--base", candidate["base"], "--head", "HEAD", "--governance-json", str(governance)])
        == 2
    )
