# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Versioned, fail-closed removal of approved legacy site API operations."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from scripts.utils.deprecated_operations import HTTP_METHODS

POLICY_PATH = Path("config/curation/v12.0.0.json")
NEUTRAL_EXAMPLE = ("e.g. Aws_vpc_site, azure_vnet_site", "for a supported view kind")
RETAINED_FAMILY_COUNTS = {
    "securemesh_site_v2": 6,
    "virtual_k8s": 6,
    "workload": 11,
    "container_registry": 5,
    "k8s_cluster": 15,
    "k8s_pod_security": 10,
}
CURATED_RESOURCE_NAMES = frozenset(
    {
        "voltstack_site",
        "views_voltstack_site",
        "securemesh_site",
        "aws_vpc_site",
        "azure_vnet_site",
        "gcp_vpc_site",
        "cloud_connect",
        "views_aws_vpc_site",
        "views_azure_vnet_site",
        "views_gcp_vpc_site",
    }
)


def _family(path: str, operation_id: str) -> str | None:
    identity = f"{path} {operation_id}"
    if "voltstack_site" in path or "voltstack_site" in operation_id:
        return "appstack-site"
    if "securemesh_site_v2" in identity:
        return None
    if re.search(r"securemesh_site(?!_v2)", identity):
        return "secure-mesh-v1"
    if re.search(r"aws_vpc_site|azure_vnet_site|gcp_vpc_site", identity):
        return "provider-cloud-site"
    if "cloud_connect" in path or ".cloud_connect." in operation_id:
        return "cloud-connect"
    return None


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    """Read and validate the complete, versioned exact-identity policy."""
    policy = json.loads(path.read_text())
    if policy.get("policyVersion") != "v12.0.0":
        raise ValueError(f"Unexpected curation policy version: {path}")
    navigation_values = policy.get("navigationValues", [])
    if (
        not isinstance(navigation_values, list)
        or any(not isinstance(value, str) for value in navigation_values)
        or len(navigation_values) != len(set(navigation_values))
    ):
        raise ValueError(f"Invalid curation navigation values: {path}")
    seen: set[tuple[str, str, str]] = set()
    for entry in policy["operations"]:
        identity = (entry["method"], entry["path"], entry["operationId"])
        if (
            identity in seen
            or entry["method"] not in HTTP_METHODS
            or entry["topic"] != _family(entry["path"], entry["operationId"])
            or entry["disposition"] not in {"remove", "retain"}
            or not entry["reason"]
        ):
            raise ValueError(f"Invalid curation policy identity: {identity}")
        seen.add(identity)
    return policy


