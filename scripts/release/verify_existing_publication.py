"""Verify an immutable API release before rebuilding its existing Pages content."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

TAG = re.compile(r"v([0-9]+\.[0-9]+\.[0-9]+)")
COMMIT = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
RECEIPT = re.compile(r"^<!-- publication-receipt:(\{[^\n]+\}) -->$", re.MULTILINE)


class PublicationError(ValueError):
    """The existing release does not satisfy its immutable publication contract."""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PublicationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def expected_assets(version: str) -> set[str]:
    """The asset contract enforced by build-publication-receipt.sh."""
    return {
        "api-catalog.json",
        "concurrency_contracts.json",
        "enrichment-coverage.json",
        f"f5xc-api-specs-v{version}.zip",
        "index.json",
        "minimal-export-defaults.json",
        "openapi.json",
        "smsv2-contract-manifest.json",
        "smsv2-contract.json",
        "smsv2-evidence-receipt.json",
        "smsv2_parity_manifest.json",
        "upstream-contract-changes.json",
        "upstream-contract-removals.json",
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def verify_release(release: dict[str, Any], assets_dir: Path, tag: str, commit: str) -> None:
    """Reject any mismatch among tag, receipt, GitHub metadata, and asset bytes."""
    tag_match = TAG.fullmatch(tag)
    if not tag_match or not COMMIT.fullmatch(commit):
        raise PublicationError("tag and commit must be exact vX.Y.Z and 40-hex values")
    version = tag_match.group(1)
    if release.get("tagName") != tag:
        raise PublicationError("release tag does not match requested tag")

    body = release.get("body")
    if not isinstance(body, str):
        raise PublicationError("release body is missing")
    receipts = RECEIPT.findall(body)
    if len(receipts) != 1:
        raise PublicationError("release must contain exactly one publication receipt")
    try:
        receipt = json.loads(receipts[0], object_pairs_hook=_unique_pairs)
    except json.JSONDecodeError as exc:
        raise PublicationError("publication receipt is invalid JSON") from exc
    if not isinstance(receipt, dict) or set(receipt) != {"assets", "commit", "version"}:
        raise PublicationError("publication receipt has an invalid schema")
    if receipt["commit"] != commit or receipt["version"] != version:
        raise PublicationError("publication receipt does not bind the tag and commit")
    expected = expected_assets(version)
    attested = receipt["assets"]
    if not isinstance(attested, dict) or set(attested) != expected:
        raise PublicationError("publication receipt asset set is incomplete or extra")

    metadata = release.get("assets")
    if not isinstance(metadata, list) or len(metadata) != len(expected):
        raise PublicationError("GitHub release asset set is incomplete or extra")
    by_name: dict[str, str] = {}
    for asset in metadata:
        if not isinstance(asset, dict):
            raise PublicationError("GitHub release asset entry is invalid")
        name, digest = asset.get("name"), asset.get("digest")
        if not isinstance(name, str) or name in by_name or not isinstance(digest, str):
            raise PublicationError("GitHub release asset name or digest is invalid")
        by_name[name] = digest
    if set(by_name) != expected:
        raise PublicationError("GitHub release asset set differs from receipt")

    if not assets_dir.is_dir() or {p.name for p in assets_dir.iterdir()} != expected:
        raise PublicationError("downloaded asset set differs from receipt")
    for name in sorted(expected):
        path = assets_dir / name
        attested_digest = attested[name]
        if (
            not path.is_file()
            or path.is_symlink()
            or not isinstance(attested_digest, str)
            or not DIGEST.fullmatch(attested_digest)
            or by_name[name] != attested_digest
            or _sha256(path) != attested_digest
        ):
            raise PublicationError(f"asset digest mismatch: {name}")


def main() -> None:
    """Validate CLI inputs and the downloaded immutable release."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-json", required=True, type=Path)
    parser.add_argument("--assets-dir", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    release = json.loads(args.release_json.read_text(), object_pairs_hook=_unique_pairs)
    if not isinstance(release, dict):
        raise PublicationError("release metadata must be a JSON object")
    verify_release(release, args.assets_dir, args.tag, args.commit)
    print(f"Verified {args.tag} assets and commit {args.commit}")


if __name__ == "__main__":
    main()
