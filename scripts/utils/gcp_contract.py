"""GCP schema boundary; schema presence never grants runtime execution."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, TypeGuard

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
    _validate_gcp_ha_diagnosis(openapi, schemas)


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


def _validate_gcp_ha_diagnosis(openapi: dict[str, Any], schemas: dict[str, Any]) -> None:
    """Validate diagnostic availability only; its untyped maps cannot qualify service health."""
    path = "/api/operate/namespaces/system/sites/{site}/vpm/debug/global/diagnosis"
    operation = openapi.get("paths", {}).get(path, {}).get("get", {})
    if (
        operation.get("operationId")
        != "ves.io.schema.operate.debug.CustomPublicAPI.DiagnosisPublic"
    ):
        raise ValueError("HA candidate diagnosis operation is unsupported")
    parameters = operation.get("parameters", [])
    expected = {
        ("site", "path"): (True, "string"),
        ("console_user", "query"): (False, "string"),
    }
    actual = {
        (item.get("name"), item.get("in")): (
            item.get("required", False),
            item.get("schema", {}).get("type"),
        )
        for item in parameters
    }
    if actual != expected or len(parameters) != len(expected):
        raise ValueError("HA candidate diagnosis parameters are unsupported")
    response = (
        operation.get("responses", {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    if response.get("$ref") != "#/components/schemas/debugDiagnosisResponse":
        raise ValueError("HA candidate diagnosis response is unsupported")
    properties = schemas.get("debugDiagnosisResponse", {}).get("properties", {})
    for field in ("responses", "curl_response", "registration_status"):
        if properties.get(field, {}).get("type") != "object":
            raise ValueError("HA candidate diagnosis map is unsupported")


def validate_gcp_kubernetes_candidate_openapi(openapi: dict[str, Any]) -> None:
    """Validate site proxy wire shapes; no completeness or core health admission is implied."""
    schemas = openapi.get("components", {}).get("schemas", {})
    base = "/api/data/namespaces/system/site/{site}"
    operations = (
        ("/api/v1/nodes", "NodeList", "v1NodeList", "corev1Node", False),
        ("/api/v1/namespaces", "NamespaceList", "v1NamespaceList", "v1Namespace", False),
        ("/api/v1/pods", "PodList_get_1", "v1PodList", "v1Pod", True),
        ("/apis/apps/v1/daemonsets", "DaemonSetList_get", "v1DaemonSetList", "v1DaemonSet", True),
    )
    for suffix, name, list_schema, row_schema, namespace in operations:
        operation = openapi.get("paths", {}).get(base + suffix, {}).get("get", {})
        if operation.get("operationId") != f"ves.io.schema.site.CustomDataK8SAPI.{name}":
            raise ValueError("Kubernetes candidate operation is unsupported")
        expected = {("site", "path"): (True, "string")}
        if namespace:
            expected[("namespace", "query")] = (False, "string")
        parameters = operation.get("parameters", [])
        actual = {
            (item.get("name"), item.get("in")): (
                item.get("required", False),
                item.get("schema", {}).get("type"),
            )
            for item in parameters
        }
        if actual != expected or len(parameters) != len(expected):
            raise ValueError("Kubernetes candidate parameters are unsupported")
        response = (
            operation.get("responses", {})
            .get("200", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        if response.get("$ref") != f"#/components/schemas/{list_schema}":
            raise ValueError("Kubernetes candidate response is unsupported")
        properties = schemas.get(list_schema, {}).get("properties", {})
        items = properties.get("items", {})
        if (
            items.get("type") != "array"
            or items.get("items", {}).get("$ref") != f"#/components/schemas/{row_schema}"
        ):
            raise ValueError("Kubernetes candidate list items are unsupported")
        _validate_gcp_kubernetes_reference(properties.get("metadata", {}), "v1ListMeta")
        _validate_gcp_kubernetes_reference(
            schemas.get(row_schema, {}).get("properties", {}).get("metadata", {}), "v1ObjectMeta"
        )
    for schema, fields in (
        ("v1ObjectMeta", ("name", "namespace", "uid", "resourceVersion")),
        ("v1ListMeta", ("continue", "resourceVersion", "remainingItemCount")),
        ("v1NodeSpec", ("providerID",)),
        ("v1PodSpec", ("nodeName",)),
    ):
        properties = schemas.get(schema, {}).get("properties", {})
        if any(properties.get(field, {}).get("type") != "string" for field in fields):
            raise ValueError("Kubernetes candidate identity/list fields are unsupported")
    for row, spec in (("corev1Node", "v1NodeSpec"), ("v1Pod", "v1PodSpec")):
        _validate_gcp_kubernetes_reference(
            schemas.get(row, {}).get("properties", {}).get("spec", {}), spec
        )


def _validate_gcp_kubernetes_reference(value: dict[str, Any], schema: str) -> None:
    """Require the owning direct or allOf schema reference without inferring a mapping."""
    references = [value.get("$ref")] + [item.get("$ref") for item in value.get("allOf", [])]
    if f"#/components/schemas/{schema}" not in references:
        raise ValueError("Kubernetes candidate schema reference is unsupported")


GCP_OPERATIONAL_CORE_SEED = (
    "argo",
    "asaf",
    "aspen",
    "bdbewaf",
    "clank",
    "envoy",
    "etcd",
    "frr",
    "gubernator",
    "ike",
    "keepalived",
    "komodo",
    "obelix",
    "openvpn",
    "opera",
    "pmtud",
    "ver",
)
GCP_OPERATIONAL_RUNTIME_DEPENDENCIES = ("coredns", "kube-proxy", "kube-multus-ds")
GCP_OPERATIONAL_COMMANDS = {
    "list": ["crictl-ps-a"],
    "inspect": ["crictl-inspect", "current_original_node_container_id"],
    "diagnosis": ["diagnosis"],
}


def validate_gcp_operational_candidate_openapi(openapi: dict[str, Any]) -> None:
    """Validate the documented lower-privilege read-only transport without granting health acceptance."""
    path = "/api/operate/namespaces/{namespace}/sites/{site}/vpm/debug/{node}/exec-user"
    operation = openapi.get("paths", {}).get(path, {}).get("post", {})
    if operation.get("operationId") != "ves.io.schema.operate.debug.CustomPublicAPI.ExecUser":
        raise ValueError("Operational Site CLI operation is unsupported")
    parameters = operation.get("parameters", [])
    expected = {(name, "path"): (True, "string") for name in ("namespace", "site", "node")}
    actual = {
        (p.get("name"), p.get("in")): (p.get("required"), p.get("schema", {}).get("type"))
        for p in parameters
    }
    if actual != expected or len(parameters) != 3:
        raise ValueError("Operational Site CLI parameters are unsupported")
    request = operation.get("requestBody", {})
    if (
        request.get("required") is not True
        or request.get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")
        != "#/components/schemas/debugExecRequest"
    ):
        raise ValueError("Operational Site CLI request is unsupported")
    response = (
        operation.get("responses", {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    if response.get("$ref") != "#/components/schemas/debugExecResponse":
        raise ValueError("Operational Site CLI response is unsupported")
    schemas = openapi.get("components", {}).get("schemas", {})
    command = schemas.get("debugExecRequest", {}).get("properties", {}).get("command", {})
    if command.get("type") != "array" or command.get("items", {}).get("type") != "string":
        raise ValueError("Operational Site CLI command wire shape is unsupported")
    fields = schemas.get("debugExecResponse", {}).get("properties", {})
    if (
        fields.get("output", {}).get("type") != "string"
        or fields.get("return_code", {}).get("type") != "integer"
    ):
        raise ValueError("Operational Site CLI result wire shape is unsupported")


def validate_gcp_operational_profile(profile: Any, pinned_digest: str) -> None:
    """Validate frozen operational coverage; a matching pin does not grant live acceptance."""
    sha = re.compile(r"^sha256:[a-f0-9]{64}$")
    name = re.compile(r"^[a-z][a-z0-9-]{0,62}$")

    def names(value: Any) -> TypeGuard[list[str]]:
        return (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(v, str) and name.fullmatch(v) for v in value)
            and len(set(value)) == len(value)
        )

    if (
        not isinstance(profile, dict)
        or not isinstance(pinned_digest, str)
        or not sha.fullmatch(pinned_digest)
    ):
        raise ValueError("Operational profile or pin malformed")
    try:
        actual = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(profile, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest()
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Operational profile malformed") from error
    if actual != pinned_digest:
        raise ValueError("Operational profile fingerprint differs")
    if (
        profile.get("schemaVersion") != 2
        or profile.get("scope") != "original-three-node-operational-health"
    ):
        raise ValueError("Operational profile scope unsupported")
    if profile.get("maximumAgeMs") != 300000 or profile.get("minimumRoundIntervalMs") != 45000:
        raise ValueError("Operational observation bounds changed")
    for field in ("sourceHashes", "qualification"):
        values = profile.get(field)
        if (
            not isinstance(values, dict)
            or not values
            or any(not isinstance(v, str) or not sha.fullmatch(v) for v in values.values())
        ):
            raise ValueError("Operational qualification provenance incomplete")
    if set(profile["qualification"]) != {
        "evidenceSha256",
        "discoveryCleanupSha256",
    }:
        raise ValueError("Operational qualification receipts incomplete")
    versions = profile.get("parserVersions")
    if (
        not isinstance(versions, dict)
        or not versions
        or any(
            not isinstance(v, str) or not re.fullmatch(r"[a-z0-9/-]+/v[1-9][0-9]*", v)
            for v in versions.values()
        )
    ):
        raise ValueError("Operational parser versions incomplete")
    states = profile.get("verRunningStates")
    if (
        not isinstance(states, list)
        or not states
        or any(
            not isinstance(v, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_/-]{0,62}", v)
            for v in states
        )
        or len(set(states)) != len(states)
    ):
        raise ValueError("Operational VER states unqualified")
    services = profile.get("inventoryServices")
    if not names(services) or not set(GCP_OPERATIONAL_CORE_SEED).issubset(services):
        raise ValueError("Operational minimum coverage reduced")
    exclusions = profile.get("exclusions")
    auxiliaries = {"vector-agent", "voucher", "kube-state-metrics"}
    if (
        not isinstance(exclusions, list)
        or any(
            not isinstance(e, dict)
            or not isinstance(e.get("service"), str)
            or e["service"] not in auxiliaries
            or e.get("reason") != "auxiliary-outside-operational-scope"
            or not isinstance(e.get("evidenceSha256"), str)
            or not sha.fullmatch(e["evidenceSha256"])
            for e in exclusions
        )
        or len({e["service"] for e in exclusions}) != len(exclusions)
    ):
        raise ValueError("Operational exclusions unqualified")
    roles = profile.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise ValueError("Operational roles unavailable")
    covered: set[str] = set()
    mapped_all = set()
    for role_name, role in roles.items():
        if (
            not isinstance(role_name, str)
            or not name.fullmatch(role_name)
            or not isinstance(role, dict)
        ):
            raise ValueError("Operational role malformed")
        runtime_role = role.get("runtimeRole")
        if runtime_role is not None and (
            not isinstance(runtime_role, str) or not name.fullmatch(runtime_role)
        ):
            raise ValueError("Operational runtime role malformed")
        components = role.get("components")
        containers = role.get("containers")
        diagnostics = role.get("diagnostics")
        if (
            not names(components)
            or not set(components).issubset(services)
            or not isinstance(containers, list)
            or not containers
        ):
            raise ValueError("Operational role coverage incomplete")
        if not names(diagnostics) or not {"peer-connectivity", "required-domains"}.issubset(
            diagnostics
        ):
            raise ValueError("Operational required diagnostics missing")
        mapped = set()
        identities = set()
        for container in containers:
            if not isinstance(container, dict) or any(
                not isinstance(container.get(field), str) or not name.fullmatch(container[field])
                for field in ("service", "name", "namespace")
            ):
                raise ValueError("Operational runtime mapping malformed")
            prefix = container.get("podPrefix")
            shape = container.get("podShape")
            if (
                not isinstance(prefix, str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{0,251}", prefix)
                or not isinstance(shape, str)
                or shape not in {"daemon", "deployment", "stateful"}
            ):
                raise ValueError("Operational pod mapping unqualified")
            identity = (container["namespace"], container["name"], prefix, shape)
            if identity in identities:
                raise ValueError("Operational runtime mapping ambiguous")
            identities.add(identity)
            mapped.add(container["service"])
        if not set(components).issubset(mapped) or not set(
            GCP_OPERATIONAL_RUNTIME_DEPENDENCIES
        ).issubset(mapped):
            raise ValueError("Operational core runtime coverage missing")
        covered.update(components)
        covered.update(mapped)
        mapped_all.update(mapped)
    if not set(services).issubset(covered):
        raise ValueError("Operational required placement uncovered")
    if not auxiliaries.issubset(mapped_all | {e["service"] for e in exclusions}):
        raise ValueError("Operational auxiliary coverage missing")
    if "placementCounts" in profile:
        counts = profile["placementCounts"]
        required = set(services) | mapped_all
        if (
            not isinstance(counts, dict)
            or set(counts) != required
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 3
                for value in counts.values()
            )
            or any(
                not role.get("runtimeRole")
                or not set(GCP_OPERATIONAL_CORE_SEED).issubset(role["components"])
                for role in roles.values()
            )
            or any(
                counts.get(service) != 3
                for service in (*GCP_OPERATIONAL_CORE_SEED, *GCP_OPERATIONAL_RUNTIME_DEPENDENCIES)
            )
        ):
            raise ValueError("Operational placement counts or runtime roles incomplete")


def validate_gcp_operational_release(
    receipt: Any, pinned_digest: str, contract_digest: str
) -> None:
    """Check the separately pinned attestation for the unchanged campaign contract."""
    sha = re.compile(r"^sha256:[a-f0-9]{64}$")
    if (
        not isinstance(receipt, dict)
        or not isinstance(pinned_digest, str)
        or not sha.fullmatch(pinned_digest)
    ):
        raise ValueError("Operational release receipt malformed")
    actual = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )
    if (
        actual != pinned_digest
        or receipt.get("contractFingerprint") != contract_digest
        or not isinstance(contract_digest, str)
        or not sha.fullmatch(contract_digest)
    ):
        raise ValueError("Operational release fingerprint differs")
    if receipt.get("schemaVersion") != 2 or any(
        not isinstance(receipt.get(field), str) or not sha.fullmatch(receipt[field])
        for field in ("bindingFingerprint", "evidenceSha256", "campaignCleanupSha256")
    ):
        raise ValueError("Operational release evidence incomplete")
    rounds = receipt.get("healthyRoundFingerprints")
    if (
        not isinstance(rounds, list)
        or len(rounds) != 2
        or any(not isinstance(v, str) or not sha.fullmatch(v) for v in rounds)
        or rounds[0] == rounds[1]
    ):
        raise ValueError("Operational release rounds incomplete")
    if receipt.get("objectsAbsent") != 18 or any(
        receipt.get(field) is not True
        for field in ("emptyDestroy", "stableState", "repeatedResumeNoSubmission")
    ):
        raise ValueError("Operational release cleanup incomplete")
