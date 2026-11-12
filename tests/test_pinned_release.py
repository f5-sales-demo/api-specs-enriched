# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Pin metadata and archive bytes while preserving latest-release collection."""

from __future__ import annotations

import copy
import hashlib
import json
import zipfile
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
import requests

from scripts import download
from scripts.utils import github_release, pinned_release

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("domains/example.json", '{"info":{"title":"Example"}}')
    raw = archive.read_bytes()
    receipt = {
        "version": "2026.11.09-1",
        "tag_name": "v2026.11.09-1",
        "published_at": "2026-11-09T17:58:55Z",
        "asset_name": "api-specs-v2026.11.09-1.zip",
        "asset_size": len(raw),
        "asset_sha256": hashlib.sha256(raw).hexdigest(),
    }
    asset = {
        "name": receipt["asset_name"],
        "size": len(raw),
        "browser_download_url": "https://github.com/example/input.zip",
    }
    release = {
        "tag_name": receipt["tag_name"],
        "published_at": receipt["published_at"],
        "assets": [asset],
    }
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    config = copy.deepcopy(download.DEFAULT_CONFIG)
    config["paths"] = {
        "original": str(tmp_path / "original"),
        "version_file": str(tmp_path / "version.json"),
    }
    lookup = Mock(return_value=release)
    monkeypatch.setattr(pinned_release, "get_release_by_tag", lookup)
    return {
        "receipt": receipt,
        "path": path,
        "asset": asset,
        "release": release,
        "config": config,
        "raw": raw,
        "lookup": lookup,
    }


def test_exact_receipt_requires_expected_bytes_even_without_upstream_digest(pinned: dict) -> None:
    release, asset = pinned_release.resolve_pinned_release(pinned["config"], pinned["path"])
    assert release is pinned["release"]
    assert asset["digest"] == f"sha256:{pinned['receipt']['asset_sha256']}"
    pinned["lookup"].assert_called_once_with(
        "f5-sales-demo", "api-specs", "v2026.11.09-1", token=None
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "other"),
        ("tag_name", 1),
        ("published_at", "bad"),
        ("asset_name", None),
        ("asset_size", True),
        ("asset_size", 0),
        ("asset_sha256", "bad"),
    ],
)
def test_malformed_receipt_fails_before_metadata_lookup(
    pinned: dict, field: str, value: object
) -> None:
    pinned["receipt"][field] = value
    pinned["path"].write_text(json.dumps(pinned["receipt"]))
    with pytest.raises(ValueError, match=r"pinned|Invalid isoformat"):
        pinned_release.resolve_pinned_release(pinned["config"], pinned["path"])
    pinned["lookup"].assert_not_called()


@pytest.mark.parametrize("case", ["missing", "extra", "array", "invalid_json"])
def test_incomplete_receipt_is_rejected(pinned: dict, case: str) -> None:
    data = pinned["receipt"]
    if case == "missing":
        data.pop("asset_sha256")
    elif case == "extra":
        data["downloaded_at"] = "today"
    elif case == "array":
        data = []
    pinned["path"].write_text("malformed" if case == "invalid_json" else json.dumps(data))
    with pytest.raises(ValueError, match=r"pinned|Expecting value"):
        pinned_release.resolve_pinned_release(pinned["config"], pinned["path"])


@pytest.mark.parametrize(
    "case", ["tag", "date", "missing_asset", "size", "digest", "duplicate", "draft", "prerelease"]
)
def test_changed_or_unavailable_release_is_rejected(pinned: dict, case: str) -> None:
    release = pinned["release"]
    if case == "tag":
        release["tag_name"] = "v2026.11.10-1"
    elif case == "date":
        release["published_at"] = "2026-11-10T17:58:55Z"
    elif case == "missing_asset":
        release["assets"] = []
    elif case == "size":
        pinned["asset"]["size"] += 1
    elif case == "digest":
        pinned["asset"]["digest"] = "sha256:" + "0" * 64
    elif case == "duplicate":
        release["assets"].append(pinned["asset"].copy())
    else:
        release[case] = True
    with pytest.raises(ValueError, match="pinned"):
        pinned_release.resolve_pinned_release(pinned["config"], pinned["path"])


@pytest.mark.parametrize("corrupt", [False, True])
def test_download_enforces_receipt_and_preserves_seed(
    pinned: dict, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    latest = Mock(side_effect=AssertionError("latest must never be consulted"))
    monkeypatch.setattr(download, "check_for_updates", latest)
    monkeypatch.setattr(download, "get_version", lambda: "1.0.0")

    def fetch(_url: str, output: Path, *, token: str | None) -> bool:
        raw = pinned["raw"]
        output.write_bytes(bytes([raw[0] ^ 1]) + raw[1:] if corrupt else raw)
        return True

    monkeypatch.setattr(download, "download_release_asset", fetch)
    ok, _release = download.download_from_github_release(pinned["config"], receipt=pinned["path"])
    assert ok is not corrupt
    original = pinned["path"].parent / "original"
    assert (original / "example.json").exists() is not corrupt
    if not corrupt:
        assert json.loads((pinned["path"].parent / "version.json").read_text()) == pinned["receipt"]
    latest.assert_not_called()


def test_tag_lookup_uses_exact_encoded_tag_and_propagates_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = Mock()
    reply.headers = {}
    reply.json.return_value = {"tag_name": "v1/2", "published_at": "today", "assets": []}
    get = Mock(return_value=reply)
    monkeypatch.setattr(github_release.requests, "get", get)
    github_release.get_release_by_tag("example", "specs", "v1/2")
    assert get.call_args.args[0].endswith("/releases/tags/v1%2F2")
    reply.raise_for_status.side_effect = requests.HTTPError("missing release")
    with pytest.raises(requests.HTTPError, match="missing release"):
        github_release.get_release_by_tag("example", "specs", "v1/2")
