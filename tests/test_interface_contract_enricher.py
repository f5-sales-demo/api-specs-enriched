"""Tests for the evidence-backed Secure Mesh interface contract."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.utils.extension_constants import X_F5XC_CE_AUTOMATION_CONTRACT
from scripts.utils.interface_contract_enricher import (
    InterfaceContractEnricher,
    InterfaceContractValidationError,
)


@pytest.fixture
def contract_config() -> dict[str, Any]:
    """Load the production contract configuration."""
    config_path = Path(__file__).parent.parent / "config" / "interface_contracts.yaml"
    with config_path.open() as source:
        return yaml.safe_load(source)


@pytest.fixture
def sms_spec() -> dict[str, Any]:
    """Return matching and nonmatching component schemas."""
    return {
        "components": {
            "schemas": {
                "viewssecuremesh_site_v2CreateSpecType": {"type": "object"},
                "viewssecuremesh_site_v2GetSpecType": {"type": "object"},
                "unrelatedSchema": {"type": "object"},
            },
        },
    }


def _write_config(tmp_path: Path, config: dict[str, Any]) -> Path:
    path = tmp_path / "interface_contracts.yaml"
    with path.open("w") as destination:
        yaml.safe_dump(config, destination, sort_keys=False)
    return path


def _contract(config: dict[str, Any]) -> dict[str, Any]:
    return config["contracts"]["securemesh_site_v2"]["contract"]


def _azure_contract(config: dict[str, Any]) -> dict[str, Any]:
    return _contract(config)["providers"]["azure"]


def test_emits_contract_for_only_securemesh_request_schemas(sms_spec: dict[str, Any]) -> None:
    enriched = InterfaceContractEnricher().enrich_spec(copy.deepcopy(sms_spec))
    schemas = enriched["components"]["schemas"]
    assert X_F5XC_CE_AUTOMATION_CONTRACT in schemas["viewssecuremesh_site_v2CreateSpecType"]
    assert X_F5XC_CE_AUTOMATION_CONTRACT in schemas["viewssecuremesh_site_v2GetSpecType"]
    assert "x-f5xc-interface-contract" not in schemas["viewssecuremesh_site_v2CreateSpecType"]
    assert X_F5XC_CE_AUTOMATION_CONTRACT not in schemas["unrelatedSchema"]


def test_contract_is_deterministic_and_guest_names_are_not_authoritative(
    sms_spec: dict[str, Any],
) -> None:
    enricher = InterfaceContractEnricher()
    once = enricher.enrich_spec(copy.deepcopy(sms_spec))
    twice = enricher.enrich_spec(copy.deepcopy(sms_spec))
    create_contract = once["components"]["schemas"]["viewssecuremesh_site_v2CreateSpecType"]
    assert once == twice
    assert (
        create_contract[X_F5XC_CE_AUTOMATION_CONTRACT]["providers"]["azure"]["runtime_evidence"][
            "guest_interface_names"
        ]
        == "observational_only"
    )
    assert (
        create_contract[X_F5XC_CE_AUTOMATION_CONTRACT]["contract_id"]
        == "f5xc-ce-automation/v1"
    )
    assert "eth" not in json.dumps(create_contract).lower()


def test_contract_defines_stable_identity_and_role_invariants() -> None:
    enricher = InterfaceContractEnricher()
    contract = enricher.contracts[0][1]
    azure = contract["providers"]["azure"]
    assert contract["contract_id"] == "f5xc-ce-automation/v1"
    assert contract["api"]["namespace"] == "system"
    assert contract["providers"]["aws"]["availability"] == "schema_only"
    assert "tgw-connect" in contract["providers"]["aws"]["unavailable_capabilities"]
    assert azure["stable_identity"]["required_fields"]
    assert [role["name"] for role in azure["roles"]] == ["slo", "external", "sli"]
    assert azure["invariants"]["bgp_bindable_roles"] == ["slo"]
    assert azure["change_risk"]["maintenance_window_required"] is True
    assert azure["change_risk"]["restarts_ce_data_plane_services"] is True


Mutation = Callable[[dict[str, Any]], Any]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda config: config["contracts"]["securemesh_site_v2"].pop("contract"),
            "contract must be an object",
        ),
        (
            lambda config: _azure_contract(config)["roles"].append(
                copy.deepcopy(_azure_contract(config)["roles"][0])
            ),
            "duplicate role",
        ),
        (
            lambda config: _azure_contract(config)["roles"][1].update({"name": "unknown"}),
            "unknown role",
        ),
        (
            lambda config: _azure_contract(config)["roles"][2].pop("identity_fields"),
            "lacks the complete stable identity",
        ),
        (
            lambda config: _azure_contract(config)["runtime_evidence"].update(
                {"guest_interface_names": "authoritative"}
            ),
            "guest interface names must be observational only",
        ),
    ],
)
def test_rejects_unsafe_or_incomplete_contracts(
    tmp_path: Path,
    contract_config: dict[str, Any],
    mutate: Mutation,
    message: str,
) -> None:
    invalid = copy.deepcopy(contract_config)
    mutate(invalid)
    with pytest.raises(InterfaceContractValidationError, match=message):
        InterfaceContractEnricher(_write_config(tmp_path, invalid))


def test_rejects_bindable_optional_role_without_authoritative_evidence(
    tmp_path: Path, contract_config: dict[str, Any]
) -> None:
    invalid = copy.deepcopy(contract_config)
    external = _azure_contract(invalid)["roles"][1]
    external.update(
        {
            "bindable": True,
            "network_option": "site_local_inside_network",
            "vrf": "site_local_inside",
            "configuration_path": "azure.not_managed.node_list[].interface_list[]",
        }
    )
    _azure_contract(invalid)["runtime_evidence"]["minimum_mapping_confidence"] = "advisory"
    with pytest.raises(InterfaceContractValidationError, match="authoritative evidence provenance"):
        InterfaceContractEnricher(_write_config(tmp_path, invalid))


def test_rejects_aws_profile_that_claims_unverified_automation(
    tmp_path: Path, contract_config: dict[str, Any]
) -> None:
    invalid = copy.deepcopy(contract_config)
    _contract(invalid)["providers"]["aws"]["availability"] = "supported"
    with pytest.raises(
        InterfaceContractValidationError, match="AWS availability must be schema_only"
    ):
        InterfaceContractEnricher(_write_config(tmp_path, invalid))


def test_site_guidance_describes_the_evidence_gate() -> None:
    root = Path(__file__).parent.parent
    best_practices = yaml.safe_load((root / "config" / "best_practices.yaml").read_text())
    workflows = yaml.safe_load((root / "config" / "guided_workflows.yaml").read_text())
    site_notes = best_practices["domains"]["sites"]["security_notes"]
    site_workflows = workflows["workflows"]["sites"]
    assert any("guest interface names" in note for note in site_notes)
    assert any(
        workflow["id"] == "evidence_gated_secure_mesh_interfaces" for workflow in site_workflows
    )
