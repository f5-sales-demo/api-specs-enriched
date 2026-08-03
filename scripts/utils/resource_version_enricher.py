# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Declare the measured optimistic-concurrency token on exact API shapes."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

PROPERTY_NAME = "resource_version"
GET_RESPONSE_SUFFIX = "GetResponse"
REPLACE_REQUEST_SUFFIX = "ReplaceRequest"

_PROPERTY_SCHEMA: dict[str, Any] = {
    "type": "string",
    "title": PROPERTY_NAME,
    "description": (
        "Opaque token identifying this version of the object. Send the token returned "
        "by a read on a replace to make the write conditional. If the object changed "
        "after it was read, the API rejects the replace with 409 "
        "RESOURCE_VERSION_MISMATCH instead of overwriting the newer object. Omit the "
        "token only when an unconditional replace is intentional."
    ),
    "x-displayname": "Resource Version",
    "x-f5xc-description-short": "Optimistic-concurrency token for conditional replace.",
    "x-f5xc-example": "opaque-version-token",
}


class ResourceVersionContractError(ValueError):
    """A source graph cannot receive the measured concurrency declaration safely."""


@dataclass(frozen=True)
class ResourceVersionAccounting:
    """Exact source-stage declaration counts."""

    get_responses: int
    replace_requests: int

    @property
    def total(self) -> int:
        """Return the total declared shapes."""
        return self.get_responses + self.replace_requests

    def to_dict(self) -> dict[str, int]:
        """Return deterministic report data including the derived total."""
        values = asdict(self)
        values["total"] = self.total
        return values


def _schemas(source_id: str, document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or not isinstance(document.get("openapi"), str):
        raise ResourceVersionContractError(
            f"{source_id}: OpenAPI 3 components.schemas must be an object"
        )
    components = document.get("components")
    if not isinstance(components, dict) or not isinstance(components.get("schemas"), dict):
        raise ResourceVersionContractError(
            f"{source_id}: OpenAPI 3 components.schemas must be an object"
        )
    return components["schemas"]


def _validate_target(source_id: str, name: str, schema: Any) -> None:
    location = f"{source_id}: {name}"
    if not isinstance(schema, dict):
        raise ResourceVersionContractError(f"{location} must be a schema object")
    if schema.get("type") != "object":
        raise ResourceVersionContractError(f"{location} type must be 'object'")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ResourceVersionContractError(f"{location} properties must be an object")
    if PROPERTY_NAME in properties:
        raise ResourceVersionContractError(f"{location} already declares resource_version")
    if "required" in schema and not isinstance(schema["required"], list):
        raise ResourceVersionContractError(f"{location} required must be an array")
    if PROPERTY_NAME in schema.get("required", []):
        raise ResourceVersionContractError(f"{location} makes resource_version required")


def declare_resource_versions(
    documents: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], ResourceVersionAccounting]:
    """Return copied documents with the exact measured declaration applied atomically.

    Only ``*GetResponse`` and ``*ReplaceRequest`` are measured. Swagger 2, malformed
    shapes, fabricated ``properties`` maps, and a newly upstream-owned declaration all
    fail the build so a contract change is reviewed instead of silently normalized.
    """
    if not isinstance(documents, Mapping) or not documents:
        raise ResourceVersionContractError("documents must be a non-empty mapping")

    targets: list[tuple[str, str, str]] = []
    for source_id in sorted(documents):
        if not isinstance(source_id, str) or not source_id:
            raise ResourceVersionContractError("source identifiers must be non-empty strings")
        schemas = _schemas(source_id, documents[source_id])
        for name in sorted(schemas):
            if not isinstance(name, str):
                raise ResourceVersionContractError(f"{source_id}: schema names must be strings")
            suffix = (
                GET_RESPONSE_SUFFIX
                if name.endswith(GET_RESPONSE_SUFFIX)
                else REPLACE_REQUEST_SUFFIX
                if name.endswith(REPLACE_REQUEST_SUFFIX)
                else None
            )
            if suffix is None:
                continue
            _validate_target(source_id, name, schemas[name])
            targets.append((source_id, name, suffix))

    declared = copy.deepcopy(dict(documents))
    get_responses = 0
    replace_requests = 0
    for source_id, name, suffix in targets:
        declared[source_id]["components"]["schemas"][name]["properties"][PROPERTY_NAME] = (
            copy.deepcopy(_PROPERTY_SCHEMA)
        )
        if suffix == GET_RESPONSE_SUFFIX:
            get_responses += 1
        else:
            replace_requests += 1

    return declared, ResourceVersionAccounting(get_responses, replace_requests)
