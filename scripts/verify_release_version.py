"""Fail closed when release identity differs across generated publication surfaces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class ReleaseVersionError(ValueError):
    """Raised when a release surface carries a different version identity."""


def _document_version(name: str, document: dict[str, Any]) -> str:
    if "openapi" in document or "swagger" in document:
        version = document.get("info", {}).get("version")
    else:
        version = document.get("version")
    if not isinstance(version, str):
        raise ReleaseVersionError(f"{name} does not carry a release version")
    return version


def verify_release_version_coherence(
    *,
    version: str,
    tag: str,
    documents: dict[str, dict[str, Any]],
    contract_manifest: dict[str, Any] | None = None,
    receipt: dict[str, Any] | None = None,
    archive_name: str | None = None,
) -> None:
    """Verify every supplied source and publication surface names one release."""
    expected_tag = f"v{version}"
    if tag != expected_tag:
        raise ReleaseVersionError(f"release tag {tag!r} does not match {expected_tag!r}")
    for name, document in documents.items():
        actual = _document_version(name, document)
        if actual != version:
            raise ReleaseVersionError(f"{name} version {actual!r} does not match {version!r}")
    if contract_manifest is not None:
        manifest_tag = contract_manifest.get("release", {}).get("tag")
        if manifest_tag != expected_tag:
            raise ReleaseVersionError(
                f"contract manifest tag {manifest_tag!r} does not match {expected_tag!r}"
            )
    if receipt is not None and receipt.get("version") != version:
        raise ReleaseVersionError(
            f"publication receipt version {receipt.get('version')!r} does not match {version!r}"
        )
    if archive_name is not None:
        expected_archive = f"f5xc-api-specs-v{version}.zip"
        if Path(archive_name).name != expected_archive:
            raise ReleaseVersionError(
                f"release archive {Path(archive_name).name!r} does not match {expected_archive!r}"
            )


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text())
    if not isinstance(document, dict):
        raise ReleaseVersionError(f"{path.name} must contain a JSON object")
    return document


def main() -> None:
    """Validate release files supplied by the publication workflow."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--document", action="append", type=Path, default=[])
    parser.add_argument("--contract-manifest", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--archive-name")
    args = parser.parse_args()
    documents = {path.name: _load(path) for path in args.document}
    verify_release_version_coherence(
        version=args.version,
        tag=args.tag,
        documents=documents,
        contract_manifest=_load(args.contract_manifest) if args.contract_manifest else None,
        receipt=_load(args.receipt) if args.receipt else None,
        archive_name=args.archive_name,
    )


if __name__ == "__main__":
    main()
