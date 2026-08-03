# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for monotonic upstream release receipt consumption."""

from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.release.upstream_receipt import require_receipt_progression


def _receipt(**overrides: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "version": "2026.07.30-1",
        "tag_name": "v2026.07.30-1",
        "published_at": "2026-08-03T15:00:00Z",
        "asset_name": "api-specs-v2026.07.30-1.zip",
        "asset_size": 123,
        "asset_digest": f"sha256:{'a' * 64}",
    }
    receipt.update(overrides)
    return receipt


def test_exact_repeated_receipt_is_idempotent() -> None:
    receipt = _receipt()

    assert require_receipt_progression(receipt, deepcopy(receipt)) == receipt


def test_later_publication_is_accepted_even_after_version_reset() -> None:
    committed = _receipt(
        version="2026.07.30-24",
        tag_name="v2026.07.30-24",
        asset_name="api-specs-v2026.07.30-24.zip",
        published_at="2026-08-03T14:30:13Z",
    )
    candidate = _receipt(published_at="2026-08-03T15:00:00Z")

    assert require_receipt_progression(committed, candidate) == candidate


def test_older_immutable_receipt_cannot_roll_content_backward() -> None:
    committed = _receipt()
    replayed = _receipt(
        version="2026.07.30-24",
        tag_name="v2026.07.30-24",
        asset_name="api-specs-v2026.07.30-24.zip",
        published_at="2026-08-03T14:30:13Z",
    )

    with pytest.raises(RuntimeError, match="older than committed receipt"):
        require_receipt_progression(committed, replayed)


def test_equal_publication_time_with_different_identity_fails_closed() -> None:
    committed = _receipt()
    conflicting = _receipt(
        asset_name="api-specs-v2026.07.30-1-conflict.zip",
        asset_digest=f"sha256:{'b' * 64}",
    )

    with pytest.raises(RuntimeError, match="same published_at"):
        require_receipt_progression(committed, conflicting)
