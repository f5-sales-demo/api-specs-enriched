"""Verify relay wire mappings cannot claim operational acceptance."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.utils.interface_contract_enricher import (
    InterfaceContractEnricher,
    InterfaceContractValidationError,
    validate_azure_runtime_contract,
)


def _azure() -> dict[str, Any]:
    config = yaml.safe_load(
        (Path(__file__).parents[1] / "config/interface_contracts.yaml").read_text()
    )
    return config["contracts"]["securemesh_site_v2"]["contract"]["providers"]["azure"]


def test_relay_uses_ordinary_slo_peers_and_keeps_direct_multihop_unavailable() -> None:
    """Schema support does not bypass the distinct direct-CE limitation."""
    azure = _azure()
    relay = azure["slo_bgp_relay"]
    assert relay["availability"] == "schema_validated_runtime_unqualified"
    assert relay["ce_operation"]["path"] == "/api/config/namespaces/{metadata.namespace}/bgps"
    assert relay["ce_operation"]["request_schema"] == "bgpCreateRequest"
    assert relay["ce_mappings"]["network_type"] == "VIRTUAL_NETWORK_SITE_LOCAL"
    assert relay["ce_mappings"]["peer_asn"] == "relay_cloud_binding.asn"
    assert relay["identity"]["relay_address"] == "owned_cloud_nic_observed_ipv4"
    assert relay["multihop_owner"] == "frr_route_server_peer_group"
    assert azure["route_server_ebgp_multihop"]["availability"] == "unavailable"
    assert azure["route_server_ebgp_multihop"]["enforcement"] == "reject_before_mutation"


def test_relay_evidence_distinguishes_wire_validation_from_all_live_profiles() -> None:
    """Receipted synthetic inputs never establish cloud or CE ownership."""
    relay = _azure()["slo_bgp_relay"]
    qualification = relay["qualification"]
    assert qualification["native_request_schema_validated"] is True
    assert qualification["terraform_provider_validated"] is True
    assert qualification["live_acceptance"] is False
    assert qualification["vendor_support"] == "not_independently_qualified"
    assert qualification["topology_acceptance"] == dict.fromkeys(
        ["single_site", "independent_sites", "three_node_ha"], False
    )
    receipt = json.loads((Path(__file__).parents[1] / qualification["receipt_path"]).read_text())
    assert receipt["fixture_inputs"] == "synthetic_schema_fixtures"
    assert receipt["wire_checks_passed"] == 13
    assert len(receipt["provider_validation"]) == 2
    assert all(row["valid"] and row["error_count"] == 0 for row in receipt["provider_validation"])
    assert not receipt["live_acceptance"]


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        (None, "availability", "available"),
        (None, "multihop_owner", "ce_data_plane"),
        ("ce_operation", "path", "/api/config/namespaces/system/legacy_bgps"),
        ("ce_operation", "request_schema", "bgpGetResponse"),
        ("ce_mappings", "network_type", "VIRTUAL_NETWORK_SITE_LOCAL_INSIDE"),
        ("ce_mappings", "peer_asn", "route_server.asn"),
        ("identity", "relay_address", "caller_selected_address"),
        ("qualification", "live_acceptance", True),
        ("qualification", "vendor_support", "supported"),
        ("qualification", "receipt_sha256", "sha256:" + "0" * 64),
    ],
)
def test_rejects_mutated_relay_claims(section: str | None, field: str, value: object) -> None:
    """Missing provenance and altered ownership or state stay inadmissible."""
    azure = copy.deepcopy(_azure())
    relay = azure["slo_bgp_relay"]
    destination = relay if section is None else relay[section]
    destination[field] = value
    with pytest.raises(InterfaceContractValidationError, match="Azure SLO BGP relay"):
        validate_azure_runtime_contract(azure)


def test_rejects_missing_relay_contract() -> None:
    """The additive contract cannot silently disappear from produced assets."""
    azure = _azure()
    azure.pop("slo_bgp_relay", None)
    with pytest.raises(InterfaceContractValidationError, match="Azure SLO BGP relay"):
        validate_azure_runtime_contract(azure)


def test_current_config_enriches_an_additive_contract_version() -> None:
    """Every matched schema carries the new contract without a breaking identifier."""
    contract = InterfaceContractEnricher().contracts[0][1]
    assert contract["version"] == "7.2.0"
    assert contract["contract_id"] == "f5xc-smsv2-api/v1"


@pytest.mark.parametrize("failure", ["missing", "modified"])
def test_rejects_missing_or_modified_source_receipt(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """A declared digest does not replace checking the actual retained evidence."""
    azure = _azure()
    target = Path(__file__).parents[1] / azure["slo_bgp_relay"]["qualification"]["receipt_path"]
    if failure == "missing":
        original_is_file = Path.is_file
        monkeypatch.setattr(
            Path, "is_file", lambda path: False if path == target else original_is_file(path)
        )
    else:
        original_read_bytes = Path.read_bytes
        monkeypatch.setattr(
            Path, "read_bytes", lambda path: b"{}" if path == target else original_read_bytes(path)
        )
    with pytest.raises(
        InterfaceContractValidationError, match="Azure SLO BGP relay qualification receipt"
    ):
        validate_azure_runtime_contract(azure)
