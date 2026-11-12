# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Validate a canonical scanner PR against immutable producer and Git evidence."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from typing import Any
from urllib.request import Request, urlopen

SCANNER_PATHS = sorted(
    [".gitleaks.toml", "scripts/check-pii.sh", "scripts/check_pii.py", "tests/test-check-pii.sh"],
)
OWNER = "f5-sales-demo/docs-control"
SHA = re.compile(r"[0-9a-f]{40}")
NOTE = re.compile(
    r"<!-- scanner-reconciler v=1 source=([a-f0-9]{40}) scope=([a-f0-9]{64}) "
    r"desired-tree=([a-f0-9]{64}) base=([a-f0-9]{40}) -->\n\nCloses #([1-9][0-9]*)",
)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _api(endpoint: str) -> Any:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"https://api.github.com/repos/{endpoint}", headers=headers)
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS API origin
        return json.load(response)


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _producer_json(path: str, revision: str) -> dict:
    receipt = _api(f"{OWNER}/contents/{path}?ref={revision}")
    _require(receipt.get("encoding") == "base64", "incomplete producer content")
    content = base64.b64decode(receipt["content"])
    blob = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()  # noqa: S324
    _require(blob == receipt.get("sha"), "producer content digest mismatch")
    value = json.loads(content)
    _require(isinstance(value, dict), "malformed producer JSON")
    return value


def _desired(source: str, repository: str, desired_hash: str) -> dict:
    producer = _api(f"{OWNER}/commits/main")["sha"]
    _require(bool(SHA.fullmatch(producer)), "invalid producer revision")
    manifest = _producer_json(".github/config/managed-files-manifest.json", producer)
    config = _producer_json(".github/config/repo-settings.json", producer)["managed_files"]
    _require(manifest["source_commit"] == source, "superseded canonical source")
    _require(config["source_repo"] == OWNER, "foreign producer")
    skipped = config.get("skip_files", {}).get(repository, [])
    _require(not set(skipped).intersection(SCANNER_PATHS), "unsupported scanner opt-out")
    selected = [entry for entry in manifest["files"] if entry["path"] in SCANNER_PATHS]
    _require([entry["path"] for entry in selected] == SCANNER_PATHS, "incomplete scanner manifest")
    policies = {entry["dest"]: entry for entry in config["files"]}
    files = []
    for entry in selected:
        policy = policies[entry["path"]]
        _require(policy["src"] == entry["src"] == entry["path"], "scanner source mismatch")
        _require(repository in policy.get("only_repos", [repository]), "scanner not applicable")
        _require(repository not in policy.get("absent_repos", []), "scanner retired for repository")
        _require(entry["mode"] in {"100644", "100755"}, "invalid scanner file mode")
        _require(bool(SHA.fullmatch(entry["sha"])), "invalid scanner blob digest")
        files.append(
            {"path": entry["path"], "sha": entry["sha"], "mode": entry["mode"], "src": entry["src"]}
        )
    desired = {"files": files, "deletes": []}
    _require(_hash(desired) == desired_hash, "scanner desired-tree digest mismatch")
    tree = _api(f"{OWNER}/git/trees/{source}?recursive=1")
    _require(tree.get("truncated") is False, "incomplete canonical source tree")
    blobs = {entry["path"]: entry for entry in tree["tree"]}
    for entry in files:
        _require(
            all(blobs[entry["src"]].get(key) == entry[key] for key in ("sha", "mode"))
            and blobs[entry["src"]].get("type") == "blob",
            "canonical source differs from manifest",
        )
    return desired


def _issue_body(note: str) -> str:
    return (
        f"{note}\n\nCanonical four-file scanner reconciliation.\n\n"
        "Problem: this repository differs from the immutable canonical privacy and secret scanners.\n\n"
        "Scope: .gitleaks.toml, scripts/check-pii.sh, scripts/check_pii.py, tests/test-check-pii.sh, "
        "subject to manifest opt-outs. Preserve all other files, settings, controls and owners.\n\n"
        "Acceptance: the linked PR changes only the selected paths; exact canonical blob SHAs and modes "
        "match its source manifest; required review and CI pass; merged main is independently verified. "
        "CE operational and UAT acceptance remain separate."
    )


