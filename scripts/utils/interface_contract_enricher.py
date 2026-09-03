"""Emit and validate evidence-backed Secure Mesh interface contracts."""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from .extension_constants import X_F5XC_CE_AUTOMATION_CONTRACT

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "interface_contracts.yaml"
SUPPORTED_ROLES = frozenset({"slo", "external", "sli"})
CONFIG_VERSION = "6.0.0"
CONTRACT_VERSION = "6.0.0"
CONTRACT_ID = "f5xc-ce-automation/v3"
CAPABILITY_STATES = frozenset({"available", "unavailable"})
STABLE_IDENTITY_FIELDS = frozenset(
    {
        "node_hostname",
        "cloud_nic_position",
        "nic_mac",
        "ip_configuration",
        "subnet",
        "control_plane_interface_reference",
    }
)
AWS_TELEMETRY_SCHEMA_ID = "f5xc-smsv2-aws-tgw-telemetry/v2"
AWS_REQUIRED_TELEMETRY_FACTS = frozenset(
    {"runtime", "gre", "bgp", "mtu", "route", "bgp_inside_cidr_block"}
)
AWS_V3_CAPABILITIES = {
    "aws_ce_create": "available",
    "runtime_status": "available",
    "tgw_connect": "available",
}
AWS_SUCCESS_OPERATIONS = ["create", "read", "replace", "delete"]
AWS_SUCCESS_TOPOLOGY = {"nodes": 3, "interfaces": 6, "bgp_peers": 6}
AWS_SUCCESS_FACTS = [
    "mac_bound_configuration",
    "nullable_public_ip",
    "runtime_health",
    "tgw_connect",
    "established_bgp_peers",
    "detailed_and_simplified_route_agreement",
    "zero_action_post_apply_plan",
]
AWS_V3_INTERFACE_IDENTITY = {
    "fields": ["node", "ethernet_interface.mac"],
    "node": {
        "input_field": "node",
        "configuration_path": "spec.aws.not_managed.node_list[].hostname",
        "nullable": False,
        "normalization": "trim",
    },
    "mac": {
        "input_field": "mac",
        "configuration_path": (
            "spec.aws.not_managed.node_list[].interface_list[].ethernet_interface.mac"
        ),
        "nullable": False,
        "normalization": "ieee802_lowercase_colon",
    },
    "uniqueness_scope": "node",
    "guest_device": "rejected",
    "known_value_policy": "reject_null_incomplete_malformed_ambiguous_or_inconsistent",
    "unknown_value_policy": "defer",
}
AWS_V3_ROLES = [
    {"name": "slo", "network_option": "site_local_network"},
    {"name": "sli", "network_option": "site_local_inside_network"},
]
AWS_V3_RUNTIME = {
    "configuration": {
        "method": "GET",
        "path": "/api/config/namespaces/{namespace}/securemesh_site_v2s/{site}",
        "operation_id": "ves.io.schema.views.securemesh_site_v2.API.Get",
        "response_schema": "securemesh_site_v2GetResponse",
        "authority": "f5xc",
        "semantics": "configuration",
        "response_mappings": {
            "nodes": "spec.aws.not_managed.node_list[]",
            "node": "hostname",
            "interfaces": "interface_list[]",
            "mac": "ethernet_interface.mac",
            "role": "network_option",
            "mtu": "mtu",
            "public_ip": "public_ip",
        },
        "nullability": {"public_ip": "nullable", "all_identity_fields": "non_null"},
        "normalization": {
            "node": "trim",
            "mac": "ieee802_lowercase_colon",
            "role": "slo_or_sli",
        },
        "correlation": ["node", "normalized_mac"],
    },
    "health": {
        "method": "GET",
        "path": "/api/operate/namespaces/system/sites/{site}/vpm/debug/global/health",
        "operation_id": "ves.io.schema.operate.debug.CustomPublicAPI.HealthPublic",
        "response_schema": "debugHealthResponse",
        "authority": "f5xc",
        "semantics": "observational_read_only",
        "response_mappings": {"node": "hostname", "health": "state"},
        "normalization": {"node": "configured_hostname_or_fqdn"},
        "correlation": ["canonical_node"],
    },
    "bgp_peers": {
        "method": "GET",
        "path": "/api/operate/namespaces/{namespace}/sites/{site}/ver/bgp_peers",
        "operation_id": "ves.io.schema.operate.bgp.CustomPublicAPI.ShowBGPPeers",
        "response_schema": "bgpBGPPeersResponse",
        "authority": "f5xc",
        "semantics": "observational_read_only",
        "response_mappings": {
            "nodes": "ver[]",
            "node": "ver[].name",
            "peers": "ver[].peer[]",
            "interface_name": "ver[].peer[].interface_name",
            "peer_address": {
                "ipv4": "ver[].peer[].peer_address.ipv4.addr",
                "ipv6": "ver[].peer[].peer_address.ipv6.addr",
            },
            "state": "ver[].peer[].protocol_status",
            "received_prefix_count": "ver[].peer[].received_prefix_count",
            "advertised_prefix_count": "ver[].peer[].advertised_prefix_count",
            "state_changed_at": "ver[].peer[].up_down_timestamp",
        },
        "normalization": {"node": "configured_hostname_or_fqdn"},
        "correlation": ["canonical_node", "peer_address"],
    },
    "bgp_routes": {
        "method": "GET",
        "path": "/api/operate/namespaces/{namespace}/sites/{site}/ver/bgp_routes",
        "operation_id": "ves.io.schema.operate.bgp.CustomPublicAPI.ShowBGPRoutes",
        "response_schema": "bgpBGPRoutesResponse",
        "authority": "f5xc",
        "semantics": "observational_read_only",
        "response_mappings": {
            "nodes": "ver[]",
            "node": "ver[].name",
            "routing_instances": "ver[].ri_table[]",
            "route_tables": "ver[].ri_table[].rt_table[]",
            "imported_routes": "ver[].ri_table[].rt_table[].imported[]",
            "exported_routes": "ver[].ri_table[].rt_table[].exported[]",
            "route_prefixes": [
                "ver[].ri_table[].rt_table[].imported[].subnet",
                "ver[].ri_table[].rt_table[].exported[].subnet",
            ],
        },
        "normalization": {"node": "configured_hostname_or_fqdn"},
        "correlation": ["canonical_node"],
    },
    "simplified_routes": {
        "method": "POST",
        "path": "/api/operate/namespaces/{namespace}/sites/{site}/ver/simplified_routes",
        "operation_id": "ves.io.schema.operate.route.CustomPublicAPI.ShowSimplifiedRoutes",
        "request_schema": "routeSimplifiedRouteRequest",
        "response_schema": "routeSimplifiedRouteResponse",
        "authority": "f5xc",
        "semantics": "observational_read_only",
        "request_mappings": {"node_scope": "all_nodes", "roles": ["slo", "sli"]},
        "response_mappings": {
            "nodes": "ver_routes[]",
            "node": "ver_routes[].node",
            "routes": "ver_routes[].route[]",
        },
        "normalization": {"node": "configured_hostname_or_fqdn"},
        "correlation": ["canonical_node", "role"],
        "convergence": {
            "expected_aws_prefixes": "present_in_detailed_bgp_routes",
            "exported_bgp_prefixes": "present_in_selected_role_simplified_routes",
        },
    },
}
AWS_V3_AUTHORITIES = {
    "f5xc": [
        "smsv2_configuration",
        "runtime_health",
        "bgp_peers",
        "bgp_routes",
        "simplified_routes",
    ],
    "aws": [
        "eni",
        "transit_gateway",
        "transit_gateway_connect",
        "gre_endpoints",
        "bgp_inside_cidrs",
        "autonomous_system_numbers",
    ],
}


