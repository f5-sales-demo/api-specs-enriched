# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Stage allowlisted Pages sources and describe every rendered output file."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
from pathlib import Path
from tempfile import TemporaryDirectory

PUBLICATION_MANIFEST_PATH = Path("api/publication-manifest.json")
STAGED_ENTRIES = (
    (Path("en"), Path("en")),
    (Path("api-reference"), Path("api-reference")),
    (Path("specifications"), Path("specifications")),
    (Path("openapi-specs-config.json"), Path("openapi-specs-config.json")),
)
ALLOWED_SOURCE_ENTRIES = frozenset(
    source.as_posix().casefold() for source, _destination in STAGED_ENTRIES
)


class PagesContentError(RuntimeError):
    """Pages inputs or rendered outputs violate the publication contract."""


def _entry_kind(path: Path) -> str:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise PagesContentError(f"required Pages source is unreadable: {path}") from exc
    if stat.S_ISLNK(mode):
        raise PagesContentError(f"Pages content contains a symlink: {path}")
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    raise PagesContentError(f"Pages content is not a regular file or directory: {path}")


def _copy_tree(source: Path, destination: Path) -> None:
    if _entry_kind(source) != "directory":
        raise PagesContentError(f"required Pages source is not a directory: {source}")
    destination.mkdir()
    for entry in sorted(source.iterdir(), key=lambda path: path.name):
        target = destination / entry.name
        kind = _entry_kind(entry)
        if kind == "directory":
            _copy_tree(entry, target)
        else:
            shutil.copyfile(entry, target)


def stage_content(source_root: Path, output_root: Path) -> None:
    """Atomically copy only the English and generated publication inputs."""
    if output_root.exists() or output_root.is_symlink():
        raise PagesContentError(f"Pages staging output already exists: {output_root}")
    if _entry_kind(source_root) != "directory":
        raise PagesContentError(f"Pages source root is not a directory: {source_root}")

    duplicate_api_reference = source_root / "en" / "api-reference"
    if duplicate_api_reference.exists() or duplicate_api_reference.is_symlink():
        raise PagesContentError(
            "duplicate English API reference source is forbidden; use docs/api-reference only",
        )

    output_root.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".pages-content-", dir=output_root.parent) as temporary:
        staged = Path(temporary) / "content"
        staged.mkdir()
        for source_relative, destination_relative in STAGED_ENTRIES:
            source = source_root / source_relative
            destination = staged / destination_relative
            kind = _entry_kind(source)
            if kind == "directory":
                _copy_tree(source, destination)
            else:
                shutil.copyfile(source, destination)
        staged.rename(output_root)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PagesContentError(f"rendered Pages file is unreadable: {path}") from exc
    return digest.hexdigest()


def write_publication_manifest(output_root: Path) -> Path:
    """Write a deterministic SHA-256 manifest for every rendered output file."""
    if _entry_kind(output_root) != "directory":
        raise PagesContentError(f"Pages output root is not a directory: {output_root}")

    manifest_path = output_root / PUBLICATION_MANIFEST_PATH
    if manifest_path.exists() or manifest_path.is_symlink():
        raise PagesContentError(f"Pages publication manifest already exists: {manifest_path}")

    entries = sorted(
        output_root.rglob("*"),
        key=lambda path: path.relative_to(output_root).as_posix(),
    )
    files: dict[str, str] = {}
    for entry in entries:
        kind = _entry_kind(entry)
        if kind == "file":
            relative = entry.relative_to(output_root).as_posix()
            files[relative] = _sha256(entry)
    if not files:
        raise PagesContentError("rendered Pages output contains no files")

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "files": files}
    try:
        with manifest_path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
    except OSError as exc:
        raise PagesContentError("Pages publication manifest could not be written") from exc
    return manifest_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage = subparsers.add_parser("stage", help="stage the allowlisted source tree")
    stage.add_argument("--source", required=True, type=Path)
    stage.add_argument("--output", required=True, type=Path)

    manifest = subparsers.add_parser("manifest", help="manifest every rendered file")
    manifest.add_argument("--root", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run one Pages content operation."""
    args = parse_args(argv)
    try:
        if args.command == "stage":
            stage_content(args.source, args.output)
        else:
            write_publication_manifest(args.root)
    except PagesContentError as exc:
        print(f"Pages content error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
