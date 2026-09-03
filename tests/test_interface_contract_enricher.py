"""Tests for the evidence-backed Secure Mesh interface contract."""

from __future__ import annotations

import copy
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
    assert create_contract[X_F5XC_CE_AUTOMATION_CONTRACT]["contract_id"] == "f5xc-ce-automation/v3"
    aws = create_contract[X_F5XC_CE_AUTOMATION_CONTRACT]["providers"]["aws"]
    assert aws["interface_identity"]["guest_device"] == "rejected"
    assert aws["interface_identity"]["fields"] == ["node", "ethernet_interface.mac"]


def test_v3_contract_defines_exact_runtime_mappings_without_freshness_claims(
    contract_config: dict[str, Any],
) -> None:
    aws = _contract(contract_config)["providers"]["aws"]
    identity = aws["interface_identity"]
    assert identity["uniqueness_scope"] == "node"
    assert identity["mac"]["normalization"] == "ieee802_lowercase_colon"
    assert identity["known_value_policy"] == (
        "reject_null_incomplete_malformed_ambiguous_or_inconsistent"
    )
    assert identity["unknown_value_policy"] == "defer"

    runtime = aws["runtime"]
    assert runtime["configuration"]["nullability"]["public_ip"] == "nullable"
    assert runtime["configuration"]["correlation"] == ["node", "normalized_mac"]
    assert runtime["bgp_peers"]["response_mappings"]["state_changed_at"] == (
        "ver[].peer[].up_down_timestamp"
    )
    assert runtime["simplified_routes"]["semantics"] == "observational_read_only"
    assert runtime["simplified_routes"]["request_mappings"]["roles"] == ["slo", "sli"]
    assert "autonomous_system_numbers" in aws["authorities"]["aws"]
    assert "observed_at" not in repr(aws)
    assert aws["evidence"]["recorded_at"].endswith("Z")


def test_contract_defines_stable_identity_and_role_invariants() -> None:
    enricher = InterfaceContractEnricher()
    contract = enricher.contracts[0][1]
    azure = contract["providers"]["azure"]
    assert contract["contract_id"] == "f5xc-ce-automation/v3"
    assert contract["api"]["namespace"] == "system"
    assert contract["api"]["operations"] == ["create", "read", "replace", "delete"]
    assert contract["providers"]["aws"]["availability"] == "schema_only"
    assert contract["version"] == "6.0.0"
    assert contract["providers"]["aws"]["capabilities"] == {
        "aws_ce_create": "unavailable",
        "runtime_status": "unavailable",
        "tgw_connect": "unavailable",
    }
    assert [role["name"] for role in contract["providers"]["aws"]["roles"]] == ["slo", "sli"]
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


def test_rejects_partially_available_schema_only_aws_automation(
    tmp_path: Path, contract_config: dict[str, Any]
) -> None:
    invalid = copy.deepcopy(contract_config)
    _contract(invalid)["providers"]["aws"]["capabilities"]["tgw_connect"] = "available"
    with pytest.raises(
        InterfaceContractValidationError, match="schema-only AWS capabilities must fail closed"
    ):
        InterfaceContractEnricher(_write_config(tmp_path, invalid))


def test_accepts_additive_current_contract_fields(
    tmp_path: Path, contract_config: dict[str, Any]
) -> None:
    compatible = copy.deepcopy(contract_config)
    contract = _contract(compatible)
    contract["api"]["status_path"] = (
        "/api/config/namespaces/{namespace}/securemesh_site_v2s/{name}/status"
    )
    contract["api"]["operations"].append("status")
    contract["providers"]["aws"]["future_additive_field"] = {"safe": True}
    contract["providers"]["gcp"] = {"availability": "schema_only"}

    enricher = InterfaceContractEnricher(_write_config(tmp_path, compatible))

    assert enricher.contracts[0][1]["version"] == "6.0.0"
    assert enricher.contracts[0][1]["providers"]["gcp"]["availability"] == "schema_only"


@pytest.mark.parametrize(
    "version", ["2", "2.1", "02.1.0", "6.0.0-dev", "2.1.0", "3.0.0", "4.9.9", "5.0.1", "7.0.0"]
)
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
    _contract(incompatible)["contract_id"] = "f5xc-ce-automation/v1"

    with pytest.raises(InterfaceContractValidationError, match="invalid contract identity"):
        InterfaceContractEnricher(_write_config(tmp_path, incompatible))


@pytest.mark.parametrize("version", [None, "3.0.0", "4.9.9", "5.0.1", "7.0.0"])
def test_rejects_noncurrent_configuration_version(
    tmp_path: Path, contract_config: dict[str, Any], version: object
) -> None:
    incompatible = copy.deepcopy(contract_config)
    incompatible["version"] = version

    with pytest.raises(
        InterfaceContractValidationError,
        match="configuration has an unsupported version",
    ):
        InterfaceContractEnricher(_write_config(tmp_path, incompatible))


