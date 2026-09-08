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
    "reason": "aws_acceptance_does_not_certify_azure_image_bootstrap",
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
