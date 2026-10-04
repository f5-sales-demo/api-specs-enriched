"""Minimum-configuration path and choice-group contract tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from scripts.site_curation import CURATED_RESOURCE_NAMES
from scripts.utils.canonical_merge import canonical_merge_sources
from scripts.utils.minimum_configuration_enricher import validate_minimum_configuration_paths

BOT_DEFENSE_PATH = "spec.bot_defense_advanced_protection"
STALE_BOT_DEFENSE_PATH = "spec.bot_defense_advanced"
BOT_DEFENSE_CONFIGS = (
    Path("config/minimum_configs.yaml"),
    Path("config/console_field_metadata.yaml"),
    Path("config/console_ui.yaml"),
    Path("config/discovered_defaults.yaml"),
)


def _target_spec() -> dict:
    sources = {}
    for path in sorted(Path("specs/original").glob("*.json")):
        if path.name == "manifest.json":
            continue
        document = json.loads(path.read_text())
        if isinstance(document, dict) and isinstance(document.get("paths"), dict):
            sources[path.name] = document
    assert sources, "download the pinned target upstream specifications before testing"
    return canonical_merge_sources(sources).merged


def test_smsv2_minimum_configuration_resolves_through_schema_graph() -> None:
    spec = json.loads(Path("docs/specifications/api/openapi.json").read_text())
    config = yaml.safe_load(Path("config/minimum_configs.yaml").read_text())

    validate_minimum_configuration_paths(spec, config, resource="securemesh_site_v2")


def test_every_minimum_configuration_path_resolves_through_schema_graph() -> None:
    spec = json.loads(Path("docs/specifications/api/openapi.json").read_text())
    config = yaml.safe_load(Path("config/minimum_configs.yaml").read_text())
    for resource_name in CURATED_RESOURCE_NAMES:
        config["resources"].pop(resource_name, None)

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
        "spec.eks_k8s",
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


def test_bot_defense_configuration_tracks_target_upstream_schema() -> None:
    schemas = _target_spec()["components"]["schemas"]
    properties = schemas["viewshttp_loadbalancerCreateSpecType"]["properties"]

    assert "bot_defense_advanced_protection" in properties
    assert "bot_defense_advanced" not in properties
    for path in BOT_DEFENSE_CONFIGS:
        text = path.read_text()
        assert BOT_DEFENSE_PATH.replace("spec.", "") in text
        assert re.search(rf"{re.escape(STALE_BOT_DEFENSE_PATH)}(?!_protection)", text) is None
