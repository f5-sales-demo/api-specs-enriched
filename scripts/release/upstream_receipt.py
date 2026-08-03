# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Enforce monotonic consumption of immutable upstream release receipts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from scripts.utils.github_release import validate_release_receipt


def _published_at(receipt: dict[str, Any]) -> datetime:
    """Parse the canonical GitHub UTC publication timestamp."""
    return datetime.strptime(receipt["published_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def require_receipt_progression(
    committed: object,
    candidate: object,
) -> dict[str, Any]:
    """Return a validated candidate that cannot roll upstream content backward.

    GitHub's immutable ``published_at`` value is the ordering authority. Producer
    versions may reset during a clean-break release, so lexical version ordering is
    deliberately not used. Repeated delivery of the exact receipt is idempotent.
    """
    committed_receipt = validate_release_receipt(committed)
    candidate_receipt = validate_release_receipt(candidate)
    committed_time = _published_at(committed_receipt)
    candidate_time = _published_at(candidate_receipt)

    if candidate_time < committed_time:
        raise RuntimeError(
            "candidate upstream receipt is older than committed receipt: "
            f"{candidate_receipt['published_at']} < {committed_receipt['published_at']}"
        )
    if candidate_time == committed_time and candidate_receipt != committed_receipt:
        raise RuntimeError(
            "candidate upstream receipt has the same published_at as a different committed identity"
        )
    return candidate_receipt
