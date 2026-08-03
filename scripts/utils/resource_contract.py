# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Build the exact downstream resource contract from the enriched OpenAPI graph.

Resource identity comes from ``x-ves-proto-message`` and ``operationId``.  Path
spelling and component-key prefixes are deliberately not guessed: F5 APIs contain
irregular plurals (for example ``alert_gen_policys``), service prefixes, and several
custom endpoints whose collection-looking paths are not Terraform resources.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any

_SCHEMA_IDENTITY = re.compile(
    r"^ves\.io\.schema\.(?P<identity>.+)\."
    r"(?P<kind>CreateSpecType|GetSpecType|ReplaceSpecType)$"
)
_RESOURCE_OPERATION = re.compile(
    r"^ves\.io\.schema\.(?P<identity>.+)\."
    r"(?P<surface>API|CustomAPI)\."
    r"(?P<action>Create|Get|List|Replace|Delete)$"
)
_CASCADE_DELETE_OPERATION = re.compile(
    r"^ves\.io\.schema\.(?P<identity>.+)\.CustomAPI\.CascadeDelete$"
)
_EXPECTED_METHOD = {
    "Create": "post",
    "Get": "get",
    "List": "get",
    "Replace": "put",
    "Delete": "delete",
}
_KIND_TO_OUTPUT = {
    "CreateSpecType": "create",
    "GetSpecType": "get",
    "ReplaceSpecType": "replace",
}
_ROLE_DEFINITIONS = {
    "Create": ("CreateSpecType", "create", "requestBody"),
    "Replace": ("ReplaceSpecType", "replace", "requestBody"),
    "Get/List": ("GetSpecType", "get", "responses"),
}
_EXPLICIT_ALTERNATE_SURFACE_IDENTITIES = {"user_group"}


class ResourceContractError(ValueError):
    """The enriched graph cannot produce one unambiguous resource contract."""


def _fail(message: str) -> ResourceContractError:
    return ResourceContractError(f"resource contract: {message}")


def _normalize_path(path: str) -> str:
    """Normalize equivalent metadata placeholders without changing path spelling."""
    return re.sub(r"\{[^}]*\.([^}]+)\}", r"{\1}", path)


def _resolve_local_pointer(document: dict[str, Any], ref: str) -> Any:
    """Resolve one RFC 6901 pointer against the complete OpenAPI document."""
    if not ref.startswith("#/"):
        raise _fail(f"non-local reference is not supported: {ref!r}")
    current: Any = document
    for encoded_token in ref[2:].split("/"):
        token = encoded_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
            continue
        if isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
            continue
        raise _fail(f"unresolved local reference: {ref!r}")
    return current


