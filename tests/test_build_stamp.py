# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""The artifact stamp must be a property of the RELEASE, not of the download.

The first version of this derived from ``specs/original/manifest.json``'s
``timestamp``, which ``scripts/download.py`` wrote as ``datetime.now()``. That made a
rebuild reproducible on one machine with one seed, and non-reproducible everywhere
else: CI downloads the same release at a different moment, gets a different stamp,
and every one of the 43 generated specs differs. The reproducibility check added in
the same change caught exactly that on api-specs-enriched#1156.

The release's own ``published_at`` is the same value for everyone who downloads it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.utils.build_stamp import artifact_timestamp, reset_cache
from scripts.utils.raw_manifest import create_raw_manifest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reset_cache()
    yield
    reset_cache()


def _write_manifest(published_at: str | None) -> None:
    manifest_dir = Path("specs/original")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "source.json").write_text("{}\n")
    receipt: dict[str, object] = {
        "version": "2026.07.28-2",
        "tag_name": "v2026.07.28-2",
        "published_at": published_at or "2026-07-30T08:27:17Z",
        "asset_name": "api-specs-v2026.07.28-2.zip",
        "asset_size": 1,
        "asset_digest": "sha256:" + "0" * 64,
    }
    payload = create_raw_manifest(
        release_receipt=receipt,
        source_dir=manifest_dir,
        files=["source.json"],
    ).as_document()
    if published_at is None:
        payload["release_receipt"].pop("published_at")
    (manifest_dir / "manifest.json").write_text(json.dumps(payload))


def test_stamp_is_the_release_publish_time_not_the_download_time():
    _write_manifest(published_at="2026-07-30T08:27:17Z")
    stamp = artifact_timestamp()
    assert stamp == "2026-07-30T08:27:17+00:00"
    assert "00:56:58" not in stamp, "stamp came from the download moment, not the release"


def test_two_downloads_of_one_release_produce_one_stamp():
    """The whole point: same release, different download moments, same artifacts."""
    _write_manifest(published_at="2026-07-30T08:27:17Z")
    first = artifact_timestamp()

    reset_cache()
    _write_manifest(published_at="2026-07-30T08:27:17Z")

    assert artifact_timestamp() == first


def test_missing_release_publish_time_is_a_hard_error():
    """Falling back to the download time silently restores the non-reproducibility."""
    _write_manifest(published_at=None)
    with pytest.raises(RuntimeError, match="published_at"):
        artifact_timestamp()


def test_absent_manifest_is_a_hard_error():
    with pytest.raises(RuntimeError, match="manifest"):
        artifact_timestamp()


def test_noncanonical_timezone_spelling_is_rejected():
    _write_manifest(published_at="2026-07-30T08:27:17Z")
    manifest_path = Path("specs/original/manifest.json")
    payload = json.loads(manifest_path.read_text())
    payload["release_receipt"]["published_at"] = "2026-07-30T08:27:17+00:00"
    manifest_path.write_text(json.dumps(payload))

    with pytest.raises(RuntimeError, match="canonical UTC"):
        artifact_timestamp()
