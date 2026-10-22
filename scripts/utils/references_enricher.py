# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Resource-reference enricher for OpenAPI specifications.

Stamps ``x-f5xc-references`` on ObjectRefType properties — the resource-reference
dimension of the dependency model. A reference field points at ANOTHER resource
that must exist before this one is created; the choice-gating (which oneOf branch
exposes the field) and required-ness come from the surrounding schema.

Two facts about F5 specs drive the design (see resource-dependency-enrichment memo):
- ObjectRefType is GENERIC (name/namespace/tenant) — it does NOT name the target
  resource kind. So the kind is resolved from a CURATED map keyed by
  ``<schema>.<field>`` (config/resource_references.yaml). Unmapped fields stamp
  ``resource_kind: null`` rather than guessing.
- Required references are often nested inside oneOf choice-variant sub-schemas, so
  the top-level scan also records the oneOf group that gates each field.

Reuses oneOf-group extraction (choice-gating) — see ConflictsWithEnricher.

Issue: resource-reference dependency metadata (x-f5xc-references)
"""

import copy
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .extension_constants import X_F5XC_REFERENCES, X_VES_ONEOF_FIELD_PREFIX

logger = logging.getLogger(__name__)

# Substrings in an allOf $ref target that mark a resource reference.
_REF_MARKERS = ("ObjectRefType", "RefType", "RefSelector")


@dataclass
class ReferencesEnrichmentStats:
    """Statistics for reference enrichment."""

    schemas_processed: int = 0
    references_stamped: int = 0
    references_unmapped: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to a serializable dictionary."""
        return {
            "schemas_processed": self.schemas_processed,
            "references_stamped": self.references_stamped,
            "references_unmapped": self.references_unmapped,
        }


class ReferencesEnricher:
    """Enrich OpenAPI specs with ``x-f5xc-references`` on ObjectRefType properties."""

    def __init__(self, kind_map: dict[str, str] | None = None) -> None:
        """Create the enricher.

        Args:
            kind_map: Curated ``<schema>.<field>`` → referred resource-kind map. The
                deterministic source of the target kind (F5 specs do not carry it).
        """
        self.kind_map = kind_map or {}
        self.field_defaults: dict[str, str] = {}
        self.stats = ReferencesEnrichmentStats()

    @classmethod
    def from_config(
        cls, config_path: Path | str = Path("config/resource_references.yaml")
    ) -> "ReferencesEnricher":
        """Build an enricher with the curated referred-kind map loaded from YAML."""
        path = Path(config_path)
        kind_map: dict[str, str] = {}
        if path.exists():
            data = yaml.safe_load(path.read_text()) or {}
            refs = data.get("references", {})
            if isinstance(refs, dict):
                kind_map = {str(k): str(v) for k, v in refs.items()}
            defaults = data.get("field_defaults", {})
            if isinstance(defaults, dict):
                enricher = cls(kind_map=kind_map)
                enricher.field_defaults = {str(k): str(v) for k, v in defaults.items()}
                return enricher
        else:
            logger.warning("resource_references.yaml not found at %s — kinds will be null", path)
        return cls(kind_map=kind_map)

    def get_stats(self) -> dict[str, Any]:
        """Return enrichment stats as a dictionary."""
        return self.stats.to_dict()

    def reset_stats(self) -> None:
        """Reset stats (called per domain to avoid cross-domain accumulation)."""
        self.stats = ReferencesEnrichmentStats()

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Stamp x-f5xc-references on ObjectRefType properties of every CreateSpecType."""
        schemas = spec.get("components", {}).get("schemas", {})
        for schema_name, schema in schemas.items():
            if not isinstance(schema, dict):
                continue
            self.stats.schemas_processed += 1
            self._enrich_schema(schema_name, schema)
        return spec

    def _enrich_schema(self, schema_name: str, schema: dict[str, Any]) -> None:
        props = schema.get("properties")
        if not isinstance(props, dict):
            return
        # Map each field to the oneOf group that gates it (choice-gating).
        field_gate = self._field_to_oneof_group(schema)
        for field_name, prop in props.items():
            if not isinstance(prop, dict):
                continue
            branch = self._reference_branch(prop)
            if branch is None:
                continue
            self._stamp(schema_name, field_name, branch, field_gate.get(field_name))
            if branch is not prop:
                prop[X_F5XC_REFERENCES] = copy.deepcopy(branch[X_F5XC_REFERENCES])

    def _reference_branch(self, prop: dict[str, Any]) -> dict[str, Any] | None:
        """Preserve reference annotations through exact null-only wrappers."""
        if self._is_object_ref(prop):
            return prop
        alternatives = prop.get("anyOf", [])
        if (
            isinstance(alternatives, list)
            and len(alternatives) == 2
            and alternatives[1] == {"type": "object", "nullable": True, "enum": [None]}
            and isinstance(alternatives[0], dict)
            and self._is_object_ref(alternatives[0])
        ):
            return alternatives[0]
        return None

    def _is_object_ref(self, prop: dict[str, Any]) -> bool:
        """True when a property is an ObjectRefType reference (allOf → *RefType)."""
        for entry in prop.get("allOf", []) or []:
            ref = entry.get("$ref", "") if isinstance(entry, dict) else ""
            if any(marker in ref for marker in _REF_MARKERS):
                return True
        return False

    def _field_to_oneof_group(self, schema: dict[str, Any]) -> dict[str, str]:
        """Reverse-map each variant field → its oneOf group name (the gating choice)."""
        mapping: dict[str, str] = {}
        for key, value in schema.items():
            if not key.startswith(X_VES_ONEOF_FIELD_PREFIX):
                continue
            group = key[len(X_VES_ONEOF_FIELD_PREFIX) :]
            variants = value
            if isinstance(value, str):
                try:
                    variants = json.loads(value)
                except (ValueError, TypeError):
                    continue
            if isinstance(variants, list):
                for v in variants:
                    mapping[v] = group
        return mapping

    def _stamp(
        self, schema_name: str, field_name: str, prop: dict[str, Any], gate_group: str | None
    ) -> None:
        if X_F5XC_REFERENCES in prop:  # idempotent
            return
        # Resolution order: exact <schema>.<field> → field-name default → null (honest gap).
        kind = self.kind_map.get(f"{schema_name}.{field_name}") or self.field_defaults.get(
            field_name
        )
        if kind is None:
            self.stats.references_unmapped += 1
            logger.debug("unmapped ObjectRef: %s.%s", schema_name, field_name)
        required = bool(prop.get("x-f5xc-required-for", {}).get("create", False))
        cardinality = "list" if prop.get("type") == "array" else "single"
        descriptor: dict[str, Any] = {
            "resource_kind": kind,
            "field_path": field_name,
            "gated_by": {"choice": gate_group} if gate_group else None,
            "required": required,
            "cardinality": cardinality,
        }
        prop[X_F5XC_REFERENCES] = [descriptor]
        self.stats.references_stamped += 1
