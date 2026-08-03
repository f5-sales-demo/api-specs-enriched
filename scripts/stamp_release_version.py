# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Stamp generated artifacts with the repository release version."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_REQUIRED_ARTIFACTS = frozenset(
    {
        "index.json",
        "minimal-export-defaults.json",
        "namespace_profiles.json",
        "openapi.json",
        "validation.json",
    }
)


def stamp_document(document: Any, version: str) -> bool:
    """Apply a build version to a generated document in place.

    OpenAPI documents carry their build identity in ``info.version``. Auxiliary
    build artifacts carry it in a top-level ``version`` field. Self-describing
    ``$schema`` artifacts have their own format version and must not be changed.
    """
    if not _SEMVER.fullmatch(version):
        raise ValueError(f"build version is not semantic: {version!r}")
    if not isinstance(document, dict) or "$schema" in document:
        return False

    info = document.get("info")
    is_openapi = "openapi" in document or "swagger" in document
    if is_openapi and isinstance(info, dict):
        if info.get("version") == version:
            return False
        info["version"] = version
        return True

    if "version" in document:
        if document["version"] == version:
            return False
        document["version"] = version
        return True

    return False


def stamp_directory(directory: Path, version: str) -> int:
    """Stamp all supported JSON artifacts in *directory*."""
    if not _SEMVER.fullmatch(version):
        raise ValueError(f"build version is not semantic: {version!r}")
    changed = 0
    for path in sorted(directory.glob("*.json")):
        document = json.loads(path.read_text())
        if not stamp_document(document, version):
            continue
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        changed += 1
    return changed


def document_build_version(document: Any) -> str | None:
    """Return a generated document's build identity, if it carries one."""
    if not isinstance(document, dict) or "$schema" in document:
        return None
    info = document.get("info")
    if ("openapi" in document or "swagger" in document) and isinstance(info, dict):
        version = info.get("version")
        return version if isinstance(version, str) else None
    version = document.get("version")
    return version if isinstance(version, str) else None


def _read_json_object(path: Path) -> Any:
    """Read one generated JSON document with a path-specific failure."""
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"generated artifact is not valid JSON: {path}") from exc


def version_mismatches(directory: Path, expected: str) -> list[tuple[Path, str]]:
    """Validate a complete generated tree and return version disagreements.

    An empty or partial candidate is never a valid way to replace the committed
    generated tree. Every non-schema JSON artifact must identify its build, and
    every domain named by the index must exist.
    """
    if not _SEMVER.fullmatch(expected):
        raise ValueError(f"expected version is not semantic: {expected!r}")
    if not directory.is_dir():
        raise ValueError(f"generated artifact directory does not exist: {directory}")

    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ValueError(f"generated artifact directory is empty: {directory}")

    names = {path.name for path in paths}
    missing_core = sorted(_REQUIRED_ARTIFACTS - names)
    if missing_core:
        raise ValueError(f"generated artifact tree is missing: {', '.join(missing_core)}")

    documents: dict[str, Any] = {}
    for path in paths:
        documents[path.name] = _read_json_object(path)

    index = documents["index.json"]
    specifications = index.get("specifications") if isinstance(index, dict) else None
    if not isinstance(specifications, list) or not specifications:
        raise ValueError("index.json has no non-empty specifications list")
    indexed_names: list[str] = []
    for entry in specifications:
        filename = entry.get("file") if isinstance(entry, dict) else None
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError(f"index.json contains an invalid domain filename: {filename!r}")
        if filename not in documents:
            raise ValueError(f"index.json references missing domain artifact: {filename}")
        indexed_names.append(filename)
    if len(indexed_names) != len(set(indexed_names)):
        raise ValueError("index.json contains a duplicate domain filename")

    mismatches = []
    build_artifact_count = 0
    for path in paths:
        document = documents[path.name]
        if isinstance(document, dict) and "$schema" in document:
            continue
        actual = document_build_version(document)
        if actual is None:
            raise ValueError(f"generated artifact has no build version: {path}")
        build_artifact_count += 1
        if actual != expected:
            mismatches.append((path, actual))
    if build_artifact_count == 0:
        raise ValueError("generated artifact tree has no versioned build artifacts")
    expected_names = set(indexed_names) | set(_REQUIRED_ARTIFACTS)
    if names != expected_names:
        missing = sorted(expected_names - names)
        unexpected = sorted(names - expected_names)
        raise ValueError(
            "generated artifact tree differs from index contract"
            f"; missing={missing or 'none'}; unexpected={unexpected or 'none'}"
        )
    return mismatches


def main() -> None:
    """Run the release-version stamper."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory containing generated JSON")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--version", help="Release version to apply")
    action.add_argument("--check-version", help="Expected release version")
    args = parser.parse_args()

    if args.check_version:
        try:
            mismatches = version_mismatches(args.directory, args.check_version)
        except (OSError, ValueError) as exc:
            print(f"Artifact validation failed: {exc}")
            raise SystemExit(1) from exc
        if mismatches:
            for path, actual in mismatches:
                print(f"{path}: expected {args.check_version}, found {actual}")
            raise SystemExit(1)
        print(f"All generated artifact versions match {args.check_version}")
        return

    version = args.version
    if version is None:
        raise SystemExit("--version is required when not checking")
    changed = stamp_directory(args.directory, version)
    print(f"Stamped {changed} generated artifact(s) with version {version}")


if __name__ == "__main__":
    main()
