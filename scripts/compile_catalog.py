#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Catalog Compiler — transforms F5XC OpenAPI specs into xcsh api-catalog.json format.

Usage:
    python -m scripts.compile_catalog --version 2.1.208
    python -m scripts.compile_catalog --version 2.1.208 --input path/to/spec.json
    python -m scripts.compile_catalog --version 2.1.208 --output release/api-catalog.json
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from scripts.utils.extension_constants import X_F5XC_SERVER_DEFAULT_VALUE
from scripts.utils.resource_contract import build_resource_catalog, validate_openapi_graph

CANONICAL_INPUT = Path("docs/specifications/api/openapi.json")
DEFAULT_OUTPUT = Path("release/api-catalog.json")


def semantic_version(value: str) -> str:
    """Validate an explicit semantic build version for argparse."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise argparse.ArgumentTypeError(f"not a semantic version: {value!r}")
    return value


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


def _snake_identifier(value: str) -> str:
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first).replace("-", "_").lower()


def generate_operation_name(operation: dict[str, Any]) -> str:
    """Generate a stable operation name from its authoritative operationId.

    Path singularization is forbidden here. It produced fabricated identities by
    removing the trailing character from words such as ``status`` and could collapse
    a CustomAPI endpoint into an unrelated resource. The fully-qualified operationId
    is already unique and exact.
    """
    operation_id = operation.get("operationId")
    if not isinstance(operation_id, str):
        raise TypeError("catalog operation has no operationId")
    parts = operation_id.split(".")
    if len(parts) < 6 or parts[:3] != ["ves", "io", "schema"]:
        raise ValueError(f"catalog operationId is not fully qualified: {operation_id!r}")

    identity = [_snake_identifier(part) for part in parts[3:-2]]
    surface = _snake_identifier(parts[-2])
    action = _snake_identifier(parts[-1])
    return "_".join([action, *identity, surface])


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

        schema = _resolve_schema_ref(schema, components)

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
                    resolved_prop = _resolve_schema_ref(prop_schema, components)
                    if "type" in resolved_prop:
                        simplified["properties"][prop_name] = {"type": resolved_prop["type"]}
        if "required" in schema and isinstance(schema["required"], list):
            simplified["required"] = schema["required"]
        if simplified:
            return simplified
    return None


def group_paths_by_resource(paths: dict[str, Any]) -> dict[str, list[tuple[str, str, dict]]]:
    """Group (path, method, operation) tuples by category name."""
    groups: dict[str, list[tuple[str, str, dict]]] = {}
    for path, path_item in sorted(paths.items()):
        if not isinstance(path_item, dict):
            continue
        category = extract_category_name(path)
        if category not in groups:
            groups[category] = []
        for method, operation in path_item.items():
            if method.lower() in ("get", "post", "put", "patch", "delete", "options"):
                groups[category].append((path, method.upper(), operation or {}))
    return groups


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
    return _resolve_schema_ref(body_schema, components)


_REFERENCE_WRAPPER_ASSERTIONS = frozenset(
    {
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maximum",
        "maxItems",
        "maxLength",
        "maxProperties",
        "minimum",
        "minItems",
        "minLength",
        "minProperties",
        "multipleOf",
        "pattern",
        "uniqueItems",
    },
)

_REFERENCE_WRAPPER_ANNOTATIONS = _REFERENCE_WRAPPER_ASSERTIONS | frozenset(
    {
        "default",
        "deprecated",
        "description",
        "example",
        "readOnly",
        "title",
        "writeOnly",
    }
)


def _reference_wrapper_annotations(
    schema: dict[str, Any],
    reference_key: str,
) -> dict[str, Any]:
    """Return supported wrapper annotations and reject structural siblings."""
    siblings = {key: value for key, value in schema.items() if key != reference_key}
    unsupported = sorted(
        key
        for key in siblings
        if key not in _REFERENCE_WRAPPER_ANNOTATIONS and not key.startswith("x-")
    )
    if unsupported:
        raise ValueError(
            "schema reference wrapper has unsupported structural sibling(s): "
            + ", ".join(unsupported)
        )
    return siblings


def _resolve_schema_ref(
    schema: dict[str, Any],
    components: dict[str, Any] | None,
    *,
    _visited_refs: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Resolve deterministic local reference wrappers to their terminal schema.

    The enriched OpenAPI graph represents many request ``spec`` properties as a
    single-reference ``allOf`` wrapper.  Those wrappers are equivalent to a direct
    local ``$ref`` for catalog projection.  Composed or malformed ``allOf`` values
    are not deterministic projections, so fail instead of silently dropping fields.
    """
    has_ref = "$ref" in schema
    has_all_of = "allOf" in schema
    if has_ref and has_all_of:
        raise ValueError("schema reference wrapper cannot contain both $ref and allOf")
    if not has_ref and not has_all_of:
        return schema

    if has_all_of:
        all_of = schema["allOf"]
        if not isinstance(all_of, list) or len(all_of) != 1:
            raise ValueError("schema allOf reference wrapper must contain exactly one reference")
        ref_wrapper = all_of[0]
        if not isinstance(ref_wrapper, dict) or set(ref_wrapper) != {"$ref"}:
            raise ValueError("schema allOf reference wrapper must contain only a $ref")
        ref = ref_wrapper["$ref"]
        annotations = _reference_wrapper_annotations(schema, "allOf")
    else:
        ref = schema["$ref"]
        annotations = _reference_wrapper_annotations(schema, "$ref")

    if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
        raise ValueError(f"schema has unsupported reference: {ref!r}")
    if not isinstance(components, dict):
        raise TypeError(f"cannot resolve schema reference without components: {ref!r}")
    schemas = components.get("schemas")
    if not isinstance(schemas, dict):
        raise TypeError(f"cannot resolve schema reference without components.schemas: {ref!r}")
    ref_key = ref.rsplit("/", 1)[-1]
    if ref_key not in schemas or not isinstance(schemas[ref_key], dict):
        raise ValueError(f"unresolved schema reference: {ref!r}")

    visited_refs = _visited_refs or frozenset()
    if ref in visited_refs:
        chain = " -> ".join((*sorted(visited_refs), ref))
        raise ValueError(f"cyclic schema reference: {chain}")
    resolved = _resolve_schema_ref(
        schemas[ref_key],
        components,
        _visited_refs=visited_refs | {ref},
    )
    conflicting_assertions = sorted(
        key
        for key, value in annotations.items()
        if key in _REFERENCE_WRAPPER_ASSERTIONS and key in resolved and resolved[key] != value
    )
    if conflicting_assertions:
        raise ValueError(
            "schema reference wrapper has conflicting assertion(s): "
            + ", ".join(conflicting_assertions)
        )
    return {**resolved, **annotations} if annotations else resolved


