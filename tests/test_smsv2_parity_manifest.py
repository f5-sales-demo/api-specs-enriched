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
    assert manifest["current_platform_removals"] == [
        "spec.segment_vrf[].segment_config.nameserver_v6",
        "spec.segment_vrf[].segment_config.secondary_nameserver_v6",
    ]
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
        assert fields[path]["classification"] == "current_platform_removal"
        assert fields[path]["server_behavior"] == "silently_removed"
        assert fields[path]["returned_unchanged"] is False
    for conclusion in fields.values():
        assert conclusion["replace_status"] == 200
        assert conclusion["restore_status"] == 200
        assert conclusion["restored_canonical_configuration"] is True
        assert not ({"tenant", "name", "body", "resource_version"} & conclusion.keys())