def _validate_local_references(openapi: dict[str, Any]) -> None:
    """Reject every malformed, external, or unresolved ref anywhere in the graph."""
    pending: list[Any] = [openapi]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if "$ref" in current:
                ref = current["$ref"]
                if not isinstance(ref, str):
                    raise _fail("OpenAPI $ref must be a string")
                _resolve_local_pointer(openapi, ref)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def validate_openapi_graph(
    openapi: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return a non-empty, structurally valid OpenAPI paths/components graph."""
    if not isinstance(openapi, dict):
        raise TypeError("OpenAPI document must be an object")
    paths = openapi.get("paths")
    components = openapi.get("components")
    if not isinstance(paths, dict):
        raise _fail("OpenAPI paths must be an object")
    if not paths:
        raise _fail("OpenAPI paths graph is empty")
    if not isinstance(components, dict):
        raise _fail("OpenAPI components must be an object")
    schemas = components.get("schemas")
    if not isinstance(schemas, dict):
        raise _fail("OpenAPI components.schemas must be an object")
    if not schemas:
        raise _fail("OpenAPI components.schemas graph is empty")
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            raise _fail("OpenAPI paths must map string keys to path-item objects")
    for schema_name, schema in schemas.items():
        if not isinstance(schema_name, str) or not isinstance(schema, dict):
            raise _fail("OpenAPI schemas must map string keys to schema objects")
    _validate_local_references(openapi)
    return paths, components, schemas


def _camel_to_snake(value: str) -> str:
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first).lower()


def _direct_refs(value: Any) -> set[str]:
    """Return local component-schema references found below ``value``."""
    refs: set[str] = set()
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            ref = current.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                refs.add(ref.rsplit("/", 1)[-1])
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return refs


def _reference_graph(schemas: dict[str, Any]) -> dict[str, set[str]]:
    return {name: _direct_refs(schema) for name, schema in schemas.items()}


def _reachable_schema_keys(
    operations: list[dict[str, Any]],
    graph: dict[str, set[str]],
    root_field: str,
) -> set[str]:
    """Return components reachable from one exact operation role's roots."""
    roots: set[str] = set()
    for record in operations:
        operation = record["operation"]
        roots.update(_direct_refs(operation.get(root_field, {})))

    reachable: set[str] = set()
    pending = deque(sorted(roots))
    while pending:
        name = pending.popleft()
        if name in reachable:
            continue
        reachable.add(name)
        pending.extend(sorted(graph.get(name, set()) - reachable))
    return reachable


def _schema_candidates(
    schemas: dict[str, Any],
) -> dict[str, dict[str, list[str]]]:
    candidates: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for key, schema in schemas.items():
        if not isinstance(schema, dict):
            continue
        proto_message = schema.get("x-ves-proto-message")
        if not isinstance(proto_message, str):
            continue
        match = _SCHEMA_IDENTITY.fullmatch(proto_message)
        if match:
            candidates[match.group("identity")][match.group("kind")].append(key)

    for kinds in candidates.values():
        for keys in kinds.values():
            keys.sort()
    return candidates


def _resource_operations(paths: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    operations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path, path_item in sorted(paths.items()):
        if not isinstance(path_item, dict):
            continue
        for method, operation in sorted(path_item.items()):
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str):
                continue
            match = _RESOURCE_OPERATION.fullmatch(operation_id)
            if not match:
                continue
            action = match.group("action")
            # A custom operation named exactly Get/List/Delete is a CRUD operation only
            # when the same API identity has a SpecType.  Longer names such as
            # GetDataplaneServers never match this expression and cannot become resources.
            operations[match.group("identity")].append(
                {
                    "action": action,
                    "method": method.lower(),
                    "operation": operation,
                    "path": _normalize_path(path),
                    "surface": match.group("surface"),
                }
            )
    return operations


def _cascade_delete_operations(paths: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    operations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path, path_item in sorted(paths.items()):
        for method, operation in sorted(path_item.items()):
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str):
                continue
            match = _CASCADE_DELETE_OPERATION.fullmatch(operation_id)
            if not match:
                continue
            operations[match.group("identity")].append(
                {
                    "action": "CascadeDelete",
                    "method": method.lower(),
                    "operation": operation,
                    "path": _normalize_path(path),
                    "surface": "CustomAPI",
                }
            )
    return operations


def _contract_operations(
    identity: str,
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return the exact primary surface and any formally excluded alternate surface.

    Registration exposes Create/Replace through ``API`` and Get/List/Delete through
    ``CustomAPI`` on the same paths.  Conversely, user_group has a separate mutable
    system-only CustomAPI beside a read-only tenant API.  Treating every exact custom
    verb as the same resource would merge those two contracts.
    """
    standard = [record for record in records if record["surface"] == "API"]
    custom = [record for record in records if record["surface"] == "CustomAPI"]
    if not standard:
        return custom, []

    standard_paths = {record["path"] for record in standard}
    matched = [record for record in custom if record["path"] in standard_paths]
    unmatched = [record for record in custom if record["path"] not in standard_paths]
    if unmatched and identity not in _EXPLICIT_ALTERNATE_SURFACE_IDENTITIES:
        raise _fail(f"{identity} has an unclassified alternate CustomAPI CRUD surface")
    return standard + matched, unmatched


def _choose_schema_key(
    identity: str,
    kind: str,
    candidates: list[str],
    reachable: set[str],
) -> str:
    referenced = sorted(set(candidates) & reachable)
    if len(referenced) == 1:
        return referenced[0]

    detail = ", ".join(candidates) if candidates else "none"
    raise _fail(
        f"{identity} has no unique {kind} schema key "
        f"(candidates: {detail}; reachable candidates: {', '.join(referenced) or 'none'})"
    )


def _one_operation(
    identity: str,
    role: str,
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if len(records) > 1:
        operation_ids = sorted(record["operation"]["operationId"] for record in records)
        raise _fail(f"{identity} has multiple {role} operations: {', '.join(operation_ids)}")
    return records[0] if records else None


def _direct_response_schema_key(operation: dict[str, Any]) -> str | None:
    """Return the one direct local schema ref used by successful JSON responses."""
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        return None

    response_keys: set[str] = set()
    for status, response in sorted(responses.items()):
        if not isinstance(status, str) or not status.startswith("2"):
            continue
        if not isinstance(response, dict):
            continue
        schema = response.get("content", {}).get("application/json", {}).get("schema")
        if not isinstance(schema, dict) or "$ref" not in schema:
            continue
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
            raise _fail("successful application/json response has no direct local schema ref")
        response_keys.add(ref.rsplit("/", 1)[-1])

    if len(response_keys) > 1:
        raise _fail(
            "successful application/json responses use multiple schemas: "
            + ", ".join(sorted(response_keys))
        )
    return next(iter(response_keys), None)


def _operation_descriptor(record: dict[str, Any], schemas: dict[str, Any]) -> dict[str, Any]:
    operation_id = record["operation"].get("operationId")
    if not isinstance(operation_id, str):
        raise _fail("resource operation has no operationId")
    descriptor = {
        "method": record["method"].upper(),
        "path": record["path"],
        "operationId": operation_id,
        "surface": record["surface"],
    }
    response_key = _direct_response_schema_key(record["operation"])
    if response_key is not None:
        if response_key not in schemas:
            raise _fail(f"{operation_id} response schema {response_key!r} does not exist")
        descriptor["responseSchema"] = response_key
    return descriptor


def _list_collection_contract(
    record: dict[str, Any],
    schemas: dict[str, Any],
    expected_item_schema: str | None,
) -> dict[str, str]:
    """Describe a list-only collection without making the consumer rediscover it."""
    operation_id = record["operation"].get("operationId", "List")
    response_key = _direct_response_schema_key(record["operation"])
    if response_key is None:
        raise _fail(f"{operation_id} has no exact response schema")
    response_schema = schemas.get(response_key)
    if not isinstance(response_schema, dict):
        raise _fail(f"{operation_id} response schema {response_key!r} does not exist")
    properties = response_schema.get("properties")
    if not isinstance(properties, dict):
        raise _fail(f"{operation_id} response schema has no collection properties")

    matches: list[tuple[str, str]] = []
    for field_name, field_schema in sorted(properties.items()):
        if not isinstance(field_schema, dict) or field_schema.get("type") != "array":
            continue
        items = field_schema.get("items")
        ref = items.get("$ref") if isinstance(items, dict) else None
        if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
            continue
        item_key = ref.rsplit("/", 1)[-1]
        if item_key == expected_item_schema:
            matches.append((field_name, item_key))

    if len(matches) != 1:
        raise _fail(
            f"{operation_id} has no unique collection field for item schema "
            f"{expected_item_schema!r}"
        )
    collection_field, item_schema = matches[0]
    return {
        "collectionField": collection_field,
        "itemSchema": item_schema,
    }


def _nonstandard_request_contract(
    record: dict[str, Any],
    schemas: dict[str, Any],
    openapi: dict[str, Any],
) -> dict[str, Any]:
    request_key = _request_schema_key(record["operation"], openapi)
    request_schema = schemas.get(request_key) if request_key else None
    if not isinstance(request_schema, dict):
        raise _fail(f"{record['action']} has no exact request schema")
    properties = request_schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise _fail(f"{record['action']} request schema has no request fields")
    path_parameters = set(re.findall(r"\{([^}]+)\}", record["path"]))
    request_body: dict[str, dict[str, str]] = {}
    for field_name in sorted(properties):
        if field_name not in path_parameters:
            raise _fail(
                f"{record['action']} request field {field_name!r} has no deterministic source"
            )
        request_body[field_name] = {
            "source": "path",
            "parameter": field_name,
        }
    return {
        "requestSchema": request_key,
        "requestBody": request_body,
    }


def _lifecycle_classification(
    identity: str,
    operations: dict[str, dict[str, Any] | None],
) -> str:
    create = operations["create"]
    read = operations["read"]
    update = operations["update"]
    delete = operations["delete"]
    if any((create, update, delete)) and read is None:
        raise _fail(f"{identity} has mutable operations but no item Get operation")
    if create is not None and read is not None and delete is not None:
        return "managed"
    if create is not None and read is not None and delete is None:
        return "non_deletable"
    if create is None and update is not None and read is not None and delete is None:
        return "replace_only"
    if create is None and update is None and delete is None:
        return "read_only"
    present = ", ".join(name for name, value in operations.items() if value is not None)
    raise _fail(f"{identity} has unsupported lifecycle operation set: {present}")


def _operation_parts(operation_id: str) -> tuple[str, str, str]:
    """Return exact identity, API surface, and operation from an operationId."""
    parts = operation_id.split(".")
    if len(parts) < 6 or parts[:3] != ["ves", "io", "schema"]:
        raise _fail(f"operationId is not a fully-qualified F5 schema operation: {operation_id!r}")
    surface = parts[-2]
    if surface not in {"API", "CustomAPI"}:
        raise _fail(f"operationId has unsupported API surface: {operation_id!r}")
    return ".".join(parts[3:-2]), surface, parts[-1]


def _request_schema_key(
    operation: dict[str, Any],
    openapi: dict[str, Any],
) -> str | None:
    """Return the direct schema key after resolving an exact request-body reference."""
    request_body = operation.get("requestBody")
    if request_body is None:
        return None
    if not isinstance(request_body, dict):
        raise _fail("operation requestBody must be an object")

    seen_refs: set[str] = set()
    while "$ref" in request_body:
        if set(request_body) != {"$ref"}:
            raise _fail("requestBody reference must not have sibling fields")
        ref = request_body["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/components/requestBodies/"):
            raise _fail(f"requestBody has unsupported reference: {ref!r}")
        if ref in seen_refs:
            raise _fail(f"cyclic requestBody reference: {ref!r}")
        seen_refs.add(ref)
        request_body = _resolve_local_pointer(openapi, ref)
        if not isinstance(request_body, dict):
            raise _fail(f"requestBody reference does not resolve to an object: {ref!r}")

    content = request_body.get("content")
    media_type = content.get("application/json") if isinstance(content, dict) else None
    schema = media_type.get("schema") if isinstance(media_type, dict) else None
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
        return None
    return ref.rsplit("/", 1)[-1]


def _action_contracts(
    openapi: dict[str, Any],
    paths: dict[str, Any],
    schemas: dict[str, Any],
    crud_by_identity: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen_names = {contract["name"] for contract in crud_by_identity.values()}
    annotated_schemas: dict[str, str] = {}
    for schema_key, schema in sorted(schemas.items()):
        if "x-f5xc-action" not in schema:
            continue
        action = schema["x-f5xc-action"]
        if not isinstance(action, str) or not action:
            raise _fail(f"action schema {schema_key!r} has an invalid x-f5xc-action")
        annotated_schemas[schema_key] = action
    bound_schemas: set[str] = set()

    for path, path_item in sorted(paths.items()):
        if not isinstance(path_item, dict):
            continue
        for method, operation in sorted(path_item.items()):
            if not isinstance(operation, dict):
                continue
            request_key = _request_schema_key(operation, openapi)
            if request_key is None:
                continue
            request_schema = schemas.get(request_key)
            if not isinstance(request_schema, dict):
                continue
            action = annotated_schemas.get(request_key)
            if action is None:
                continue
            bound_schemas.add(request_key)
            if method.lower() != "post":
                raise _fail(f"action {request_key} uses {method.upper()}, expected POST at {path}")

            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str):
                raise _fail(f"action {request_key} has no operationId at {path}")
            identity, surface, _ = _operation_parts(operation_id)
            crud = crud_by_identity.get(identity)
            if crud is None:
                raise _fail(f"action {request_key} has no exact sibling CRUD identity {identity}")

            read_schema = crud["schemaKeys"]["get"]
            read_operation = crud["operations"]["read"]
            if read_schema is None or read_operation is None:
                raise _fail(f"action {request_key} has no exact sibling read schema/operation")

            normalized_path = _normalize_path(path)
            if normalized_path.rstrip("/").rsplit("/", 1)[-1] != action:
                raise _fail(
                    f"action annotation {action!r} does not match path suffix at {normalized_path}"
                )

            proto_message = request_schema.get("x-ves-proto-message")
            proto_identity, separator, proto_leaf = (
                proto_message.rpartition(".") if isinstance(proto_message, str) else ("", "", "")
            )
            expected_proto_identity = f"ves.io.schema.{identity}"
            if (
                not separator
                or proto_identity != expected_proto_identity
                or not proto_leaf.endswith("Req")
            ):
                raise _fail(
                    f"action {request_key} proto message does not match sibling identity "
                    f"{expected_proto_identity}"
                )
            action_kind = proto_leaf.removesuffix("Req")
            if not action_kind:
                raise _fail(f"action {request_key} has an empty proto message action identity")
            name = f"{crud['name']}_{_camel_to_snake(action_kind)}"
            if name in seen_names:
                raise _fail(f"duplicate resource name {name!r}")
            seen_names.add(name)
            action_record = {
                "action": action_kind,
                "method": method.lower(),
                "operation": operation,
                "path": normalized_path,
                "surface": surface,
            }
            actions.append(
                {
                    "name": name,
                    "kind": "action",
                    "apiIdentity": f"ves.io.schema.{identity}",
                    "action": action,
                    "schemaKeys": {
                        "request": request_key,
                        "read": read_schema,
                    },
                    "operations": {
                        "action": _operation_descriptor(action_record, schemas),
                        "read": read_operation,
                    },
                }
            )
    unbound_schemas = sorted(set(annotated_schemas) - bound_schemas)
    if unbound_schemas:
        raise _fail(
            "annotated action schemas are not bound to exact operations: "
            + ", ".join(unbound_schemas)
        )
    return actions


def _excluded_operation_contract(
    identity: str,
    classification: str,
    reason: str,
    records: list[dict[str, Any]],
    schemas: dict[str, Any],
) -> dict[str, Any]:
    return {
        "apiIdentity": f"ves.io.schema.{identity}",
        "classification": classification,
        "reason": reason,
        "operations": sorted(
            (_operation_descriptor(record, schemas) for record in records),
            key=lambda operation: operation["operationId"],
        ),
    }


def build_resource_catalog(openapi: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Build deterministic resources and formal exclusions from the exact graph."""
    paths, _, schemas = validate_openapi_graph(openapi)
    candidates_by_identity = _schema_candidates(schemas)
    operations_by_identity = _resource_operations(paths)
    cascade_delete_by_identity = _cascade_delete_operations(paths)
    graph = _reference_graph(schemas)
    exclusions: list[dict[str, Any]] = []

    for identity in sorted(set(operations_by_identity) - set(candidates_by_identity)):
        records = operations_by_identity[identity]
        if any(record["surface"] == "API" for record in records):
            raise _fail(f"standard API CRUD identity has no SpecType schemas: {identity}")
        exclusions.append(
            _excluded_operation_contract(
                identity,
                "custom_api_without_spec_type",
                "CustomAPI CRUD-looking operations have no exact SpecType schema identity.",
                records,
                schemas,
            )
        )

    for identity in sorted(set(candidates_by_identity) - set(operations_by_identity)):
        kinds = candidates_by_identity[identity]
        exclusions.append(
            {
                "apiIdentity": f"ves.io.schema.{identity}",
                "classification": "spec_type_without_crud_operations",
                "reason": "SpecType schemas have no exact API or CustomAPI CRUD operations.",
                "schemaKeys": {
                    output_name: kinds.get(kind, [])
                    for kind, output_name in _KIND_TO_OUTPUT.items()
                },
            }
        )

    identities = sorted(set(candidates_by_identity) & set(operations_by_identity))
    leaf_owners: dict[str, str] = {}
    contracts: list[dict[str, Any]] = []

    for identity in identities:
        resource_name = identity.rsplit(".", 1)[-1]
        previous = leaf_owners.get(resource_name)
        if previous is not None and previous != identity:
            raise _fail(
                f"resource name {resource_name!r} is shared by {previous!r} and {identity!r}"
            )
        leaf_owners[resource_name] = identity

        records, alternate_records = _contract_operations(
            identity, operations_by_identity[identity]
        )
        if alternate_records:
            exclusions.append(
                _excluded_operation_contract(
                    identity,
                    "alternate_custom_api_surface",
                    "CustomAPI CRUD operations use paths outside the primary API resource surface.",
                    alternate_records,
                    schemas,
                )
            )
        if not records:
            raise _fail(f"{identity} has no primary CRUD operations")
        for record in records:
            expected = _EXPECTED_METHOD[record["action"]]
            if record["method"] != expected:
                raise _fail(
                    f"{identity} {record['action']} uses {record['method'].upper()}, "
                    f"expected {expected.upper()} at {record['path']}"
                )

        role_records = {
            "create": [record for record in records if record["action"] == "Create"],
            "read": [record for record in records if record["action"] == "Get"],
            "list": [record for record in records if record["action"] == "List"],
            "update": [record for record in records if record["action"] == "Replace"],
            "delete": [record for record in records if record["action"] == "Delete"],
        }
        selected_records = {
            role: _one_operation(identity, role, matching_records)
            for role, matching_records in role_records.items()
        }

        cascade_records = cascade_delete_by_identity.get(identity, [])
        cascade_record = _one_operation(identity, "cascade delete", cascade_records)
        if cascade_record is not None:
            if selected_records["delete"] is not None:
                raise _fail(f"{identity} has both Delete and CascadeDelete operations")
            if cascade_record["method"] != "post":
                raise _fail(
                    f"{identity} CascadeDelete uses {cascade_record['method'].upper()}, expected POST"
                )
            selected_records["delete"] = cascade_record

        kinds = candidates_by_identity[identity]
        schema_keys: dict[str, str | None] = {
            "create": None,
            "get": None,
            "replace": None,
        }
        role_inputs = {
            "Create": role_records["create"],
            "Replace": role_records["update"],
            "Get/List": role_records["read"] + role_records["list"],
        }
        for role, (kind, output_name, root_field) in _ROLE_DEFINITIONS.items():
            matching_records = role_inputs[role]
            if not matching_records:
                continue
            reachable = _reachable_schema_keys(matching_records, graph, root_field)
            schema_keys[output_name] = _choose_schema_key(
                identity,
                kind,
                kinds.get(kind, []),
                reachable,
            )

        operation_descriptors = {
            role: _operation_descriptor(record, schemas) if record is not None else None
            for role, record in selected_records.items()
        }
        if selected_records["read"] is None and selected_records["list"] is not None:
            list_descriptor = operation_descriptors["list"]
            if list_descriptor is None:
                raise _fail(f"{identity} list descriptor was not built")
            list_descriptor.update(
                _list_collection_contract(
                    selected_records["list"],
                    schemas,
                    schema_keys["get"],
                )
            )
        if cascade_record is not None:
            delete_descriptor = operation_descriptors["delete"]
            if delete_descriptor is None:
                raise _fail(f"{identity} CascadeDelete descriptor was not built")
            delete_descriptor.update(
                _nonstandard_request_contract(cascade_record, schemas, openapi)
            )
        contracts.append(
            {
                "name": resource_name,
                "kind": "crud",
                "apiIdentity": f"ves.io.schema.{identity}",
                "schemaKeys": schema_keys,
                "manageability": _lifecycle_classification(identity, selected_records),
                "operations": operation_descriptors,
            }
        )

    crud_by_identity = {
        contract["apiIdentity"].removeprefix("ves.io.schema."): contract for contract in contracts
    }
    contracts.extend(_action_contracts(openapi, paths, schemas, crud_by_identity))
    return {
        "resources": sorted(contracts, key=lambda contract: (contract["name"], contract["kind"])),
        "resourceExclusions": sorted(
            exclusions,
            key=lambda exclusion: (exclusion["apiIdentity"], exclusion["classification"]),
        ),
    }
