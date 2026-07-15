"""Signal-fusion namespace-scope classifier.

Determines, per resource, whether it is creatable in tenant (custom/default/shared)
namespaces or restricted to the system namespace, by fusing several signals in
priority order:

  1. CRUD result (authoritative) — live create-probe verdict from
     ``discover_namespace_crud.py`` output (``--crud <report.yaml>``).
  2. Hardcoded-system path — a canonical create path templated to
     ``/namespaces/system/`` with no ``{namespace}`` variant (strong: system).
  3. NL description — schema ``namespace`` fields saying "namespace is always
     system" / "only system namespace" (strong, upstream: system).
  4. Console breadcrumb — ``config/console_ui.yaml`` breadcrumb containing the
     ``{namespace}`` token implies the console renders a namespace selector
     (soft: tenant); absence / administration workspace leans system.

The output is deterministic and offline (except that the CRUD report is produced
by a separate live run). It emits a per-resource verdict with confidence,
method, and evidence, plus a reconciliation report of signal disagreements —
the worklist for further CRUD verification.

Usage:
    python -m scripts.classify_namespace_scope --crud /tmp/nsverify/crud_full.yaml \
        --output reports/namespace_scope_classification.yaml
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from scripts.audit_namespace_profiles import (
    SYSTEM_DESCRIPTION_PATTERNS,
    _schema_to_resource,
)
from scripts.discover_namespace_crud import build_create_path_index

TENANT_ALLOWED = ["custom", "default", "shared"]
SYSTEM_ALLOWED = ["system"]
SHARED_ALLOWED = ["shared"]


def _load_crud_verdicts(crud_path: Path | None) -> dict[str, dict[str, Any]]:
    """Load per-resource verdicts from a discover_namespace_crud.py report."""
    if not crud_path or not crud_path.exists():
        return {}
    with crud_path.open() as f:
        data = yaml.safe_load(f) or {}
    out: dict[str, dict[str, Any]] = {}
    for name, entry in data.items():
        verdict = entry.get("verdict")
        if verdict in {"any_namespace", "tenant"}:
            out[name] = {"scope": "tenant", "allowed": TENANT_ALLOWED}
        elif verdict in {"system_only", "system_confirmed"}:
            out[name] = {"scope": "system", "allowed": SYSTEM_ALLOWED}
        elif verdict == "shared_only":
            out[name] = {"scope": "shared", "allowed": SHARED_ALLOWED}
        # inconclusive → not a verdict, leave for weaker signals
    return out


def _system_path_resources(specs_dir: Path) -> set[str]:
    """Resources whose canonical create path is hardcoded to /namespaces/system/."""
    system_only: set[str] = set()
    opid = re.compile(r"^ves\.io\.schema\.(?:views\.)?([a-z0-9_]+)\.API\.Create$")
    for spec_path in sorted(specs_dir.glob("*.json")):
        if spec_path.name in {
            "index.json",
            "namespace_profiles.json",
            "openapi.json",
            "minimal-export-defaults.json",
        }:
            continue
        try:
            spec = json.loads(spec_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for path, methods in spec.get("paths", {}).items():
            post = methods.get("post") if isinstance(methods, dict) else None
            if not isinstance(post, dict):
                continue
            if "{name}" in path or "{metadata.name}" in path:
                continue
            segs = path.rstrip("/").split("/")
            if "namespaces" not in segs:
                continue
            ni = segs.index("namespaces")
            if len(segs) != ni + 3:
                continue
            m = opid.match(post.get("operationId", ""))
            if not m:
                continue
            if segs[ni + 1] == "system":
                system_only.add(m.group(1))
    return system_only


def _nl_system_resources(specs_dir: Path) -> set[str]:
    """Resources with an upstream NL 'only system namespace' description."""
    hits: set[str] = set()
    for spec_path in sorted(specs_dir.glob("*.json")):
        if spec_path.name in {
            "index.json",
            "namespace_profiles.json",
            "openapi.json",
            "minimal-export-defaults.json",
        }:
            continue
        try:
            spec = json.loads(spec_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for schema_name, schema in spec.get("components", {}).get("schemas", {}).items():
            if not isinstance(schema, dict):
                continue
            ns = schema.get("properties", {}).get("namespace", {})
            desc = ns.get("description", "") if isinstance(ns, dict) else ""
            if any(p.search(desc) for p in SYSTEM_DESCRIPTION_PATTERNS):
                hits.add(_schema_to_resource(schema_name))
    return hits


def _console_breadcrumb_scope(console_path: Path) -> dict[str, str]:
    """Map resource -> 'tenant'|'system' from console_ui breadcrumb namespace token."""
    if not console_path.exists():
        return {}
    with console_path.open() as f:
        data = yaml.safe_load(f) or {}
    out: dict[str, str] = {}
    for name, entry in (data.get("resources") or {}).items():
        if not isinstance(entry, dict):
            continue
        bc = entry.get("breadcrumbs", []) or []
        has_ns = any("{namespace}" in str(x) for x in bc)
        out[name] = "tenant" if has_ns else "system"
    return out


def classify(
    specs_dir: Path,
    crud_path: Path | None,
    console_path: Path,
) -> dict[str, dict[str, Any]]:
    """Fuse all signals into a per-resource verdict."""
    universe = sorted(build_create_path_index(specs_dir).keys())
    crud = _load_crud_verdicts(crud_path)
    system_path = _system_path_resources(specs_dir)
    nl_system = _nl_system_resources(specs_dir)
    console = _console_breadcrumb_scope(console_path)

    results: dict[str, dict[str, Any]] = {}
    for name in universe:
        evidence: list[str] = []
        # Priority 1: CRUD (authoritative)
        if name in crud:
            scope = crud[name]["scope"]
            results[name] = {
                "scope": scope,
                "allowed": crud[name]["allowed"],
                "method": "crud",
                "confidence": "high",
                "evidence": [f"crud: created/{scope}"],
                "console": console.get(name),
            }
            continue
        # Priority 2: hardcoded-system path (strong system)
        if name in system_path:
            evidence.append("path: hardcoded /namespaces/system/")
        if name in nl_system:
            evidence.append("nl: 'only system namespace' description")
        if name in system_path or name in nl_system:
            results[name] = {
                "scope": "system",
                "allowed": SYSTEM_ALLOWED,
                "method": "spec_signal",
                "confidence": "high",
                "evidence": evidence,
                "console": console.get(name),
            }
            continue
        # Priority 3: console breadcrumb (soft)
        cscope = console.get(name)
        if cscope == "tenant":
            results[name] = {
                "scope": "tenant",
                "allowed": TENANT_ALLOWED,
                "method": "console_signal",
                "confidence": "low",
                "evidence": ["console: breadcrumb has {namespace}"],
                "console": cscope,
            }
            continue
        # Default: unknown -> default-deny (system) pending CRUD
        results[name] = {
            "scope": "system",
            "allowed": SYSTEM_ALLOWED,
            "method": "default_deny",
            "confidence": "none",
            "evidence": ["no conclusive signal — default-deny pending CRUD"],
            "console": cscope,
        }
    return results


def reconcile_report(results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """List resources where the console signal disagrees with the fused verdict."""
    diffs = []
    for name, r in sorted(results.items()):
        c = r.get("console")
        if c and c != r["scope"] and r["method"] != "crud":
            diffs.append(
                {
                    "resource": name,
                    "verdict": r["scope"],
                    "method": r["method"],
                    "console": c,
                }
            )
    return diffs


def main() -> None:
    """Run the classifier and emit the report."""
    parser = argparse.ArgumentParser(description="Signal-fusion namespace-scope classifier")
    parser.add_argument("--specs-dir", type=Path, default=Path("docs/specifications/api"))
    parser.add_argument("--crud", type=Path, default=None, help="discover_namespace_crud report")
    parser.add_argument("--console", type=Path, default=Path("config/console_ui.yaml"))
    parser.add_argument(
        "--output", type=Path, default=Path("reports/namespace_scope_classification.yaml")
    )
    args = parser.parse_args()

    results = classify(args.specs_dir, args.crud, args.console)

    by_method = Counter(r["method"] for r in results.values())
    by_scope = Counter(r["scope"] for r in results.values())
    diffs = reconcile_report(results)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        yaml.dump(
            {"results": results, "disagreements": diffs},
            f,
            default_flow_style=False,
            sort_keys=True,
        )

    print(f"Classified {len(results)} resources -> {args.output}")
    print(f"  by scope:  {dict(by_scope)}")
    print(f"  by method: {dict(by_method)}")
    print(f"  console/verdict disagreements (worklist): {len(diffs)}")
    for d in diffs[:30]:
        print(
            f"    {d['resource']:32s} verdict={d['verdict']:7s} console={d['console']} ({d['method']})"
        )


if __name__ == "__main__":
    main()
