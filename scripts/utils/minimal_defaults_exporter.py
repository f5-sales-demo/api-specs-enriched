# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Emit ``minimal-export-defaults.json`` for downstream minimum-settings export.

Downstream tools (xcsh, vscode-xcsh) export F5XC resources with only the
settings that differ from server-applied defaults. Rather than have each tool
re-walk the enriched OpenAPI, this exporter computes the per-kind defaults once
here — the single source of truth — and publishes a flat artifact:

    {
      "version": "<spec-version>",
      "resources": {
        "<kind>": {
          "serverDefaultFields": ["spec.loadbalancer_algorithm", ...],
          "fieldDefaults": { "spec.loadbalancer_algorithm": "ROUND_ROBIN", ... },
          "minimumConfigFields": ["spec.origin_servers", ...],
          "fieldConflicts": { "spec.round_robin": ["least_active", "random"] }
        }
      }
    }

Resource -> SpecType schema mapping reuses the ``schema_pattern`` regexes from
``config/discovered_defaults.yaml`` (the same patterns the enricher uses to place
the ``x-f5xc-*`` markers). Paths are ``spec.``-prefixed dot-paths; ``allOf``/``$ref``
are resolved so nested object fields are reached.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.utils.default_value_enricher import (
    DefaultValueConfigError,
    DefaultValueEnricher,
    exact_json_value_equal,
)
from scripts.utils.extension_constants import (
    X_F5XC_CONFLICTS_WITH,
    X_F5XC_REQUIRED_FOR,
    X_F5XC_SERVER_DEFAULT,
    X_F5XC_SERVER_DEFAULT_VALUE,
)
from scripts.utils.json_writer import write_json_file

if TYPE_CHECKING:
    import re
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

# Bound recursion into nested object schemas; mirrors DefaultValueEnricher.
_MAX_DEPTH = 5


