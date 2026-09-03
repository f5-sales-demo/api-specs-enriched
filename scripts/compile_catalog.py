#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Catalog Compiler — transforms F5XC OpenAPI specs into xcsh api-catalog.json format.

Usage:
    python -m scripts.compile_catalog                         # Uses specs/discovered/openapi.json
    python -m scripts.compile_catalog --input path/to/spec.json
    python -m scripts.compile_catalog --output release/api-catalog.json
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from scripts.utils.canonical_merge import canonical_merge_sources
from scripts.utils.pii_sanitizer import sanitize_emails
from scripts.utils.version_calculator import get_version_from_tags

DEFAULT_INPUT = Path("specs/discovered/openapi.json")
DEFAULT_OUTPUT = Path("release/api-catalog.json")

F5XC_AUTH = {
    "type": "api_token",
    "headerName": "Authorization",
    "headerTemplate": "APIToken {token}",
    "tokenSource": "F5XC_API_TOKEN",
    "baseUrlSource": "F5XC_API_URL",
}

F5XC_DEFAULTS = {
    "namespace": {"source": "F5XC_NAMESPACE"},
}

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options"})
_CANONICAL_CRUD_RPCS = frozenset({"Create", "List", "Get", "Replace", "Delete"})


def merge_spec_files(dir_path: Path) -> dict[str, Any]:
    """Load the canonical master, or fail-closed merge genuine source files.

    Release catalog compilation must never reconstruct a contract from enriched domain
    projections. The directory form remains useful for source fixtures and delegates to
    the same canonical engine as the pipeline.
    """
    master = dir_path / "openapi.json"
    if master.is_file():
        with master.open(encoding="utf-8") as stream:
            return json.load(stream)
    specs: dict[str, dict[str, Any]] = {}
    versions: set[str] = set()

    for spec_file in sorted(dir_path.glob("*.json")):
        if spec_file.name in {
            "index.json",
            "namespace_profiles.json",
            "resource_coverage.json",
            "validation.json",
        }:
            continue
        try:
            with spec_file.open() as f:
                spec = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        paths = spec.get("paths")
        if not paths or not isinstance(paths, dict):
            continue
        version = spec.get("info", {}).get("version")
        if isinstance(version, str) and version:
            versions.add(version)

        specs[spec_file.name] = spec

    if len(versions) > 1:
        raise ValueError(f"input specifications have inconsistent versions: {sorted(versions)}")

    result = canonical_merge_sources(specs)
    merged = {"openapi": "3.0.3", **result.merged}
    if versions:
        merged["info"] = {"version": next(iter(versions))}
    return merged


_DANGER_MAP: dict[str, str] = {
    "GET": "low",
    "OPTIONS": "low",
    "POST": "medium",
    "PUT": "medium",
    "PATCH": "medium",
    "DELETE": "high",
}


def assign_danger_level(method: str) -> str:
    """Map HTTP method to danger level."""
    return _DANGER_MAP.get(method.upper(), "medium")


def extract_category_name(path: str) -> str:
    """Derive kebab-case category name from an API path.

    Examples:
        /api/config/namespaces/{namespace}/http_loadbalancers       -> http-loadbalancers
        /api/config/namespaces/{namespace}/http_loadbalancers/{name} -> http-loadbalancers
        /api/web/namespaces                                          -> namespaces
    """
    segments = [s for s in path.split("/") if s and not s.startswith("{")]
    prefix = {"api", "config", "web", "ml", "data"}
    filtered = [s for s in segments if s not in prefix]
    resource_segments = []
    skip_next = False
    for seg in filtered:
        if seg == "namespaces":
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            continue
        resource_segments.append(seg)
    resource = (
        "-".join(resource_segments)
        if resource_segments
        else (filtered[-1] if filtered else "unknown")
    )
    return resource.replace("_", "-")


def generate_operation_name(method: str, path: str) -> str:
    """Generate a snake_case operation name from HTTP method and path.

    Rules:
        GET  /resources        -> list_resources
        GET  /resources/{name} -> get_resource   (singular)
        POST /resources        -> create_resource (singular)
        PUT  /resources/{name} -> replace_resource (singular)
        PATCH /resources/{name}-> update_resource (singular)
        DELETE /resources/{name}-> delete_resource (singular)
    """
    category = extract_category_name(path)
    resource_snake = category.replace("-", "_")
    singular = resource_snake.rstrip("s") if resource_snake.endswith("s") else resource_snake

    segments = path.rstrip("/").split("/")
    last_segment = segments[-1] if segments else ""
    is_item = last_segment.startswith("{") and last_segment.endswith("}")

    method = method.upper()
    _method_prefix: dict[str, str] = {
        "POST": "create",
        "PUT": "replace",
        "PATCH": "update",
        "DELETE": "delete",
    }
    if method == "GET":
        return f"list_{resource_snake}" if not is_item else f"get_{singular}"
    prefix = _method_prefix.get(method)
    if prefix:
        return f"{prefix}_{singular}"
    return f"{method.lower()}_{singular}"


