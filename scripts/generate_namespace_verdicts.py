"""Generate the machine-owned namespace verdicts file from CRUD evidence.

Reads committed live-API CRUD evidence (``config/namespace_crud_evidence.yaml``,
produced by ``discover_namespace_crud.py``) and writes ``config/namespace_verdicts.yaml``
— an authoritative, wholesale-regenerated map of resource → verified namespace
constraint. The enricher layers these verdicts OVER the hand-curated
``config/namespace_profile.yaml`` so live-verified facts always win, while the
curated file (with its section comments and default-deny classifications) stays
pristine and human-owned.

This split keeps verification deterministic and idempotent: the same evidence
yields the same verdicts file in CI, with no comment-destroying round-trips.

Usage:
    python -m scripts.generate_namespace_verdicts
    python -m scripts.generate_namespace_verdicts --crud config/namespace_crud_evidence.yaml
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from scripts.discover_namespace_crud import build_create_path_index
from scripts.utils.yaml_writer import write_yaml

TENANT_ALLOWED = ["custom", "default", "shared"]
SYSTEM_ALLOWED = ["system"]
SHARED_ALLOWED = ["shared"]

_TENANT_VERDICTS = {"any_namespace", "tenant"}
_SYSTEM_VERDICTS = {"system_only", "system_confirmed"}
_SHARED_VERDICTS = {"shared_only"}


def build_verdicts(crud_path: Path, specs_dir: Path, config_path: Path) -> dict[str, Any]:
    """Map CRUD verdicts to authoritative constraints, then default-deny fill.

    1. Conclusive CRUD → verified tenant/system verdicts.
    2. Any resource with a canonical create path that is neither curated in
       ``namespace_profile.yaml`` nor conclusively CRUD-verified is filled with a
       default-deny (system, restricted) entry — so the authoritative layer covers
       the entire create-path universe and nothing silently inherits the tenant
       default. These fills are the worklist for future CRUD verification.
    """
    with crud_path.open() as f:
        crud = yaml.safe_load(f) or {}

    verdicts: dict[str, Any] = {}
    for name, entry in sorted(crud.items()):
        verdict = entry.get("verdict")
        probes = entry.get("probes", {})
        if verdict in _TENANT_VERDICTS:
            allowed = TENANT_ALLOWED
            evidence = "created in a custom namespace"
        elif verdict in _SHARED_VERDICTS:
            # "shared" is not a custom namespace — scope precisely to shared.
            allowed = SHARED_ALLOWED
            evidence = "API: allowed to be created only in shared"
            for ns in ("default", "demo"):
                err = (probes.get(ns, {}) or {}).get("error") or ""
                if "shared" in err.lower():
                    evidence = err[:160]
                    break
        elif verdict in _SYSTEM_VERDICTS:
            allowed = SYSTEM_ALLOWED
            evidence = "restricted to system namespace"
            for ns in ("default", "demo"):
                err = (probes.get(ns, {}) or {}).get("error") or ""
                if "system" in err.lower():
                    evidence = err[:160]
                    break
        else:
            continue  # inconclusive → handled by default-deny fill below

        verdicts[name] = {
            "constraint": {"allowed": allowed},
            "_verification": {
                "status": "verified",
                "method": "crud",
                "evidence": evidence,
            },
        }

    # Default-deny fill: every create-path resource not otherwise classified.
    with config_path.open() as f:
        curated = set((yaml.safe_load(f) or {}).get("resources", {}).keys())
    universe = set(build_create_path_index(specs_dir).keys())
    for name in sorted(universe):
        if name in verdicts or name in curated:
            continue
        verdicts[name] = {
            "constraint": {"allowed": SYSTEM_ALLOWED},
            "_verification": {
                "status": "restricted",
                "method": "default_deny",
                "evidence": "unclassified create-path resource — hidden pending CRUD verification",
            },
        }
    return verdicts


def main() -> None:
    """Generate config/namespace_verdicts.yaml from CRUD evidence."""
    parser = argparse.ArgumentParser(description="Generate namespace verdicts from CRUD evidence")
    parser.add_argument(
        "--crud",
        type=Path,
        default=Path("config/namespace_crud_evidence.yaml"),
        help="Committed CRUD evidence file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("config/namespace_verdicts.yaml"),
        help="Machine-owned verdicts output",
    )
    parser.add_argument("--specs-dir", type=Path, default=Path("docs/specifications/api"))
    parser.add_argument("--config", type=Path, default=Path("config/namespace_profile.yaml"))
    args = parser.parse_args()

    verdicts = build_verdicts(args.crud, args.specs_dir, args.config)

    header = (
        "# MACHINE-GENERATED — do not edit by hand.\n"
        "# Regenerate: python -m scripts.generate_namespace_verdicts\n"
        "# Source evidence: config/namespace_crud_evidence.yaml\n"
        "#\n"
        "# Authoritative, live-CRUD-verified namespace constraints. The enricher\n"
        "# layers these OVER config/namespace_profile.yaml (verdict wins)."
    )
    write_yaml({"version": 1, "verdicts": verdicts}, args.output, header=header, sort_keys=True)

    tenant = sum(1 for v in verdicts.values() if v["constraint"]["allowed"] == TENANT_ALLOWED)
    system = sum(1 for v in verdicts.values() if v["constraint"]["allowed"] == SYSTEM_ALLOWED)
    print(f"Wrote {len(verdicts)} verdicts to {args.output} (tenant={tenant}, system={system})")


if __name__ == "__main__":
    main()
