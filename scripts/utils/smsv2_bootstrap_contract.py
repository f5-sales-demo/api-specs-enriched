"""Validate bootstrap wire mappings separately from platform acceptance evidence."""

from __future__ import annotations

import json
from typing import Any

# This is a wire contract, not permission to deploy or proof of current health.
AWS_BOOTSTRAP: dict[str, Any] = {
    "mode": "site_bound_jwt_cloud_init",
    "reference": "deployment_bound_opaque_one_use",
    "headless_checkout": "available",
    "schema_support": "available",
    "runtime_verification": "historical_aws_acceptance",
    "interface_configuration": {
        "device_wire_requirement": "nonempty_observed_device_required_when_configuring_interfaces",
        "device_identity": "node_and_mac_remain_authoritative",
        "mac_only_create": "rejected_by_live_api",
        "primary_interface_settings": "configure_before_registration_immutable_after_registration",
        "discovery_requirement": "observe_guest_devices_before_configured_site_creation",
        "replacement_requirement": "couple_vm_and_site_when_initial_discovery_requires_registration",
        "runtime_acceptance": "awaiting_configured_preboot_vm_evidence",
        "source_issue": "f5-sales-demo/api-specs-enriched#1750",
        "receipt_path": "config/evidence/aws-preboot-interface-api-20260908.json",
        "receipt_sha256": "b613d9fe858f58a36f3dc4dc7409ea95bafcb0669a70bda5d92cfd32abdb04eb",
    },
    "sequence": ["site_create", "jwt_issue", "cloud_init_issue", "deploy", "registration"],
    "token": {
        "method": "POST",
        "path": "/api/register/namespaces/system/tokens",
        "request_fields": {"spec.type": 1, "spec.site_name": "site_name"},
        "metadata_fields": {"name": "token_name", "namespace": "system"},
        "site_response_path": "spec.site_name",
        "type_response_path": "spec.type",
        "credential_response_path": "spec.content",
        "accepted_response_types": [1, "JWT"],
        "site_binding_required": True,
    },
    "cloud_init": {
        "method": "GET",
        "path": "/api/register/namespaces/system/get-cloud-init-config",
        "operation_id": "ves.io.schema.token.CustomAPI.GetCloudInitConfig",
        "operation_role": "issuance",
        "query_fields": {
            "provider": "aws",
            "site_name": "site_name",
            "enable_management_network": False,
        },
        "response_path": "cloud_init_config",
        "sensitive": True,
        "refresh_on_read": False,
    },
    "material": {
        "preserve_paths": ["/etc/vpm/config.yaml"],
        "issued_path": "/etc/vpm/user_data",
        "token_placeholders": ["{{ .Token }}", "{{ .token }}"],
        "placeholder_value": "spec.content",
        "reject_unresolved_placeholders": True,
        "storage": "restricted_deployment_storage",
    },
    "evidence": {
        "scope": "aws_only",
        "source_repository": "f5-sales-demo/mcn",
        "source_commit": "4dc25ec4f423c82aa9b052f81ca3c114b25a129c",
        "source_issue": "f5-sales-demo/mcn#1103",
        "fresh_acceptance_required": True,
    },
}

AZURE_BOOTSTRAP: dict[str, Any] = {
    "schema_support": "available",
    "runtime_verification": "awaiting_evidence",
    "headless_checkout": "unavailable",
    "reason": "azure_api_verified_image_runtime_not_accepted",
    "api_verification": {
        "status": "verified_api_only",
        "scope": "azure_bootstrap_api_only",
        "schema_commit": "5ce68bff35b444e5af967433ad3d14ca96a1ffe0",
        "source_issue": "f5-sales-demo/api-specs-enriched#1748",
        "receipt_path": "config/evidence/azure-bootstrap-api-20260908.json",
        "receipt_sha256": "ba3975c417a43e9e6a68d02bb51bec76bfe043aa1d1a756b199377be0f450e21",
        "runtime_acceptance": False,
    },
    "source_repository": "f5-sales-demo/mcn",
    "source_commit": "c1310d030cfc252571bf0e816db21b618697d991",
    "source_path": "docs/en/customer-edge/smsv2/azure-route-server.mdx",
}


def validate_bootstrap_contract(providers: Any) -> None:
    """Reject unknown mappings and claims beyond the pinned platform evidence."""
    if not isinstance(providers, dict):
        raise TypeError("bootstrap providers must be an object")
    for provider, expected in (("aws", AWS_BOOTSTRAP), ("azure", AZURE_BOOTSTRAP)):
        profile = providers.get(provider)
        if not isinstance(profile, dict) or json.dumps(
            profile.get("bootstrap"), sort_keys=True
        ) != json.dumps(expected, sort_keys=True):
            raise ValueError(f"{provider} bootstrap mapping or evidence is unsupported")
