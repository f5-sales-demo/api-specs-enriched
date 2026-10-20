"""The operational release producer cannot infer qualification or change pins."""

import json
from pathlib import Path

import pytest

from scripts.gcp_operational_release_assets import build_gcp_operational_assets
from tests.test_gcp_operational_candidate import profile_fixture


def test_absent_operational_assets_do_not_enable_a_profile(tmp_path: Path):
    assert build_gcp_operational_assets(tmp_path / "absent") == {}


@pytest.mark.parametrize("pin", [{}, [], {"contractFingerprint": "sha256:" + "a" * 64}])
def test_incomplete_pin_is_rejected(tmp_path: Path, pin):
    (tmp_path / "pins.json").write_text(json.dumps(pin))
    with pytest.raises(ValueError, match="pins malformed"):
        build_gcp_operational_assets(tmp_path)


def test_missing_qualification_cannot_publish(tmp_path: Path):
    (tmp_path / "pins.json").write_text(
        json.dumps(
            {
                "contractFingerprint": "sha256:" + "a" * 64,
                "qualificationFingerprint": "sha256:" + "b" * 64,
            }
        )
    )
    (tmp_path / "gcp-operational-health-v2.json").write_text("{}")
    with pytest.raises(FileNotFoundError):
        build_gcp_operational_assets(tmp_path)


def test_exact_assets_publish_and_changed_pin_or_bytes_fail(tmp_path: Path):
    profile, pin, _ = profile_fixture()
    contract = pin(profile)
    qualification = {
        "schemaVersion": 2,
        "contractFingerprint": contract,
        "bindingFingerprint": "sha256:" + "a" * 64,
        "healthyRoundFingerprints": ["sha256:" + "b" * 64, "sha256:" + "c" * 64],
        "evidenceSha256": "sha256:" + "d" * 64,
        "campaignCleanupSha256": "sha256:" + "e" * 64,
        "objectsAbsent": 18,
        "emptyDestroy": True,
        "stableState": True,
        "repeatedResumeNoSubmission": True,
    }
    values = {
        "gcp-operational-health-v2.json": profile,
        "gcp-operational-qualification-v2.json": qualification,
    }
    for name, value in values.items():
        (tmp_path / name).write_bytes(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        )
    pins = {"contractFingerprint": contract, "qualificationFingerprint": pin(qualification)}
    (tmp_path / "pins.json").write_text(json.dumps(pins))
    assets = build_gcp_operational_assets(tmp_path)
    assert set(assets) == set(values)
    profile["rawBody"] = "private inspection"
    (tmp_path / "gcp-operational-health-v2.json").write_bytes(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
    )
    pins["contractFingerprint"] = pin(profile)
    qualification["contractFingerprint"] = pins["contractFingerprint"]
    pins["qualificationFingerprint"] = pin(qualification)
    (tmp_path / "pins.json").write_text(json.dumps(pins))
    (tmp_path / "gcp-operational-qualification-v2.json").write_bytes(
        json.dumps(qualification, sort_keys=True, separators=(",", ":")).encode()
    )
    with pytest.raises(ValueError, match="projection fields"):
        build_gcp_operational_assets(tmp_path)
    for name, content in assets.items():
        (tmp_path / name).write_bytes(content)
    pins = {
        "contractFingerprint": contract,
        "qualificationFingerprint": pin(
            {
                **qualification,
                "contractFingerprint": contract,
            }
        ),
    }
    (tmp_path / "pins.json").write_text(json.dumps(pins))
    qualification_path = tmp_path / "gcp-operational-qualification-v2.json"
    qualification_path.write_bytes(assets[qualification_path.name] + b"\n")
    with pytest.raises(ValueError, match="canonical"):
        build_gcp_operational_assets(tmp_path)
    qualification_path.write_bytes(assets[qualification_path.name])
    pins["qualificationFingerprint"] = "sha256:" + "f" * 64
    (tmp_path / "pins.json").write_text(json.dumps(pins))
    with pytest.raises(ValueError, match="fingerprint"):
        build_gcp_operational_assets(tmp_path)
