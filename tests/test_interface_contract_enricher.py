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
    validate_aws_telemetry_intake,
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
    assert create_contract[X_F5XC_CE_AUTOMATION_CONTRACT]["contract_id"] == "f5xc-ce-automation/v1"
    assert "eth" not in json.dumps(create_contract).lower()


def test_contract_defines_stable_identity_and_role_invariants() -> None:
    enricher = InterfaceContractEnricher()
    contract = enricher.contracts[0][1]
    azure = contract["providers"]["azure"]
    assert contract["contract_id"] == "f5xc-ce-automation/v1"
    assert contract["api"]["namespace"] == "system"
    assert contract["api"]["operations"] == ["create", "read", "replace", "delete"]
    assert contract["providers"]["aws"]["availability"] == "evidence_backed"
    assert contract["providers"]["aws"]["capabilities"] == {
        "aws_ce_create": "available",
        "runtime_status": "unavailable",
        "tgw_connect": "unavailable",
    }
    assert contract["providers"]["aws"]["bootstrap"]["mode"] == "interactive_console_only"
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
    _contract(invalid)["providers"]["aws"]["capabilities"]["tgw_connect"] = "available"
    with pytest.raises(
        InterfaceContractValidationError, match="AWS capability model must fail closed"
    ):
        InterfaceContractEnricher(_write_config(tmp_path, invalid))


def test_accepts_additive_v1_contract_fields_and_revision(
    tmp_path: Path, contract_config: dict[str, Any]
) -> None:
    compatible = copy.deepcopy(contract_config)
    contract = _contract(compatible)
    contract["version"] = "2.7.9"
    contract["api"]["status_path"] = (
        "/api/config/namespaces/{namespace}/securemesh_site_v2s/{name}/status"
    )
    contract["api"]["operations"].append("status")
    contract["providers"]["aws"]["future_additive_field"] = {"safe": True}
    contract["providers"]["aws"]["capabilities"]["future_capability"] = "unavailable"
    contract["providers"]["aws"]["unavailable_capabilities"].append("future_capability")
    contract["providers"]["gcp"] = {"availability": "schema_only"}

    enricher = InterfaceContractEnricher(_write_config(tmp_path, compatible))

    assert enricher.contracts[0][1]["version"] == "2.7.9"
    assert enricher.contracts[0][1]["providers"]["gcp"]["availability"] == "schema_only"


@pytest.mark.parametrize("version", ["2", "2.1", "02.1.0", "2.1.0-dev", "3.0.0"])
def test_rejects_malformed_or_incompatible_schema_versions(
    tmp_path: Path, contract_config: dict[str, Any], version: str
) -> None:
    incompatible = copy.deepcopy(contract_config)
    _contract(incompatible)["version"] = version

    with pytest.raises(InterfaceContractValidationError, match="unsupported contract version"):
        InterfaceContractEnricher(_write_config(tmp_path, incompatible))


def test_rejects_unknown_contract_identity_major(
    tmp_path: Path, contract_config: dict[str, Any]
) -> None:
    incompatible = copy.deepcopy(contract_config)
    _contract(incompatible)["contract_id"] = "f5xc-ce-automation/v2"

    with pytest.raises(InterfaceContractValidationError, match="invalid contract identity"):
        InterfaceContractEnricher(_write_config(tmp_path, incompatible))


def test_accepts_schema_only_aws_when_every_capability_fails_closed(
    tmp_path: Path, contract_config: dict[str, Any]
) -> None:
    compatible = copy.deepcopy(contract_config)
    aws = _contract(compatible)["providers"]["aws"]
    aws["availability"] = "schema_only"
    aws["capabilities"] = dict.fromkeys(aws["capabilities"], "unavailable")
    aws["unavailable_capabilities"] = list(aws["capabilities"])
    aws.pop("bootstrap")
    aws.pop("evidence")

    InterfaceContractEnricher(_write_config(tmp_path, compatible))


def test_rejects_schema_only_aws_automation_claims(
    tmp_path: Path, contract_config: dict[str, Any]
) -> None:
    invalid = copy.deepcopy(contract_config)
    aws = _contract(invalid)["providers"]["aws"]
    aws["availability"] = "schema_only"

    with pytest.raises(
        InterfaceContractValidationError,
        match="schema-only AWS capabilities must fail closed",
    ):
        InterfaceContractEnricher(_write_config(tmp_path, invalid))


def test_evidence_backed_aws_requires_provenance(
    tmp_path: Path, contract_config: dict[str, Any]
) -> None:
    invalid = copy.deepcopy(contract_config)
    _contract(invalid)["providers"]["aws"]["evidence"].pop("provenance")

    with pytest.raises(
        InterfaceContractValidationError, match="AWS evidence provenance is required"
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


def test_rejects_headless_aws_bootstrap_contract(
    tmp_path: Path, contract_config: dict[str, Any]
) -> None:
    invalid = copy.deepcopy(contract_config)
    _contract(invalid)["providers"]["aws"]["bootstrap"]["mode"] = "headless"
    with pytest.raises(
        InterfaceContractValidationError, match="bootstrap must remain console-only"
    ):
        InterfaceContractEnricher(_write_config(tmp_path, invalid))


def test_rejects_unsanitized_aws_evidence(tmp_path: Path, contract_config: dict[str, Any]) -> None:
    invalid = copy.deepcopy(contract_config)
    _contract(invalid)["providers"]["aws"]["evidence"]["receipts"][0]["sanitized"] = False
    with pytest.raises(InterfaceContractValidationError, match="evidence receipt is invalid"):
        InterfaceContractEnricher(_write_config(tmp_path, invalid))


def test_aws_telemetry_intake_accepts_complete_required_observations(
    contract_config: dict[str, Any],
) -> None:
    intake = copy.deepcopy(_contract(contract_config)["providers"]["aws"]["telemetry_intake"])
    intake["availability"] = "available"
    intake["observed_facts"] = list(intake["required_facts"])
    intake["unavailable_facts"] = []
    intake["complete"] = True
    assert validate_aws_telemetry_intake(intake) is True


@pytest.mark.parametrize(
    "mutation",
    [
        lambda intake: intake["required_facts"].remove("runtime"),
        lambda intake: intake["observed_facts"].append("runtime"),
        lambda intake: intake.update({"complete": True}),
        lambda intake: intake.update({"schema_id": "unknown/v1"}),
        lambda intake: intake.update(
            {
                "availability": "available",
                "observed_facts": list(intake["required_facts"]),
                "unavailable_facts": ["additional_demonstrated_fact"],
                "complete": True,
            }
        ),
    ],
)
def test_aws_telemetry_intake_fails_closed(
    contract_config: dict[str, Any], mutation: Mutation
) -> None:
    intake = copy.deepcopy(_contract(contract_config)["providers"]["aws"]["telemetry_intake"])
    mutation(intake)
    with pytest.raises(InterfaceContractValidationError):
        validate_aws_telemetry_intake(intake)
