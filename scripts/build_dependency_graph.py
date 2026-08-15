# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Generate config/resource_dependency_graph.json as a PROJECTION.

DRY: the coarse resource→dependency graph is derived from the per-field
``x-f5xc-references`` extensions (the single source of truth), never hand-maintained.
Each reference edge carries `conditional` (true when the source field is gated by a
oneOf choice) and `required` so consumers can distinguish must-create-first deps from
optional/conditional ones. A topological order is included for deterministic CRUD
provisioning.

Run after enrichment (the references must be stamped first):
    python -m scripts.build_dependency_graph
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.utils.extension_constants import X_F5XC_REFERENCES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Edge:
    """A dependency edge: src resource references target resource-kind."""

    src: str
    target: str
    conditional: bool
    required: bool


def _schema_to_resource(schema_name: str) -> str | None:
    """Map a CreateSpecType schema name to a kebab resource id, else None."""
    if "CreateSpecType" not in schema_name:
        return None
    kind = re.sub(r"^(views|schema)", "", schema_name.replace("CreateSpecType", ""))
    return kind.replace("_", "-") or None


def collect_edges(specs: list[dict[str, Any]]) -> list[Edge]:
    """Collect dependency edges from x-f5xc-references across enriched specs."""
    edges: dict[tuple[str, str], Edge] = {}
    for spec in specs:
        for schema_name, schema in spec.get("components", {}).get("schemas", {}).items():
            src = _schema_to_resource(schema_name)
            if not src or not isinstance(schema, dict):
                continue
            for prop in (schema.get("properties") or {}).values():
                if not isinstance(prop, dict):
                    continue
                for ref in prop.get(X_F5XC_REFERENCES, []) or []:
                    kind = ref.get("resource_kind")
                    if not kind:
                        continue  # unresolved — excluded from the coarse graph
                    target = kind.replace("_", "-")
                    if target == src:
                        continue
                    conditional = ref.get("gated_by") is not None
                    required = bool(ref.get("required"))
                    key = (src, target)
                    # Prefer the strongest edge: required + unconditional wins.
                    prior = edges.get(key)
                    if (
                        prior is None
                        or (required and not prior.required)
                        or (not conditional and prior.conditional)
                    ):
                        edges[key] = Edge(
                            src=src, target=target, conditional=conditional, required=required
                        )
    return list(edges.values())


def project_graph(edges: list[Edge], resource_ids: list[str]) -> dict[str, Any]:
    """Project edges into a graph with topological order, leaves, prerequisites."""
    by_src: dict[str, list[Edge]] = {}
    for e in edges:
        by_src.setdefault(e.src, []).append(e)

    # Kahn's algorithm; deps (targets) come before dependents (srcs).
    deps: dict[str, set[str]] = {r: set() for r in resource_ids}
    for e in edges:
        if e.src in deps and e.target in deps:
            deps[e.src].add(e.target)

    sorted_ids: list[str] = []
    visited: set[str] = set()
    # Repeatedly take nodes whose deps are all satisfied.
    remaining = set(resource_ids)
    progress = True
    while remaining and progress:
        progress = False
        for r in sorted(remaining):
            if deps[r] <= visited:
                sorted_ids.append(r)
                visited.add(r)
                remaining.discard(r)
                progress = True
    # Cycles / leftovers appended deterministically.
    sorted_ids.extend(sorted(remaining))

    edge_out = {
        src: [
            {"target": e.target, "conditional": e.conditional, "required": e.required}
            for e in sorted(items, key=lambda x: x.target)
        ]
        for src, items in sorted(by_src.items())
    }
    targets = {e.target for e in edges}
    return {
        "edges": edge_out,
        "sorted": sorted_ids,
        "leaves": sorted([r for r in resource_ids if not deps.get(r)]),
        "prerequisites": sorted([r for r in resource_ids if r in targets]),
    }


def main() -> int:
    """Build the projection from enriched specs and write the graph JSON."""
    specs_dir = Path("docs/specifications/api")
    specs: list[dict[str, Any]] = []
    for f in sorted(specs_dir.glob("*.json")):
        if f.name in {
            "index.json",
            "namespace_profiles.json",
            "resource_coverage.json",
            "validation.json",
        }:
            continue
        try:
            specs.append(json.loads(f.read_text()))
        except (ValueError, OSError):
            continue
    resource_ids = sorted(
        {
            r
            for spec in specs
            for n in spec.get("components", {}).get("schemas", {})
            if (r := _schema_to_resource(n))
        }
    )
    edges = collect_edges(specs)
    graph = project_graph(edges, resource_ids)
    graph = {
        "description": "Resource dependency graph — PROJECTION of per-field x-f5xc-references (single source of truth).",
        "source": "Generated by scripts/build_dependency_graph.py from enriched specs' x-f5xc-references.",
        "edge_count": len(edges),
        **graph,
    }
    out = Path("config/resource_dependency_graph.json")
    out.write_text(json.dumps(graph, indent=2) + "\n")
    logger.info("wrote %s: %d edges", out, len(edges))
    print(f"wrote {out}: {len(edges)} edges, {len(graph['prerequisites'])} prerequisites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
