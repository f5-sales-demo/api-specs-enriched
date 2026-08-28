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
CONFIG_VERSION = "3.0.0"
CONTRACT_VERSION = "3.0.0"
CONTRACT_ID = "f5xc-ce-automation/v2"
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
AWS_TELEMETRY_SCHEMA_ID = "f5xc-smsv2-aws-tgw-telemetry/v1"
AWS_REQUIRED_TELEMETRY_FACTS = frozenset(
    {"runtime", "gre", "bgp", "mtu", "route", "bgp_inside_cidr_block"}
)


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
        if availability == "schema_only" and any(
            state != "unavailable" for state in capabilities.values()
        ):
            raise InterfaceContractValidationError(
                f"{resource}: schema-only AWS capabilities must fail closed"
            )
        required_capabilities = {
            "aws_ce_create": "available" if availability == "evidence_backed" else "unavailable",
        }
        if (
            any(capabilities.get(name) != state for name, state in required_capabilities.items())
            or not {"runtime_status", "tgw_connect"}.issubset(capabilities)
            or not set(capabilities.values()) <= CAPABILITY_STATES
            or any(
                state != "unavailable"
                for name, state in capabilities.items()
                if name not in set(required_capabilities) | {"runtime_status", "tgw_connect"}
            )
        ):
            raise InterfaceContractValidationError(
                f"{resource}: AWS capability model must fail closed"
            )

        telemetry_complete = validate_aws_telemetry_intake(profile.get("telemetry_intake"))
        runtime_available = capabilities["runtime_status"] == "available"
        tgw_available = capabilities["tgw_connect"] == "available"
        if runtime_available and not telemetry_complete:
            raise InterfaceContractValidationError(
                f"{resource}: AWS runtime availability requires completed telemetry intake"
            )
        if tgw_available and not (runtime_available and telemetry_complete):
            raise InterfaceContractValidationError(
                f"{resource}: AWS capability model must fail closed; TGW availability "
                "requires runtime and completed telemetry"
            )
        if profile.get("prohibited_legacy_apis") != ["aws_vpc_site", "aws_tgw_site"]:
            raise InterfaceContractValidationError(
                f"{resource}: legacy AWS site APIs must remain prohibited"
            )

        unsupported = profile.get("unavailable_capabilities")
        unavailable = {name for name, state in capabilities.items() if state == "unavailable"}
        if (
            not isinstance(unsupported, list)
            or not unsupported
            or any(not isinstance(capability, str) or not capability for capability in unsupported)
            or len(unsupported) != len(set(unsupported))
            or not unavailable.issubset(unsupported)
        ):
            raise InterfaceContractValidationError(
                f"{resource}: AWS unsupported capabilities must fail closed"
            )

        if availability == "schema_only":
            if set(capabilities.values()) != {"unavailable"}:
                raise InterfaceContractValidationError(
                    f"{resource}: schema-only AWS capabilities must fail closed"
                )
            return

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
        if not isinstance(evidence.get("observed_at"), str) or not evidence["observed_at"].endswith(
            "Z"
        ):
            raise InterfaceContractValidationError(
                f"{resource}: AWS evidence timestamp is required"
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
        expected_receipt_fields = {"operations", "sanitized", "redaction"}
        if set(receipt) != expected_receipt_fields:
            raise InterfaceContractValidationError(
                f"{resource}: AWS evidence receipt contains unsupported fields"
            )
        if (
            receipt.get("operations") != ["create", "read", "replace", "delete"]
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
