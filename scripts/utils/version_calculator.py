# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Resolve the deterministic build version from the committed artifact tree."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_INDEX_PATH = "docs/specifications/api/index.json"
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_RELEASE_BRANCH = re.compile(r"^release/v(?P<version>\d+\.\d+\.\d+)$")


def _git_output(*args: str) -> str:
    """Return one Git command's standard output or fail closed."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        message = f"cannot resolve committed build version: git {' '.join(args)} failed"
        raise RuntimeError(message) from exc
    return result.stdout.strip()


def get_build_version() -> str:
    """Return the build identity committed in the generated index.

    The generated tree is the release candidate, so its committed index is the
    authority. Tags are publication receipts and may legitimately lag while an
    automated release is still being reconciled; using them as generator input
    silently downgraded every artifact on release branches.
    """
    try:
        document = json.loads(_git_output("show", f"HEAD:{_INDEX_PATH}"))
    except json.JSONDecodeError as exc:
        message = "committed build version is unavailable: index.json is not valid JSON"
        raise RuntimeError(message) from exc

    version = document.get("version") if isinstance(document, dict) else None
    if not isinstance(version, str) or not _SEMVER.fullmatch(version) or version == "0.0.0":
        message = "committed build version is missing or is not a release semantic version"
        raise RuntimeError(message)

    branch = _git_output("branch", "--show-current")
    if branch.startswith("release/"):
        match = _RELEASE_BRANCH.fullmatch(branch)
        if match is None:
            message = f"release branch {branch!r} does not encode a semantic version"
            raise RuntimeError(message)
        branch_version = match.group("version")
        if branch_version != version:
            message = (
                f"release branch version {branch_version} disagrees with committed artifact "
                f"version {version}"
            )
            raise RuntimeError(message)
    return version


def highest_version(first: str, second: str) -> str:
    """Return the greater of two strict semantic versions."""
    for value in (first, second):
        if not _SEMVER.fullmatch(value):
            message = f"invalid semantic version: {value!r}"
            raise ValueError(message)
    return max((first, second), key=lambda value: tuple(map(int, value.split("."))))


def main(argv: Sequence[str] | None = None) -> int:
    """Print the committed version, optionally bounded by a published tag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-with", metavar="VERSION")
    args = parser.parse_args(argv)

    committed_version = get_build_version()
    version = (
        highest_version(committed_version, args.max_with)
        if args.max_with is not None
        else committed_version
    )
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
