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
    "api_verification": {
        "availability": "verified_api_only",
        "runtime_acceptance": False,
        "receipt_path": "config/evidence/gcp-bootstrap-api-20261018.json",
        "receipt_sha256": "sha256:33cb727fdee919b3adc41e3f39739c3b07e9eb05cd9e395d4d8af64a83836b2a",
        "cloud_init_semantics": "token_placeholder_only_not_image_bootstrap",
    },
    "bootstrap_candidate": {
        "availability": "unavailable",
        "strategy": "guide_backed_gcp_user_data",
        "source_url": "https://docs.cloud.f5.com/docs-v2/multi-cloud-network-connect/how-to/site-management/deploy-sms-gcp-clickops",
        "metadata_key": "user-data",
        "dns_metadata": {"VmDnsSetting": "ZonePreferred"},
        "issued_path": "/etc/vpm/user_data",
        "preserve_path": "/etc/vpm/config.yaml",
        "runtime_acceptance": False,
        "qualification_required": "exact_image_boot_and_registration",
        "token": {
            "method": "POST",
            "path": "/api/register/namespaces/system/tokens",
            "request_fields": {"spec.type": 1, "spec.site_name": "site_name"},
            "metadata_fields": {"name": "token_name", "namespace": "system"},
            "site_binding_required": True,
            "credential_response_path": "spec.content",
            "accepted_response_types": [1, "JWT"],
        },
        "material": {
            "format": "cloud_config",
            "metadata_key": "user-data",
            "issued_path": "/etc/vpm/user_data",
            "content_field": "token",
            "preserve_paths": ["/etc/vpm/config.yaml"],
            "private_storage_required": True,
            "one_use_checkout_required": True,
            "binding": ["deployment", "site", "node", "engine"],
        },
    },
    "single_node_qualification": {
        "availability": "verified_boot_registration_identity_only",
        "engines": ["native", "terraform"],
        "topology": "one_node_two_interfaces",
        "receipt_path": "config/evidence/gcp-single-node-both-engines-20261018.json",
        "receipt_sha256": "sha256:768787fefcb48043328c4514b063e44c6d3fed03a8ddda451306712b76b11bef",
        "health_acceptance": False,
        "ha_acceptance": False,
        "routing_acceptance": False,
        "upgrade_acceptance": False,
        "fresh_acceptance_required": True,
    },
    "single_node_lifecycle": {
        "availability": "evidence_backed_single_node",
        "node_count": 1,
        "interface_count": 2,
        "interface_roles": ["slo", "sli"],
        "engines": ["native", "terraform"],
        "fresh_acceptance_required": True,
        "consumer_runtime_qualification_required": True,
        "site_create": {
            "method": "POST",
            "path": "/api/config/namespaces/system/securemesh_site_v2s",
            "request_schema": "securemesh_site_v2CreateRequest",
            "spec": {
                "gcp": {"not_managed": {}},
                "disable_ha": {},
                "disable_management_network": {},
                "block_all_services": {},
                "no_network_policy": {},
                "no_forward_proxy": {},
                "f5_proxy": {},
                "no_proxy_bypass": {},
                "logs_streaming_disabled": {},
                "no_s2s_connectivity_sli": {},
                "no_s2s_connectivity_slo": {},
                "disable_url_categorization": {},
                "local_vrf": {"default_config": {}, "default_sli_config": {}},
            },
            "ownership_labels": ["xcsh-deployment", "xcsh-engine"],
            "configured_interface_strategy": "registration_discovery",
        },
        "site_delete": {
            "method": "DELETE",
            "path": "/api/config/namespaces/system/securemesh_site_v2s/{site}",
            "exact_ownership_recheck": True,
            "absence_required": True,
        },
        "bootstrap": {
            "strategy": "guide_backed_gcp_user_data",
            "token_method": "POST",
            "token_path": "/api/register/namespaces/system/tokens",
            "token_type": 1,
            "site_binding_required": True,
            "credential_response_path": "spec.content",
            "metadata_key": "user-data",
            "issued_path": "/etc/vpm/user_data",
            "preserve_path": "/etc/vpm/config.yaml",
            "private_deployment_storage": True,
            "one_use_checkout_required": True,
            "terraform_sensitive_state_required": True,
            "cloud_init_placeholder_only": "reject",
        },
        "receipt_path": "config/evidence/gcp-single-node-lifecycle-20261018.json",
        "receipt_sha256": "sha256:1f8d185527e00cc7743376753c517cb4e04b26299dc6088ac5614239faaa86c2",
        "ha": "unavailable",
        "routing": "unavailable",
        "site_upgrade": "unavailable",
        "recovery120seconds": "unqualified",
    },
    "global_health_observation": {
        "availability": "observational_read_only",
        "method": "GET",
        "path": "/api/operate/namespaces/system/sites/{site}/vpm/debug/global/health",
        "operation_id": "ves.io.schema.operate.debug.CustomPublicAPI.HealthPublic",
        "response_schema": "debugHealthResponse",
        "state_schema": "debugVpmState",
        "state_values": [
            "WAITING_FOR_CONFIG",
            "WAITING_FOR_APPROVAL",
            "PROVISIONING",
            "PROVISIONED",
            "FAILED",
            "RESETTING",
        ],
        "hostname_field": "hostname",
        "state_field": "state",
        "healthy_state": "PROVISIONED",
        "scope": "site_global_only",
        "exact_node_join_required": True,
        "requires": ["owned_cloud_identity", "online_registration", "physical_interface_join"],
        "receipt_path": "config/evidence/gcp-global-health-both-engines-20261018.json",
        "receipt_sha256": "sha256:403ea2810ab9a2b8ddb6943fa0f08dbec3723e63d12d23015bb7289c97ee8d8c",
        "fresh_acceptance_required": True,
        "ha_health_acceptance": False,
        "routing_acceptance": False,
        "application_traffic_acceptance": False,
        "explicit_upgrade_acceptance": False,
        "recovery_120_seconds_acceptance": False,
    },
    "registration_observation": {
        "availability": "observational_read_only",
        "method": "GET",
        "path": "/api/register/namespaces/system/registrations_by_site/{site_name}",
        "mappings": {
            "items": "items[]",
            "provider": "items[].get_spec.infra.provider",
            "instance_id": "items[].get_spec.infra.instance_id",
            "hostname": "items[].get_spec.infra.hostname",
            "zone": "items[].get_spec.infra.availability_zone",
            "site_name": "items[].get_spec.passport.cluster_name",
            "state": "items[].object.status.current_state",
        },
        "required_provider": "GOOGLE",
        "accepted_state": "ONLINE",
        "join": [
            "exact_owned_vm_numeric_id",
            "configured_node_hostname",
            "explicit_zone",
            "exact_site_name",
        ],
        "reject_partial_list": True,
        "reject_ambiguous_matches": True,
    },
    "physical_interface_observation": {
        "availability": "observational_read_only",
        "configuration_path": "spec.gcp.not_managed.node_list[].interface_list[]",
        "configured_device": "ethernet_interface.device",
        "configured_mac": "ethernet_interface.mac",
        "hardware_networks": "items[].get_spec.infra.hw_info.network[]",
        "hardware_device": "name",
        "hardware_mac": "mac_address",
        "hardware_addresses": "ip_address[]",
        "cloud_nics": "networkInterfaces[]",
        "cloud_address": "networkIP",
        "cloud_order": "name",
        "slo_primary": True,
        "sli_primary": False,
        "slo_network_option": "site_local_network",
        "sli_network_option": "site_local_inside_network",
        "exact_network_subnet_join": True,
        "guest_names_alone": "not_authoritative",
        "join_cardinality": "exactly_one_per_interface",
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


def validate_gcp_ha_candidate_openapi(openapi: dict[str, Any]) -> None:
    """Check candidate wire shapes only; never promote HA lifecycle or health."""
    schemas = openapi.get("components", {}).get("schemas", {})
    for name in (
        "viewssecuremesh_site_v2CreateSpecType",
        "viewssecuremesh_site_v2GetSpecType",
        "viewssecuremesh_site_v2ReplaceSpecType",
    ):
        value = schemas.get(name, {}).get("properties", {}).get("enable_ha", {})
        refs = [value.get("$ref")] + [item.get("$ref") for item in value.get("allOf", [])]
        if "#/components/schemas/ioschemaEmpty" not in refs:
            raise ValueError("HA candidate enable_ha schema is unsupported")
    path = "/api/operate/namespaces/{namespace}/sites/{site}/vpm/debug/global/{vesnamespace}/status"
    operation = openapi.get("paths", {}).get(path, {}).get("get", {})
    if operation.get("operationId") != "ves.io.schema.operate.debug.CustomPublicAPI.Status":
        raise ValueError("HA candidate component operation is unsupported")
    parameters = operation.get("parameters", [])
    expected = {
        ("namespace", "path"): (True, "string"),
        ("site", "path"): (True, "string"),
        ("vesnamespace", "path"): (True, "string"),
        ("cached", "query"): (False, "boolean"),
    }
    actual = {
        (item.get("name"), item.get("in")): (
            item.get("required", False),
            item.get("schema", {}).get("type"),
        )
        for item in parameters
    }
    if actual != expected or len(parameters) != len(expected):
        raise ValueError("HA candidate component parameters are unsupported")
    response = (
        operation.get("responses", {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    if response.get("$ref") != "#/components/schemas/debugStatusResponse":
        raise ValueError("HA candidate component response is unsupported")
    status = schemas.get("debugStatusResponse", {}).get("properties", {}).get("status", {})
    if (
        status.get("type") != "array"
        or status.get("items", {}).get("$ref") != "#/components/schemas/debugStatus"
    ):
        raise ValueError("HA candidate component row schema is unsupported")
    properties = schemas.get("debugStatus", {}).get("properties", {})
    for field in ("node", "name", "status", "message"):
        if properties.get(field, {}).get("type") != "string":
            raise ValueError("HA candidate component identity/status field is unsupported")
    _validate_gcp_ha_service_inventory(openapi, schemas)


def _validate_gcp_ha_service_inventory(openapi: dict[str, Any], schemas: dict[str, Any]) -> None:
    """Require the independent inventory wire shape without assuming critical-service coverage."""
    path = "/api/operate/namespaces/{namespace}/sites/{site}/vpm/debug/global/list-service"
    operation = openapi.get("paths", {}).get(path, {}).get("get", {})
    if (
        operation.get("operationId")
        != "ves.io.schema.operate.debug.CustomPublicAPI.ListVolterraServices"
    ):
        raise ValueError("HA candidate service inventory operation is unsupported")
    parameters = operation.get("parameters", [])
    expected = {
        ("namespace", "path"): (True, "string"),
        ("site", "path"): (True, "string"),
    }
    actual = {
        (item.get("name"), item.get("in")): (
            item.get("required", False),
            item.get("schema", {}).get("type"),
        )
        for item in parameters
    }
    if actual != expected or len(parameters) != len(expected):
        raise ValueError("HA candidate service inventory parameters are unsupported")
    response = (
        operation.get("responses", {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    if response.get("$ref") != "#/components/schemas/debugListServicesResponse":
        raise ValueError("HA candidate service inventory response is unsupported")
    service = schemas.get("debugListServicesResponse", {}).get("properties", {}).get("service", {})
    if (
        service.get("type") != "array"
        or service.get("items", {}).get("$ref") != "#/components/schemas/debugService"
    ):
        raise ValueError("HA candidate service inventory row schema is unsupported")
    properties = schemas.get("debugService", {}).get("properties", {})
    for field in ("node", "name", "value"):
        if properties.get(field, {}).get("type") != "string":
            raise ValueError("HA candidate service inventory identity field is unsupported")
