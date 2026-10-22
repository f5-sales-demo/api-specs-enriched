"""Reject publication recovery when tag, PR or release evidence is ambiguous."""

import json
import subprocess
from unittest.mock import patch

import pytest

from scripts.resolve_resume_release import resolve

COMMIT = "a" * 40


@pytest.mark.parametrize(
    "failure",
    [None, "invalid-tag", "foreign-pr", "open-pr", "duplicate", "release-present", "auth-error"],
)
def test_resume_requires_exact_merged_unpublished_identity(failure: str | None) -> None:
    prs = [
        {
            "number": 1978,
            "state": "OPEN" if failure == "open-pr" else "MERGED",
            "mergeCommit": {"oid": "b" * 40 if failure == "foreign-pr" else COMMIT},
        }
    ]
    if failure == "duplicate":
        prs *= 2

    def output(args: list[str], **_kwargs: object) -> str:
        return COMMIT if args[0] == "git" else json.dumps(prs)

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0)
        return subprocess.CompletedProcess(
            args,
            0 if failure == "release-present" else 1,
            stdout="{}",
            stderr="HTTP 401" if failure == "auth-error" else "HTTP 404",
        )

    with (
        patch("scripts.resolve_resume_release.subprocess.check_output", side_effect=output),
        patch("scripts.resolve_resume_release.subprocess.run", side_effect=run),
    ):
        if failure:
            with pytest.raises(ValueError, match="Resume"):
                resolve("main" if failure == "invalid-tag" else "v12.2.2")
        else:
            assert resolve("v12.2.2") == {
                "current_version": "12.2.2",
                "new_version": "12.2.2",
                "bump_type": "patch",
            }
