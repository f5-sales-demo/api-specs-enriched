"""Verify the GCP schema-only contract and reject unsupported promotion."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.utils.gcp_contract import (
    GCP_SCHEMA_PROFILE,
    validate_gcp_contract,
    validate_gcp_openapi,
)
from scripts.utils.interface_contract_enricher import (
    InterfaceContractEnricher,
    InterfaceContractValidationError,
)


def test_source_contract_emits_gcp_unavailable_boundary() -> None:
    profile = InterfaceContractEnricher().contracts[0][1]["providers"]["gcp"]
    validate_gcp_contract(profile)
    assert set(profile["capabilities"].values()) == {"unavailable"}
    assert profile["availability"] == "schema_only"


@pytest.mark.parametrize("field", list(GCP_SCHEMA_PROFILE["capabilities"]))
def test_rejects_capability_promotion(field: str) -> None:
    profile = copy.deepcopy(GCP_SCHEMA_PROFILE)
    profile["capabilities"][field] = "available"
    with pytest.raises(ValueError, match="schema-only"):
        validate_gcp_contract(profile)


def test_rejects_inferred_bootstrap_or_cloud_identity() -> None:
    mutations: list[dict[str, Any]] = [
        {"bootstrap": {"provider": "gcp", "headless_checkout": "available"}},
        {"availability": "evidence_backed"},
        {"interface_list_path": "aws.not_managed.node_list[].interface_list[]"},
    ]
    for mutation in mutations:
        profile = {**copy.deepcopy(GCP_SCHEMA_PROFILE), **mutation}
        with pytest.raises(ValueError, match="schema-only"):
            validate_gcp_contract(profile)


def test_enricher_rejects_missing_gcp_profile(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "config/interface_contracts.yaml"
    config = yaml.safe_load(source.read_text())
    del config["contracts"]["securemesh_site_v2"]["contract"]["providers"]["gcp"]
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(InterfaceContractValidationError, match="GCP"):
        InterfaceContractEnricher(path)


def test_schema_chain_is_required() -> None:
    with pytest.raises(ValueError, match="reference chain"):
        validate_gcp_openapi({"components": {"schemas": {}}})


def _schema_fixture() -> dict[str, Any]:
    schemas: dict[str, Any] = {}
    chains = [
        ("viewssecuremesh_site_v2CreateSpecType", "gcp", "securemesh_site_v2GCPProviderType"),
        ("viewssecuremesh_site_v2GetSpecType", "gcp", "securemesh_site_v2GCPProviderType"),
        ("viewssecuremesh_site_v2ReplaceSpecType", "gcp", "securemesh_site_v2GCPProviderType"),
        ("securemesh_site_v2GCPProviderType", "not_managed", "securemesh_site_v2NodeList"),
        ("securemesh_site_v2NodeList", "node_list", "viewssecuremesh_site_v2Node"),
        ("viewssecuremesh_site_v2Node", "interface_list", "securemesh_site_v2Interface"),
        (
            "securemesh_site_v2Interface",
            "ethernet_interface",
            "viewssecuremesh_site_v2EthernetInterfaceType",
        ),
    ]
    for schema, field, target in chains:
        ref = {"$ref": f"#/components/schemas/{target}"}
        value = (
            {"type": "array", "items": ref}
            if field in {"node_list", "interface_list"}
            else {"allOf": [ref]}
        )
        schemas.setdefault(schema, {"properties": {}})["properties"][field] = value
    for schema, field in [
        ("viewssecuremesh_site_v2Node", "hostname"),
        ("viewssecuremesh_site_v2EthernetInterfaceType", "device"),
        ("viewssecuremesh_site_v2EthernetInterfaceType", "mac"),
    ]:
        schemas.setdefault(schema, {"properties": {}})["properties"][field] = {"type": "string"}
    return {"components": {"schemas": schemas}}


def test_accepts_exact_schema_chain_and_rejects_cross_provider_reference() -> None:
    spec = _schema_fixture()
    validate_gcp_openapi(spec)
    spec["components"]["schemas"]["viewssecuremesh_site_v2CreateSpecType"]["properties"]["gcp"] = {
        "allOf": [{"$ref": "#/components/schemas/securemesh_site_v2AWSProviderType"}]
    }
    with pytest.raises(ValueError, match="reference chain"):
        validate_gcp_openapi(spec)


@pytest.mark.parametrize("field", ["device", "mac"])
def test_rejects_malformed_interface_identity_schema(field: str) -> None:
    spec = _schema_fixture()
    spec["components"]["schemas"]["viewssecuremesh_site_v2EthernetInterfaceType"]["properties"][
        field
    ] = {"type": "integer"}
    with pytest.raises(ValueError, match="identity field"):
        validate_gcp_openapi(spec)


def test_api_receipt_is_sanitized_and_does_not_promote_bootstrap() -> None:
    import hashlib
    import json

    verification = GCP_SCHEMA_PROFILE["api_verification"]
    receipt_path = Path(__file__).parents[1] / verification["receipt_path"]
    raw = receipt_path.read_bytes()
    assert verification["receipt_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    receipt = json.loads(raw)
    assert receipt["sanitized"] is True
    assert receipt["runtime_acceptance"] is False
    assert receipt["facts"]["cloud_init_token_placeholder_only"] is True
    assert receipt["facts"]["usable_image_bootstrap"] is False
    assert GCP_SCHEMA_PROFILE["bootstrap_candidate"]["availability"] == "unavailable"
    assert GCP_SCHEMA_PROFILE["capabilities"]["bootstrap"] == "unavailable"


def test_both_engine_qualification_does_not_claim_health_or_ha() -> None:
    import hashlib
    import json

    qualification = GCP_SCHEMA_PROFILE["single_node_qualification"]
    raw = (Path(__file__).parents[1] / qualification["receipt_path"]).read_bytes()
    assert qualification["receipt_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    receipt = json.loads(raw)
    assert receipt["sanitized"] is True
    assert receipt["engines"]["native"]["registration_online"] is True
    assert receipt["engines"]["terraform"]["post_registration_zero_change_plan"] is True
    assert receipt["engines"]["terraform"]["empty_final_state"] is True
    assert receipt["health_observation"]["acceptance"] is False
    assert qualification["ha_acceptance"] is False
    assert set(GCP_SCHEMA_PROFILE["capabilities"].values()) == {"unavailable"}


def test_exact_gcp_registration_and_physical_mapping_requires_owned_joins() -> None:
    registration = GCP_SCHEMA_PROFILE["registration_observation"]
    assert registration["required_provider"] == "GOOGLE"
    assert registration["accepted_state"] == "ONLINE"
    assert registration["reject_partial_list"] is True
    assert registration["reject_ambiguous_matches"] is True
    physical = GCP_SCHEMA_PROFILE["physical_interface_observation"]
    assert physical["hardware_addresses"] == "ip_address[]"
    assert physical["join_cardinality"] == "exactly_one_per_interface"
    assert physical["guest_names_alone"] == "not_authoritative"