def test_accepts_schema_only_aws_when_every_capability_fails_closed(
    tmp_path: Path, contract_config: dict[str, Any]
) -> None:
    compatible = copy.deepcopy(contract_config)
    aws = _contract(compatible)["providers"]["aws"]
    aws["availability"] = "schema_only"
    aws["capabilities"] = dict.fromkeys(aws["capabilities"], "unavailable")
    aws["unavailable_capabilities"] = list(aws["capabilities"])
    aws["telemetry_intake"]["availability"] = "unavailable"
    aws["telemetry_intake"]["complete"] = False

    InterfaceContractEnricher(_write_config(tmp_path, compatible))


def test_rejects_available_telemetry_for_schema_only_aws(
    tmp_path: Path, contract_config: dict[str, Any]
) -> None:
    invalid = copy.deepcopy(contract_config)
    intake = _contract(invalid)["providers"]["aws"]["telemetry_intake"]
    intake["availability"] = "available"
    intake["complete"] = True

    with pytest.raises(
        InterfaceContractValidationError,
        match="schema-only AWS telemetry must fail closed",
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


def test_aws_evidence_receipt_has_closed_modern_shape(
    contract_config: dict[str, Any],
) -> None:
    assert contract_config["version"] == "6.0.0"
    receipt = _contract(contract_config)["providers"]["aws"]["evidence"]["receipts"][0]
    assert set(receipt) == {
        "operations",
        "result",
        "blocking_conditions",
        "sanitized",
        "redaction",
    }
    assert receipt["result"] == "rejected"
    assert receipt["blocking_conditions"] == [
        "mac_only_interface_rejected_by_live_api",
        "public_ip_empty_string_null_round_trip",
    ]


def test_rejects_undeclared_aws_receipt_field(
    tmp_path: Path, contract_config: dict[str, Any]
) -> None:
    invalid = copy.deepcopy(contract_config)
    receipt = _contract(invalid)["providers"]["aws"]["evidence"]["receipts"][0]
    receipt["source_url"] = "https://example.invalid/legacy"
    with pytest.raises(
        InterfaceContractValidationError, match="evidence receipt contains unsupported fields"
    ):
        InterfaceContractEnricher(_write_config(tmp_path, invalid))


def test_aws_telemetry_intake_accepts_complete_required_observations(
    contract_config: dict[str, Any],
) -> None:
    intake = copy.deepcopy(_contract(contract_config)["providers"]["aws"]["telemetry_intake"])
    intake["availability"] = "available"
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


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda aws: aws["interface_identity"].update({"field": "ethernet_interface.device"}),
            "MAC-bound",
        ),
        (
            lambda aws: aws["roles"].append(
                {"name": "external", "network_option": "site_local_network"}
            ),
            "exactly slo and sli",
        ),
        (
            lambda aws: aws["runtime"]["configuration"].update(
                {"path": "/api/config/namespaces/{namespace}/sites/{site}/interface"}
            ),
            "incomplete or legacy",
        ),
        (
            lambda aws: aws["runtime"]["simplified_routes"].update(
                {"path": "/api/operate/namespaces/{namespace}/sites/{site}/ver/routes"}
            ),
            "incomplete or legacy",
        ),
        (
            lambda aws: aws["authorities"]["aws"].append("runtime_health"),
            "authority declarations",
        ),
        (
            lambda aws: aws["authorities"]["aws"].remove("autonomous_system_numbers"),
            "authority declarations",
        ),
        (
            lambda aws: aws["runtime"]["configuration"]["nullability"].update(
                {"public_ip": "non_null"}
            ),
            "incomplete or legacy",
        ),
        (
            lambda aws: aws["runtime"]["bgp_peers"]["response_mappings"].update(
                {"state_changed_at": "ver[].peer[].observed_at"}
            ),
            "incomplete or legacy",
        ),
        (
            lambda aws: aws["runtime"]["simplified_routes"].update(
                {"semantics": "resource_creating"}
            ),
            "incomplete or legacy",
        ),
        (
            lambda aws: aws["interface_identity"]["mac"].update({"nullable": True}),
            "node/MAC-bound",
        ),
        (
            lambda aws: aws["interface_identity"].update({"uniqueness_scope": "global"}),
            "node/MAC-bound",
        ),
    ],
)
def test_rejects_non_v3_runtime_identity_and_authority(
    tmp_path: Path,
    contract_config: dict[str, Any],
    mutation: Mutation,
    message: str,
) -> None:
    invalid = copy.deepcopy(contract_config)
    mutation(_contract(invalid)["providers"]["aws"])
    with pytest.raises(InterfaceContractValidationError, match=message):
        InterfaceContractEnricher(_write_config(tmp_path, invalid))