def authorized_scanner_paths(
    event: dict, head_ref: str, repository: str, base: str, head: str
) -> set[str]:
    """Return exactly authorized scanner paths, rejecting incomplete ownership proof."""
    original = event["pull_request"]
    _require(
        re.fullmatch(r"f5-sales-demo/[A-Za-z0-9_.-]+", repository) is not None, "foreign repository"
    )
    _require(original["head"]["repo"]["full_name"] == repository, "fork scanner PR")
    pr = _api(f"{repository}/pulls/{original['number']}")
    _require(
        pr["head"]["sha"] == original["head"]["sha"] and pr["body"] == original["body"],
        "stale scanner event",
    )
    match = NOTE.fullmatch(pr["body"])
    _require(match is not None, "malformed scanner ownership note")
    assert match is not None
    source, scope, desired_hash, bound_base, issue_number = match.groups()
    name = repository.rsplit("/", 1)[1]
    expected_branch = (
        f"governance/scanner-{source[:12]}-{_hash(SCANNER_PATHS)[:12]}-{_hash(name)[:12]}"
    )
    _require(head_ref == pr["head"]["ref"] == expected_branch, "scanner branch identity differs")
    _require(scope == _hash(SCANNER_PATHS), "scanner scope digest differs")
    _require(
        pr["title"] == f"Reconcile canonical scanners @ {source[:12]}", "scanner title differs"
    )
    _require(
        pr["head"]["repo"]["full_name"] == pr["base"]["repo"]["full_name"] == repository,
        "scanner repository differs",
    )
    _require(
        pr["base"]["ref"] == "main" and pr["base"]["sha"] == bound_base, "scanner base advanced"
    )
    _require(_git("rev-parse", base) == bound_base, "local scanner base differs")
    revision = pr["head"]["sha"]
    _require(bool(SHA.fullmatch(revision)), "invalid scanner revision")
    _require(
        _git("rev-list", f"{bound_base}..{revision}").splitlines() == [revision],
        "scanner requires one introduced commit",
    )
    _require(_git("show", "-s", "--format=%P", revision) == bound_base, "scanner parent differs")
    _require(
        _git("show", "-s", "--format=%B", revision)
        == f"chore: reconcile canonical scanners @ {source}",
        "scanner commit identity differs",
    )
    local_head = _git("rev-parse", head)
    if local_head != revision:
        _require(
            _git("show", "-s", "--format=%P", local_head) == f"{bound_base} {revision}",
            "unbound checkout merge",
        )
        _require(
            not _git("diff", "--name-only", revision, local_head),
            "checkout merge changes scanner tree",
        )
    desired = _desired(source, name, desired_hash)
    changed = _git("diff", "--name-status", "--no-renames", bound_base, revision).splitlines()
    _require(
        bool(changed) and all(line.split("\t")[0] in {"A", "M"} for line in changed),
        "scanner deletion or empty change",
    )
    paths = {line.split("\t")[1] for line in changed}
    _require(paths <= set(SCANNER_PATHS), "scanner changed paths outside scope")
    expected_changes = set()
    for entry in desired["files"]:
        wanted = f"{entry['mode']} blob {entry['sha']}\t{entry['path']}"
        _require(
            _git("ls-tree", revision, "--", entry["path"]) == wanted,
            "partial canonical scanner head",
        )
        if _git("ls-tree", bound_base, "--", entry["path"]) != wanted:
            expected_changes.add(entry["path"])
    _require(paths == expected_changes, "scanner changes differ from canonical plan")
    issue = _api(f"{repository}/issues/{issue_number}")
    _require(
        issue["number"] == int(issue_number) and "pull_request" not in issue,
        "scanner issue identity differs",
    )
    _require(
        issue["title"] == pr["title"] and issue["body"] == _issue_body(pr["body"].split("\n\n")[0]),
        "scanner issue provenance differs",
    )
    _require(
        bool(pr["user"]["login"]) and issue["user"]["login"] == pr["user"]["login"],
        "scanner issue owner differs",
    )
    return paths
