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


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reset_cache()
    yield
    reset_cache()


def _write_manifest(
    published_at: str | None, downloaded: str = "2026-07-31T00:56:58.785637+00:00"
) -> None:
    manifest_dir = Path("specs/original")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "version": "2.1.201",
        "release_version": "2026.07.28-2",
        "timestamp": downloaded,
        "file_count": 283,
    }
    if published_at is not None:
        payload["release_published_at"] = published_at
    (manifest_dir / "manifest.json").write_text(json.dumps(payload))


def test_stamp_is_the_release_publish_time_not_the_download_time():
    _write_manifest(published_at="2026-07-30T08:27:17Z")
    stamp = artifact_timestamp()
    assert stamp.startswith("2026-07-30T08:27:17")
    assert "00:56:58" not in stamp, "stamp came from the download moment, not the release"


def test_two_downloads_of_one_release_produce_one_stamp():
    """The whole point: same release, different download moments, same artifacts."""
    _write_manifest(published_at="2026-07-30T08:27:17Z", downloaded="2026-07-31T00:56:58+00:00")
    first = artifact_timestamp()

    reset_cache()
    _write_manifest(
        published_at="2026-07-30T08:27:17Z",
        downloaded="2026-07-31T09:14:02.113000+00:00",  # a later download of the same release
    )

    assert artifact_timestamp() == first


def test_missing_release_publish_time_is_a_hard_error():
    """Falling back to the download time silently restores the non-reproducibility."""
    _write_manifest(published_at=None)
    with pytest.raises(RuntimeError, match="release_published_at"):
        artifact_timestamp()


def test_absent_manifest_is_a_hard_error():
    with pytest.raises(RuntimeError, match="manifest"):
        artifact_timestamp()


def test_timezone_spelling_does_not_change_the_stamp():
    _write_manifest(published_at="2026-07-30T08:27:17Z")
    with_z = artifact_timestamp()

    reset_cache()
    Path("specs/original/manifest.json").unlink()
    _write_manifest(published_at="2026-07-30T08:27:17+00:00")
    assert artifact_timestamp() == with_z
