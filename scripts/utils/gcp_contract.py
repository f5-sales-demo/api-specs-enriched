"""GCP schema boundary; schema presence never grants runtime execution."""

from __future__ import annotations

from typing import Any

GCP_SCHEMA_PROFILE: dict[str, Any] = {
    "schema_version": 1,
    "availability": "schema_only",
    "enforcement": "reject_before_mutation",
    "node_list_path": "gcp.not_managed.node_list[]",
    "interface_list_path": "gcp.not_managed.node_list[].interface_list[]",
    "schema_mappings": {
        "provider_schema": "securemesh_site_v2GCPProviderType",
        "node_list_schema": "securemesh_site_v2NodeList",
        "node_schema": "viewssecuremesh_site_v2Node",
        "interface_schema": "securemesh_site_v2Interface",
        "ethernet_schema": "viewssecuremesh_site_v2EthernetInterfaceType",
        "node_hostname": "hostname",
        "ethernet_device": "ethernet_interface.device",
        "ethernet_mac": "ethernet_interface.mac",
        "mapping_semantics": "schema_presence_only_not_runtime_identity",
    },
    "capabilities": {
        "gcp_ce_create": "unavailable",
        "bootstrap": "unavailable",
        "registration": "unavailable",
        "physical_interface_identity": "unavailable",
        "runtime_status": "unavailable",
        "routing": "unavailable",
        "site_upgrade": "unavailable",
    },
    "qualification_required": [
        "site_configuration_api",
        "site_bound_bootstrap",
        "image_boot_registration",
        "cloud_guest_platform_interface_join",
        "runtime_health",
        "routing_return_path",
        "software_os_upgrade",
    ],
    "source": {
        "repository": "f5-sales-demo/api-specs-enriched",
        "tag": "v12.0.10",
        "asset": "openapi.json",
        "sha256": "sha256:5fe5ebefa9aac5a6a307897aab18ee37491b767ed5a59bbb9451aa83ecc17789",
    },
}


def validate_gcp_contract(profile: Any) -> None:
    """Reject inferred wire mappings and evidence-free capability promotion."""
    if profile != GCP_SCHEMA_PROFILE:
        raise ValueError(
            "GCP schema-only boundary requires exact mappings and unavailable runtime capabilities"
        )


def validate_gcp_openapi(openapi: dict[str, Any]) -> None:
    """Verify the provider-to-node-to-interface reference chain explicitly."""
    schemas = openapi.get("components", {}).get("schemas", {})
    expected = {
        ("viewssecuremesh_site_v2CreateSpecType", "gcp"): "securemesh_site_v2GCPProviderType",
        ("viewssecuremesh_site_v2GetSpecType", "gcp"): "securemesh_site_v2GCPProviderType",
        ("viewssecuremesh_site_v2ReplaceSpecType", "gcp"): "securemesh_site_v2GCPProviderType",
        ("securemesh_site_v2GCPProviderType", "not_managed"): "securemesh_site_v2NodeList",
        ("securemesh_site_v2NodeList", "node_list"): "viewssecuremesh_site_v2Node",
        ("viewssecuremesh_site_v2Node", "interface_list"): "securemesh_site_v2Interface",
        (
            "securemesh_site_v2Interface",
            "ethernet_interface",
        ): "viewssecuremesh_site_v2EthernetInterfaceType",
    }
    for (schema, field), target in expected.items():
        value = schemas.get(schema, {}).get("properties", {}).get(field, {})
        if field in {"node_list", "interface_list"}:
            if value.get("type") != "array":
                raise ValueError("GCP node/interface list must be an array")
            value = value.get("items", {})
        refs = [value.get("$ref")] + [item.get("$ref") for item in value.get("allOf", [])]
        if f"#/components/schemas/{target}" not in refs:
            raise ValueError("GCP schema reference chain is unsupported")
    for schema, field in [
        ("viewssecuremesh_site_v2Node", "hostname"),
        ("viewssecuremesh_site_v2EthernetInterfaceType", "device"),
        ("viewssecuremesh_site_v2EthernetInterfaceType", "mac"),
    ]:
        if schemas.get(schema, {}).get("properties", {}).get(field, {}).get("type") != "string":
            raise ValueError("GCP schema identity field is unsupported")
