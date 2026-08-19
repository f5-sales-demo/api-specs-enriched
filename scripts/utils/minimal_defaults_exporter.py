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

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from scripts.utils.extension_constants import (
    X_F5XC_CONFLICTS_WITH,
    X_F5XC_REQUIRED_FOR,
    X_F5XC_SERVER_DEFAULT,
)
from scripts.utils.json_writer import write_json_file

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

# Bound recursion into nested object schemas; mirrors DefaultValueEnricher.
_MAX_DEPTH = 5

# Prefer the canonical full-spec schema when several SpecTypes match a pattern.
_SCHEMA_PREFERENCE = ("GlobalSpecType", "CreateSpecType", "ReplaceSpecType", "GetSpecType")


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
        try:
            with self.config_path.open() as f:
                config = yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.warning("discovered_defaults config not found: %s", self.config_path)
            return
        for name, cfg in (config.get("resources") or {}).items():
            pattern = (cfg or {}).get("schema_pattern")
            if not pattern:
                continue
            try:
                self._patterns[name] = re.compile(pattern)
            except re.error as exc:
                logger.warning("Invalid schema_pattern for %s: %s (%s)", name, pattern, exc)

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
        if "properties" in node:
            return node, None
        ref = node.get("$ref")
        if not ref:
            for item in node.get("allOf", []):
                if isinstance(item, dict) and "$ref" in item:
                    ref = item["$ref"]
                    break
        if not ref:
            return None, None
        name = self._ref_name(ref)
        if name in seen:
            return None, None
        target = schemas.get(name)
        if not isinstance(target, dict):
            return None, None
        return target, name

    def _select_schema(self, schemas: Iterable[str]) -> str | None:
        matches = list(schemas)
        if not matches:
            return None
        for marker in _SCHEMA_PREFERENCE:
            preferred = [name for name in matches if marker in name]
            if preferred:
                return min(preferred)
        return min(matches)

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

            required_for = prop.get(X_F5XC_REQUIRED_FOR)
            if isinstance(required_for, dict) and required_for.get("minimum_config") is True:
                out["minimumConfigFields"].append(path)

            conflicts = prop.get(X_F5XC_CONFLICTS_WITH)
            if isinstance(conflicts, list) and conflicts:
                out["fieldConflicts"][path] = list(conflicts)

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
            chosen = self._select_schema(n for n in schemas if pattern.search(n))
            if not chosen:
                continue
            entry = self._build_resource(schemas[chosen], schemas)
            if entry is not None:
                resources[kind] = entry
        return {"version": version, "resources": dict(sorted(resources.items()))}

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
        """Merge ``components.schemas`` across one or more specs into one map."""
        merged: dict[str, Any] = {}
        for spec in specs:
            schemas = (spec.get("components") or {}).get("schemas") or {}
            merged.update(schemas)
        return merged
