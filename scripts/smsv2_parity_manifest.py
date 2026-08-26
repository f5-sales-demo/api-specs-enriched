"""Build the deterministic complete nested-path manifest for Secure Mesh Site v2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from scripts.utils.json_writer import write_json_file

_REF_PREFIX = "#/components/schemas/"


def _reference(node: dict[str, Any]) -> str | None:
    value = node.get("$ref")
    if isinstance(value, str):
        return value
    all_of = node.get("allOf")
    if isinstance(all_of, list) and all_of and isinstance(all_of[0], dict):
        value = all_of[0].get("$ref")
        return value if isinstance(value, str) else None
    return None


def _resolved(node: dict[str, Any], schemas: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    reference = _reference(node)
    if not reference:
        return node, None
    if not reference.startswith(_REF_PREFIX):
        return node, reference
    return schemas.get(reference[len(_REF_PREFIX) :], {}), reference


def _type(node: dict[str, Any], resolved: dict[str, Any]) -> str:
    return str(node.get("type") or resolved.get("type") or ("object" if resolved else "unknown"))


def _platform_evidence(path: Path | None = None) -> dict[str, Any]:
    evidence_path = path or Path("config/smsv2_platform_evidence.yaml")
    evidence = yaml.safe_load(evidence_path.read_text())
    fields = evidence.get("fields", {}) if isinstance(evidence, dict) else {}
    if not isinstance(fields, dict):
        raise TypeError("SMSv2 platform evidence fields must be an object")
    return fields


def build_parity_manifest(
    spec: dict[str, Any], evidence_path: Path | None = None
) -> dict[str, Any]:
    """Return every reachable SMSv2 create path with wire and access semantics."""
    schemas = spec.get("components", {}).get("schemas", {})
    root_name = "securemesh_site_v2CreateRequest"
    root = schemas[root_name]
    entries: list[dict[str, Any]] = []
    choice_groups: dict[str, list[str]] = {}

    def walk(node: dict[str, Any], prefix: str, stack: tuple[str, ...]) -> None:
        resolved, reference = _resolved(node, schemas)
        if reference in stack:
            return
        next_stack = (*stack, reference) if reference else stack
        properties = resolved.get("properties", {})
        if not isinstance(properties, dict):
            return
        extension_prefix = "x-ves-oneof-field-"
        for key, value in sorted(resolved.items()):
            if not key.startswith(extension_prefix):
                continue
            fields = json.loads(value) if isinstance(value, str) else value
            if not isinstance(fields, list):
                raise TypeError(f"{key} must contain an array")
            group = key[len(extension_prefix) :]
            group_path = f"{prefix}.{group}" if prefix else group
            choice_groups[group_path] = [
                f"{prefix}.{field}" if prefix else str(field) for field in fields
            ]
        required = set(resolved.get("required", []) or [])
        for wire_key in sorted(properties):
            prop = properties[wire_key]
            if not isinstance(prop, dict):
                continue
            prop_resolved, prop_ref = _resolved(prop, schemas)
            prop_type = _type(prop, prop_resolved)
            path = f"{prefix}.{wire_key}" if prefix else wire_key
            if prop_type == "array":
                path += "[]"
            entry: dict[str, Any] = {
                "path": path,
                "wire_key": wire_key,
                "type": prop_type,
                "cardinality": "list" if prop_type == "array" else "single",
                "required": wire_key in required,
                "create_required": bool(
                    wire_key in required
                    or prop.get("x-ves-required") == "true"
                    or prop.get("x-f5xc-required-for", {}).get("create") is True
                ),
                "read_only": bool(prop.get("readOnly") or prop_resolved.get("readOnly")),
                "write_only": bool(prop.get("writeOnly") or prop_resolved.get("writeOnly")),
            }
            if prop_ref:
                entry["schema"] = prop_ref.removeprefix(_REF_PREFIX)
            for key in ("minItems", "maxItems", "minLength", "maxLength", "default", "enum"):
                value = prop.get(key, prop_resolved.get(key))
                if value is not None:
                    entry[key] = value
            conflicts = prop.get("x-f5xc-conflicts-with")
            if conflicts:
                entry["conflicts_with"] = conflicts
            entries.append(entry)

            child = prop
            if prop_type == "array":
                child = prop.get("items", prop_resolved.get("items", {}))
            walk(child, path, next_stack)

    walk(root, "", ())

    paths = sorted(entries, key=lambda entry: entry["path"])
    evidence = _platform_evidence(evidence_path)
    platform_removals = sorted(
        path
        for path, conclusion in evidence.items()
        if conclusion.get("classification") == "current_platform_removal"
    )
    return {
        "version": spec.get("info", {}).get("version"),
        "resource": "securemesh_site_v2",
        "root_schema": root_name,
        "path_count": len(paths),
        "paths": paths,
        "choice_groups": dict(sorted(choice_groups.items())),
        "deprecated_exclusions": ["spec.log_receiver", "spec.private_adn", "spec.rseries"],
        "current_platform_removals": platform_removals,
    }


def main() -> None:
    """Write the manifest from a canonical OpenAPI document."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("docs/specifications/api/openapi.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build = build_parity_manifest(json.loads(args.input.read_text()))
    write_json_file(build, args.output, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
