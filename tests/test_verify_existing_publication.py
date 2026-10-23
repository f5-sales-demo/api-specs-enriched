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
    assert "target-commit: ${{ needs.verify.outputs.content_ref }}" in text
    assert "verify-release-docs-ref.sh" in text
    selector = Path("scripts/release/verify-release-docs-ref.sh").read_text()
    assert 'git diff --quiet "$release_commit" "$protected_main" -- docs release' in selector
    assert "verify_existing_publication.py" in text
    assert "gh release download" in text
    assert "gh release create" not in text


OPERATIONAL = {"gcp-operational-health-v2.json", "gcp-operational-qualification-v2.json"}


def add_assets(release: dict, assets: Path, names: set[str]) -> None:
    receipt_line = release["body"].split("publication-receipt:", 1)[1].split(" -->", 1)[0]
    receipt = json.loads(receipt_line)
    for name in names:
        content = f"verified-{name}".encode()
        (assets / name).write_bytes(content)
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        receipt["assets"][name] = digest
        release["assets"].append({"name": name, "digest": digest})
    release["body"] = (
        "<!-- publication-receipt:"
        + json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        + " -->"
    )


def test_complete_operational_pair_passes(tmp_path: Path) -> None:
    release, assets = publication(tmp_path)
    add_assets(release, assets, OPERATIONAL)
    verify_release(release, assets, TAG, COMMIT)


@pytest.mark.parametrize(
    "names",
    [
        {"gcp-operational-health-v2.json"},
        {"gcp-operational-qualification-v2.json"},
        OPERATIONAL | {"unrelated.json"},
    ],
)
def test_partial_or_extra_operational_set_fails(tmp_path: Path, names: set[str]) -> None:
    release, assets = publication(tmp_path)
    add_assets(release, assets, names)
    with pytest.raises(PublicationError, match="asset set"):
        verify_release(release, assets, TAG, COMMIT)


@pytest.mark.parametrize("mutation", ["duplicate", "bytes", "metadata", "missing-download"])
def test_operational_pair_preserves_exact_evidence_checks(tmp_path: Path, mutation: str) -> None:
    release, assets = publication(tmp_path)
    add_assets(release, assets, OPERATIONAL)
    name = "gcp-operational-health-v2.json"
    if mutation == "duplicate":
        release["assets"].append(next(row for row in release["assets"] if row["name"] == name))
    elif mutation == "bytes":
        (assets / name).write_bytes(b"changed")
    elif mutation == "metadata":
        next(row for row in release["assets"] if row["name"] == name)["digest"] = (
            "sha256:" + "0" * 64
        )
    else:
        (assets / name).unlink()
    with pytest.raises(PublicationError):
        verify_release(release, assets, TAG, COMMIT)
