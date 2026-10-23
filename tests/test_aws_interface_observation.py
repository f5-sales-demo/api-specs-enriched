"""Physical link mappings must retain identity and acceptance boundaries."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from scripts.smsv2_release_assets import build_release_assets
from scripts.utils.interface_contract_enricher import (
    InterfaceContractEnricher,
    InterfaceContractValidationError,
    validate_aws_v3_contract,
)

ROOT = Path(__file__).parents[1]


def profile():
    """Load the actual producer profile, not a manufactured valid constant."""
    config = yaml.safe_load((ROOT / "config/interface_contracts.yaml").read_text())
    return config["contracts"]["securemesh_site_v2"]["contract"]["providers"]["aws"]


def test_public_wire_paths_and_explicit_boolean_are_schema_backed():
    """Configuration objects do not supply the operational link boolean."""
    interface = profile()["runtime"]["physical_interfaces"]
    sites = json.loads((ROOT / "docs/specifications/api/sites.json").read_text())
    generated = json.loads((ROOT / "docs/specifications/api/ce_management.json").read_text())
    op = sites["paths"][interface["path"]]["get"]
    assert op["operationId"] == interface["operation_id"]
    op = generated["paths"][interface["generated_objects"]["path"]]["get"]
    assert op["operationId"] == interface["generated_objects"]["operation_id"]
    schema = sites["components"]["schemas"][interface["interface_schema"]]
    assert schema["properties"]["link_state"]["type"] == "boolean"
    assert interface["state_handling"]["missing_link_state"] == "unknown"
    assert interface["state_handling"]["active_state_values"] == [
        "STATE_UNKNOWN",
        "STATE_ACTIVE",
        "STATE_BACKUP",
    ]
    assert interface["state_handling"]["administrative_state"] == "not_exposed_by_this_observation"


def test_observed_up_does_not_qualify_freshness_down_ha_or_mutation():
    """Two physical up interfaces are evidence for only the observed read path."""
    interface = profile()["runtime"]["physical_interfaces"]
    q = interface["qualification"]
    assert q["installed_two_interface_up_observation"] is True
    assert q["six_interface_transition"] is False
    assert q["freshness"] is False
    assert q["supported_sli_control"] is False
    assert q["least_privilege"] is False
    assert q["fresh_published_asset_qualification_required"] is True
    assert interface["mutation_admission"] is False
    assert interface["freshness"]["original_ce_sample_timestamp"] == "unqualified"
    assert interface["freshness"]["creation_timestamp"] == "object_creation_not_ce_heartbeat"
    assert (
        interface["state_handling"]["down_acceptance"]
        == "complete_current_identity_verified_explicit_false_only"
    )
    assert interface["state_handling"]["unqualified_freshness_down_acceptance"] == "unknown"
    assert {
        "foreign_owner",
        "foreign_publisher",
        "duplicate",
        "partial",
        "malformed",
        "stale",
        "unauthorized",
        "unavailable",
    }.issubset(interface["unknown_conditions"])


@pytest.mark.parametrize(
    "change",
    [
        ("mutation_admission", True),
        ("qualification.freshness", True),
        ("qualification.six_interface_transition", True),
        ("qualification.receipt_sha256", "sha256:" + "0" * 64),
        ("state_handling.missing_link_state", "down"),
        ("state_handling.unqualified_freshness_down_acceptance", "down"),
        ("freshness.original_ce_sample_timestamp", "metadata.creation_timestamp"),
        ("generated_objects.operation_id", "ves.io.schema.network_interface.API.Create"),
    ],
)
def test_rejects_altered_state_ownership_or_qualification_claims(change):
    """Unsafe producer modifications fail validation before generating assets."""
    aws = copy.deepcopy(profile())
    target = aws["runtime"]["physical_interfaces"]
    path, value = change
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    with pytest.raises(InterfaceContractValidationError):
        validate_aws_v3_contract(aws)


def test_missing_or_modified_retained_receipt_is_rejected(monkeypatch):
    """A declared hash cannot stand in for checking the retained receipt bytes."""
    aws = profile()
    target = ROOT / aws["runtime"]["physical_interfaces"]["qualification"]["receipt_path"]
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda p: b"{}" if p == target else original(p))
    with pytest.raises(
        InterfaceContractValidationError, match="AWS physical interface qualification receipt"
    ):
        validate_aws_v3_contract(aws)


def test_additive_assets_include_hash_bound_interface_evidence(tmp_path):
    """Producer assets carry the same evidence and preserve pending acceptance."""
    contract = InterfaceContractEnricher().contracts[0][1]
    assert contract["version"] == "7.3.0"
    assert contract["contract_id"] == "f5xc-smsv2-api/v1"
    assets = build_release_assets(
        ROOT / "config/interface_contracts.yaml", tmp_path, "v12.5.0", "a" * 40
    )
    receipt = json.loads(assets["smsv2-evidence-receipt.json"])["aws_physical_interfaces"]
    assert receipt["receipt"]["sanitized"] is True
    assert receipt["receipt"]["physical_interface_count"] == 2
    assert receipt["receipt"]["freshness_qualified"] is False
    assert receipt["receipt"]["transition_qualified"] is False
