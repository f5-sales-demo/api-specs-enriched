"""Qualify observed optional nulls in the SMSv2 GET response only (#1975)."""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from scripts.utils.extension_constants import X_F5XC_RESPONSE_NULLABILITY

ROOT = "securemesh_site_v2GetResponse"
PREFIX = "#/components/schemas/"
OPERATION = "ves.io.schema.views.securemesh_site_v2.API.Get"
PATHS = (
    ("create_form",),
    ("replace_form",),
    ("spec", "admin_user_credentials"),
    ("spec", "dns_ntp_config"),
    ("spec", "load_balancing"),
    ("spec", "local_vrf"),
    ("spec", "offline_survivability_mode"),
    ("spec", "performance_enhancement_mode"),
    ("spec", "re_select"),
    ("spec", "software_settings", "waf_signatures"),
    ("spec", "upgrade_settings"),
    ("system_metadata", "deletion_timestamp"),
    ("system_metadata", "initializers"),
    ("system_metadata", "owner_view"),
)
PROVENANCE = {"issue": "#1975", "paths": [".".join(path) for path in PATHS]}


def references(value: Any) -> set[str]:
    """Return local component references in a schema or operation."""
    if isinstance(value, dict):
        found = set()
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith(PREFIX):
            found.add(reference[len(PREFIX) :])
        for child in value.values():
            found.update(references(child))
        return found
    if isinstance(value, list):
        return set().union(*(references(child) for child in value))
    return set()


def qualify_smsv2_get_nullability(spec: dict[str, Any]) -> None:
    """Isolate response branches before adding null-only alternatives.

    Referenced request/shared components remain unchanged. Missing or ambiguous
    qualified fields fail closed. An existing identical qualification is a no-op.
    """
    schemas = spec.get("components", {}).get("schemas", {})
    if ROOT not in schemas:
        return
    operations = [
        methods["get"]
        for methods in spec.get("paths", {}).values()
        if isinstance(methods.get("get"), dict) and methods["get"].get("operationId") == OPERATION
    ]
    if not operations:
        return
    if len(operations) != 1 or ROOT not in references(operations[0].get("responses", {})):
        raise ValueError("Ambiguous SMSv2 GET response operation mapping")
    marker = schemas[ROOT].get(X_F5XC_RESPONSE_NULLABILITY)
    if marker is not None:
        if marker != PROVENANCE:
            raise ValueError("Conflicting SMSv2 response nullability qualification")
        return

    request_references = set()
    for methods in spec.get("paths", {}).values():
        for operation in methods.values():
            if isinstance(operation, dict):
                request_references.update(references(operation.get("requestBody", {})))
    pending = list(request_references)
    visited = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        if name == ROOT:
            raise ValueError("SMSv2 GET response is reachable from a request schema")
        visited.add(name)
        pending.extend(references(schemas.get(name, {})))

    # Build on a private graph so a failed declaration cannot partially mutate
    # a source graph shared between domain projections.
    working = copy.deepcopy(schemas)
    created = set()

    def contains(node: dict[str, Any], key: str, seen: set[str] | None = None) -> bool:
        seen = set() if seen is None else seen
        if key in node.get("properties", {}):
            return True
        reference = node.get("$ref")
        if reference:
            if not reference.startswith(PREFIX):
                raise ValueError("Unsupported response schema reference")
            name = reference[len(PREFIX) :]
            if name not in seen:
                if name not in working:
                    raise ValueError("Missing response schema reference")
                if contains(working[name], key, seen | {name}):
                    return True
        return any(contains(part, key, seen) for part in node.get("allOf", []))

    def container(node: dict[str, Any], key: str, context: tuple[str, ...]) -> dict[str, Any]:
        if key in node.get("properties", {}):
            return node["properties"]
        reference = node.get("$ref")
        if reference:
            if not reference.startswith(PREFIX):
                raise ValueError("Unsupported response schema reference")
            name = reference[len(PREFIX) :]
            if not contains(node, key):
                raise ValueError("Qualified response property is absent")
            if name not in created:
                suffix = hashlib.sha256(".".join(context).encode()).hexdigest()[:12]
                clone = f"{name}__smsv2_read_{suffix}"
                if clone in working:
                    raise ValueError("Response schema clone identity collision")
                working[clone] = copy.deepcopy(working[name])
                created.add(clone)
                node["$ref"] = PREFIX + clone
                name = clone
            return container(working[name], key, context)
        matches = [part for part in node.get("allOf", []) if contains(part, key)]
        if len(matches) != 1:
            raise ValueError("Qualified response property is absent or ambiguous")
        return container(matches[0], key, context)

    for path in PATHS:
        node = working[ROOT]
        for index, field in enumerate(path):
            properties = container(node, field, (ROOT, *path[:index]))
            original = properties[field]
            if index == len(path) - 1:
                properties[field] = {
                    "anyOf": [
                        copy.deepcopy(original),
                        {"type": "object", "nullable": True, "enum": [None]},
                    ],
                    **{
                        key: copy.deepcopy(value)
                        for key, value in original.items()
                        if key.startswith("x-")
                        or key in ("description", "title", "readOnly", "writeOnly")
                    },
                }
            else:
                node = original
    working[ROOT][X_F5XC_RESPONSE_NULLABILITY] = copy.deepcopy(PROVENANCE)
    # Commit only the isolated response root and new response components.
    schemas[ROOT] = working[ROOT]
    for name in sorted(created):
        schemas[name] = working[name]
