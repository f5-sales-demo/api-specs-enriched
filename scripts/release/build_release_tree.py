# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Run the sole canonical enriched release-tree build command graph."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

SEMANTIC_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


class ReleaseTreeBuildError(RuntimeError):
    """The canonical release-tree build failed."""


def canonical_commands(
    *,
    root: Path,
    input_dir: Path,
    version: str,
    python: str,
    biome: str,
) -> tuple[tuple[str, ...], ...]:
    """Return the exact command graph shared by local, CI, and verification builds."""
    specifications = root / "docs" / "specifications" / "api"
    reports = root / "reports"
    api_reference = root / "docs" / "api-reference"
    openapi_config = root / "docs" / "openapi-specs-config.json"
    catalog = root / "release" / "api-catalog.json"
    return (
        (
            python,
            "-m",
            "scripts.pipeline",
            "--version",
            version,
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(specifications),
            "--report-dir",
            str(reports),
        ),
        (
            python,
            "-m",
            "scripts.stamp_release_version",
            str(specifications),
            "--check-version",
            version,
        ),
        (
            python,
            "-m",
            "scripts.compile_catalog",
            "--version",
            version,
            "--input",
            str(specifications / "openapi.json"),
            "--output",
            str(catalog),
        ),
        (
            python,
            "-m",
            "scripts.generate_api_viewer",
            "--spec-dir",
            str(specifications),
            "--mdx-dir",
            str(api_reference),
            "--openapi-config",
            str(openapi_config),
        ),
        (
            biome,
            "format",
            "--write",
            str(specifications),
            str(api_reference),
            str(openapi_config),
        ),
    )


def build_release_tree(
    *,
    root: Path,
    input_dir: Path,
    version: str,
    python: str = sys.executable,
    biome: str = "biome",
) -> None:
    """Execute the canonical command graph in a normalized environment."""
    if not SEMANTIC_VERSION.fullmatch(version):
        raise ReleaseTreeBuildError(f"build version is not semantic: {version!r}")
    if not root.is_dir() or root.is_symlink():
        raise ReleaseTreeBuildError(f"build root is missing or unsafe: {root}")
    if not input_dir.is_dir() or input_dir.is_symlink():
        raise ReleaseTreeBuildError(f"input directory is missing or unsafe: {input_dir}")
    environment = dict(os.environ)
    environment.pop("API_SPECS_SKIP_BIOME", None)
    environment.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONHASHSEED": "0",
            "PYTHONUTF8": "1",
            "TZ": "UTC",
        }
    )
    command: tuple[str, ...] = ()
    try:
        for command in canonical_commands(
            root=root,
            input_dir=input_dir,
            version=version,
            python=python,
            biome=biome,
        ):
            subprocess.run(command, check=True, env=environment)
    except (OSError, subprocess.CalledProcessError) as exc:
        label = command[2] if len(command) > 2 and command[1] == "-m" else command[0]
        raise ReleaseTreeBuildError(f"canonical build command failed: {label}") from exc


def _semantic_version(value: str) -> str:
    if not SEMANTIC_VERSION.fullmatch(value):
        raise argparse.ArgumentTypeError(f"not a semantic version: {value!r}")
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse canonical release-tree build arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, type=_semantic_version)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--biome", default="biome")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Build one release tree using the current Python interpreter."""
    args = parse_args(argv)
    try:
        build_release_tree(
            root=args.root,
            input_dir=args.input_dir,
            version=args.version,
            biome=args.biome,
        )
    except ReleaseTreeBuildError as exc:
        print(f"Release-tree build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
