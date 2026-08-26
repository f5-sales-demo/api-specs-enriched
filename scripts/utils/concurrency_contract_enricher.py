"""Derive and enforce optimistic-concurrency contracts for config objects."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from .extension_constants import X_F5XC_CONCURRENCY_TOKEN

DEFAULT_CONFIG = Path(__file__).parents[2] / "config" / "concurrency_exclusions.yaml"
TOKEN_PROPERTY = "resource_version"  # noqa: S105 -- API field name, not a credential
TOKEN_SCHEMA = {
    "type": "string",
    X_F5XC_CONCURRENCY_TOKEN: {
        "server_assigned": True,
        "echo_on_operations": ["replace"],
    },
}


class ConcurrencyContractError(ValueError):
    """Raised when a configuration-object concurrency contract is incomplete."""


def _schema_ref(operation: dict[str, Any], *, response: bool) -> str:
    if response:
        schemas = [
            operation.get("responses", {})
            .get(code, {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
            for code in ("200", "201")
        ]
    else:
        schemas = [
            operation.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        ]
    for schema in schemas:
        if isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
            prefix = "#/components/schemas/"
            if schema["$ref"].startswith(prefix):
                return schema["$ref"][len(prefix) :]
    raise ConcurrencyContractError(
        f"{operation.get('operationId', 'unknown operation')} has no direct JSON schema reference"
    )


class ConcurrencyContractEnricher:
    """Discover standard API Get/Replace pairs and stamp their envelope tokens."""

    def __init__(self, config_path: Path | None = None) -> None:
        """Load the evidence-backed non-config-object exclusions."""
        path = config_path or DEFAULT_CONFIG
        data = yaml.safe_load(path.read_text()) if path.exists() else {}
        self.exclusions = (data or {}).get("exclusions", {})
        if not isinstance(self.exclusions, dict):
            raise ConcurrencyContractError("concurrency exclusions must be an object")

    @staticmethod
    def _operations(spec: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for path, path_item in spec.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            for method in ("get", "post", "put"):
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue
                operation_id = operation.get("operationId")
                if not isinstance(operation_id, str) or "." not in operation_id:
                    continue
                identity, action = operation_id.rsplit(".", 1)
                if action not in {"Create", "Get", "Replace"}:
                    continue
                grouped.setdefault(identity, {})[action] = {
                    "method": method.upper(),
                    "path": path,
                    "operation": operation,
                }
        return grouped

    @staticmethod
    def _validate_token(schema_name: str, token: object) -> None:
        if not isinstance(token, dict):
            raise ConcurrencyContractError(f"{schema_name} resource_version must be an object")
        if token.get("type") != "string":
            raise ConcurrencyContractError(f"{schema_name} resource_version type must be string")
        metadata = token.get(X_F5XC_CONCURRENCY_TOKEN)
        if not isinstance(metadata, dict):
            raise ConcurrencyContractError(
                f"{schema_name} resource_version concurrency metadata is missing"
            )
        if metadata.get("server_assigned") is not True:
            raise ConcurrencyContractError(
                f"{schema_name} resource_version server_assigned must be true"
            )
        if metadata.get("echo_on_operations") != ["replace"]:
            raise ConcurrencyContractError(
                f"{schema_name} resource_version echo_on_operations must equal [replace]"
            )
        if token != TOKEN_SCHEMA:
            raise ConcurrencyContractError(
                f"{schema_name} resource_version has unsupported extra contract fields"
            )

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Mutate *spec* with tokens and return its deterministic coverage inventory."""
        groups = self._operations(spec)
        schemas = spec.get("components", {}).get("schemas", {})
        if not isinstance(schemas, dict):
            raise ConcurrencyContractError("components.schemas is missing")

        resources: list[dict[str, Any]] = []
        used_exclusions: set[str] = set()
        exclusions: list[dict[str, str]] = []
        for identity in sorted(groups):
            actions = groups[identity]
            replace = actions.get("Replace")
            get = actions.get("Get")
            exclusion = self.exclusions.get(identity)
            if replace and exclusion is not None:
                if not isinstance(exclusion, dict) or not exclusion.get("evidence"):
                    raise ConcurrencyContractError(
                        f"{identity}.Replace has a malformed evidence-backed exclusion"
                    )
                used_exclusions.add(identity)
                exclusions.append(
                    {
                        "api_identity": identity,
                        "operation": str(exclusion.get("operation", "Replace")),
                        "reason": str(exclusion.get("reason", "")),
                    }
                )
                continue
            if replace and not get:
                raise ConcurrencyContractError(
                    f"{identity}.Replace lacks Get and an evidence-backed exclusion"
                )
            if not replace or not get:
                continue

            get_schema = _schema_ref(get["operation"], response=True)
            replace_schema = _schema_ref(replace["operation"], response=False)
            create_schema = None
            if "Create" in actions:
                create_schema = _schema_ref(actions["Create"]["operation"], response=False)

            if create_schema == replace_schema:
                replacement_name = f"{replace_schema}ConcurrencyReplace"
                if replacement_name in schemas:
                    raise ConcurrencyContractError(
                        f"generated concurrency schema {replacement_name} already exists"
                    )
                schemas[replacement_name] = copy.deepcopy(schemas[replace_schema])
                replace_schema = replacement_name
                request_schema = (
                    replace["operation"]
                    .get("requestBody", {})
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                )
                request_schema["$ref"] = f"#/components/schemas/{replacement_name}"

            for schema_name in (get_schema, replace_schema):
                schema = schemas.get(schema_name)
                if not isinstance(schema, dict):
                    raise ConcurrencyContractError(f"{identity} references missing {schema_name}")
                properties = schema.setdefault("properties", {})
                existing = properties.get(TOKEN_PROPERTY)
                if existing is None:
                    properties[TOKEN_PROPERTY] = copy.deepcopy(TOKEN_SCHEMA)
                else:
                    self._validate_token(schema_name, existing)

            if create_schema:
                schema = schemas.get(create_schema)
                if not isinstance(schema, dict):
                    raise ConcurrencyContractError(f"{identity} references missing {create_schema}")
                if TOKEN_PROPERTY in schema.get("properties", {}):
                    raise ConcurrencyContractError(
                        f"{create_schema} create schema must not contain resource_version"
                    )

            resources.append(
                {
                    "api_identity": identity,
                    "get": {"path": get["path"], "schema": get_schema},
                    "replace": {"path": replace["path"], "schema": replace_schema},
                    "create_schema": create_schema,
                    "token": TOKEN_PROPERTY,
                }
            )

        present_identities = set(groups)
        unused = (set(self.exclusions) & present_identities) - used_exclusions
        if unused:
            raise ConcurrencyContractError(
                f"stale concurrency exclusions are not present in the API graph: {sorted(unused)}"
            )

        version = spec.get("info", {}).get("version")
        return {
            "version": version,
            "eligible_count": len(resources),
            "covered_count": len(resources),
            "excluded_count": len(exclusions),
            "resources": resources,
            "exclusions": exclusions,
        }
