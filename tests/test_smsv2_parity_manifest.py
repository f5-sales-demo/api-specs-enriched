"""Exhaustive SMSv2 nested-path manifest tests."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.smsv2_parity_manifest import build_parity_manifest


def test_manifest_contains_complete_nested_paths_and_semantics() -> None:
    spec = json.loads(Path("docs/specifications/api/openapi.json").read_text())
    manifest = build_parity_manifest(spec)
    paths = {entry["path"]: entry for entry in manifest["paths"]}

    assert manifest["resource"] == "securemesh_site_v2"
    assert manifest["path_count"] == len(paths)
    assert len(paths) > 250
    assert paths["spec.openshift_virtualization"]["wire_key"] == "openshift_virtualization"
    assert paths["spec.segment_vrf[]"]["type"] == "array"
    assert paths["spec.segment_vrf[]"]["cardinality"] == "list"
    assert paths["spec.segment_vrf[].segment_network"]["wire_key"] == "segment_network"
    assert paths["spec.segment_vrf[].segment_network"]["cardinality"] == "single"
    assert paths["spec.segment_vrf[].segment_network.name"]["type"] == "string"
    interface_prefix = "spec.baremetal.not_managed.node_list[].interface_list[]"
    assert paths[f"{interface_prefix}.is_management"]["read_only"] is True
    assert paths[f"{interface_prefix}.is_primary"]["read_only"] is True
    assert not ({"spec.log_receiver", "spec.private_adn", "spec.rseries"} & paths.keys())
    assert manifest["current_platform_removals"] == ["spec.rseries"]
    assert manifest["verified_removals"] == ["spec.private_adn"]
    assert not set(manifest["current_platform_removals"]) & paths.keys()
    assert len(manifest["choice_groups"]) > 50
    assert manifest["choice_groups"]["spec.provider_choice"] == [
        "spec.aws",
        "spec.azure",
        "spec.baremetal",
        "spec.equinix",
        "spec.gcp",
        "spec.kvm",
        "spec.nutanix",
        "spec.oci",
        "spec.openshift_virtualization",
        "spec.openstack",
        "spec.vmware",
    ]


def test_manifest_is_deterministic() -> None:
    spec = json.loads(Path("docs/specifications/api/openapi.json").read_text())
    assert build_parity_manifest(spec) == build_parity_manifest(spec)


def test_platform_evidence_records_only_sanitized_behavioral_conclusions() -> None:
    evidence = yaml.safe_load(Path("config/smsv2_platform_evidence.yaml").read_text())
    assert set(evidence) == {"version", "resource", "probe_date", "scope", "fields"}
    assert evidence["resource"] == "securemesh_site_v2"
    fields = evidence["fields"]
    assert fields["spec.segment_vrf[].segment_network"]["returned_unchanged"] is True
    for path in (
        "spec.segment_vrf[].segment_config.nameserver_v6",
        "spec.segment_vrf[].segment_config.secondary_nameserver_v6",
    ):
        assert fields[path]["classification"] == "behavior_requires_investigation"
        assert fields[path]["server_behavior"] == "silently_removed"
        assert fields[path]["returned_unchanged"] is False
    for path in (
        "spec.segment_vrf[].segment_network",
        "spec.segment_vrf[].segment_config.nameserver_v6",
        "spec.segment_vrf[].segment_config.secondary_nameserver_v6",
    ):
        conclusion = fields[path]
        assert conclusion["replace_status"] == 200
        assert conclusion["restore_status"] == 200
        assert conclusion["restored_canonical_configuration"] is True
    device = fields["spec.aws.not_managed.node_list[].interface_list[].ethernet_interface.device"]
    assert device["classification"] == "current_platform_blocker"
    assert device["replace_status"] == 400
    assert device["server_behavior"] == "omitted_device_validated_as_empty_string"
    assert device["desired_contract"] == "mac_only_identity"
    public_ip = fields["spec.aws.not_managed.node_list[].public_ip"]
    assert public_ip["classification"] == "current_parity"
    assert public_ip["request_value"] is None
    assert public_ip["response_value"] is None
    assert public_ip["legacy_value"] == "empty_string"
    for conclusion in fields.values():
        assert not ({"tenant", "name", "body", "resource_version"} & conclusion.keys())


def test_deprecation_is_not_an_automatic_parity_exclusion(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.yaml"
    evidence.write_text("fields: {}\n")
    spec = {
        "info": {"version": "test"},
        "components": {
            "schemas": {"securemesh_site_v2CreateRequest": {"type": "object", "properties": {}}}
        },
    }
    assert not build_parity_manifest(spec, evidence)["deprecated_exclusions"]


def test_manifest_separates_logical_paths_from_wire_names(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.yaml"
    evidence.write_text("fields: {}\n")
    for reference in (
        {"$ref": "#/components/schemas/Services"},
        {"allOf": [{"$ref": "#/components/schemas/Services"}]},
    ):
        spec = {
            "components": {
                "schemas": {
                    "securemesh_site_v2CreateRequest": {
                        "properties": {
                            "blocked_service": {**reference, "x-f5xc-wire-name": "blocked_sevice"}
                        }
                    },
                    "Services": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"dns": {"type": "boolean"}}},
                    },
                }
            }
        }
        paths = {entry["path"]: entry for entry in build_parity_manifest(spec, evidence)["paths"]}
        assert paths["blocked_service[]"]["wire_key"] == "blocked_sevice"
        assert paths["blocked_service[].dns"]["wire_key"] == "dns"


def test_platform_removal_requires_explicit_rejection_receipt(tmp_path: Path) -> None:
    import pytest

    evidence = tmp_path / "evidence.yaml"
    evidence.write_text("fields:\n  spec.rseries:\n    classification: current_platform_removal\n")
    spec = {
        "components": {
            "schemas": {"securemesh_site_v2CreateRequest": {"type": "object", "properties": {}}}
        }
    }
    with pytest.raises(ValueError, match="removal evidence"):
        build_parity_manifest(spec, evidence)


def test_rseries_removal_retains_verified_api_evidence() -> None:
    spec = json.loads(Path("docs/specifications/api/openapi.json").read_text())
    manifest = build_parity_manifest(spec)
    assert manifest["current_platform_removals"] == ["spec.rseries"]
    proof = manifest["platform_removal_evidence"]["spec.rseries"]
    assert proof["http_status"] == 400
    assert proof["server_message"] == "Rseries provider is not supported for SecureMeshSite"
    assert proof["proof_kind"] == "explicit_api_rejection"


def test_private_adn_removal_retains_create_read_normalization_evidence() -> None:
    spec = json.loads(Path("docs/specifications/api/openapi.json").read_text())
    manifest = build_parity_manifest(spec)
    proof = manifest["verified_removal_evidence"]["spec.private_adn"]
    assert proof["proof_kind"] == "create_read_normalization"
    assert proof["create_status"] == 200
    assert proof["get_status"] == 200
    assert proof["server_behavior"] == "silently_removed"
    assert proof["absence_after_probe_verified"] is True


def test_feature_removal_requires_complete_create_read_evidence(tmp_path: Path) -> None:
    import pytest

    spec = {
        "components": {
            "schemas": {"securemesh_site_v2CreateRequest": {"type": "object", "properties": {}}}
        }
    }
    evidence = tmp_path / "evidence.yaml"
    evidence.write_text(
        json.dumps(
            {
                "fields": {
                    "spec.private_adn": {
                        "classification": "current_feature_removal",
                        "proof_kind": "create_read_normalization",
                        "create_status": 200,
                        "get_status": 200,
                        "server_behavior": "silently_removed",
                        "absence_after_probe_verified": True,
                        "observed_date": "2026-09-06",
                        "legacy_fixture_sha256": "sha256:" + "a" * 64,
                    }
                }
            }
        )
    )
    with pytest.raises(ValueError, match="verified removal evidence"):
        build_parity_manifest(spec, evidence)


def test_removal_null_digest_fails_closed(tmp_path: Path) -> None:
    import pytest

    spec = {
        "components": {
            "schemas": {"securemesh_site_v2CreateRequest": {"type": "object", "properties": {}}}
        }
    }
    cases = (
        (
            "spec.rseries",
            {
                "classification": "current_platform_removal",
                "proof_kind": "explicit_api_rejection",
                "http_status": 400,
                "server_message": "Rseries provider is not supported for SecureMeshSite",
                "observed_date": "2026-09-06",
            },
            "platform removal evidence",
        ),
        (
            "spec.private_adn",
            {
                "classification": "current_feature_removal",
                "proof_kind": "create_read_normalization",
                "create_status": 200,
                "get_status": 200,
                "server_behavior": "silently_removed",
                "absence_after_probe_verified": True,
                "observed_date": "2026-09-06",
            },
            "verified removal evidence",
        ),
    )
    for path, conclusion, error in cases:
        evidence = tmp_path / "evidence.yaml"
        conclusion["legacy_fixture_sha256"] = None
        conclusion["probe_receipt_sha256"] = "sha256:" + "b" * 64
        evidence.write_text(json.dumps({"fields": {path: conclusion}}))
        with pytest.raises(ValueError, match=error):
            build_parity_manifest(spec, evidence)


def test_api_validation_and_entitlement_errors_cannot_prove_platform_removal(
    tmp_path: Path,
) -> None:
    import pytest

    spec = {
        "components": {
            "schemas": {"securemesh_site_v2CreateRequest": {"type": "object", "properties": {}}}
        }
    }
    for message in (
        "A subscription to addon f5xc-ipv6-standard is required",
        "Invalid interface configuration",
        None,
        400,
        "Aws provider is not supported for SecureMeshSite",
    ):
        evidence = tmp_path / "evidence.yaml"
        evidence.write_text(
            json.dumps(
                {
                    "fields": {
                        "spec.rseries": {
                            "classification": "current_platform_removal",
                            "proof_kind": "explicit_api_rejection",
                            "http_status": 400,
                            "server_message": message,
                            "observed_date": "2026-09-06",
                            "legacy_fixture_sha256": "sha256:" + "a" * 64,
                            "probe_receipt_sha256": "sha256:" + "b" * 64,
                        }
                    }
                }
            )
        )
        with pytest.raises(ValueError, match="removal evidence"):
            build_parity_manifest(spec, evidence)
