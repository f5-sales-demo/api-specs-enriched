"""Describe qualified relay wire mappings without claiming runtime acceptance."""

from __future__ import annotations

from typing import Any

AZURE_SLO_BGP_RELAY: dict[str, Any] = {
    "availability": "schema_validated_runtime_unqualified",
    "topology": "ce_slo_to_frr_to_azure_route_server",
    "multihop_owner": "frr_route_server_peer_group",
    "ce_operation": {
        "method": "POST",
        "path": "/api/config/namespaces/{metadata.namespace}/bgps",
        "operation_id": "ves.io.schema.bgp.API.Create",
        "request_schema": "bgpCreateRequest",
    },
    "ce_mappings": {
        "network_type": "VIRTUAL_NETWORK_SITE_LOCAL",
        "site_reference": "owned_site_name_and_namespace",
        "local_asn": "owning_site_binding.asn",
        "local_address": "derive_from_observed_slo_interface",
        "peers": "ce_nodes_cross_two_owned_relays",
        "peer_asn": "relay_cloud_binding.asn",
        "peer_address": "relay_cloud_binding.observed_ipv4",
        "peer_interface": "authoritative_slo_interface_reference",
        "peer_port": 179,
        "ce_multihop_request_control": "absent",
    },
    "identity": {
        "ce_join": [
            "site_uid",
            "node",
            "normalized_mac",
            "device",
            "role",
            "physical_site_reference",
            "publisher",
        ],
        "ce_role": "slo",
        "relay_join": [
            "vm_resource_id",
            "nic_resource_id",
            "ip_configuration",
            "subnet_resource_id",
            "ownership_tags",
            "engine",
            "plan_sha256",
        ],
        "relay_address": "owned_cloud_nic_observed_ipv4",
        "route_server_join": [
            "virtual_hub_resource_id",
            "bgp_connection_resource_ids",
            "observed_service_addresses",
            "ownership_tags",
        ],
        "unknown_policy": "reject_missing_stale_ambiguous_partial_foreign_or_unresolved_bindings",
    },
    "execution_requirements": {
        "engines": ["native", "terraform"],
        "relay_count": 2,
        "ce_topologies": ["single_site", "independent_sites", "three_node_ha"],
        "image_and_software": "resolve_exact_versions_and_digests_before_execution",
        "ownership": "all_cloud_platform_and_relay_resources_bound_to_saved_plan",
        "ce_configuration": "ordinary_external_peers_on_authoritative_slo_only",
        "frr_configuration": "route_server_peer_group_multihop_with_explicit_vip_prefix_policy",
        "traffic": "qualify_forwarding_and_vip_translation_with_return_path",
        "lifecycle": [
            "create",
            "replace",
            "interrupt_resume",
            "wrong_engine_rejection",
            "failover",
            "restore",
            "coordinated_teardown",
        ],
        "cleanup": "exact_ids_tags_sites_interfaces_tokens_empty_state_final_no_change_and_sensitive_retirement",
    },
    "qualification": {
        "native_request_schema_validated": True,
        "terraform_provider_validated": True,
        "live_acceptance": False,
        "vendor_support": "not_independently_qualified",
        "topology_acceptance": {
            "single_site": False,
            "independent_sites": False,
            "three_node_ha": False,
        },
        "receipt_path": "config/evidence/azure-slo-bgp-relay-wire-20261023.json",
        "receipt_sha256": "sha256:1376c39fcbe83d15acf2e22bcf6b0eceb76680c2f4688232b3b11b4d8c847b23",
        "fresh_published_asset_qualification_required": True,
    },
}