def extract_parameters(path: str, operation: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract parameters from path template and OpenAPI operation definition."""
    params: list[dict[str, Any]] = []

    for match in re.finditer(r"\{([^}]+)\}", path):
        raw_name = match.group(1)
        # Normalize dotted params: metadata.namespace -> namespace
        name = raw_name.split(".")[-1] if "." in raw_name else raw_name
        param: dict[str, Any] = {
            "name": name,
            "in": "path",
            "required": True,
            "type": "string",
        }
        if name == "namespace":
            param["default"] = "$F5XC_NAMESPACE"
        params.append(param)

    params.extend(
        {
            "name": op_param["name"],
            "in": "query",
            "required": op_param.get("required", False),
            "type": op_param.get("schema", {}).get("type", "string"),
        }
        for op_param in operation.get("parameters", [])
        if op_param.get("in") == "query"
    )

    return params


def extract_response_schema(
    operation: dict[str, Any],
    components: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Extract and simplify response schema from an OpenAPI operation.

    Checks 200 then 201 response codes. Resolves $ref references using
    components.schemas if provided. Returns simplified {type, properties, required}
    format. Returns None if no usable response schema is found.
    """
    for code in ("200", "201"):
        schema = (
            operation.get("responses", {})
            .get(code, {})
            .get("content", {})
            .get("application/json", {})
            .get("schema")
        )
        if not schema or not isinstance(schema, dict):
            continue

        # Resolve $ref if present
        if "$ref" in schema and components:
            ref_key = schema["$ref"].split("/")[-1]
            schema = components.get("schemas", {}).get(ref_key, {})

        if not schema:
            continue

        simplified: dict[str, Any] = {}
        if "type" in schema:
            simplified["type"] = schema["type"]
        if "properties" in schema and isinstance(schema["properties"], dict):
            simplified["properties"] = {}
            for prop_name, prop_schema in schema["properties"].items():
                if isinstance(prop_schema, dict):
                    # Resolve nested $ref for property type
                    resolved_prop = prop_schema
                    if "$ref" in prop_schema and components:
                        ref_key = prop_schema["$ref"].split("/")[-1]
                        resolved_prop = components.get("schemas", {}).get(ref_key, {})
                    if "type" in resolved_prop:
                        simplified["properties"][prop_name] = {"type": resolved_prop["type"]}
        if "required" in schema and isinstance(schema["required"], list):
            simplified["required"] = schema["required"]
        if simplified:
            return simplified
    return None


def group_paths_by_resource(paths: dict[str, Any]) -> dict[str, list[tuple[str, str, dict]]]:
    """Group operations without separating one canonical API's CRUD surface.

    Canonical ``API.Create/List/Get/Replace/Delete`` methods are grouped by API
    identity and their resource path.  Custom/action operations intentionally
    remain path-grouped.
    """
    groups: dict[str, list[tuple[str, str, dict]]] = {}
    for path, path_item in sorted(paths.items()):
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            parsed = extract_api_identity(operation.get("operationId") or "")
            if parsed and parsed[1] == "API" and parsed[2] in _CANONICAL_CRUD_RPCS:
                category = _canonical_resource_category(path)
            else:
                category = extract_category_name(path)
            groups.setdefault(category, []).append((path, method.upper(), operation))
    return groups


def _canonical_resource_category(path: str) -> str:
    """Derive the canonical collection category across namespace spellings."""
    segments = [segment for segment in path.strip("/").split("/") if segment]
    try:
        namespace_index = segments.index("namespaces")
    except ValueError:
        return extract_category_name(path)
    prefix = [
        segment
        for segment in segments[2:namespace_index]
        if segment not in {"config", "web", "data", "ml"}
    ]
    tail_index = namespace_index + 1
    if tail_index < len(segments) and (
        segments[tail_index] == "system" or segments[tail_index].startswith("{")
    ):
        tail_index += 1
    if tail_index >= len(segments):
        return extract_category_name(path)
    return "-".join([*prefix, segments[tail_index]]).replace("_", "-")


def normalize_path_placeholders(path: str) -> str:
    """Normalize dotted path placeholders: {metadata.namespace} -> {namespace}."""
    return re.sub(r"\{[^}]*\.([^}]+)\}", r"{\1}", path)


def _resolve_body_schema(
    operation: dict[str, Any],
    components: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract and resolve the request body JSON schema from an OpenAPI operation."""
    body_schema = (
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
    )
    if not body_schema:
        return None
    ref = _schema_ref(body_schema)
    if ref and components:
        ref_key = ref.split("/")[-1]
        resolved = components.get("schemas", {}).get(ref_key, {})
        if resolved:
            return resolved
    return body_schema


def _schema_ref(schema: dict[str, Any]) -> str | None:
    """Return a direct or single-member allOf-wrapped local reference."""
    direct = schema.get("$ref")
    if isinstance(direct, str):
        return direct
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for member in all_of:
            if isinstance(member, dict) and isinstance(member.get("$ref"), str):
                return member["$ref"]
    return None


def _resolve_schema_ref(
    schema: dict[str, Any], components: dict[str, Any] | None
) -> dict[str, Any]:
    """Resolve a $ref to its target schema. Returns original if unresolvable."""
    ref = _schema_ref(schema)
    if not ref or not components:
        return schema
    ref_key = ref.split("/")[-1]
    resolved = (components.get("schemas") or {}).get(ref_key)
    return resolved or schema


_ENRICHMENT_KEYS = frozenset(
    {
        "x-f5xc-constraints",
        "x-f5xc-required-for",
        "x-f5xc-server-default",
        "x-f5xc-recommended-value",
        "x-f5xc-conflicts-with",
        "x-f5xc-requires",
        "x-f5xc-description",
        # Upstream pass-through (api-specs#686): the original misspelled key that
        # F5 accepts on the wire. Request builders need it, so it must survive
        # this projection — see terraform-provider-xcsh#1257.
        "x-f5xc-wire-name",
    }
)


def _merge_schema(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge schema layers while preserving properties supplied by each layer."""
    merged = dict(base)
    for key, value in overlay.items():
        if key == "properties" and isinstance(value, dict):
            merged[key] = {**merged.get(key, {}), **value}
        else:
            merged[key] = value
    return merged


def _expand_schema(
    schema: dict[str, Any],
    components: dict[str, Any] | None,
    active_refs: frozenset[str],
) -> tuple[dict[str, Any], frozenset[str]]:
    """Resolve this schema's ref and allOf layers without descending into children."""
    merged: dict[str, Any] = {}
    refs = active_refs
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref not in refs:
        target = _resolve_schema_ref(schema, components)
        if target is not schema:
            refs = refs | {ref}
            expanded, refs = _expand_schema(target, components, refs)
            merged = _merge_schema(merged, expanded)
    for member in schema.get("allOf", []):
        if isinstance(member, dict):
            expanded, member_refs = _expand_schema(member, components, refs)
            merged = _merge_schema(merged, expanded)
            refs = refs | member_refs
    local = {key: value for key, value in schema.items() if key not in {"$ref", "allOf"}}
    return _merge_schema(merged, local), refs


def _walk_schema(
    schema: dict[str, Any],
    components: dict[str, Any] | None,
    *,
    prefix: str = "",
    active_refs: frozenset[str] = frozenset(),
) -> list[tuple[str, dict[str, Any]]]:
    """Return every reachable schema node with canonical ``[]`` array paths."""
    resolved, refs = _expand_schema(schema, components, active_refs)
    nodes = [(prefix, resolved)]
    properties = resolved.get("properties")
    if isinstance(properties, dict):
        for name, child in properties.items():
            if isinstance(child, dict):
                path = f"{prefix}.{name}" if prefix else name
                nodes.extend(_walk_schema(child, components, prefix=path, active_refs=refs))
    items = resolved.get("items")
    if isinstance(items, dict):
        path = f"{prefix}[]" if prefix else "[]"
        nodes.extend(_walk_schema(items, components, prefix=path, active_refs=refs))
    return nodes


def validate_payload_against_schema(
    payload: Any,
    schema: dict[str, Any],
    components: dict[str, Any] | None,
    *,
    path: str = "$",
    active_refs: frozenset[str] = frozenset(),
) -> list[str]:
    """Validate the structural contract needed for executable minimum examples."""
    resolved, refs = _expand_schema(schema, components, active_refs)
    errors: list[str] = []
    variants = resolved.get("oneOf")
    if isinstance(variants, list) and variants:
        matches = [
            member
            for member in variants
            if isinstance(member, dict)
            and not validate_payload_against_schema(
                payload, member, components, path=path, active_refs=refs
            )
        ]
        if len(matches) != 1:
            return [f"{path} must match exactly one oneOf member (matched {len(matches)})"]
        resolved, refs = _expand_schema(matches[0], components, refs)

    schema_type = resolved.get("type")
    if schema_type is None and isinstance(resolved.get("properties"), dict):
        schema_type = "object"
    type_matches = {
        "object": isinstance(payload, dict),
        "array": isinstance(payload, list),
        "string": isinstance(payload, str),
        "integer": isinstance(payload, int) and not isinstance(payload, bool),
        "number": isinstance(payload, (int, float)) and not isinstance(payload, bool),
        "boolean": isinstance(payload, bool),
    }
    if schema_type in type_matches and not type_matches[schema_type]:
        return [f"{path} must be {schema_type}"]

    if isinstance(payload, dict):
        properties = resolved.get("properties") or {}
        if isinstance(properties, dict):
            unknown = sorted(set(payload) - set(properties))
            errors.extend(
                f"{path}.{name} is not declared by the request schema" for name in unknown
            )
            required = set(resolved.get("required") or [])
            for name, child in properties.items():
                if isinstance(child, dict):
                    required_for = child.get("x-f5xc-required-for") or {}
                    if child.get("x-ves-required") == "true" or (
                        isinstance(required_for, dict)
                        and (
                            required_for.get("minimum_config") is True
                            or required_for.get("create") is True
                        )
                    ):
                        required.add(name)
            errors.extend(f"{path}.{name} is required" for name in sorted(required - set(payload)))
            for name in sorted(set(payload) & set(properties)):
                child = properties[name]
                if isinstance(child, dict):
                    errors.extend(
                        validate_payload_against_schema(
                            payload[name],
                            child,
                            components,
                            path=f"{path}.{name}",
                            active_refs=refs,
                        )
                    )
    elif isinstance(payload, list):
        items = resolved.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(payload):
                errors.extend(
                    validate_payload_against_schema(
                        item, items, components, path=f"{path}[{index}]", active_refs=refs
                    )
                )
    return errors


def _extract_field_metadata(
    schema: dict[str, Any],
    components: dict[str, Any] | None,
    *,
    prefix: str = "",
    depth: int = 0,
    max_depth: int = 3,
    visited: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Extract enriched metadata from every reachable schema node."""
    del depth, max_depth
    result: dict[str, dict[str, Any]] = {}
    for field_path, prop_resolved in _walk_schema(
        schema, components, prefix=prefix, active_refs=frozenset(visited or ())
    ):
        has_enrichment = field_path and any(k in prop_resolved for k in _ENRICHMENT_KEYS)
        if not has_enrichment:
            continue
        entry: dict[str, Any] = {"type": prop_resolved.get("type", "object")}
        desc = prop_resolved.get("x-f5xc-description") or prop_resolved.get("description")
        if desc:
            entry["description"] = desc
        constraints = prop_resolved.get("x-f5xc-constraints")
        if constraints:
            entry["constraints"] = constraints
        required_for = prop_resolved.get("x-f5xc-required-for")
        if required_for:
            entry["required_for"] = required_for
        if prop_resolved.get("x-f5xc-server-default"):
            entry["serverDefault"] = True
        default_val = prop_resolved.get("default")
        if default_val is not None:
            entry["default"] = default_val
        recommended = prop_resolved.get("x-f5xc-recommended-value")
        if recommended is not None:
            entry["recommendedValue"] = recommended
        conflicts = prop_resolved.get("x-f5xc-conflicts-with")
        if conflicts:
            entry["conflictsWith"] = conflicts
        requires = prop_resolved.get("x-f5xc-requires")
        if requires:
            entry["requires"] = requires
        wire_name = prop_resolved.get("x-f5xc-wire-name")
        if wire_name:
            entry["wireName"] = wire_name
        result[field_path] = entry
    return result


def _collect_oneof_recommendations(
    schema: dict[str, Any],
    components: dict[str, Any] | None,
    *,
    prefix: str = "",
    depth: int = 0,
    max_depth: int = 3,
    visited: set[str] | None = None,
) -> dict[str, str]:
    """Collect recommended oneOf variants from all reachable schema nodes."""
    del depth, max_depth
    result: dict[str, str] = {}
    for path, resolved in _walk_schema(
        schema, components, prefix=prefix, active_refs=frozenset(visited or ())
    ):
        oneof_map = resolved.get("x-f5xc-recommended-oneof-variant")
        if isinstance(oneof_map, dict):
            for group_name, variant in oneof_map.items():
                key = f"{path}.{group_name}" if path else group_name
                result[key] = variant
    return result


def _collect_oneof_variants(
    schema: dict[str, Any],
    components: dict[str, Any] | None,
    *,
    prefix: str = "",
    depth: int = 0,
    max_depth: int = 3,
    visited: set[str] | None = None,
) -> dict[str, list[str]]:
    """Collect complete oneOf variant lists from all reachable schema nodes."""
    del depth, max_depth
    result: dict[str, list[str]] = {}
    for path, resolved in _walk_schema(
        schema, components, prefix=prefix, active_refs=frozenset(visited or ())
    ):
        for key, val in resolved.items():
            if not key.startswith("x-ves-oneof-field-"):
                continue
            group_name = key[len("x-ves-oneof-field-") :]
            full_key = f"{path}.{group_name}" if path else group_name
            if isinstance(val, list):
                result[full_key] = val
            elif isinstance(val, str):
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, list):
                        result[full_key] = parsed
                except (json.JSONDecodeError, ValueError):
                    pass
    return result


def _extract_raw_response_schema(
    operation: dict[str, Any],
    components: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract the raw response schema with $refs resolved but descriptions preserved."""
    responses = operation.get("responses", {})
    for code in ("200", "201"):
        resp = responses.get(code)
        if not resp:
            continue
        schema = resp.get("content", {}).get("application/json", {}).get("schema")
        if schema:
            return _resolve_schema_ref(schema, components)
    return None


def _build_operation(
    path: str,
    method: str,
    operation: dict[str, Any],
    op_name: str,
    components: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a single catalog operation entry from an OpenAPI path/method/operation."""
    normalized_path = normalize_path_placeholders(path)
    op: dict[str, Any] = {
        "name": op_name,
        "operationId": operation.get("operationId") or f"{method.lower()}:{normalized_path}",
        "description": (
            operation.get("summary") or operation.get("description") or f"{method} {path}"
        ),
        "method": method,
        "path": normalized_path,
        "dangerLevel": assign_danger_level(method),
        "parameters": extract_parameters(path, operation),
    }
    aliases = operation.get("x-f5xc-operation-aliases")
    if aliases:
        op["operationAliases"] = list(aliases)
    body_schema = _resolve_body_schema(operation, components)
    if body_schema:
        op["bodySchema"] = body_schema
    response_schema = extract_response_schema(operation, components)
    if response_schema:
        op["responseSchema"] = response_schema

    # Extract minimumPayload from x-f5xc-minimum-configuration
    if body_schema and method.upper() in {"POST", "PUT", "PATCH"}:
        min_config = body_schema.get("x-f5xc-minimum-configuration")
        if min_config and min_config.get("example_json"):
            try:
                parsed_json = json.loads(min_config["example_json"])
                errors = validate_payload_against_schema(parsed_json, body_schema, components)
                if errors:
                    operation_id = operation.get("operationId") or f"{method.upper()} {path}"
                    raise ValueError(
                        f"{operation_id} has an invalid minimum payload: {'; '.join(errors)}"
                    )
                op["minimumPayload"] = {
                    "json": parsed_json,
                    "requiredFields": min_config.get("required_fields", []),
                    "description": min_config.get("description", ""),
                }
            except (json.JSONDecodeError, TypeError) as exc:
                operation_id = operation.get("operationId") or f"{method.upper()} {path}"
                raise ValueError(f"{operation_id} has invalid minimum payload JSON") from exc
        elif min_config and isinstance(min_config.get("diagnostic"), dict):
            op["minimumPayloadDiagnostic"] = min_config["diagnostic"]

    # Extract fieldMetadata from enriched properties (POST/PUT/PATCH only)
    if body_schema and method.upper() in {"POST", "PUT", "PATCH"}:
        field_meta = _extract_field_metadata(body_schema, components)
        if field_meta:
            op["fieldMetadata"] = field_meta

        oneof_recs = _collect_oneof_recommendations(body_schema, components)
        if oneof_recs:
            op["oneOfRecommendations"] = oneof_recs

        oneof_variants = _collect_oneof_variants(body_schema, components)
        if oneof_variants:
            op["oneOfVariants"] = oneof_variants

    # Extract responseSummary from raw operation responses (not the simplified response_schema)
    raw_resp_schema = _extract_raw_response_schema(operation, components)
    if raw_resp_schema:
        resp_props = raw_resp_schema.get("properties", {})
        if resp_props:
            summary = []
            for field_name, field_schema in resp_props.items():
                field_type = field_schema.get("type", "object")
                field_desc = field_schema.get("description", "")
                if "$ref" in field_schema:
                    ref_key = field_schema["$ref"].split("/")[-1]
                    field_type = ref_key
                    resolved = _resolve_schema_ref(field_schema, components)
                    if resolved.get("description"):
                        field_desc = resolved["description"]
                summary.append({"field": field_name, "type": field_type, "description": field_desc})
            if summary:
                op["responseSummary"] = summary

    return op


def _build_category_operations(
    entries: list[tuple[str, str, dict]],
    components: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build deduplicated operations list for a single category."""
    operations = []
    seen_op_names: set[str] = set()
    for path, method, operation in sorted(entries, key=lambda e: (e[0], e[1])):
        op_name = generate_operation_name(method, path)
        if op_name in seen_op_names:
            identity = (
                operation.get("operationId") or f"{method}:{normalize_path_placeholders(path)}"
            )
            suffix = re.sub(r"[^a-z0-9]+", "_", identity.lower()).strip("_")
            candidate = f"{op_name}_{suffix}"
            if candidate in seen_op_names:
                digest = hashlib.sha256(identity.encode()).hexdigest()[:8]
                candidate = f"{candidate}_{digest}"
            op_name = candidate
        seen_op_names.add(op_name)
        operations.append(_build_operation(path, method, operation, op_name, components))
    return operations


def _deduplicate_global_op_names(categories: list[dict[str, Any]]) -> None:
    """Deterministically suffix duplicate names without dropping operations."""
    global_seen: set[str] = set()
    for cat in categories:
        for op in cat["operations"]:
            candidate = op["name"]
            if candidate in global_seen:
                candidate = f"{candidate}_{cat['name'].replace('-', '_')}"
            if candidate in global_seen:
                digest = hashlib.sha256(op["operationId"].encode()).hexdigest()[:8]
                candidate = f"{candidate}_{digest}"
            op["name"] = candidate
            global_seen.add(candidate)


DEFAULT_EXCLUSIONS_CONFIG = Path("config/api_exclusions.yaml")

# An operationId is "<identity>.<RpcContainer>.<Method>", where the identity is a
# lowercase dotted run under ves.io.schema and the container is the first
# CamelCase segment.
#
# The container is matched structurally, NOT by looking for "API". The published
# specifications use 58 distinct container names, and four of them spell it "Api"
# (UpgradeStatusCustomApi, SoftwareVersionOsImageCustomApi,
# WafSignatureChangelogCustomApi, SignatureCustomApi). Splitting on a literal
# ".API." silently loses seven operations across three identities; handling only
# API/CustomAPI loses far more. Publishing the identity so no consumer has to
# repeat this parse is the reason these sections exist.
_API_OPERATION_ID = re.compile(
    r"^(?P<identity>ves\.io\.schema(?:\.[a-z0-9_]+)*)"
    r"\.(?P<container>[A-Z][A-Za-z0-9_]*)"
    r"\.(?P<rpc>[A-Za-z0-9_]+)$",
)

# Path items hold these beside their operations; they are not operations.
_NON_OPERATION_KEYS = frozenset(
    {"parameters", "$ref", "summary", "description", "servers"},
)


def extract_api_identity(operation_id: str) -> tuple[str, str, str] | None:
    """Split an operationId into (apiIdentity, rpcContainer, rpcMethod).

    Returns None when the value is not a schema-qualified operationId.
    """
    match = _API_OPERATION_ID.match(operation_id or "")
    if match is None:
        return None
    return match["identity"], match["container"], match["rpc"]


def surface_from_path(path: str) -> str:
    """Return the API surface a path is published on, e.g. "config" or "web".

    The surface is the segment after /api/, read from the path rather than from
    `tags` because the path is what a caller actually requests.

    Not every published path sits under /api/ — `/no_auth/namespaces/...` is one
    real example — so a path whose first segment is not "api" reports that first
    segment as its surface.
    """
    segments = [segment for segment in (path or "").split("/") if segment]
    if not segments:
        raise ValueError(f"path does not carry an API surface: {path!r}")
    if segments[0] != "api":
        return segments[0]
    if len(segments) < 2:
        raise ValueError(f"path does not carry an API surface: {path!r}")
    return segments[1]


def _request_schema_key(operation: dict[str, Any]) -> str | None:
    """Return the request body's schema key, or None when it takes no body."""
    schema = (
        (operation.get("requestBody") or {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    ref = _schema_ref(schema) if isinstance(schema, dict) else None
    if not isinstance(ref, str) or not ref:
        return None
    return ref.rsplit("/", 1)[-1]


def _response_schema_key(operation: dict[str, Any]) -> str | None:
    """Return the unique successful JSON response schema key, when referenced."""
    refs: set[str] = set()
    for status, response in (operation.get("responses") or {}).items():
        if not str(status).startswith("2") or not isinstance(response, dict):
            continue
        schema = (response.get("content") or {}).get("application/json", {}).get("schema", {})
        ref = _schema_ref(schema) if isinstance(schema, dict) else None
        if isinstance(ref, str) and ref:
            refs.add(ref.rsplit("/", 1)[-1])
    if len(refs) > 1:
        operation_id = operation.get("operationId") or "<unknown>"
        raise ValueError(
            f"{operation_id} has ambiguous successful JSON response schemas: {sorted(refs)}"
        )
    return next(iter(refs), None)


def build_api_operations(paths: dict[str, Any]) -> list[dict[str, Any]]:
    """Group every published operation under its ves.io.schema identity.

    Ordering is total and independent of traversal order: identities sorted, then
    operations by (method, path). An unparseable operationId or a duplicate one is
    an error rather than a skip — silently dropping either is how a consumer ends
    up with a contract that looks complete and is not.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen_operation_ids: dict[str, str] = {}
    seen_terraform_names: dict[str, str] = {}

    for path, path_item in (paths or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method in _NON_OPERATION_KEYS or not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId") or ""
            parsed = extract_api_identity(operation_id)
            if parsed is None:
                # An operationId that claims to be schema-qualified but does not
                # parse is a hard error: that is the casing-and-format bug class
                # this inventory exists to eliminate, and skipping it is exactly
                # how a consumer ends up with a contract that looks complete.
                #
                # An operationId that makes no such claim is simply not an F5
                # schema API — a hand-written endpoint, a fixture — and has no
                # identity to publish, so it is not part of this section.
                if operation_id.startswith("ves.io.schema"):
                    raise ValueError(
                        f"{method.upper()} {path} has a schema-qualified operationId "
                        f"that does not parse: {operation_id!r}",
                    )
                continue
            identity = parsed[0]
            if operation_id in seen_operation_ids:
                raise ValueError(
                    f"duplicate operationId {operation_id!r}: "
                    f"{seen_operation_ids[operation_id]} and {method.upper()} {path}",
                )
            seen_operation_ids[operation_id] = f"{method.upper()} {path}"

            entry: dict[str, Any] = {
                "method": method.upper(),
                "path": path,
                "operationId": operation_id,
                "surface": surface_from_path(path),
            }
            request_schema = _request_schema_key(operation)
            if request_schema is not None:
                entry["requestSchema"] = request_schema
            response_schema = _response_schema_key(operation)
            if response_schema is not None:
                entry["responseSchema"] = response_schema
            operation_role = operation.get("x-f5xc-operation-role")
            if operation_role is not None:
                if operation_role not in {"query", "issuance", "collection", "action"}:
                    raise ValueError(
                        f"{operation_id} has unsupported x-f5xc-operation-role {operation_role!r}"
                    )
                terraform_name = operation.get("x-f5xc-terraform-name")
                if not isinstance(terraform_name, str) or not re.fullmatch(
                    r"[a-z][a-z0-9_]*", terraform_name
                ):
                    raise ValueError(
                        f"{operation_role} operation {operation_id} requires a valid "
                        "x-f5xc-terraform-name"
                    )
                if terraform_name in seen_terraform_names:
                    raise ValueError(
                        f"duplicate x-f5xc-terraform-name {terraform_name!r}: "
                        f"{seen_terraform_names[terraform_name]} and {operation_id}"
                    )
                seen_terraform_names[terraform_name] = operation_id
                method_upper = method.upper()
                has_query_parameters = any(
                    isinstance(parameter, dict) and parameter.get("in") == "query"
                    for parameter in operation.get("parameters", [])
                )
                has_input_parameters = any(
                    isinstance(parameter, dict) and parameter.get("in") in {"path", "query"}
                    for parameter in operation.get("parameters", [])
                )
                if response_schema is None:
                    raise ValueError(
                        f"{operation_role} operation {operation_id} requires a response schema"
                    )
                if method_upper == "POST" and request_schema is None:
                    raise ValueError(
                        f"POST {operation_role} operation {operation_id} requires a request schema"
                    )
                if (
                    method_upper == "GET"
                    and operation_role in {"query", "issuance"}
                    and not has_query_parameters
                ):
                    raise ValueError(
                        f"GET {operation_role} operation {operation_id} requires query parameters"
                    )
                if (
                    method_upper == "GET"
                    and operation_role == "collection"
                    and not has_input_parameters
                ):
                    raise ValueError(
                        f"GET collection operation {operation_id} requires path or query parameters"
                    )
                allowed_methods = {"POST"} if operation_role == "action" else {"GET", "POST"}
                if method_upper not in allowed_methods:
                    raise ValueError(
                        f"{operation_role} operation {operation_id} must use "
                        f"{'POST' if operation_role == 'action' else 'GET or POST'}, "
                        f"got {method_upper}"
                    )
                if operation_role in {"query", "collection"}:
                    if operation.get("x-f5xc-danger-level") != "low":
                        raise ValueError(
                            f"{operation_role} operation {operation_id} must have low danger"
                        )
                    if operation.get("x-f5xc-side-effects"):
                        raise ValueError(
                            f"{operation_role} operation {operation_id} must not have side effects"
                        )
                elif operation_role == "issuance":
                    if operation.get("x-f5xc-danger-level") != "medium":
                        raise ValueError(
                            f"issuance operation {operation_id} must have medium danger"
                        )
                    creates = (operation.get("x-f5xc-side-effects") or {}).get("creates", [])
                    if not creates:
                        raise ValueError(
                            f"issuance operation {operation_id} must declare a created credential"
                        )
                else:
                    if operation.get("x-f5xc-danger-level") != "medium":
                        raise ValueError(f"action operation {operation_id} must have medium danger")
                    modifies = (operation.get("x-f5xc-side-effects") or {}).get("modifies", [])
                    if not modifies:
                        raise ValueError(
                            f"action operation {operation_id} must declare a modified object"
                        )
                entry["role"] = operation_role
                entry["terraformName"] = terraform_name
            elif operation.get("x-f5xc-terraform-name") is not None:
                raise ValueError(
                    f"operation {operation_id} has x-f5xc-terraform-name without an operation role"
                )
            grouped.setdefault(identity, []).append(entry)

    return [
        {
            "apiIdentity": identity,
            "operations": sorted(operations, key=lambda o: (o["method"], o["path"])),
        }
        for identity, operations in sorted(grouped.items())
    ]


def collision_exclusions(
    collisions: list[dict[str, Any]],
    published_identities: set[str],
) -> list[dict[str, Any]]:
    """Turn displaced path claims into stated exclusions.

    Only an identity that lost every claim it had is excluded. One that lost a
    single path but still owns another stays published — excluding it would claim
    the whole API is unavailable when one operation was displaced.
    """
    excluded: dict[str, dict[str, Any]] = {}
    for collision in collisions or []:
        parsed = extract_api_identity(collision.get("losingOperationId") or "")
        if parsed is None:
            continue
        identity = parsed[0]
        if identity in published_identities or identity in excluded:
            continue
        winner = extract_api_identity(collision.get("winningOperationId") or "")
        winner_identity = winner[0] if winner else collision.get("winningOperationId")
        excluded[identity] = {
            "apiIdentity": identity,
            "classification": "path-collision",
            "reason": (
                f"{collision['method']} {collision['path']} is claimed by both this API "
                f"and {winner_identity}, which owns it in the published specification"
            ),
        }
    return list(excluded.values())


def build_api_exclusions(
    config_path: Path,
    published_identities: set[str],
) -> list[dict[str, Any]]:
    """Read the deliberately-withheld identities, if any are declared.

    An absent configuration means nothing is withheld, which is a different
    statement from "unknown" — that distinction is the reason consumers can tell a
    deliberate exclusion from a missing API. Every entry must carry a
    classification and a reason, and an identity cannot be both published and
    excluded: the two sections partition the identity space or "excluded" means
    nothing.
    """
    if not Path(config_path).exists():
        return []

    import yaml  # noqa: PLC0415 — optional dependency, only needed when configured

    document = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    declared = document.get("exclusions") or []

    exclusions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in declared:
        identity = (item or {}).get("apiIdentity")
        if not identity:
            raise ValueError("every exclusion requires an apiIdentity")
        classification = item.get("classification")
        reason = item.get("reason")
        if not classification or not reason:
            raise ValueError(
                f"exclusion {identity} requires both a classification and a reason",
            )
        if identity in seen:
            raise ValueError(f"duplicate exclusion for {identity}")
        if identity in published_identities:
            raise ValueError(f"{identity} is both published and excluded")
        seen.add(identity)
        exclusions.append(
            {
                "apiIdentity": identity,
                "classification": classification,
                "reason": reason,
            },
        )

    return sorted(exclusions, key=lambda entry: (entry["apiIdentity"], entry["classification"]))


def compile_catalog(openapi: dict[str, Any]) -> dict[str, Any]:
    """Transform an OpenAPI 3.0 spec dict into xcsh api-catalog.json format."""
    paths = openapi.get("paths", {})
    components = openapi.get("components")
    groups = group_paths_by_resource(paths)

    categories = []
    for category_name in sorted(groups.keys()):
        operations = _build_category_operations(groups[category_name], components)
        if operations:
            categories.append(
                {
                    "name": category_name,
                    "displayName": category_name.replace("-", " ").title(),
                    "operations": operations,
                },
            )

    _deduplicate_global_op_names(categories)

    authoritative = sorted(
        (
            operation.get("operationId") or f"{method.lower()}:{normalize_path_placeholders(path)}",
            method.upper(),
            normalize_path_placeholders(path),
        )
        for path, path_item in paths.items()
        if isinstance(path_item, dict)
        for method, operation in path_item.items()
        if method.lower() in _HTTP_METHODS and isinstance(operation, dict)
    )
    browsable = sorted(
        (operation["operationId"], operation["method"], operation["path"])
        for category in categories
        for operation in category["operations"]
    )
    if browsable != authoritative:
        missing = sorted(set(authoritative) - set(browsable))[:5]
        extra = sorted(set(browsable) - set(authoritative))[:5]
        raise ValueError(
            "browsable catalog inventory differs from authoritative OpenAPI inventory: "
            f"authoritative={len(authoritative)}, browsable={len(browsable)}, "
            f"missing={missing}, extra={extra}"
        )

    env_version = os.environ.get("CATALOG_VERSION", "")
    info_version = openapi.get("info", {}).get("version", "")
    tag_version = get_version_from_tags()
    version = env_version or info_version or (tag_version if tag_version != "0.0.0" else "1.0.0")

    # The operation inventory, keyed by the API's own ves.io.schema identity.
    # `categories` groups operations for human browsing; this groups them by the
    # identity a machine consumer keys on, and states each operation's exact
    # method, path and surface so nothing has to be inferred from a name.
    api_operations = build_api_operations(paths)
    published_identities = {entry["apiIdentity"] for entry in api_operations}
    api_exclusions = build_api_exclusions(DEFAULT_EXCLUSIONS_CONFIG, published_identities)
    # Identities displaced by a path collision are stated here rather than lost.
    api_exclusions = sorted(
        api_exclusions
        + collision_exclusions(openapi.get("x-f5xc-path-collisions") or [], published_identities),
        key=lambda entry: (entry["apiIdentity"], entry["classification"]),
    )

    return {
        "service": "f5xc",
        "displayName": "F5 Distributed Cloud",
        "version": version,
        "specSource": "f5-sales-demo/api-specs-enriched",
        "auth": F5XC_AUTH,
        "defaults": F5XC_DEFAULTS,
        "categories": categories,
        "apiOperations": api_operations,
        "apiExclusions": api_exclusions,
    }


def main() -> int:
    """CLI entry point: compile OpenAPI spec(s) into xcsh api-catalog.json."""
    parser = argparse.ArgumentParser(description="Compile F5XC OpenAPI spec to xcsh catalog JSON")
    parser.add_argument("--input", type=Path, default=None, help="Single OpenAPI spec input file")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Directory of OpenAPI spec files to merge",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output api-catalog.json path",
    )
    args = parser.parse_args()

    if args.input_dir:
        if not args.input_dir.is_dir():
            print(f"Error: input directory not found: {args.input_dir}", file=sys.stderr)
            return 1
        openapi = merge_spec_files(args.input_dir)
    elif args.input:
        if not args.input.exists():
            print(f"Error: input file not found: {args.input}", file=sys.stderr)
            return 1
        with args.input.open(encoding="utf-8") as f:
            openapi = json.load(f)
    else:
        if not DEFAULT_INPUT.exists():
            print(f"Error: default input not found: {DEFAULT_INPUT}", file=sys.stderr)
            return 1
        with DEFAULT_INPUT.open(encoding="utf-8") as f:
            openapi = json.load(f)

    catalog = sanitize_emails(compile_catalog(openapi))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)
        f.write("\n")

    total_ops = sum(len(c["operations"]) for c in catalog["categories"])
    n_cats = len(catalog["categories"])
    print(f"Compiled {total_ops} operations across {n_cats} categories -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