class MinimalDefaultsExporter:
    """Walks enriched SpecType schemas and emits the minimal-defaults artifact."""

    def __init__(self, config_path: Path | None = None) -> None:
        """Load schema-pattern regexes from ``config/discovered_defaults.yaml``."""
        self.config_path = (
            config_path
            or Path(__file__).parent.parent.parent / "config" / "discovered_defaults.yaml"
        )
        self._patterns: dict[str, re.Pattern[str]] = {}
        self._load_patterns()

    def _load_patterns(self) -> None:
        """Reuse the enricher's strict, atomic configuration contract."""
        enricher = DefaultValueEnricher(config_path=self.config_path)
        self._patterns = enricher.compiled_patterns

    # -- schema resolution --------------------------------------------------

    @staticmethod
    def _ref_name(ref: str) -> str:
        return ref.rsplit("/", maxsplit=1)[-1]

    def _resolve_nested(
        self, node: dict[str, Any], schemas: dict[str, Any], seen: set[str]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Resolve a property to the object schema holding its ``properties``.

        Handles inline objects, a direct ``$ref``, and a single-item
        ``allOf: [{$ref}]`` wrapper. Returns ``(schema, ref_name)`` — ``ref_name``
        is ``None`` for inline objects, and ``(None, None)`` on cycles or when no
        object schema is reachable.
        """
        schema_node = node
        if node.get("type") == "array" and isinstance(node.get("items"), dict):
            schema_node = node["items"]
        if "properties" in schema_node:
            return schema_node, None
        references: list[Any] = []
        if "$ref" in schema_node:
            references.append(schema_node["$ref"])
        all_of = schema_node.get("allOf", [])
        if not isinstance(all_of, list):
            raise DefaultValueConfigError("export schema allOf must be an array")
        references.extend(
            item["$ref"] for item in all_of if isinstance(item, dict) and "$ref" in item
        )
        if any(not isinstance(ref, str) or not ref for ref in references):
            raise DefaultValueConfigError("export schema references must be non-empty strings")
        unique_references = sorted(set(references))
        if len(unique_references) > 1:
            raise DefaultValueConfigError(
                f"export schema has ambiguous reference targets: {unique_references}"
            )
        if not unique_references:
            return None, None
        ref = unique_references[0]
        name = self._ref_name(ref)
        if name in seen:
            return None, None
        target = schemas.get(name)
        if not isinstance(target, dict):
            return None, None
        return target, name

    # -- marker collection --------------------------------------------------

    def _walk(
        self,
        schema: dict[str, Any],
        prefix: str,
        schemas: dict[str, Any],
        seen: set[str],
        out: dict[str, Any],
        depth: int,
    ) -> None:
        if depth > _MAX_DEPTH:
            return
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return
        for name, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            path = f"{prefix}.{name}"

            is_server_default = prop.get(X_F5XC_SERVER_DEFAULT) is True
            if is_server_default:
                out["serverDefaultFields"].append(path)
                if "default" in prop:
                    out["fieldDefaults"][path] = prop["default"]
                elif prop.get(X_F5XC_SERVER_DEFAULT_VALUE) == {"type": "null", "value": None}:
                    out["fieldDefaults"][path] = None

            required_for = prop.get(X_F5XC_REQUIRED_FOR)
            if isinstance(required_for, dict) and required_for.get("minimum_config") is True:
                out["minimumConfigFields"].append(path)

            conflicts = prop.get(X_F5XC_CONFLICTS_WITH)
            if isinstance(conflicts, list) and conflicts:
                out["fieldConflicts"][path] = sorted(set(conflicts))

            nested, ref_name = self._resolve_nested(prop, schemas, seen)
            if nested is not None:
                child_seen = seen | ({ref_name} if ref_name else set())
                self._walk(nested, path, schemas, child_seen, out, depth + 1)

    def _build_resource(
        self, spec_schema: dict[str, Any], schemas: dict[str, Any]
    ) -> dict[str, Any] | None:
        out: dict[str, Any] = {
            "serverDefaultFields": [],
            "fieldDefaults": {},
            "minimumConfigFields": [],
            "fieldConflicts": {},
        }
        self._walk(spec_schema, "spec", schemas, set(), out, depth=1)
        if not any(out[k] for k in out):
            return None
        out["serverDefaultFields"] = sorted(out["serverDefaultFields"])
        out["minimumConfigFields"] = sorted(out["minimumConfigFields"])
        return out

    # -- public API ---------------------------------------------------------

    def build(self, schemas: dict[str, Any], version: str = "unknown") -> dict[str, Any]:
        """Build the artifact dict from a flat component-schemas map."""
        resources: dict[str, Any] = {}
        for kind, pattern in self._patterns.items():
            matches = sorted(name for name in schemas if pattern.search(name))
            if not matches:
                raise DefaultValueConfigError(
                    f"resource {kind!r} schema_pattern matches no published schema",
                )
            entries = [
                entry
                for schema_name in matches
                if (entry := self._build_resource(schemas[schema_name], schemas)) is not None
            ]
            if entries:
                resources[kind] = self._merge_resource_entries(kind, entries)
        return {"version": version, "resources": dict(sorted(resources.items()))}

    @staticmethod
    def _merge_resource_entries(kind: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
        """Union matching SpecType outputs and reject inconsistent field metadata."""
        merged: dict[str, Any] = {
            "serverDefaultFields": [],
            "fieldDefaults": {},
            "minimumConfigFields": [],
            "fieldConflicts": {},
        }
        for entry in entries:
            merged["serverDefaultFields"].extend(entry["serverDefaultFields"])
            merged["minimumConfigFields"].extend(entry["minimumConfigFields"])
            for path, value in entry["fieldDefaults"].items():
                if path in merged["fieldDefaults"] and not exact_json_value_equal(
                    merged["fieldDefaults"][path], value
                ):
                    raise ValueError(
                        f"resource {kind!r} has conflicting fieldDefaults values for {path!r}",
                    )
                merged["fieldDefaults"][path] = value
            for path, conflicts in entry["fieldConflicts"].items():
                merged["fieldConflicts"][path] = sorted(
                    set(merged["fieldConflicts"].get(path, [])) | set(conflicts)
                )
        merged["serverDefaultFields"] = sorted(set(merged["serverDefaultFields"]))
        merged["minimumConfigFields"] = sorted(set(merged["minimumConfigFields"]))
        return merged

    def export(
        self, schemas: dict[str, Any], output_path: Path, version: str = "unknown"
    ) -> dict[str, Any]:
        """Build the artifact and write it (Biome-formatted under the docs tree)."""
        artifact = self.build(schemas, version=version)
        write_json_file(artifact, output_path, indent=2, sort_keys=True, ensure_ascii=False)
        logger.info("Wrote %s (%d resources)", output_path, len(artifact["resources"]))
        return artifact

    @staticmethod
    def collect_schemas(specs: Iterable[dict[str, Any]]) -> dict[str, Any]:
        """Merge exact ``components.schemas`` without last-writer behavior."""
        merged: dict[str, Any] = {}
        for spec in specs:
            schemas = (spec.get("components") or {}).get("schemas") or {}
            if not isinstance(schemas, dict):
                raise TypeError("components.schemas must be a mapping")
            for name, schema in schemas.items():
                if name in merged and not exact_json_value_equal(merged[name], schema):
                    raise DefaultValueConfigError(
                        f"conflicting collected schema values for {name!r}"
                    )
                if name not in merged:
                    merged[name] = copy.deepcopy(schema)
        return dict(sorted(merged.items()))