class InterfaceContractValidationError(ValueError):
    """Raised when an interface contract would be unsafe to publish."""


def validate_aws_telemetry_intake(intake: object) -> bool:
    """Validate the fail-closed AWS TGW observation boundary.

    Returns the validated completion state so configured contracts and immutable
    release assets use exactly the same decision.
    """
    if not isinstance(intake, dict):
        raise InterfaceContractValidationError("AWS telemetry intake must be an object")
    if intake.get("schema_id") != AWS_TELEMETRY_SCHEMA_ID:
        raise InterfaceContractValidationError("AWS telemetry intake schema identity is invalid")
    availability = intake.get("availability")
    complete = intake.get("complete")
    if availability not in CAPABILITY_STATES or not isinstance(complete, bool):
        raise InterfaceContractValidationError("AWS telemetry intake state is malformed")

    def fact_set(field: str) -> set[str]:
        value = intake.get(field)
        if (
            not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))
        ):
            raise InterfaceContractValidationError(
                f"AWS telemetry intake {field} must be a unique string list"
            )
        return set(value)

    required = fact_set("required_facts")
    observed = fact_set("observed_facts")
    unavailable = fact_set("unavailable_facts")
    if not AWS_REQUIRED_TELEMETRY_FACTS.issubset(required):
        raise InterfaceContractValidationError("AWS telemetry intake lacks required observations")
    if observed & unavailable:
        raise InterfaceContractValidationError(
            "AWS telemetry observed and unavailable facts must be disjoint"
        )
    derived_complete = (
        availability == "available" and required.issubset(observed) and not unavailable
    )
    if complete != derived_complete:
        raise InterfaceContractValidationError("AWS telemetry intake completion is inconsistent")
    return complete


