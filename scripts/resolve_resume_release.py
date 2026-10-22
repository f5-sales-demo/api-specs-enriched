"""Verify a merged, tagged, unpublished release before owning-workflow recovery."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def resolve(tag: str) -> dict[str, str]:
    """Require the exact merged release PR, reachable tag and absent release."""
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Resume release tag must be an exact semantic version")
    commit = subprocess.check_output(["git", "rev-parse", f"{tag}^{{}}"], text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Resume tag did not resolve to a commit")
    subprocess.run(["git", "merge-base", "--is-ancestor", commit, "origin/main"], check=True)
    prs = json.loads(
        subprocess.check_output(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "all",
                "--head",
                f"release/{tag}",
                "--json",
                "number,state,mergeCommit",
            ],
            text=True,
        )
    )
    if len(prs) != 1 or prs[0]["state"] != "MERGED" or prs[0]["mergeCommit"]["oid"] != commit:
        raise ValueError("Resume tag differs from the unique merged release PR")
    result = subprocess.run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/releases/tags/{tag}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 or "HTTP 404" not in result.stderr:
        raise ValueError(
            "Resume requires a verified absent release; service errors cannot prove absence"
        )
    version = tag.removeprefix("v")
    return {"current_version": version, "new_version": version, "bump_type": "patch"}


def main() -> None:
    """Emit owning-workflow outputs only after every identity gate passes."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    values = resolve(args.tag)
    with args.output.open("a", encoding="utf-8") as stream:
        stream.writelines(f"{key}={value}\n" for key, value in values.items())


if __name__ == "__main__":
    main()
