"""Tests for the checksum-bound SMSv2 contract release assets."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

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
_NOW = datetime(2026, 9, 6, tzinfo=UTC)


def _assets(tmp_path: Path) -> dict[str, bytes]:
    return build_release_assets(
        Path(__file__).parents[1] / "config" / "interface_contracts.yaml",
        tmp_path,
        "v7.0.0",
        _COMMIT,
    )


def _release(**overrides: object) -> dict[str, object]:
    release: dict[str, object] = {
        "tag_name": "v7.0.0",
        "target_commitish": _COMMIT,
        "draft": False,
        "prerelease": False,
        "immutable": True,
        "published_at": "2026-09-05T01:00:00Z",
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
    assert manifest["release"] == {"tag": "v7.0.0", "commit": _COMMIT}
    assert digest(first[CONTRACT_FILE]) == manifest["assets"][CONTRACT_FILE]
    assert digest(first[EVIDENCE_FILE]) == manifest["assets"][EVIDENCE_FILE]
    assert b"bearer" not in first[EVIDENCE_FILE].lower()


def test_validates_stable_receipted_release(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    manifest = json.loads(assets[MANIFEST_FILE])
    contract = validate_release_assets(
        assets[MANIFEST_FILE], assets, _release(), _receipt(manifest), now=_NOW
    )
    assert contract["version"] == "7.0.0"
    assert contract["providers"]["aws"]["availability"] == "evidence_backed"
    assert contract["providers"]["aws"]["capabilities"] == {
        "aws_ce_create": "available",
        "runtime_status": "available",
        "site_upgrade": "available",
        "tgw_connect": "available",
    }
    assert contract["contract_id"] == "f5xc-smsv2-api/v1"
    aws = contract["providers"]["aws"]
    assert aws["interface_identity"]["fields"] == ["node", "ethernet_interface.mac"]
    assert aws["interface_identity"]["uniqueness_scope"] == "node"
    assert aws["runtime"]["configuration"]["nullability"]["public_ip"] == "nullable"
    assert aws["runtime"]["bgp_peers"]["response_mappings"]["state_changed_at"] == (
        "ver[].peer[].up_down_timestamp"
    )
    route_mappings = aws["runtime"]["bgp_routes"]["response_mappings"]
    assert route_mappings["route_tables"] == "ver[].ri_table[].rt_table[]"
    assert route_mappings["route_prefixes"] == [
        "ver[].ri_table[].rt_table[].imported[].subnet",
        "ver[].ri_table[].rt_table[].exported[].subnet",
    ]
    assert aws["runtime"]["simplified_routes"]["semantics"] == "observational_read_only"
    azure = contract["providers"]["azure"]
    assert azure["bootstrap"]["headless_checkout"] == "available"
    assert azure["bootstrap"]["image_runtime_acceptance"] is False
    assert azure["bootstrap"]["cloud_init"]["query_fields"]["provider"] == "azure"
    assert azure["runtime"]["configuration"]["response_mappings"]["nodes"] == (
        "spec.azure.not_managed.node_list[]"
    )
    assert azure["runtime"]["configuration"]["response_mappings"]["provider"] == (
        "spec.azure.not_managed"
    )
    assert azure["runtime"]["bgp_peers"]["response_schema"] == "bgpBGPPeersResponse"
    assert azure["runtime"]["bgp_routes"]["response_schema"] == "bgpBGPRoutesResponse"
    assert azure["route_server_ebgp_multihop"]["availability"] == "unavailable"
    assert azure["route_server_ebgp_multihop"]["enforcement"] == "reject_before_mutation"
    assert "observed_at" not in repr(contract)


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


def test_rejects_tampered_success_evidence(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    evidence = json.loads(assets[EVIDENCE_FILE])
    evidence["receipts"][0]["result"] = "rejected"
    assets[EVIDENCE_FILE] = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    manifest = json.loads(assets[MANIFEST_FILE])
    manifest["assets"][EVIDENCE_FILE] = digest(assets[EVIDENCE_FILE])
    assets[MANIFEST_FILE] = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()

    with pytest.raises(Smsv2ReleaseValidationError, match="success evidence"):
        validate_release_assets(
            assets[MANIFEST_FILE], assets, _release(), _receipt(manifest), now=_NOW
        )


def test_rejects_tampered_site_upgrade_evidence(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    evidence = json.loads(assets[EVIDENCE_FILE])
    evidence["receipts"][1]["result"] = "rejected"
    assets[EVIDENCE_FILE] = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    manifest = json.loads(assets[MANIFEST_FILE])
    manifest["assets"][EVIDENCE_FILE] = digest(assets[EVIDENCE_FILE])
    assets[MANIFEST_FILE] = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()

    with pytest.raises(Smsv2ReleaseValidationError, match="site upgrade evidence"):
        validate_release_assets(
            assets[MANIFEST_FILE], assets, _release(), _receipt(manifest), now=_NOW
        )


def test_validates_schema_only_release_with_blocking_receipt(tmp_path: Path) -> None:
    config = yaml.safe_load(
        (Path(__file__).parents[1] / "config" / "interface_contracts.yaml").read_text()
    )
    aws = config["contracts"]["securemesh_site_v2"]["contract"]["providers"]["aws"]
    aws["availability"] = "schema_only"
    aws["capabilities"] = dict.fromkeys(aws["capabilities"], "unavailable")
    aws["unavailable_capabilities"] = list(aws["capabilities"])
    aws["telemetry_intake"]["availability"] = "unavailable"
    aws["telemetry_intake"]["complete"] = False
    aws["evidence"]["receipts"] = [
        {
            "operations": ["replace"],
            "result": "rejected",
            "blocking_conditions": [
                "mac_only_interface_rejected_by_live_api",
                "public_ip_empty_string_null_round_trip",
            ],
            "sanitized": True,
            "redaction": "no tenant response, token, bootstrap material, or resource identifier",
        }
    ]
    config_path = tmp_path / "interface_contracts.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    assets = build_release_assets(config_path, tmp_path / "assets", "v7.0.0", _COMMIT)
    manifest = json.loads(assets[MANIFEST_FILE])

    contract = validate_release_assets(
        assets[MANIFEST_FILE], assets, _release(), _receipt(manifest), now=_NOW
    )

    assert contract["providers"]["aws"]["availability"] == "schema_only"


def test_rejects_release_race_with_changed_tag(tmp_path: Path) -> None:
    assets = _assets(tmp_path)
    manifest = json.loads(assets[MANIFEST_FILE])
    with pytest.raises(Smsv2ReleaseValidationError, match="tag"):
        validate_release_assets(
            assets[MANIFEST_FILE],
            assets,
            _release(tag_name="v6.1.1"),
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
            lambda contract: contract["providers"]["aws"]["bootstrap"][
                "interface_configuration"
            ].update({"mac_only_create": "available"}),
            "bootstrap",
        ),
        (
            lambda contract: contract["providers"]["aws"]["bootstrap"]["token"].update(
                {"credential_response_path": "system_metadata.uid"}
            ),
            "bootstrap",
        ),
        (
            lambda contract: contract["providers"]["azure"]["bootstrap"].update(
                {"runtime_verification": "verified"}
            ),
            "bootstrap",
        ),
        (
            lambda contract: contract["providers"]["azure"]["runtime"]["bgp_peers"].update(
                {"response_schema": "bgpBGPPeerResponse"}
            ),
            "Azure runtime endpoints or schemas are incomplete",
        ),
        (
            lambda contract: contract["providers"]["azure"]["route_server_ebgp_multihop"].update(
                {"availability": "available"}
            ),
            "Azure Route Server eBGP multihop capability is unavailable",
        ),
        (
            lambda contract: contract["providers"]["aws"]["capabilities"].update(
                {"runtime_status": "unavailable"}
            ),
            "incomplete",
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
            lambda contract: contract["providers"]["aws"].pop("site_upgrade"),
            "site upgrade contract",
        ),
        (
            lambda contract: contract["providers"]["aws"]["authorities"]["aws"].append(
                "runtime_health"
            ),
            "authority declarations",
        ),
    ],
)
def test_rejects_legacy_or_inconsistent_v3_contract_assets(
    tmp_path: Path, mutation: object, message: str
) -> None:
    assets = _assets(tmp_path)
    assets, receipt = _mutate_contract_asset(assets, mutation)
    with pytest.raises(Smsv2ReleaseValidationError, match=message):
        validate_release_assets(assets[MANIFEST_FILE], assets, _release(), receipt, now=_NOW)


def test_preboot_interface_evidence_preserves_api_observations_and_azure_separation(
    tmp_path: Path,
) -> None:
    assets = _assets(tmp_path)
    contract = json.loads(assets[CONTRACT_FILE])
    aws = contract["providers"]["aws"]["bootstrap"]["interface_configuration"]
    assert aws["runtime_acceptance"] == "observed_single_node_native_preboot_registration"
    assert aws["mac_only_create"] == "rejected_by_live_api"
    assert "interface_configuration" not in contract["providers"]["azure"]["bootstrap"]
    body = Path(aws["receipt_path"]).read_bytes()
    assert digest(body) == f"sha256:{aws['receipt_sha256']}"
    receipt = json.loads(body)
    assert receipt["sanitized"] is True
    assert receipt["observations"]["missing_device_create_http_status"] == 400
    assert receipt["observations"]["test_site_absence_http_status"] == 404
    assert "configured_preboot_vm_deployment" in receipt["not_performed"]


def test_native_preboot_receipt_limits_registration_proof_to_observed_topology(
    tmp_path: Path,
) -> None:
    contract = json.loads(_assets(tmp_path)[CONTRACT_FILE])
    evidence = contract["providers"]["aws"]["bootstrap"]["interface_configuration"][
        "runtime_evidence"
    ]
    body = Path(evidence["receipt_path"]).read_bytes()
    assert digest(body) == f"sha256:{evidence['receipt_sha256']}"
    assert evidence["fresh_acceptance_required"] is True
    receipt = json.loads(body)
    # Publication compacts short arrays; retain the original indented qualification digest too.
    original = (json.dumps(receipt, indent=2) + "\n").encode()
    assert digest(original) == f"sha256:{evidence['qualification_receipt_sha256']}"
    assert receipt["engine"] == "native"
    assert receipt["topology"] == {"sites": 1, "nodes": 1, "interfaces": 2, "ha": False}
    assert receipt["registration"]["state"] == "ONLINE"
    assert receipt["registration"]["hardwareBindingsVerified"] is True
    assert receipt["registration"]["configuredMtu"] == [1500, 1500]
    assert receipt["replacement"]["enisRetained"] == 2
    assert receipt["bootstrap"]["deployedMaterialMatchesPrivateCheckpoint"] is True
    assert receipt["bootstrap"]["certifiedConfigYamlPreserved"] is True
    assert receipt["productionContractPublished"] is False
    for key in ("f5Global", "routing", "traffic", "packetMtu"):
        assert receipt["health"][key] == "unknown"
    assert {"three-node-ha", "terraform-replacement", "azure-runtime", "full-parity"}.issubset(
        receipt["excludedAcceptance"]
    )
