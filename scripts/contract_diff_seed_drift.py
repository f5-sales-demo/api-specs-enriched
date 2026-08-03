# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""One-shot seeder for the contract-diff known-drift fixture.

Reads the current ``reports/contract_diff.json`` and emits
``tests/fixtures/contract_diff_known_drift.json``.

Run after ``python -m scripts.contract_diff ...`` has produced the
report. Each emitted entry is fingerprinted and linked to one of four
tracking issues (f5-sales-demo/api-specs-enriched #193-#196) based
on category. Re-running this script against a fresh report regenerates
the fixture with today's date and the canonical issue refs.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.contract_diff import _fingerprint_violation, _normalize_pointer

_CATEGORY_ISSUES = {
    "maxLength-tightened": "f5-sales-demo/api-specs-enriched#193",
    "removal": "f5-sales-demo/api-specs-enriched#194",
    "ref-retarget": "f5-sales-demo/api-specs-enriched#195",
    "operationId-rename": "f5-sales-demo/api-specs-enriched#196",
    "misc": "f5-sales-demo/api-specs-enriched#196",
}


def _categorize_for_drift(violation: dict) -> str:
    """Return one of the five category keys used to select an issue ref."""
    change_type = violation["change_type"]
    before = violation.get("before")
    after = violation.get("after")
    pointer = violation["pointer"]
    # DeepDiff pointers look like root['a']['b']['maxLength'] — carve out
    # the last quoted segment. Non-quoted forms fall through to `misc`.
    terminal = pointer.rsplit("'", 2)[-2] if "'" in pointer else ""

    # maxLength tightened
    if (
        terminal == "maxLength"
        and change_type == "values_changed"
        and isinstance(before, int)
        and isinstance(after, int)
    ):
        return "maxLength-tightened"

    # Any removal
    if violation.get("rule_category") == "removal":
        return "removal"

    # $ref retarget
    if terminal == "$ref" and change_type == "values_changed":
        return "ref-retarget"

    # operationId rename
    if terminal == "operationId" and change_type == "values_changed":
        return "operationId-rename"

    return "misc"


def build_drift_entries(report_path: Path) -> list[dict]:
    """Return drift entries derived from the contract-diff JSON report."""
    report = json.loads(report_path.read_text())
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    entries = []
    for i, v in enumerate(report):
        try:
            change_type = v["change_type"]
            pointer = v["pointer"]
        except KeyError as e:
            msg = f"report entry {i} missing required key {e.args[0]!r}"
            raise ValueError(msg) from e
        fp = _fingerprint_violation(change_type, pointer, v.get("before"), v.get("after"))
        category = _categorize_for_drift(v)
        entries.append(
            {
                "fingerprint": fp,
                "change_type": change_type,
                "pointer_glob": _normalize_pointer(pointer),
                "before": v.get("before"),
                "after": v.get("after"),
                "issue": _CATEGORY_ISSUES[category],
                "category": category,
                "added": today,
            }
        )
    return entries


def _write_payload(out: Path, payload: dict) -> None:
    """Write payload as JSON, gzipped when the suffix is .gz.

    The gate-side loader (scripts/contract_diff.load_known_drift)
    auto-detects gzip via suffix, so callers pick the encoding by
    path alone.
    """
    # Compact separators + one-entry-per-line for readable diffs when
    # tracking issues close and entries are pruned.
    entries = payload["entries"]
    header = {k: v for k, v in payload.items() if k != "entries"}
    body = json.dumps(header, separators=(",", ":"))[:-1] + ',"entries":['
    parts = [body]
    for i, entry in enumerate(entries):
        sep = "" if i == 0 else ","
        parts.append(sep + "\n" + json.dumps(entry, separators=(",", ":")))
    parts.append("\n]}\n")
    text = "".join(parts)

    if out.suffix == ".gz":
        with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
            f.write(text)
    else:
        out.write_text(text)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the seed script."""
    parser = argparse.ArgumentParser(description="Seed known_drift from reports/contract_diff.json")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("reports/contract_diff.json"),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("tests/fixtures/contract_diff_known_drift.json.gz"),
    )
    args = parser.parse_args(argv)

    if not args.report.exists():
        print(f"Missing {args.report}; run contract_diff first.", file=sys.stderr)
        return 1

    entries = build_drift_entries(args.report)
    # Dedupe by fingerprint; first occurrence wins.
    seen: set[str] = set()
    deduped = []
    for e in entries:
        if e["fingerprint"] in seen:
            continue
        seen.add(e["fingerprint"])
        deduped.append(e)

    payload = {
        "version": 1,
        "description": (
            "Violations the contract-diff gate tolerates today. Each entry "
            "is a real gap between enricher behavior and spec §4.3 that "
            "needs individual triage. Add fingerprints here ONLY when linked "
            "to a tracking issue. Entries should shrink over time."
        ),
        "entries": sorted(deduped, key=lambda e: (e["category"], e["fingerprint"])),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    _write_payload(args.out, payload)
    print(f"Wrote {len(deduped)} entries to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