def validate_aws_v3_contract(profile: object) -> None:
    """Validate the exact clean-break MAC-bound AWS SMSv2 contract."""
    if not isinstance(profile, dict):
        raise InterfaceContractValidationError("AWS v3 profile must be an object")
    if profile.get("interface_identity") != AWS_V3_INTERFACE_IDENTITY:
        raise InterfaceContractValidationError("AWS interface identity must be node/MAC-bound")
    if profile.get("roles") != AWS_V3_ROLES:
        raise InterfaceContractValidationError("AWS runtime roles must be exactly slo and sli")
    if profile.get("runtime") != AWS_V3_RUNTIME:
        raise InterfaceContractValidationError(
            "AWS runtime endpoints or schemas are incomplete or legacy"
        )
    if profile.get("authorities") != AWS_V3_AUTHORITIES:
        raise InterfaceContractValidationError("AWS and F5 authority declarations are invalid")


@dataclass
class InterfaceContractStats:
    """Track schema-level interface-contract enrichment."""

    schemas_processed: int = 0
    schemas_matched: int = 0
    contracts_applied: int = 0

    def to_dict(self) -> dict[str, int]:
        """Return stable serializable statistics."""
        return asdict(self)


class InterfaceContractEnricher:
    """Inject the validated Secure Mesh Site v2 automation contract.

    The contract intentionally describes cloud and control-plane identities,
    never Linux guest interface names. An optional role can only become bindable
    when the contract revision records authoritative evidence provenance.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Load and validate the configured schema-level contracts."""
        self.config_path = Path(config_path) if config_path else CONFIG_PATH
        self.contracts = self._load_contracts()
        self.stats = InterfaceContractStats()

    def _load_contracts(self) -> list[tuple[tuple[re.Pattern[str], ...], dict[str, Any]]]:
        if not self.config_path.exists():
            raise InterfaceContractValidationError(
                f"interface contract configuration is missing: {self.config_path}"
            )
        with self.config_path.open() as config_file:
            config = yaml.safe_load(config_file) or {}

        if config.get("version") != CONFIG_VERSION:
            raise InterfaceContractValidationError(
                "interface contract configuration has an unsupported version"
            )

        contracts = config.get("contracts")
        if not isinstance(contracts, dict) or not contracts:
            raise InterfaceContractValidationError(
                "interface contract configuration has no contracts"
            )

        loaded: list[tuple[tuple[re.Pattern[str], ...], dict[str, Any]]] = []
        for resource, entry in sorted(contracts.items()):
            if not isinstance(entry, dict):
                raise InterfaceContractValidationError(
                    f"{resource}: contract entry must be an object"
                )
            patterns = entry.get("schema_patterns")
            contract = entry.get("contract")
            if (
                not isinstance(patterns, list)
                or not patterns
                or not all(isinstance(pattern, str) for pattern in patterns)
            ):
                raise InterfaceContractValidationError(
                    f"{resource}: schema_patterns must be a non-empty string list"
                )
            if not isinstance(contract, dict):
                raise InterfaceContractValidationError(f"{resource}: contract must be an object")
            self._validate_contract(resource, contract)
            try:
                compiled = tuple(re.compile(pattern) for pattern in patterns)
            except re.error as error:
                raise InterfaceContractValidationError(
                    f"{resource}: invalid schema pattern: {error}"
                ) from error
            loaded.append((compiled, copy.deepcopy(contract)))
        return loaded

    @staticmethod
    def _required_object(contract: dict[str, Any], field: str, *, resource: str) -> dict[str, Any]:
        value = contract.get(field)
        if not isinstance(value, dict):
            raise InterfaceContractValidationError(f"{resource}: {field} must be an object")
        return value

    def _validate_contract(self, resource: str, contract: dict[str, Any]) -> None:
        if contract.get("version") != CONTRACT_VERSION:
            raise InterfaceContractValidationError(f"{resource}: unsupported contract version")
        if contract.get("resource") != resource:
            raise InterfaceContractValidationError(
                f"{resource}: resource identity must match its key"
            )
        if contract.get("contract_id") != CONTRACT_ID:
            raise InterfaceContractValidationError(f"{resource}: invalid contract identity")
        api = self._required_object(contract, "api", resource=resource)
        required_api = {
            "collection_path": "/api/config/namespaces/{namespace}/securemesh_site_v2s",
            "item_path": "/api/config/namespaces/{namespace}/securemesh_site_v2s/{name}",
            "namespace": "system",
        }
        if any(api.get(field) != value for field, value in required_api.items()):
            raise InterfaceContractValidationError(f"{resource}: API paths must match SMSv2")
        operations = api.get("operations")
        required_operations = {"create", "read", "replace", "delete"}
        if (
            not isinstance(operations, list)
            or not required_operations.issubset(operations)
            or any(not isinstance(operation, str) or not operation for operation in operations)
        ):
            raise InterfaceContractValidationError(
                f"{resource}: CRUD operations must be demonstrated"
            )
        providers = self._required_object(contract, "providers", resource=resource)
        if not {"aws", "azure"}.issubset(providers):
            raise InterfaceContractValidationError(
                f"{resource}: providers must include aws and azure"
            )
        azure = providers["azure"]
        aws = providers["aws"]
        if not isinstance(azure, dict) or not isinstance(aws, dict):
            raise InterfaceContractValidationError(f"{resource}: provider profiles must be objects")
        self._validate_aws_profile(resource, aws)
        self._validate_interface_profile(resource, azure)

    def _validate_aws_profile(self, resource: str, profile: dict[str, Any]) -> None:
        if profile.get("node_list_path") != "aws.not_managed.node_list[]":
            raise InterfaceContractValidationError(
                f"{resource}: AWS node path is not schema-backed"
            )
        if profile.get("interface_list_path") != "aws.not_managed.node_list[].interface_list[]":
            raise InterfaceContractValidationError(
                f"{resource}: AWS interface path is not schema-backed"
            )
        availability = profile.get("availability")
        if availability not in {"schema_only", "evidence_backed"}:
            raise InterfaceContractValidationError(f"{resource}: AWS availability is invalid")

        capabilities = self._required_object(profile, "capabilities", resource=resource)
        telemetry_intake = profile.get("telemetry_intake")
        if not isinstance(telemetry_intake, dict):
            raise InterfaceContractValidationError(
                f"{resource}: AWS telemetry intake must be an object"
            )
        telemetry_complete = validate_aws_telemetry_intake(telemetry_intake)
        validate_aws_v3_contract(profile)
        if availability == "schema_only":
            if not capabilities or set(capabilities.values()) != {"unavailable"}:
                raise InterfaceContractValidationError(
                    f"{resource}: schema-only AWS capabilities must fail closed"
                )
            if telemetry_intake.get("availability") != "unavailable" or telemetry_complete:
                raise InterfaceContractValidationError(
                    f"{resource}: schema-only AWS telemetry must fail closed"
                )
        elif capabilities != AWS_V3_CAPABILITIES:
            raise InterfaceContractValidationError(
                f"{resource}: AWS v3 capability model is incomplete or unavailable"
            )
        elif not telemetry_complete:
            raise InterfaceContractValidationError(
                f"{resource}: AWS v3 requires completed telemetry intake"
            )

        if profile.get("prohibited_legacy_apis") != ["aws_vpc_site", "aws_tgw_site"]:
            raise InterfaceContractValidationError(
                f"{resource}: legacy AWS site APIs must remain prohibited"
            )

        unsupported = profile.get("unavailable_capabilities")
        unavailable = {name for name, state in capabilities.items() if state == "unavailable"}
        if (
            not isinstance(unsupported, list)
            or any(not isinstance(capability, str) or not capability for capability in unsupported)
            or len(unsupported) != len(set(unsupported))
            or set(unsupported) != unavailable
        ):
            raise InterfaceContractValidationError(
                f"{resource}: AWS unsupported capabilities must fail closed"
            )

        bootstrap = self._required_object(profile, "bootstrap", resource=resource)
        required_bootstrap = {
            "mode": "interactive_console_only",
            "reference": "session_bound_opaque_one_use",
            "headless_checkout": "unavailable",
        }
        if any(bootstrap.get(field) != value for field, value in required_bootstrap.items()):
            raise InterfaceContractValidationError(
                f"{resource}: AWS bootstrap must remain console-only"
            )
        evidence = self._required_object(profile, "evidence", resource=resource)
        if not isinstance(evidence.get("provenance"), str) or not evidence["provenance"]:
            raise InterfaceContractValidationError(
                f"{resource}: AWS evidence provenance is required"
            )
        if not isinstance(evidence.get("recorded_at"), str) or not evidence["recorded_at"].endswith(
            "Z"
        ):
            raise InterfaceContractValidationError(
                f"{resource}: AWS evidence record timestamp is required"
            )
        if evidence.get("profiles") != ["aws-shaped-ce-configuration"]:
            raise InterfaceContractValidationError(
                f"{resource}: AWS evidence profile is incomplete"
            )
        receipts = evidence.get("receipts")
        if (
            not isinstance(receipts, list)
            or len(receipts) != 1
            or not isinstance(receipts[0], dict)
        ):
            raise InterfaceContractValidationError(f"{resource}: AWS evidence receipt is required")
        receipt = receipts[0]
        if availability == "evidence_backed":
            expected_receipt_fields = {
                "operations",
                "result",
                "topology",
                "validated_facts",
                "sanitized",
                "redaction",
            }
            receipt_is_valid = (
                receipt.get("operations") == AWS_SUCCESS_OPERATIONS
                and receipt.get("result") == "accepted"
                and receipt.get("topology") == AWS_SUCCESS_TOPOLOGY
                and receipt.get("validated_facts") == AWS_SUCCESS_FACTS
            )
        else:
            expected_receipt_fields = {
                "operations",
                "result",
                "blocking_conditions",
                "sanitized",
                "redaction",
            }
            receipt_is_valid = (
                receipt.get("operations") == ["replace"]
                and receipt.get("result") == "rejected"
                and receipt.get("blocking_conditions")
                == [
                    "mac_only_interface_rejected_by_live_api",
                    "public_ip_empty_string_null_round_trip",
                ]
            )
        if set(receipt) != expected_receipt_fields:
            raise InterfaceContractValidationError(
                f"{resource}: AWS evidence receipt contains unsupported fields"
            )
        if (
            not receipt_is_valid
            or receipt.get("sanitized") is not True
            or not isinstance(receipt.get("redaction"), str)
        ):
            raise InterfaceContractValidationError(f"{resource}: AWS evidence receipt is invalid")

    def _validate_interface_profile(self, resource: str, contract: dict[str, Any]) -> None:
        if contract.get("availability") != "evidence_backed":
            raise InterfaceContractValidationError(
                f"{resource}: Azure availability must be evidence_backed"
            )

        stable_identity = self._required_object(contract, "stable_identity", resource=resource)
        fields = stable_identity.get("required_fields")
        if not isinstance(fields, list) or set(fields) != STABLE_IDENTITY_FIELDS:
            raise InterfaceContractValidationError(
                f"{resource}: stable_identity.required_fields is incomplete"
            )
        if len(fields) != len(set(fields)):
            raise InterfaceContractValidationError(
                f"{resource}: stable_identity.required_fields contains duplicates"
            )

        roles = contract.get("roles")
        if not isinstance(roles, list) or not roles:
            raise InterfaceContractValidationError(f"{resource}: roles must be a non-empty list")
        names: list[str] = []
        runtime_evidence = self._required_object(contract, "runtime_evidence", resource=resource)
        for role in roles:
            if not isinstance(role, dict):
                raise InterfaceContractValidationError(f"{resource}: role must be an object")
            name = role.get("name")
            if name not in SUPPORTED_ROLES:
                raise InterfaceContractValidationError(f"{resource}: unknown role {name!r}")
            names.append(name)
            if not isinstance(role.get("required"), bool) or not isinstance(
                role.get("bindable"), bool
            ):
                raise InterfaceContractValidationError(
                    f"{resource}: role {name} must declare required and bindable booleans"
                )
            identity_fields = role.get("identity_fields")
            if (
                not isinstance(identity_fields, list)
                or set(identity_fields) != STABLE_IDENTITY_FIELDS
            ):
                raise InterfaceContractValidationError(
                    f"{resource}: role {name} lacks the complete stable identity"
                )
            if len(identity_fields) != len(set(identity_fields)):
                raise InterfaceContractValidationError(
                    f"{resource}: role {name} repeats a stable identity field"
                )
            for field in ("network_option", "vrf", "configuration_path"):
                if field not in role:
                    raise InterfaceContractValidationError(f"{resource}: role {name} lacks {field}")
            if name == "slo":
                if not role["required"] or not role["bindable"]:
                    raise InterfaceContractValidationError(
                        f"{resource}: slo must be required and bindable"
                    )
                if role["network_option"] != "site_local_network":
                    raise InterfaceContractValidationError(
                        f"{resource}: slo must use site_local_network"
                    )
                if role["vrf"] != "site_local_outside" or not isinstance(
                    role["configuration_path"], str
                ):
                    raise InterfaceContractValidationError(
                        f"{resource}: slo must declare its VRF and configuration path"
                    )
            elif role["required"]:
                raise InterfaceContractValidationError(f"{resource}: {name} must remain optional")
            elif role["bindable"]:
                required_mapping_fields = ("network_option", "vrf", "configuration_path")
                if not all(
                    isinstance(role[field], str) and role[field]
                    for field in required_mapping_fields
                ):
                    raise InterfaceContractValidationError(
                        f"{resource}: bindable {name} requires network option, VRF, and path"
                    )
                if runtime_evidence.get(
                    "minimum_mapping_confidence"
                ) != "authoritative" or not runtime_evidence.get("provenance_required"):
                    raise InterfaceContractValidationError(
                        f"{resource}: bindable {name} requires authoritative evidence provenance"
                    )
            elif any(
                role[field] is not None for field in ("network_option", "vrf", "configuration_path")
            ):
                raise InterfaceContractValidationError(
                    f"{resource}: unbindable {name} must not declare a configuration mapping"
                )

        if len(names) != len(set(names)):
            raise InterfaceContractValidationError(f"{resource}: duplicate role")
        if set(names) != SUPPORTED_ROLES:
            raise InterfaceContractValidationError(
                f"{resource}: roles must define slo, external, and sli"
            )

        invariants = self._required_object(contract, "invariants", resource=resource)
        for invariant in (
            "first_azure_nic_is_slo_anchor",
            "macs_unique_per_node",
            "role_bindings_unique_per_node",
            "homogeneous_ha_role_vrf_shape",
        ):
            if invariants.get(invariant) is not True:
                raise InterfaceContractValidationError(
                    f"{resource}: invariant {invariant} is required"
                )
        if invariants.get("bgp_bindable_roles") != ["slo"]:
            raise InterfaceContractValidationError(f"{resource}: BGP may bind only to slo")

        if runtime_evidence.get("guest_interface_names") != "observational_only":
            raise InterfaceContractValidationError(
                f"{resource}: guest interface names must be observational only"
            )
        if runtime_evidence.get("minimum_mapping_confidence") != "authoritative":
            raise InterfaceContractValidationError(
                f"{resource}: authoritative mapping confidence is required"
            )
        if (
            runtime_evidence.get("provenance_required") is not True
            or runtime_evidence.get("replacement_observation_required") is not True
        ):
            raise InterfaceContractValidationError(f"{resource}: evidence provenance is required")

        change_risk = self._required_object(contract, "change_risk", resource=resource)
        for field in (
            "maintenance_window_required",
            "restarts_ce_data_plane_services",
            "adding_or_removing_interfaces_disruptive",
        ):
            if change_risk.get(field) is not True:
                raise InterfaceContractValidationError(
                    f"{resource}: change-risk field {field} is required"
                )

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Inject every matching interface contract at schema level."""
        schemas = spec.get("components", {}).get("schemas", {})
        if not isinstance(schemas, dict):
            return spec
        for schema_name, schema in schemas.items():
            if not isinstance(schema, dict):
                continue
            self.stats.schemas_processed += 1
            for patterns, contract in self.contracts:
                if any(pattern.search(schema_name) for pattern in patterns):
                    schema[X_F5XC_CE_AUTOMATION_CONTRACT] = copy.deepcopy(contract)
                    self.stats.schemas_matched += 1
                    self.stats.contracts_applied += 1
                    break
        return spec

    def get_stats(self) -> dict[str, int]:
        """Return enrichment statistics."""
        return self.stats.to_dict()

    def reset_stats(self) -> None:
        """Reset per-spec statistics after pipeline accounting."""
        self.stats = InterfaceContractStats()


__all__ = [
    "InterfaceContractEnricher",
    "InterfaceContractStats",
    "InterfaceContractValidationError",
]
