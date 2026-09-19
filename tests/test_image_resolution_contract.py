"""Observed failures and bootstrap templates must not become fabricated contracts."""
# pylint: disable=protected-access

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from scripts.utils.operation_metadata_enricher import OperationMetadataEnricher

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ves.io.schema.registration.CustomAPI.GetImageDownloadUrl"
CLOUD_INIT = "ves.io.schema.token.CustomAPI.GetCloudInitConfig"


def metadata():
    return yaml.safe_load((ROOT / "config/operation_metadata.yaml").read_text())


def test_image_failure_preserves_unknown_lookup_scope_and_immutable_evidence():
    requirement = metadata()["query_operations"][IMAGE]["prerequisites"][0]
    assert requirement["availability"] == "unresolved_server_lookup"
    assert requirement["lookup_scope"] == "unknown"
    assert requirement["lookup_count"] == "unknown"
    assert "tenant must" not in requirement["reason"].lower()
    source = requirement["source"]
    receipt = ROOT / source["receipt_path"]
    assert hashlib.sha256(receipt.read_bytes()).hexdigest() == source["receipt_sha256"]
    evidence = json.loads(receipt.read_text())
    assert evidence["source_commit"] == source["source_commit"]
    assert evidence["spec_sha256"] == source["spec_sha256"]
    assert evidence["correction_verified"] is False


def test_cloud_init_template_is_not_mislabeled_as_token_issuance():
    config = metadata()
    assert CLOUD_INIT not in config.get("issuance_operations", {})
    query = config["query_operations"][CLOUD_INIT]
    assert query["terraform_name"] == "site_cloud_init"
    assert query["required_fields"] == ["provider", "site_name"]
    assert "creates" not in query


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("availability", "external_tenant_prerequisite"),
        ("lookup_scope", "tenant"),
        ("lookup_count", 0),
        ("cardinality", {"exactly": True}),
    ],
)
def test_validator_rejects_inferred_or_malformed_lookup_semantics(field, value):
    contract = copy.deepcopy(metadata()["query_operations"][IMAGE])
    contract["prerequisites"][0][field] = value
    with pytest.raises(ValueError, match="prerequisite"):
        OperationMetadataEnricher._validated_prerequisites(contract, IMAGE)


def test_validator_rejects_unbound_runtime_error_provenance():
    contract = copy.deepcopy(metadata()["query_operations"][IMAGE])
    contract["prerequisites"][0]["source"].pop("receipt_sha256", None)
    with pytest.raises(ValueError, match="prerequisite"):
        OperationMetadataEnricher._validated_prerequisites(contract, IMAGE)
