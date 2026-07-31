# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Stamp generated artifacts with the repository release version."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def stamp_document(document: Any, version: str) -> bool:
    """Apply a build version to a generated document in place.

    OpenAPI documents carry their build identity in ``info.version``. Auxiliary
    build artifacts carry it in a top-level ``version`` field. Self-describing
    ``$schema`` artifacts have their own format version and must not be changed.
    """
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
    changed = 0
    for path in sorted(directory.glob("*.json")):
        document = json.loads(path.read_text())
        if not stamp_document(document, version):
            continue
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        changed += 1
    return changed


def main() -> None:
    """Run the release-version stamper."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory containing generated JSON")
    parser.add_argument("--version", required=True, help="Release version to apply")
    args = parser.parse_args()

    changed = stamp_directory(args.directory, args.version)
    print(f"Stamped {changed} generated artifact(s) with version {args.version}")


if __name__ == "__main__":
    main()
