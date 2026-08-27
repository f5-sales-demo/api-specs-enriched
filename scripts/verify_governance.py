#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Verify a PR does not modify files governed by docs-control.

`.claude/governance.json`'s ``protected_files`` list holds ~40 config
and workflow paths that the docs-control governance template owns.
A Claude Code session is blocked from editing them by the
`protect-managed-files.sh` PreToolUse hook, but that hook only fires
inside Claude Code — a developer editing the same file in any other
editor (or amending a commit on the CLI) bypasses it silently. The
next upstream governance-sync PR would then either clobber their
work or land a drift that nobody notices until the next release run
fails some obscure sub-linter.

This script closes the gap on the CI side:

    python3 -m scripts.verify_governance [--base REF] [--head REF]

Resolves changed paths against the range ``REF_BASE..REF_HEAD`` (default
``origin/main..HEAD``) and exits non-zero if any of them appears in
``.claude/governance.json``'s ``protected_files`` set.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_GOVERNANCE_JSON = Path(".claude/governance.json")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# Automation branches carry the governed files this check would otherwise
# reject. Authorization requires an exact branch identity and same-repository
# ownership proven by GitHub's pull-request event receipt.
AUTHORIZED_AUTOMATION_BRANCH = re.compile(
    r"(?:governance/sync-managed-files(?:-[0-9a-f]{12}-[1-9][0-9]*-[1-9][0-9]*)?|"
    r"sync/exact-caller-[0-9a-f]{160})",
)


def _is_authorized_automation_branch() -> bool:
    """Return whether GitHub proves an exact, same-repository automation PR."""
    head_ref = os.environ.get("GITHUB_HEAD_REF", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if (
        not head_ref
        or not repository
        or not event_path
        or AUTHORIZED_AUTOMATION_BRANCH.fullmatch(head_ref) is None
    ):
        return False

    try:
        with Path(event_path).open(encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, UnicodeError, ValueError):
        return False

    if not isinstance(event, dict):
        return False
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        return False
    head = pull_request.get("head")
    if not isinstance(head, dict):
        return False
    head_repo = head.get("repo")
    if not isinstance(head_repo, dict):
        return False
    head_repository = head_repo.get("full_name")
    return isinstance(head_repository, str) and head_repository == repository


class GovernanceViolationError(RuntimeError):
    """Raised when a diff touches a governed file."""


def _load_protected(governance_path: Path) -> set[str]:
    with governance_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    protected = data.get("protected_files", [])
    if not isinstance(protected, list):
        msg = f"{governance_path}: 'protected_files' must be a list, got {type(protected).__name__}"
        raise GovernanceViolationError(msg)
    return {str(entry) for entry in protected}


def _opted_out_paths(governance_path: Path) -> set[str]:
    """Return exact local exceptions declared by the governance receipt."""
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if REPOSITORY_RE.fullmatch(repository) is None:
        return set()
    repository_name = repository.rsplit("/", 1)[1]
    with governance_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    skip_files = data.get("skip_files", {})
    if not isinstance(skip_files, dict):
        raise GovernanceViolationError(f"{governance_path}: 'skip_files' must be an object")
    paths = skip_files.get(repository_name, [])
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise GovernanceViolationError(
            f"{governance_path}: skip_files[{repository_name!r}] must be a list of strings"
        )
    return set(paths)


def _changed_paths(base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..{head}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def verify(
    base: str,
    head: str,
    governance_path: Path = DEFAULT_GOVERNANCE_JSON,
) -> list[str]:
    """Return the list of violating paths (empty if clean).

    Raises:
        GovernanceViolationError: governance.json is missing or malformed.
        subprocess.CalledProcessError: `git diff` failed (bad ref, etc.).
    """
    if not governance_path.exists():
        msg = f"Governance manifest not found: {governance_path}"
        raise GovernanceViolationError(msg)

    protected = _load_protected(governance_path)
    opted_out = _opted_out_paths(governance_path)
    changed = _changed_paths(base, head)
    return sorted(path for path in changed if path in protected and path not in opted_out)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        description="Fail if a diff touches files governed by docs-control.",
    )
    parser.add_argument(
        "--base",
        default="origin/main",
        help="Base ref for the diff (default: origin/main)",
    )
    parser.add_argument(
        "--head",
        default="HEAD",
        help="Head ref for the diff (default: HEAD)",
    )
    parser.add_argument(
        "--governance-json",
        type=Path,
        default=DEFAULT_GOVERNANCE_JSON,
        help="Path to governance.json (default: .claude/governance.json)",
    )
    args = parser.parse_args(argv)

    if _is_authorized_automation_branch():
        head_ref = os.environ.get("GITHUB_HEAD_REF", "")
        print(f"ok: skipping governance check on authorized automation branch {head_ref}")
        return 0

    try:
        violations = verify(args.base, args.head, args.governance_json)
    except GovernanceViolationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not violations:
        print(f"ok: no governed files modified between {args.base} and {args.head}")
        return 0

    print(
        f"error: {len(violations)} governed file(s) modified between {args.base} and {args.head}:",
        file=sys.stderr,
    )
    for path in violations:
        print(f"  - {path}", file=sys.stderr)
    print(
        "These files are owned by f5-sales-demo/docs-control. "
        "Open an upstream issue/PR there instead of editing locally.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
