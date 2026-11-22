"""Generated object UID and read-boundary requirements stay explicit in SMSv2."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from scripts.utils.interface_contract_enricher import (
    InterfaceContractValidationError,
    validate_aws_v3_contract,
)


def profile() -> dict:
    config = yaml.safe_load(
        (Path(__file__).parents[1] / "config/interface_contracts.yaml").read_text()
    )
    return config["contracts"]["securemesh_site_v2"]["contract"]["providers"]["aws"]


def test_generated_uid_is_distinct_from_the_owning_site_uid() -> None:
    observation = profile()["runtime"]["physical_interfaces"]
    assert observation["response_mappings"]["generated_interface_uid"] == "items[].uid"
    exact = observation["generated_objects"]["exact_item"]
    assert exact["uid_field"] == "system_metadata.uid"
    assert exact["owner_field"] == "system_metadata.owner_view"
    assert "uid" in observation["identity"]["generated_interface"]


def test_exact_get_identity_is_rechecked_after_publication_without_claiming_freshness() -> None:
    observation = profile()["runtime"]["physical_interfaces"]
    exact = observation["generated_objects"]["exact_item"]
    assert exact["reobserve_after_publication"] is True
    assert observation["identity"]["generated_uid_policy"] == (
        "observed_uuid_bind_after_discovery_reject_change"
    )
    assert observation["freshness"]["original_ce_sample_timestamp"] == "unqualified"
    assert observation["qualification"]["generated_uid_continuity"] is False
    assert observation["qualification"]["exact_get_identity_join"] is False


@pytest.mark.parametrize("case", ["missing-uid", "owner-uid", "no-recheck", "false-qualification"])
def test_incomplete_or_forged_uid_contract_is_rejected(case: str) -> None:
    contract = copy.deepcopy(profile())
    observation = contract["runtime"]["physical_interfaces"]
    if case == "missing-uid":
        observation["response_mappings"].pop("generated_interface_uid", None)
    elif case == "owner-uid":
        observation["response_mappings"]["generated_interface_uid"] = "items[].owner_view.uid"
    elif case == "no-recheck":
        observation["generated_objects"]["exact_item"]["reobserve_after_publication"] = False
    else:
        observation["qualification"]["generated_uid_continuity"] = True
    with pytest.raises(InterfaceContractValidationError, match="runtime endpoints or schemas"):
        validate_aws_v3_contract(contract)
