"""Fail closed on incomplete or fabricated Site-UID image resolution semantics."""

import copy
from pathlib import Path

import pytest
import yaml

from scripts.smsv2_release_assets import _evidence
from scripts.utils.kvm_image_contract import validate_kvm_image_contract


def contract():
    config = yaml.safe_load(
        (Path(__file__).parents[1] / "config/interface_contracts.yaml").read_text()
    )
    return config["contracts"]["securemesh_site_v2"]["contract"]["providers"]["kvm"][
        "image_resolution"
    ]


def test_kvm_image_contract_is_complete():
    validate_kvm_image_contract(contract())


def test_kvm_image_contract_replaces_provider_only_image_surface():
    value = contract()
    assert value["terraform_data_source"] == "site_image"
    assert value["replaces_operation"] == "ves.io.schema.registration.CustomAPI.GetImageDownloadUrl"
    assert (
        value["configuration_get"]["path"]
        == "/api/config/namespaces/system/securemesh_site_v2s/{site_name}"
    )
    assert value["validation"]["required_platform_field"] == "spec.kvm"


def test_release_evidence_includes_kvm_query_and_verified_boot():
    config = yaml.safe_load(
        (Path(__file__).parents[1] / "config/interface_contracts.yaml").read_text()
    )
    evidence = _evidence(config["contracts"]["securemesh_site_v2"]["contract"])
    image = evidence["kvm_image_resolution"]
    assert image["source_sha256"] == contract()["provenance"]["receipt_sha256"]
    assert image["receipt"]["acceptance"]["site_uid_query_verified"] is True
    assert image["receipt"]["acceptance"]["kvm_boot_verified"] is True


@pytest.mark.parametrize(
    "field",
    [
        "strategy",
        "configuration_list",
        "site_list",
        "identity",
        "query",
        "response",
        "validation",
        "provenance",
    ],
)
def test_kvm_image_contract_rejects_missing_semantics(field):
    value = copy.deepcopy(contract())
    del value[field]
    with pytest.raises(ValueError, match="contract is incomplete"):
        validate_kvm_image_contract(value)


@pytest.mark.parametrize(
    "replacement", ["configuration_uid", "software_version_uid", "site_name", "caller_supplied_uid"]
)
def test_kvm_image_contract_rejects_fabricated_uid_semantics(replacement):
    value = copy.deepcopy(contract())
    value["identity"]["request_uid"] = replacement
    with pytest.raises(ValueError, match="identity semantics are unsupported"):
        validate_kvm_image_contract(value)


def test_kvm_image_contract_rejects_static_fallback():
    value = copy.deepcopy(contract())
    value["validation"]["static_fallback"] = True
    with pytest.raises(ValueError, match="validation semantics are unsupported"):
        validate_kvm_image_contract(value)


def test_kvm_image_contract_rejects_fabricated_receipt_digest():
    value = copy.deepcopy(contract())
    value["provenance"]["receipt_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="receipt digest mismatch"):
        validate_kvm_image_contract(value)
