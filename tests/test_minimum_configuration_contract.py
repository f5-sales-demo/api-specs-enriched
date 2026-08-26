"""Minimum-configuration path and choice-group contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.utils.minimum_configuration_enricher import validate_minimum_configuration_paths


def test_smsv2_minimum_configuration_resolves_through_schema_graph() -> None:
    spec = json.loads(Path("docs/specifications/api/openapi.json").read_text())
    config = yaml.safe_load(Path("config/minimum_configs.yaml").read_text())

    validate_minimum_configuration_paths(spec, config, resource="securemesh_site_v2")


def test_every_minimum_configuration_path_resolves_through_schema_graph() -> None:
    spec = json.loads(Path("docs/specifications/api/openapi.json").read_text())
    config = yaml.safe_load(Path("config/minimum_configs.yaml").read_text())

    validate_minimum_configuration_paths(spec, config)


def test_smsv2_provider_choice_is_complete_and_not_a_synthetic_required_field() -> None:
    config = yaml.safe_load(Path("config/minimum_configs.yaml").read_text())
    smsv2 = config["resources"]["securemesh_site_v2"]

    assert "spec.provider_choice" not in smsv2["required_fields"]
    provider_choice = next(
        group for group in smsv2["mutually_exclusive_groups"] if group["name"] == "provider_choice"
    )
    assert provider_choice["required"] is True
    assert provider_choice["fields"] == [
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
    logs = next(
        group
        for group in smsv2["mutually_exclusive_groups"]
        if group["name"] == "logs_receiver_choice"
    )
    assert logs["fields"] == [
        "spec.log_receiver_with_net",
        "spec.logs_streaming_disabled",
    ]