def _operations(spec: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    found = {}
    for path, item in spec.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method in HTTP_METHODS and isinstance(operation, dict):
                found[(method, path, operation.get("operationId", ""))] = operation
    return found


def _reachable_components(spec: dict[str, Any], roots: list[Any]) -> set[tuple[str, str]]:
    """Follow local component refs, including cycles and non-schema intermediates."""
    components = spec.get("components", {})
    visited: set[tuple[str, str]] = set()

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/"):
                pieces = ref.split("/")
                if len(pieces) >= 4:
                    kind = pieces[2].replace("~1", "/").replace("~0", "~")
                    name = pieces[3].replace("~1", "/").replace("~0", "~")
                    key = (kind, name)
                    if key not in visited:
                        visited.add(key)
                        target = components.get(kind, {}).get(name)
                        if target is not None:
                            visit(target)
            for key, child in node.items():
                if key != "$ref":
                    visit(child)

    for root in roots:
        visit(root)
    return visited


def _reachable_schemas(spec: dict[str, Any], roots: list[Any]) -> set[str]:
    return {name for kind, name in _reachable_components(spec, roots) if kind == "schemas"}


def _scrub_surviving_text(node: Any, removed_ids: set[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _scrub_surviving_text(item, removed_ids)
    elif isinstance(node, dict):
        aliases = node.get("x-f5xc-operation-aliases")
        if isinstance(aliases, list):
            node["x-f5xc-operation-aliases"] = [
                alias for alias in aliases if alias not in removed_ids
            ]
            if not node["x-f5xc-operation-aliases"]:
                del node["x-f5xc-operation-aliases"]
        for key, value in list(node.items()):
            if isinstance(value, str) and key in {
                "description",
                "summary",
                "x-f5xc-description-short",
                "x-f5xc-description-medium",
            }:
                node[key] = value.replace(*NEUTRAL_EXAMPLE)
            elif isinstance(value, (dict, list)):
                _scrub_surviving_text(value, removed_ids)


def curate_metadata(node: Any) -> None:
    """Drop legacy resource inventory entries from retained publication metadata."""
    if isinstance(node, list):
        for value in node:
            curate_metadata(value)
    elif isinstance(node, dict):
        for key in ("x-f5xc-primary-resources", "x-f5xc-critical-resources"):
            values = node.get(key)
            if isinstance(values, list):
                node[key] = [
                    value
                    for value in values
                    if (value.get("name") if isinstance(value, dict) else value)
                    not in CURATED_RESOURCE_NAMES
                ]
        for value in node.values():
            if isinstance(value, (dict, list)):
                curate_metadata(value)


def curate_sidecar_resources(artifact: dict[str, Any]) -> dict[str, Any]:
    """Remove curated resource keys from generated resource-map sidecars."""

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for name in CURATED_RESOURCE_NAMES:
                node.pop(name, None)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            node[:] = [
                value
                for value in node
                if (not isinstance(value, str) or value not in CURATED_RESOURCE_NAMES)
            ]
            for value in node:
                visit(value)

    visit(artifact)
    coverage = artifact.get("_coverage")
    if isinstance(coverage, dict) and isinstance(artifact.get("resources"), dict):
        resources = artifact["resources"]
        coverage["total_explicit"] = len(resources)
        for status in ("verified", "assumed"):
            coverage[status] = sum(
                value.get("_meta", {}).get("verification") == status for value in resources.values()
            )
        coverage["unverified"] = (
            coverage["total_explicit"] - coverage["verified"] - coverage["assumed"]
        )
        worklist = [
            name
            for name, value in resources.items()
            if value.get("_meta", {}).get("method") == "default_deny"
        ]
        coverage["default_deny_worklist"] = sorted(worklist)
        coverage["default_deny_worklist_count"] = len(worklist)
    resource_coverage = artifact.get("coverage")
    if isinstance(resource_coverage, dict) and isinstance(artifact.get("resources"), dict):
        resources = artifact["resources"]
        for disposition in ("generated", "manual", "excluded"):
            resource_coverage[disposition] = sum(
                value.get("disposition") == disposition for value in resources.values()
            )
        resource_coverage["total"] = len(resources)
    return artifact


def curate_catalog_metadata(catalog: dict[str, Any]) -> dict[str, Any]:
    """Remove retired console resource hints and stale examples from the catalog."""
    curate_sidecar_resources(catalog)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str):
                    node[key] = value.replace(*NEUTRAL_EXAMPLE)
                else:
                    visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(catalog)
    return catalog


def curate_spec(
    spec: dict[str, Any],
    policy: dict[str, Any] | None = None,
    audit_path: Path | None = None,
    protect_smsv2: bool = False,
    protect_retained_families: bool = False,
) -> dict[str, Any]:
    """Curate in place, writing a sorted audit even when drift stops the build."""
    policy = policy or load_policy()
    entries = policy["operations"]
    approved = {(item["method"], item["path"], item["operationId"]): item for item in entries}
    removals = {
        identity: item for identity, item in approved.items() if item["disposition"] == "remove"
    }
    actual = _operations(spec)
    removed = sorted(set(actual) & set(removals))
    absent = sorted(set(removals) - set(actual))
    candidates = sorted(
        identity
        for identity in actual
        if _family(identity[1], identity[2]) and identity not in approved
    )
    moved = sorted(
        (method, path, actual_id)
        for method, path, operation_id in absent
        for actual_id in (spec.get("paths", {}).get(path, {}).get(method, {}).get("operationId"),)
        if actual_id and actual_id != operation_id
    )
    retained = [operation for identity, operation in actual.items() if identity not in removals]
    removed_ops = [actual[identity] for identity in removed]
    removed_components = _reachable_components(spec, removed_ops)
    retained_components = _reachable_components(spec, retained) if removed else set()
    removed_schemas = {name for kind, name in removed_components if kind == "schemas"}
    retained_schemas = {name for kind, name in retained_components if kind == "schemas"}
    exclusive_components = sorted(removed_components - retained_components)
    exclusive = sorted(removed_schemas - retained_schemas)
    shared = sorted(removed_schemas & retained_schemas)
    navigation_values = set(policy.get("navigationValues", []))
    link_type = spec.get("components", {}).get("schemas", {}).get("commonDashboardLinkType", {})
    present_navigation = set(link_type.get("enum", [])) & navigation_values

    def record(identity: tuple[str, str, str]) -> dict[str, str]:
        method, path, operation_id = identity
        return {"method": method, "path": path, "operationId": operation_id}

    audit: dict[str, Any] = {
        "policyVersion": policy["policyVersion"],
        "removed": [record(x) for x in removed],
        "already_absent": [record(x) for x in absent],
        "retained_shared": shared,
        "removed_schemas": exclusive,
        "removed_other_components": [
            {"kind": kind, "name": name} for kind, name in exclusive_components if kind != "schemas"
        ],
        "newly_discovered_candidates": [record(x) for x in candidates],
        "changed_identities": [record(x) for x in moved],
        "navigation_values_removed": sorted(present_navigation),
        "navigation_values_already_absent": sorted(navigation_values - present_navigation),
    }
    if audit_path is not None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    if candidates or moved:
        raise ValueError(
            f"Unreviewed legacy site API drift: {len(candidates)} new candidates, "
            f"{len(moved)} changed identities; see {audit_path or 'curation audit'}"
        )

    smsv2_before = None
    if protect_smsv2:
        smsv2 = {
            identity: operation
            for identity, operation in actual.items()
            if "securemesh_site_v2" in identity[1] or "securemesh_site_v2" in identity[2]
        }
        if len(smsv2) != 6 or not any("cloud_resources" in key[1] for key in smsv2):
            raise ValueError("Expected all six SMSv2 operations, including cloud resources")
        smsv2_before = (
            copy.deepcopy(smsv2),
            {
                key: copy.deepcopy(value)
                for key, value in spec.get("components", {}).get("schemas", {}).items()
                if key in _reachable_schemas(spec, list(smsv2.values()))
            },
        )

    protected_before = None
    if protect_retained_families:
        protected_before = {}
        for family, expected_count in RETAINED_FAMILY_COUNTS.items():
            operations = {
                identity: operation
                for identity, operation in actual.items()
                if family in identity[1] or family in identity[2]
            }
            if len(operations) != expected_count:
                raise ValueError(
                    f"Expected {expected_count} retained {family} operations, "
                    f"found {len(operations)}"
                )
            closure = _reachable_schemas(spec, list(operations.values()))
            protected_before[family] = (
                copy.deepcopy(operations),
                {
                    name: copy.deepcopy(schema)
                    for name, schema in spec.get("components", {}).get("schemas", {}).items()
                    if name in closure
                },
            )

    for method, path, _ in removed:
        del spec["paths"][path][method]
        if not any(key in HTTP_METHODS for key in spec["paths"][path]):
            del spec["paths"][path]
    components = spec.get("components", {})
    for kind, name in exclusive_components:
        bucket = components.get(kind)
        if isinstance(bucket, dict):
            bucket.pop(name, None)
    schemas = components.get("schemas", {})
    if isinstance(link_type, dict) and navigation_values:
        values = link_type.get("enum")
        if isinstance(values, list):
            link_type["enum"] = [value for value in values if value not in navigation_values]
        description = link_type.get("description")
        if isinstance(description, str):
            blocks = re.split(r"(?=^- [A-Z_]+: )", description, flags=re.MULTILINE)
            link_type["description"] = "".join(
                block
                for block in blocks
                if not any(block.startswith(f"- {value}: ") for value in navigation_values)
            )
    removed_ids = {item["operationId"] for item in removals.values()}
    for item in spec.get("paths", {}).values():
        _scrub_surviving_text(item, removed_ids)
    for schema in schemas.values():
        _scrub_surviving_text(schema, removed_ids)
    curate_metadata(spec)

    if smsv2_before is not None:
        smsv2_after = {
            identity: operation
            for identity, operation in _operations(spec).items()
            if "securemesh_site_v2" in identity[1] or "securemesh_site_v2" in identity[2]
        }
        closure_after = {
            key: copy.deepcopy(value)
            for key, value in schemas.items()
            if key in _reachable_schemas(spec, list(smsv2_after.values()))
        }
        if (smsv2_after, closure_after) != smsv2_before:
            raise ValueError("Curation changed the SMSv2 operation or schema contract")
    if protected_before is not None:
        after = _operations(spec)
        for family, before in protected_before.items():
            operations = {
                identity: operation
                for identity, operation in after.items()
                if family in identity[1] or family in identity[2]
            }
            closure = _reachable_schemas(spec, list(operations.values()))
            schemas_after = {
                name: copy.deepcopy(schema) for name, schema in schemas.items() if name in closure
            }
            if (operations, schemas_after) != before:
                raise ValueError(f"Curation changed retained {family} operations or schemas")
    return audit