_ENRICHMENT_KEYS = frozenset(
    {
        "x-f5xc-constraints",
        "x-f5xc-required-for",
        "x-f5xc-server-default",
        X_F5XC_SERVER_DEFAULT_VALUE,
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


def _extract_field_metadata(
    schema: dict[str, Any],
    components: dict[str, Any] | None,
    *,
    prefix: str = "",
    depth: int = 0,
    max_depth: int = 3,
    visited: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Walk schema properties to max_depth, resolving $refs, extracting x-f5xc-* metadata."""
    if visited is None:
        visited = set()

    resolved = _resolve_schema_ref(schema, components)

    # Cycle detection
    ref = schema.get("$ref", "")
    if ref:
        if ref in visited:
            return {}
        visited = visited | {ref}
        resolved = _resolve_schema_ref(schema, components)

    if depth >= max_depth:
        return {}

    properties = resolved.get("properties")
    if not properties:
        return {}

    result: dict[str, dict[str, Any]] = {}

    for prop_name, prop_schema in properties.items():
        prop_resolved = _resolve_schema_ref(prop_schema, components)
        field_path = f"{prefix}.{prop_name}" if prefix else prop_name

        if prop_schema is not prop_resolved:
            inline_keys = _ENRICHMENT_KEYS | {"default"}
            inline_extensions = {k: prop_schema[k] for k in inline_keys if k in prop_schema}
            if inline_extensions:
                prop_resolved = {**prop_resolved, **inline_extensions}

        has_enrichment = any(k in prop_resolved for k in _ENRICHMENT_KEYS)

        if has_enrichment:
            entry: dict[str, Any] = {
                "type": prop_resolved.get("type", "object"),
            }
            desc = prop_resolved.get("x-f5xc-description") or prop_resolved.get("description")
            if desc:
                entry["description"] = desc

            constraints = prop_resolved.get("x-f5xc-constraints")
            if constraints:
                entry["constraints"] = constraints

            required_for = prop_resolved.get("x-f5xc-required-for")
            if required_for:
                entry["required_for"] = required_for

            server_default = prop_resolved.get("x-f5xc-server-default")
            if server_default is not None and not isinstance(server_default, bool):
                raise TypeError(f"{field_path} has a non-boolean x-f5xc-server-default")
            if server_default is True:
                entry["serverDefault"] = True

            has_default = "default" in prop_resolved
            has_typed_default = X_F5XC_SERVER_DEFAULT_VALUE in prop_resolved
            if has_default and has_typed_default:
                raise ValueError(
                    f"{field_path} declares both default and {X_F5XC_SERVER_DEFAULT_VALUE}"
                )
            if has_typed_default:
                typed_default = prop_resolved[X_F5XC_SERVER_DEFAULT_VALUE]
                if typed_default != {"type": "null", "value": None}:
                    raise ValueError(f"{field_path} has malformed {X_F5XC_SERVER_DEFAULT_VALUE}")
                entry["default"] = None
            elif has_default:
                entry["default"] = prop_resolved["default"]

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

        # Recurse into nested objects
        if prop_resolved.get("type") == "object" or prop_resolved.get("properties"):
            nested = _extract_field_metadata(
                prop_resolved,
                components,
                prefix=field_path,
                depth=depth + 1,
                max_depth=max_depth,
                visited=visited,
            )
            result.update(nested)

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
    """Walk schemas reachable via $ref, collecting x-f5xc-recommended-oneof-variant entries."""
    if visited is None:
        visited = set()

    ref = schema.get("$ref", "")
    if ref:
        if ref in visited:
            return {}
        visited = visited | {ref}

    resolved = _resolve_schema_ref(schema, components)

    if depth > max_depth:
        return {}

    result: dict[str, str] = {}

    oneof_map = resolved.get("x-f5xc-recommended-oneof-variant")
    if isinstance(oneof_map, dict):
        for group_name, variant in oneof_map.items():
            key = f"{prefix}.{group_name}" if prefix else group_name
            result[key] = variant

    properties = resolved.get("properties")
    if properties:
        for prop_name, prop_schema in properties.items():
            prop_path = f"{prefix}.{prop_name}" if prefix else prop_name
            nested = _collect_oneof_recommendations(
                prop_schema,
                components,
                prefix=prop_path,
                depth=depth + 1,
                max_depth=max_depth,
                visited=visited,
            )
            result.update(nested)

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
    """Walk schemas collecting full oneOf variant lists from x-ves-oneof-field-* extensions.

    Returns {group_name: [variant1, variant2, ...]} for every oneOf group found.
    """
    if visited is None:
        visited = set()

    ref = schema.get("$ref", "")
    if ref:
        if ref in visited:
            return {}
        visited = visited | {ref}

    resolved = _resolve_schema_ref(schema, components)

    if depth > max_depth:
        return {}

    result: dict[str, list[str]] = {}

    # Collect x-ves-oneof-field-* extensions from this schema.
    # Values may be JSON-encoded strings (e.g. '["a","b"]') or native lists.
    for key, val in resolved.items():
        if not key.startswith("x-ves-oneof-field-"):
            continue
        group_name = key[len("x-ves-oneof-field-") :]
        full_key = f"{prefix}.{group_name}" if prefix else group_name
        if isinstance(val, list):
            result[full_key] = val
        elif isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    result[full_key] = parsed
            except (json.JSONDecodeError, ValueError):
                pass

    # Recurse into properties
    properties = resolved.get("properties")
    if properties:
        for prop_name, prop_schema in properties.items():
            prop_path = f"{prefix}.{prop_name}" if prefix else prop_name
            nested = _collect_oneof_variants(
                prop_schema,
                components,
                prefix=prop_path,
                depth=depth + 1,
                max_depth=max_depth,
                visited=visited,
            )
            result.update(nested)

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
        "description": (
            operation.get("summary") or operation.get("description") or f"{method} {path}"
        ),
        "method": method,
        "path": normalized_path,
        "dangerLevel": assign_danger_level(method),
        "parameters": extract_parameters(path, operation),
    }
    body_schema = _resolve_body_schema(operation, components)
    if body_schema:
        op["bodySchema"] = body_schema
    response_schema = extract_response_schema(operation, components)
    if response_schema:
        op["responseSchema"] = response_schema

    # Extract minimumPayload from x-f5xc-minimum-configuration
    if body_schema and method.upper() in {"POST", "PUT", "PATCH"}:
        min_config = body_schema.get("x-f5xc-minimum-configuration")
        if min_config is not None and not isinstance(min_config, dict):
            raise TypeError(f"{op_name} has non-object x-f5xc-minimum-configuration")
        if min_config and min_config.get("example_json"):
            try:
                parsed_json = json.loads(min_config["example_json"])
            except (json.JSONDecodeError, TypeError) as error:
                raise ValueError(
                    f"{op_name} has malformed minimum-configuration example_json"
                ) from error
            op["minimumPayload"] = {
                "json": parsed_json,
                "requiredFields": min_config.get("required_fields", []),
                "description": min_config.get("description", ""),
            }

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
        op_name = generate_operation_name(operation)
        if op_name in seen_op_names:
            raise ValueError(f"duplicate catalog operation name {op_name!r}")
        seen_op_names.add(op_name)
        operations.append(_build_operation(path, method, operation, op_name, components))
    return operations


def _validate_global_op_names(categories: list[dict[str, Any]]) -> None:
    """Reject duplicate exact operation identities instead of path-derived suffixing."""
    global_seen: dict[str, str] = {}
    for cat in categories:
        for op in cat["operations"]:
            if op["name"] in global_seen:
                previous = global_seen[op["name"]]
                raise ValueError(
                    f"duplicate catalog operation name {op['name']!r} "
                    f"in categories {previous!r} and {cat['name']!r}"
                )
            global_seen[op["name"]] = cat["name"]


def compile_catalog(openapi: dict[str, Any], version: str) -> dict[str, Any]:
    """Transform an OpenAPI 3.0 spec dict into xcsh api-catalog.json format."""
    paths, components, _ = validate_openapi_graph(openapi)
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

    _validate_global_op_names(categories)

    resource_catalog = build_resource_catalog(openapi)
    return {
        "service": "f5xc",
        "displayName": "F5 Distributed Cloud",
        "version": version,
        "specSource": "f5-sales-demo/api-specs-enriched",
        "auth": F5XC_AUTH,
        "defaults": F5XC_DEFAULTS,
        **resource_catalog,
        "categories": categories,
    }


def write_catalog(catalog: dict[str, Any], destination: Path) -> None:
    """Write canonical catalog bytes shared by builds and release recovery."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as stream:
        json.dump(catalog, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def main() -> int:
    """CLI entry point: compile one canonical OpenAPI graph into api-catalog.json."""
    parser = argparse.ArgumentParser(description="Compile F5XC OpenAPI spec to xcsh catalog JSON")
    parser.add_argument(
        "--version",
        required=True,
        type=semantic_version,
        help="Explicit semantic build version for the catalog",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=CANONICAL_INPUT,
        help=f"Canonical OpenAPI input file (default: {CANONICAL_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output api-catalog.json path",
    )
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        return 1
    with args.input.open(encoding="utf-8") as f:
        openapi = json.load(f)

    catalog = compile_catalog(openapi, args.version)

    write_catalog(catalog, args.output)

    total_ops = sum(len(c["operations"]) for c in catalog["categories"])
    n_cats = len(catalog["categories"])
    print(f"Compiled {total_ops} operations across {n_cats} categories -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
