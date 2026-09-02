"""Tests for the checksum-bound SMSv2 contract release assets."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.smsv2_release_assets import (
    CONTRACT_FILE,
    EVIDENCE_FILE,
    MANIFEST_FILE,
    Smsv2ReleaseValidationError,
    build_release_assets,
    digest,
    validate_release_assets,
)

_COMMIT = "a" * 40
_NOW = datetime(2026, 8, 17, tzinfo=UTC)


def _assets(tmp_path: Path) -> dict[str, bytes]:
    return build_release_assets(
        Path(__file__).parents[1] / "config" / "interface_contracts.yaml",
        tmp_path,
        "v2.1.222",
        _COMMIT,
    )


def _release(**overrides: object) -> dict[str, object]:
    release: dict[str, object] = {
        "tag_name": "v2.1.222",
        "target_commitish": _COMMIT,
        "draft": False,
        "prerelease": False,
        "immutable": True,
        "published_at": "2026-08-16T00:00:00Z",
    }
    release.update(overrides)
    return release


def _receipt(manifest: dict[str, object]) -> dict[str, object]:
    return {"commit": _COMMIT, "assets": copy.deepcopy(manifest["assets"])}


def test_builds_deterministic_sanitized_assets(tmp_path: Path) -> None:
    first = _assets(tmp_path / "one")
    second = _assets(tmp_path / "two")
    assert first == second
    manifest = json.loads(first[MANIFEST_FILE])
    assert manifest["release"] == {"tag": "v2.1.222", "commit": _COMMIT}
    assert digest(first[CONTRACT_FILE]) == manifest["assets"][CONTRACT_FILE]
    assert digest(first[EVIDENCE_FILE]) == manifest["assets"][EVIDENCE_FILE]
    assert b"bearer" not in first[EVIDENCE_FILE].lower()


def test_validates_stable_receipted_release(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    manifest = json.loads(assets[MANIFEST_FILE])
    contract = validate_release_assets(
        assets[MANIFEST_FILE], assets, _release(), _receipt(manifest), now=_NOW
    )
    assert contract["version"] == "5.0.0"
    assert contract["providers"]["aws"]["availability"] == "schema_only"
    assert contract["providers"]["aws"]["capabilities"] == {
        "aws_ce_create": "unavailable",
        "runtime_status": "unavailable",
        "tgw_connect": "unavailable",
    }


@pytest.mark.parametrize(
    ("release_overrides", "receipt_overrides", "asset_name", "message"),
    [
        ({"draft": True}, {}, None, "draft"),
        ({"prerelease": True}, {}, None, "draft"),
        ({"immutable": False}, {}, None, "mutable"),
        ({"published_at": "2020-01-01T00:00:00Z"}, {}, None, "stale"),
        ({"target_commitish": "deadbeef"}, {}, None, "commit"),
        ({}, {"commit": "b" * 40}, None, "commit"),
        ({}, {}, CONTRACT_FILE, "checksum"),
    ],
)
def test_rejects_unavailable_or_tampered_release(
    tmp_path: Path,
    release_overrides: dict[str, object],
    receipt_overrides: dict[str, object],
    asset_name: str | None,
    message: str,
) -> None:
    assets = _assets(tmp_path)
    manifest = json.loads(assets[MANIFEST_FILE])
    receipt = _receipt(manifest)
    receipt.update(receipt_overrides)
    if asset_name:
        assets[asset_name] += b"tampered"
    with pytest.raises(Smsv2ReleaseValidationError, match=message):
        validate_release_assets(
            assets[MANIFEST_FILE], assets, _release(**release_overrides), receipt, now=_NOW
        )


def test_rejects_malformed_manifest_and_sensitive_evidence(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    manifest = json.loads(assets[MANIFEST_FILE])
    receipt = _receipt(manifest)
    with pytest.raises(Smsv2ReleaseValidationError, match="malformed"):
        validate_release_assets(b"{", assets, _release(), receipt, now=_NOW)
    evidence = json.loads(assets[EVIDENCE_FILE])
    evidence["receipts"][0]["source_url"] = "https://example.invalid/?authorization=Bearer%20bad"
    assets[EVIDENCE_FILE] = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    manifest["assets"][EVIDENCE_FILE] = digest(assets[EVIDENCE_FILE])
    assets[MANIFEST_FILE] = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    receipt = _receipt(manifest)
    with pytest.raises(Smsv2ReleaseValidationError, match="sensitive"):
        validate_release_assets(assets[MANIFEST_FILE], assets, _release(), receipt, now=_NOW)


def test_rejects_tampered_blocking_evidence(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    evidence = json.loads(assets[EVIDENCE_FILE])
    evidence["receipts"][0]["blocking_conditions"] = ["unknown"]
    assets[EVIDENCE_FILE] = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    manifest = json.loads(assets[MANIFEST_FILE])
    manifest["assets"][EVIDENCE_FILE] = digest(assets[EVIDENCE_FILE])
    assets[MANIFEST_FILE] = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()

    with pytest.raises(Smsv2ReleaseValidationError, match="blocking evidence"):
        validate_release_assets(
            assets[MANIFEST_FILE], assets, _release(), _receipt(manifest), now=_NOW
        )


def test_rejects_release_race_with_changed_tag(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    manifest = json.loads(assets[MANIFEST_FILE])
    with pytest.raises(Smsv2ReleaseValidationError, match="tag"):
        validate_release_assets(
            assets[MANIFEST_FILE],
            assets,
            _release(tag_name="v2.1.223"),
            _receipt(manifest),
            now=_NOW,
        )


def _mutate_contract_asset(
    assets: dict[str, bytes], mutation: object
) -> tuple[dict[str, bytes], dict[str, object]]:
    contract = json.loads(assets[CONTRACT_FILE])
    assert callable(mutation)
    mutation(contract)
    assets[CONTRACT_FILE] = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    manifest = json.loads(assets[MANIFEST_FILE])
    manifest["assets"][CONTRACT_FILE] = digest(assets[CONTRACT_FILE])
    assets[MANIFEST_FILE] = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return assets, _receipt(manifest)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda contract: contract.update({"contract_id": "f5xc-ce-automation/v1"}), "identity"),
        (
            lambda contract: contract["providers"]["aws"]["capabilities"].update(
                {"runtime_status": "available"}
            ),
            "fail closed",
        ),
        (
            lambda contract: contract["providers"]["aws"]["runtime"]["configuration"].update(
                {"path": "/api/config/namespaces/{namespace}/sites/{site}/interface"}
            ),
            "incomplete or legacy",
        ),
        (
            lambda contract: contract["providers"]["aws"]["runtime"]["simplified_routes"].update(
                {"path": "/api/operate/namespaces/{namespace}/sites/{site}/ver/routes"}
            ),
            "incomplete or legacy",
        ),
        (
            lambda contract: contract["providers"]["aws"]["authorities"]["aws"].append(
                "runtime_health"
            ),
            "authority declarations",
        ),
    ],
)
def test_rejects_legacy_or_inconsistent_v2_contract_assets(
    tmp_path: Path, mutation: object, message: str
) -> None:
    assets = _assets(tmp_path)
    assets, receipt = _mutate_contract_asset(assets, mutation)
    with pytest.raises(Smsv2ReleaseValidationError, match=message):
        validate_release_assets(assets[MANIFEST_FILE], assets, _release(), receipt, now=_NOW)
