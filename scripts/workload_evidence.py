#!/usr/bin/env python3
"""Build deterministic workload evidence and evaluate performance gates."""
# ruff: noqa: D103, ICN001, PERF401, S314

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree

if TYPE_CHECKING:
    from collections.abc import Iterable

SCHEMA_VERSION = 1
VARIANT_RE = re.compile(r"^(?P<runner>d8|d16)-w(?P<workers>1|2|4|8)$")


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def tree_manifest(root: Path) -> list[dict[str, Any]]:
    result = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        content = path.read_bytes()
        result.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": len(content),
                "sha256": digest(content),
            }
        )
    return result


def normalized_pytest_outcomes(path: Path) -> list[dict[str, str]]:
    result = []
    for case in ElementTree.parse(path).getroot().iter("testcase"):
        outcome = next(
            (name for name in ("failure", "error", "skipped") if case.find(name) is not None),
            "passed",
        )
        result.append(
            {
                "classname": case.get("classname", ""),
                "name": case.get("name", ""),
                "outcome": outcome,
            }
        )
    return sorted(result, key=lambda item: (item["classname"], item["name"]))


def deterministic_zip(root: Path, output: Path) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for item in tree_manifest(root):
            info = zipfile.ZipInfo(item["path"], (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (root / item["path"]).read_bytes())
    return digest(output.read_bytes())


def build_evidence(
    tree: Path | None = None,
    pytest_xml: Path | None = None,
    manifests: Iterable[Path] = (),
    archive: Path | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    if tree:
        files = tree_manifest(tree)
        value["generated_tree"] = {"files": files, "digest": digest(canonical_json(files))}
    if pytest_xml:
        outcomes = normalized_pytest_outcomes(pytest_xml)
        value["pytest"] = {"outcomes": outcomes, "digest": digest(canonical_json(outcomes))}
    items = []
    for path in sorted(manifests, key=lambda item: item.as_posix()):
        document = json.loads(path.read_text(encoding="utf-8"))
        items.append({"name": path.name, "digest": digest(canonical_json(document))})
    if items:
        value["release_manifests"] = {"items": items, "digest": digest(canonical_json(items))}
    if archive:
        if not tree:
            raise ValueError("archive output requires a generated tree")
        value["archive"] = {"digest": deterministic_zip(tree, archive)}
    value["output_digest"] = digest(canonical_json(value))
    return value


def enrich_profile(profile_path: Path, evidence_path: Path, memory_path: Path | None) -> None:
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if profile.get("schema_version") != 1 or evidence.get("schema_version") != 1:
        raise ValueError("unsupported profile or evidence schema")
    profile["output_digest"] = evidence["output_digest"]
    if memory_path:
        memory = json.loads(memory_path.read_text(encoding="utf-8"))
        timings = [
            {"name": item["name"], "duration_seconds": item["phase_duration_seconds"]}
            for item in memory.get("checkpoints", [])
        ]
        if timings:
            profile["phase_timings"] = timings
    profile_path.write_bytes(canonical_json(profile))


def percentile95(values: list[float]) -> float:
    return sorted(values)[max(0, math.ceil(0.95 * len(values)) - 1)]


def evaluate_group(
    baselines: list[dict[str, Any]], candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    baseline = {str(item["pair_id"]): item for item in baselines if item.get("pair_id")}
    candidate = {str(item["pair_id"]): item for item in candidates if item.get("pair_id")}
    pairs = sorted(set(baseline) & set(candidate))
    before = [float(baseline[pair]["duration_seconds"]) for pair in pairs]
    after = [float(candidate[pair]["duration_seconds"]) for pair in pairs]
    before_median = statistics.median(before) if before else None
    after_median = statistics.median(after) if after else None
    improvement = (
        (before_median - after_median) / before_median
        if before_median and after_median is not None
        else None
    )
    equivalent = len(pairs) >= 5 and all(
        baseline[pair].get("output_digest") is not None
        and baseline[pair].get("output_digest") == candidate[pair].get("output_digest")
        for pair in pairs
    )
    stable = len(pairs) >= 5 and all(
        item.get("exit", {}).get("code") == 0
        and item.get("memory", {}).get("events", {}).get("oom", 0) == 0
        and item.get("memory", {}).get("events", {}).get("oom_kill", 0) == 0
        for pair in pairs
        for item in (baseline[pair], candidate[pair])
    )
    memory_ok = len(pairs) >= 5 and all(
        candidate[pair].get("memory", {}).get("peak_limit_ratio") is not None
        and candidate[pair]["memory"]["peak_limit_ratio"] < 0.8
        for pair in pairs
    )
    before_p95 = percentile95(before) if before else None
    after_p95 = percentile95(after) if after else None
    qualifies = bool(
        len(pairs) >= 5
        and improvement is not None
        and improvement >= 0.2
        and before_p95 is not None
        and after_p95 is not None
        and after_p95 <= before_p95
        and equivalent
        and stable
        and memory_ok
    )
    return {
        "paired_runs": len(pairs),
        "baseline_median_seconds": before_median,
        "candidate_median_seconds": after_median,
        "median_improvement_ratio": improvement,
        "baseline_p95_seconds": before_p95,
        "candidate_p95_seconds": after_p95,
        "output_equivalent": equivalent,
        "stable": stable,
        "memory_below_80_percent": memory_ok,
        "qualifies": qualifies,
    }


def select_candidate(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    qualified = [item for item in results if item.get("qualifies")]
    if not qualified:
        return None
    best_p95 = min(float(item["candidate_p95_seconds"]) for item in qualified)
    close = [item for item in qualified if float(item["candidate_p95_seconds"]) <= best_p95 * 1.05]

    def rank(item: dict[str, Any]) -> tuple[int, int, float, str]:
        match = VARIANT_RE.fullmatch(str(item.get("variant", "")))
        runner = match.group("runner") if match else "z"
        workers = int(match.group("workers")) if match else 999
        return (
            0 if runner == "d8" else 1,
            workers,
            float(item["candidate_p95_seconds"]),
            str(item.get("variant")),
        )

    return min(close, key=rank)


def evaluate_profiles(paths: Iterable[Path]) -> dict[str, Any]:
    profiles = []
    for path in paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema_version") != 1:
            raise ValueError(f"{path}: unsupported workload profile")
        profiles.append(value)
    results = []
    groups = sorted({(str(item.get("phase")), str(item.get("cache_state"))) for item in profiles})
    for phase, cache_state in groups:
        values = [
            item
            for item in profiles
            if item.get("phase") == phase and str(item.get("cache_state")) == cache_state
        ]
        baselines = [item for item in values if item.get("variant") == "baseline"]
        for variant in sorted({str(item.get("variant")) for item in values} - {"baseline"}):
            results.append(
                {
                    "phase": phase,
                    "cache_state": cache_state,
                    "variant": variant,
                    **evaluate_group(
                        baselines, [item for item in values if item.get("variant") == variant]
                    ),
                }
            )
    return {"schema_version": 1, "comparisons": results, "selected": select_candidate(results)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    evidence = commands.add_parser("evidence")
    evidence.add_argument("--tree", type=Path)
    evidence.add_argument("--pytest-xml", type=Path)
    evidence.add_argument("--manifest", action="append", type=Path, default=[])
    evidence.add_argument("--archive", type=Path)
    evidence.add_argument("--output", required=True, type=Path)
    enrich = commands.add_parser("enrich-profile")
    enrich.add_argument("--profile", required=True, type=Path)
    enrich.add_argument("--evidence", required=True, type=Path)
    enrich.add_argument("--memory", type=Path)
    gate = commands.add_parser("gate")
    gate.add_argument("profiles", nargs="+", type=Path)
    gate.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "evidence":
        value = build_evidence(args.tree, args.pytest_xml, args.manifest, args.archive)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_json(value))
    elif args.command == "enrich-profile":
        enrich_profile(args.profile, args.evidence, args.memory)
    else:
        args.output.write_bytes(canonical_json(evaluate_profiles(args.profiles)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
