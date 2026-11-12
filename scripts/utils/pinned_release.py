# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Resolve a reproducible upstream input from its committed release receipt."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import TYPE_CHECKING

from scripts.utils.github_release import (
    find_release_asset,
    get_release_by_tag,
    parse_release_version,
)

if TYPE_CHECKING:
    from pathlib import Path

FIELDS = {"version", "tag_name", "published_at", "asset_name", "asset_size", "asset_sha256"}


def resolve_pinned_release(
    config: dict, receipt_path: Path, token: str | None = None
) -> tuple[dict, dict]:
    """Resolve an exact six-field receipt, enforcing metadata and downloaded bytes."""
    receipt = json.loads(receipt_path.read_text())
    if not isinstance(receipt, dict) or set(receipt) != FIELDS:
        raise ValueError("pinned upstream receipt must contain exactly six identity fields")
    for field in FIELDS - {"asset_size"}:
        if not isinstance(receipt[field], str) or not receipt[field]:
            raise ValueError(f"pinned upstream receipt has invalid {field}")
    if (
        not isinstance(receipt["asset_size"], int)
        or isinstance(receipt["asset_size"], bool)
        or receipt["asset_size"] <= 0
    ):
        raise ValueError("pinned upstream asset size must be a positive integer")
    if re.fullmatch(r"[0-9a-f]{64}", receipt["asset_sha256"]) is None:
        raise ValueError("pinned upstream asset digest must be SHA-256")
    if receipt["version"] != parse_release_version(receipt["tag_name"]):
        raise ValueError("pinned upstream version and tag disagree")
    datetime.fromisoformat(receipt["published_at"].replace("Z", "+00:00"))
    source = config["source"]
    repository = source["repository"]
    release = get_release_by_tag(
        repository["owner"], repository["name"], receipt["tag_name"], token=token
    )
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("pinned upstream release must be published and stable")
    if (
        release["tag_name"] != receipt["tag_name"]
        or release["published_at"] != receipt["published_at"]
    ):
        raise ValueError("pinned upstream release metadata changed")
    asset = find_release_asset(release, receipt["asset_name"])
    matches = [item for item in release["assets"] if item.get("name") == receipt["asset_name"]]
    if asset is None or len(matches) != 1 or asset["size"] != receipt["asset_size"]:
        raise ValueError("pinned upstream asset is missing, ambiguous, or has changed size")
    digest = f"sha256:{receipt['asset_sha256']}"
    if asset.get("digest") is not None and asset["digest"].lower() != digest:
        raise ValueError("pinned upstream asset digest differs from release metadata")
    # The byte verifier always enforces the receipt, including older releases
    # whose GitHub metadata does not provide a digest.
    return release, {**asset, "digest": digest}
