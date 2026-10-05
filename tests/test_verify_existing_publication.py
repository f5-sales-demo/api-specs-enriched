"""Fail-closed maintenance rebuild checks for an already published API release."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.release.verify_existing_publication import (
    PublicationError,
    expected_assets,
    verify_release,
)

TAG = "v12.0.0"
COMMIT = "a9be0360815e3fd3fa08ded845e03c0bd4afd6ec"


def publication(tmp_path: Path) -> tuple[dict, Path]:
    assets = tmp_path / "assets"
    assets.mkdir()
    hashes = {}
    for name in expected_assets("12.0.0"):
        content = f"verified-{name}".encode()
        (assets / name).write_bytes(content)
        hashes[name] = "sha256:" + hashlib.sha256(content).hexdigest()
    receipt = {"assets": hashes, "commit": COMMIT, "version": "12.0.0"}
    release = {
        "tagName": TAG,
        "body": "Existing release\n<!-- publication-receipt:"
        + json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        + " -->\n",
        "assets": [{"name": name, "digest": digest} for name, digest in hashes.items()],
    }
    return release, assets


def test_verified_existing_release_passes(tmp_path: Path) -> None:
    release, assets = publication(tmp_path)
    verify_release(release, assets, TAG, COMMIT)


@pytest.mark.parametrize(
    "mutation",
    [
        "tag",
        "commit",
        "missing_receipt_asset",
        "missing_github_asset",
        "metadata_digest",
        "asset_bytes",
        "extra_download",
        "duplicate_receipt",
    ],
)
def test_ambiguous_or_changed_release_fails(tmp_path: Path, mutation: str) -> None:
    release, assets = publication(tmp_path)
    release = copy.deepcopy(release)
    if mutation == "tag":
        release["tagName"] = "v12.0.1"
    elif mutation == "commit":
        release["body"] = release["body"].replace(COMMIT, "0" * 40)
    elif mutation == "missing_receipt_asset":
        receipt_line = release["body"].split("publication-receipt:", 1)[1].split(" -->", 1)[0]
        receipt = json.loads(receipt_line)
        receipt["assets"].pop("openapi.json")
        release["body"] = (
            "<!-- publication-receipt:"
            + json.dumps(receipt, sort_keys=True, separators=(",", ":"))
            + " -->"
        )
    elif mutation == "missing_github_asset":
        release["assets"].pop()
    elif mutation == "metadata_digest":
        release["assets"][0]["digest"] = "sha256:" + "0" * 64
    elif mutation == "asset_bytes":
        (assets / "openapi.json").write_text("changed")
    elif mutation == "extra_download":
        (assets / "extra.json").write_text("unexpected")
    elif mutation == "duplicate_receipt":
        release["body"] += release["body"].splitlines()[-1] + "\n"
    with pytest.raises(PublicationError):
        verify_release(release, assets, TAG, COMMIT)


def test_workflow_cannot_deploy_without_release_verification() -> None:
    text = Path(".github/workflows/rebuild-pages-from-release.yml").read_text()
    assert "needs: verify" in text
    assert "target-commit: ${{ needs.verify.outputs.target_commit }}" in text
    assert "verify_existing_publication.py" in text
    assert "gh release download" in text
    assert "gh release create" not in text
