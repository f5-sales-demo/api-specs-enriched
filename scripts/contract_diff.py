# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Contract-diff gate: ensures enrichment only makes additive changes.

Compares an input spec (from ``specs/original``) against an enriched output spec
(from ``docs/specifications/api/``) and reports violations of the additive
allowlist defined in spec §4.3.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import os
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from deepdiff import DeepDiff

from scripts.utils.additive_allowlist import is_additive_change
from scripts.utils.canonical_merge import canonical_merge_sources
from scripts.utils.schema_override_enricher import (
    CONFIG_PATH as SCHEMA_OVERRIDES_PATH,
    SchemaOverrideEnricher,
    remove_schema_property,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


_ARRAY_INDEX_RE = re.compile(r"\[\d+\]")


def _normalize_pointer(pointer: str) -> str:
    """Collapse array indices in a DeepDiff pointer to ``[]``.

    Reordering upstream does not force mass re-fingerprinting:
    ``parameters[2]`` and ``parameters[3]`` both collapse to
    ``parameters[]``.
    """
    return _ARRAY_INDEX_RE.sub("[]", pointer)


def _fingerprint_violation(
    change_type: str,
    pointer: str,
    before: object,
    after: object,
) -> str:
    r"""Stable 40-hex SHA1 fingerprint of a violation.

    Hashes ``(change_type, normalized_pointer, canonical-JSON before,
    canonical-JSON after)`` separated by ASCII unit-separator ``\x1f``
    bytes, matching the scheme used by
    ``scripts/utils/discrepancy_fingerprint.py`` in api-specs.
    """
    parts = [
        change_type,
        _normalize_pointer(pointer),
        json.dumps(before, sort_keys=True, separators=(",", ":"), default=str),
        json.dumps(after, sort_keys=True, separators=(",", ":"), default=str),
    ]
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha1(payload, usedforsecurity=False).hexdigest()


def load_known_drift(path: Path | str | None) -> set[str]:
    """Load the fingerprint set from a known_drift JSON (or .json.gz) file.

    Returns an empty set if ``path`` is None, the file is missing, or
    the file is an empty JSON. Raises ``ValueError`` with a pointer to
    the offending index if any entry is missing ``fingerprint`` or the
    value is not a string — the drift file is hand-edited (spec §5.5),
    so a clear error beats a bare ``KeyError``.
    """
    if path is None:
        return set()
    p = Path(path)
    if not p.exists():
        return set()
    if p.suffix == ".gz":
        with gzip.open(p, "rt", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.loads(p.read_text())
    out: set[str] = set()
    for i, entry in enumerate(data.get("entries", [])):
        fp = entry.get("fingerprint")
        if not isinstance(fp, str):
            # ValueError (not TypeError) is the contract: the drift file as a
            # whole is malformed — missing key and wrong type are one class of
            # hand-edit mistake from the operator's perspective.
            raise ValueError(  # noqa: TRY004
                f"{p}: entries[{i}] missing or non-string 'fingerprint'",
            )
        out.add(fp)
    return out


_CONSTRAINT_KEYS = frozenset(
    {
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "enum",
        "format",
    },
)
_SHAPE_KEYS = frozenset({"type", "$ref", "required", "operationId", "oneOf", "anyOf", "allOf"})


@dataclass
class Violation:
    """One non-additive change detected between input and output specs."""

    change_type: str
    pointer: str
    before: Any
    after: Any
    rule_category: str


@dataclass(frozen=True)
class DeclaredPropertyRemoval:
    """One canonical, issue-linked breaking property removal."""

    schema_pattern: str
    property_name: str
    issue: str


def load_declared_removals(
    path: Path | str = SCHEMA_OVERRIDES_PATH,
) -> list[DeclaredPropertyRemoval]:
    """Load canonical property removals from ``schema_overrides.yaml``.

    ``SchemaOverrideEnricher`` performs the fail-closed canonical and issue-link
    validation. Reusing its compiled configuration prevents the pipeline and the
    contract gate from interpreting two subtly different declaration formats.
    """
    enricher = SchemaOverrideEnricher(config_path=Path(path))
    return [
        DeclaredPropertyRemoval(**declaration)
        for declaration in enricher.get_removal_declarations()
    ]


def _normalize_declared_removals(
    input_spec: dict[str, Any],
    output_spec: dict[str, Any],
    declarations: Sequence[DeclaredPropertyRemoval],
) -> tuple[dict[str, Any], list[Violation]]:
    """Apply declared removals to input and flag incomplete output cleanup."""
    normalized = copy.deepcopy(input_spec)
    input_schemas = normalized.get("components", {}).get("schemas", {})
    output_schemas = output_spec.get("components", {}).get("schemas", {})
    violations: list[Violation] = []

    for declaration in declarations:
        pattern = re.compile(declaration.schema_pattern)
        matching_names = sorted(name for name in input_schemas if pattern.search(name))
        if not matching_names:
            message = (
                "declared removal pattern matched no canonical schema: "
                f"{declaration.schema_pattern} ({declaration.issue})"
            )
            raise ValueError(message)

        for schema_name in matching_names:
            input_schema = input_schemas[schema_name]
            if not isinstance(input_schema, dict):
                continue
            try:
                remove_schema_property(input_schema, declaration.property_name)
            except KeyError as error:
                message = (
                    "declared canonical removal target is absent: "
                    f"{schema_name}.{declaration.property_name}"
                )
                raise ValueError(message) from error

            output_schema = output_schemas.get(schema_name)
            if not isinstance(output_schema, dict):
                continue
            output_probe = copy.deepcopy(output_schema)
            residue = remove_schema_property(
                output_probe,
                declaration.property_name,
                require_present=False,
            )
            if not (
                residue.properties_removed
                or residue.metadata_references_removed
                or residue.oneof_groups_removed
            ):
                continue
            pointer = f"root['components']['schemas']['{schema_name}']"
            violations.append(
                Violation(
                    change_type="declared_removal_incomplete",
                    pointer=pointer,
                    before={
                        "property": declaration.property_name,
                        "issue": declaration.issue,
                    },
                    after={
                        "property_present": bool(residue.properties_removed),
                        "stale_metadata_references": residue.metadata_references_removed,
                    },
                    rule_category="declared-removal-incomplete",
                ),
            )
    return normalized, violations


def _categorize(change_type: str, pointer: str) -> str:
    """Classify a non-additive change into a rule category for reporting."""
    terminal = ""
    if "'" in pointer:
        parts = pointer.rsplit("'", 2)
        if len(parts) >= 2:
            terminal = parts[-2]
    if change_type.endswith("_removed") or change_type == "iterable_item_removed":
        return "removal"
    if terminal in _CONSTRAINT_KEYS:
        return "constraint-change"
    if terminal in _SHAPE_KEYS or change_type == "type_changes":
        return "shape-change"
    return "other"


def _flatten_allof_refs(obj: Any) -> Any:
    """Collapse single-element allOf $ref wrappers for diffing.

    ``{"allOf": [{"$ref": "X"}], "x-foo": 1}`` becomes
    ``{"$ref": "X", "x-foo": 1}`` so that the contract diff sees
    allOf-wrapped refs as equivalent to direct refs.

    Both forms mean the same thing in JSON Schema, and the wrapper appears on
    *either* side: enrichment wraps a bare ``$ref`` so it can attach a sibling
    ``description`` (OpenAPI 3.0 ignores siblings of ``$ref``), while a handful
    of upstream properties already ship wrapped. So this normalization must be
    applied to both specs — see ``run_contract_diff``.
    """
    if isinstance(obj, dict):
        allof = obj.get("allOf")
        if (
            isinstance(allof, list)
            and len(allof) == 1
            and isinstance(allof[0], dict)
            and list(allof[0].keys()) == ["$ref"]
        ):
            flat: dict[str, Any] = {"$ref": allof[0]["$ref"]}
            for k, v in obj.items():
                if k != "allOf":
                    flat[k] = _flatten_allof_refs(v)
            return flat
        return {k: _flatten_allof_refs(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_flatten_allof_refs(item) for item in obj]
    return obj


def run_contract_diff(
    input_spec: dict,
    output_spec: dict,
    known_drift: set[str] | None = None,
    declared_removals: Sequence[DeclaredPropertyRemoval] | None = None,
) -> list[Violation]:
    """Compare two spec dicts and return all non-additive changes as violations.

    Args:
        input_spec: the stage-1 (upstream) merged spec dict.
        output_spec: the enriched merged spec dict.
        known_drift: optional set of violation fingerprints to tolerate.
            Any violation whose ``_fingerprint_violation`` hash is in this
            set is suppressed (design spec 2026-04-22 §5).
        declared_removals: canonical, issue-linked property removals to normalize.
    """
    known = known_drift or set()
    input_spec, removal_violations = _normalize_declared_removals(
        input_spec,
        output_spec,
        declared_removals or (),
    )
    # Normalize both sides: an allOf-wrapped $ref is semantically identical to a
    # direct $ref, and either spec may use either form. Flattening only the
    # output turned upstream-wrapped properties into a phantom `allOf` removal
    # plus `$ref` add.
    input_spec = _flatten_allof_refs(input_spec)
    output_spec = _flatten_allof_refs(output_spec)
    diff = DeepDiff(
        input_spec,
        output_spec,
        ignore_order=True,
        view="tree",
        threshold_to_diff_deeper=0.0,
    )
    violations: list[Violation] = list(removal_violations)
    for change_type, changes in diff.items():
        for change in changes:
            pointer = change.path()
            before = getattr(change, "t1", None)
            after = getattr(change, "t2", None)
            if is_additive_change(change_type, pointer, before, after):
                continue
            fp = _fingerprint_violation(change_type, pointer, before, after)
            if fp in known:
                continue
            violations.append(
                Violation(
                    change_type=change_type,
                    pointer=pointer,
                    before=before,
                    after=after,
                    rule_category=_categorize(change_type, pointer),
                ),
            )
    return violations


_MERGE_KEYS: tuple[str, ...] = ("paths", "definitions")
_COMPONENT_MERGE_KEYS: tuple[str, ...] = (
    "schemas",
    "responses",
    "parameters",
    "examples",
    "requestBodies",
    "headers",
    "securitySchemes",
    "links",
    "callbacks",
)


def _merge_specs(
    specs: list[tuple[str, dict]],
) -> dict[str, Any]:
    """Union the path/definitions/components.* maps across many OpenAPI specs.

    The enrichment pipeline merges ~520 per-resource upstream specs
    into ~40 per-category enriched specs, so a 1:1 file comparison is
    not meaningful. Instead, union every spec's ``paths``,
    ``definitions``, and ``components.<bucket>`` maps into a single
    aggregate dict on each side and diff those.

    Args:
        specs: list of ``(filename, parsed_json)`` tuples.

    Returns:
        A single dict with unioned top-level maps. Uses recursive
        union merge (superset of all sources) to match the pipeline.
    """
    return canonical_merge_sources(specs).merged


def _load_specs_from_dir(spec_dir: Path) -> list[tuple[str, dict]]:
    """Load every ``*.json`` under ``spec_dir`` except ``manifest.json``.

    ``index.json`` in the enriched output is also skipped — it is a
    pipeline-emitted catalog of domains, not an OpenAPI spec.
    """
    loaded: list[tuple[str, dict]] = []
    for path in sorted(spec_dir.glob("*.json")):
        if path.name in {
            "manifest.json",
            "index.json",
            "namespace_profiles.json",
            "resource_coverage.json",
            "validation.json",
        }:
            continue
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict):
            loaded.append((path.name, doc))
    return loaded


def run_directory_diff(
    input_dir: Path,
    output_dir: Path,
    known_drift: set[str] | None = None,
    declared_removals: Sequence[DeclaredPropertyRemoval] | None = None,
) -> list[Violation]:
    """Diff the merged contract between two directories of OpenAPI specs.

    The pipeline fans in ~520 per-resource upstream specs and fans out
    into ~40 per-category enriched specs, so a naive per-file diff
    would report every input filename as ``file_removed``. Instead we
    union every spec's paths and components on each side and compare
    the two aggregates.
    """
    input_specs = _load_specs_from_dir(input_dir)
    canonical_output = output_dir / "openapi.json"
    if canonical_output.is_file():
        output_specs = [(canonical_output.name, json.loads(canonical_output.read_text()))]
    else:
        output_specs = _load_specs_from_dir(output_dir)
    merged_input = _merge_specs(input_specs)
    merged_output = (
        {
            "paths": output_specs[0][1].get("paths", {}),
            "components": output_specs[0][1].get("components", {}),
        }
        if canonical_output.is_file()
        else _merge_specs(output_specs)
    )
    return run_contract_diff(
        merged_input,
        merged_output,
        known_drift=known_drift,
        declared_removals=declared_removals,
    )


def render_markdown_report(violations: list[Violation]) -> str:
    """Render a human-readable markdown table of violations grouped by category."""
    if not violations:
        return "## Contract-diff gate\n\nNo violations.\n"
    lines = ["## Contract-diff gate\n", f"**{len(violations)} violation(s).**\n"]
    by_cat: dict[str, list[Violation]] = {}
    for v in violations:
        by_cat.setdefault(v.rule_category, []).append(v)
    for cat, vs in sorted(by_cat.items()):
        lines.append(f"### {cat} ({len(vs)})")
        lines.append("")
        lines.append("| Pointer | Before | After |")
        lines.append("|---|---|---|")
        lines.extend(f"| `{v.pointer}` | `{v.before!r}` | `{v.after!r}` |" for v in vs)
        lines.append("")
    return "\n".join(lines)


def live_sample(
    spec: dict,
    *,
    n: int,
    probe: Callable[[str, str], dict],
    rng_seed: int | None = None,
) -> list[dict]:
    """Probe N random paths from the enriched spec against the live API.

    ``probe(path, method)`` MUST return a dict including at least
    ``status_code`` and ``ok``. Each returned entry includes the probed
    ``path`` and ``method`` alongside the probe result.
    """
    rng = random.Random(rng_seed)
    paths = list(spec.get("paths", {}).keys())
    rng.shuffle(paths)
    sampled = paths[: min(n, len(paths))]
    results = []
    for p in sampled:
        ops = spec["paths"][p]
        method = "GET" if "get" in ops else "OPTIONS"
        results.append({"path": p, "method": method, **probe(p, method)})
    return results


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: diff two directories and emit JSON + Markdown reports."""
    # The additive-allowlist classifier runs an inner DeepDiff per candidate
    # dict-rewrite (Rule 3). On deeply-nested OpenAPI subtrees deepdiff's own
    # recursion can exceed the 1000-frame default; bump the limit for the CLI.
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Directory with stage-1 JSON files",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Directory with enriched JSON files",
    )
    parser.add_argument("--report", type=Path, default=Path("reports/contract_diff.json"))
    parser.add_argument("--markdown", type=Path, default=Path("reports/contract_diff.md"))
    parser.add_argument(
        "--live-sample",
        type=int,
        default=0,
        help="Probe N random paths against the live F5XC API",
    )
    parser.add_argument(
        "--live-fail-on-mismatch",
        action="store_true",
        help="Non-zero exit when any live probe fails (default: informational only)",
    )
    parser.add_argument(
        "--known-drift",
        type=Path,
        default=Path("tests/fixtures/contract_diff_known_drift.json.gz"),
        help="Path to known_drift JSON (fingerprints to tolerate). "
        "Missing file = empty set (no tolerance).",
    )
    parser.add_argument(
        "--declared-removals",
        type=Path,
        default=SCHEMA_OVERRIDES_PATH,
        help="Canonical issue-linked remove_properties declarations.",
    )
    args = parser.parse_args(argv)

    known_drift = load_known_drift(args.known_drift)
    declared_removals = load_declared_removals(args.declared_removals)
    violations = run_directory_diff(
        args.input,
        args.output,
        known_drift=known_drift,
        declared_removals=declared_removals,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(
            [v.__dict__ for v in violations],
            indent=2,
            default=str,
            sort_keys=True,
        ),
    )
    args.markdown.write_text(render_markdown_report(violations))
    rc = 1 if violations else 0

    if args.live_sample > 0:
        api_url = os.environ.get("F5XC_API_URL")
        api_token = os.environ.get("F5XC_API_TOKEN")
        if not api_url or not api_token:
            print(
                "live-sample requested but F5XC_API_URL/TOKEN not set; skipping",
                file=sys.stderr,
            )
        else:
            client = httpx.Client(
                base_url=api_url,
                headers={"Authorization": f"Bearer {api_token}"},
                timeout=30.0,
            )
            try:
                # Pick a representative merged spec to sample; openapi.json aggregates all paths.
                merged = args.output / "openapi.json"
                if merged.exists():
                    spec = json.loads(merged.read_text())

                    def _probe(path: str, method: str) -> dict:
                        try:
                            r = client.request(method, path)
                        except httpx.HTTPError as exc:
                            return {"status_code": 0, "ok": False, "error": str(exc)}
                        return {
                            "status_code": r.status_code,
                            "ok": 200 <= r.status_code < 300,
                        }

                    live_results = live_sample(
                        spec,
                        n=args.live_sample,
                        probe=_probe,
                        rng_seed=0,
                    )
                    (args.report.parent / "contract_diff_live.json").write_text(
                        json.dumps(live_results, indent=2),
                    )
                    if args.live_fail_on_mismatch and any(not r.get("ok") for r in live_results):
                        rc = max(1, rc)
            finally:
                client.close()

    return rc


if __name__ == "__main__":
    sys.exit(main())
