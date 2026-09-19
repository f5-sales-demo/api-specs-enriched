"""Source-owned, fail-closed current SMSv2 KVM image resolution contract."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

KVM_IMAGE_SEMANTICS: dict[str, Any] = {
    "availability": "evidence_backed",
    "enforcement": "required",
    "strategy": "site_uid_os_image",
    "namespace": "system",
    "terraform_data_source": "site_image",
    "replaces_operation": "ves.io.schema.registration.CustomAPI.GetImageDownloadUrl",
    "configuration_get": {
        "method": "GET",
        "path": "/api/config/namespaces/system/securemesh_site_v2s/{site_name}",
        "operation_id": "ves.io.schema.views.securemesh_site_v2.API.Get",
    },
    "configuration_list": {
        "method": "GET",
        "path": "/api/config/namespaces/system/securemesh_site_v2s",
        "operation_id": "ves.io.schema.views.securemesh_site_v2.API.List",
    },
    "site_list": {
        "method": "GET",
        "path": "/api/config/namespaces/system/sites",
        "operation_id": "ves.io.schema.site.API.List",
    },
    "identity": {
        "configuration_name": "items[].name",
        "configuration_uid": "items[].uid",
        "site_uid": "items[].uid",
        "owner_kind": "items[].owner_view.kind",
        "required_owner_kind": "securemesh_site_v2",
        "owner_uid": "items[].owner_view.uid",
        "request_uid": "site_uid",
        "configuration_matches": 1,
        "owner_matches": 1,
    },
    "query": {
        "method": "POST",
        "path": "/api/maurice/software_os_version",
        "operation_id": "ves.io.schema.virtual_appliance.SoftwareVersionOsImageCustomApi.GetImage",
        "request_schema": "virtual_applianceGetImageRequest",
        "response_schema": "virtual_applianceGetImageResponse",
        "request_field": "uids",
        "request_cardinality": 1,
        "side_effects": "none",
    },
    "response": {
        "mapping": "images",
        "mapping_key": "site_uid",
        "download_url": "download_image_link",
        "image_name": "copy_image_name",
        "md5": "image_md5_sum",
        "error": "error_description",
    },
    "validation": {
        "caller_supplied_uid": False,
        "static_fallback": False,
        "require_current_owner_mapping": True,
        "required_platform_field": "spec.kvm",
        "reject_nonempty_error": True,
        "require_complete_image_fields": True,
        "download_scheme": "https",
        "download_hosts": ["downloads.volterra.io"],
        "md5_pattern": "^[0-9a-fA-F]{32}$",
        "verify_artifact_checksum": True,
        "boot_acceptance_required": True,
    },
}


def validate_kvm_image_contract(value: Any) -> None:
    """Do not infer a resolver from similar names, UIDs, or old static defaults."""
    if not isinstance(value, dict) or set(value) != set(KVM_IMAGE_SEMANTICS) | {"provenance"}:
        raise ValueError("KVM image resolution contract is incomplete")
    for key, expected in KVM_IMAGE_SEMANTICS.items():
        if value[key] != expected:
            raise ValueError(f"KVM image resolution {key} semantics are unsupported")
    provenance = value["provenance"]
    expected_fields = {
        "source_issue",
        "source_commit",
        "upstream_spec_sha256",
        "receipt_path",
        "receipt_sha256",
    }
    if not isinstance(provenance, dict) or set(provenance) != expected_fields:
        raise ValueError("KVM image resolution provenance is incomplete")
    if provenance["source_issue"] != "f5-sales-demo/api-specs-enriched#1805":
        raise ValueError("KVM image resolution requires its source issue")
    if provenance["receipt_path"] != "config/evidence/kvm-site-image-resolution-20260919.json":
        raise ValueError("KVM image resolution requires its verified receipt")
    for key, length in (
        ("source_commit", 40),
        ("upstream_spec_sha256", 64),
        ("receipt_sha256", 64),
    ):
        if not isinstance(provenance[key], str) or not re.fullmatch(
            rf"[0-9a-f]{{{length}}}", provenance[key]
        ):
            raise ValueError(f"KVM image resolution {key} is malformed")
    receipt_bytes = (Path(__file__).parents[2] / provenance["receipt_path"]).read_bytes()
    if hashlib.sha256(receipt_bytes).hexdigest() != provenance["receipt_sha256"]:
        raise ValueError("KVM image resolution receipt digest mismatch")
    receipt = json.loads(receipt_bytes)
    if any(
        receipt.get(key) != provenance[key]
        for key in ("source_issue", "source_commit", "upstream_spec_sha256")
    ):
        raise ValueError("KVM image resolution receipt provenance mismatch")
    if receipt.get("acceptance", {}).get("site_uid_query_verified") is not True:
        raise ValueError("KVM image resolution lacks verified query evidence")
