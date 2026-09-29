"""Gate upstream removals and publish deterministic contract-change evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
import yaml

from scripts.download import extract_zip, load_config, verify_release_asset_digest
from scripts.utils.canonical_merge import canonical_merge_sources
from scripts.utils.github_release import download_release_asset

STABLE_TAG = re.compile(r"^v\d{4}\.\d{2}\.\d{2}-\d+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ISSUE = re.compile(r"^(?:[\w.-]+/[\w.-]+)?#\d+$")
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
GITHUB_API = "https://api.github.com"


class UpstreamRemovalError(ValueError):
    """Raised when upstream evidence or removal acknowledgement is unsafe."""


@dataclass(frozen=True)
class Removal:
    """One removed upstream contract member."""

    category: str
    pointer: str
    value: Any
    fingerprint: str


def _fingerprint(category: str, pointer: str, value: Any) -> str:
    payload = json.dumps(
        [category, pointer, value], sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def load_release_receipt(path: Path) -> dict[str, Any]:
    """Load and strictly validate a committed upstream release receipt."""
    try:
        receipt = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise UpstreamRemovalError(
            f"cannot read upstream release receipt {path}: {error}"
        ) from error
    if not isinstance(receipt, dict):
        raise UpstreamRemovalError(f"upstream release receipt {path} must be an object")
    tag = receipt.get("tag_name")
    if not isinstance(tag, str) or not STABLE_TAG.fullmatch(tag):
        raise UpstreamRemovalError(f"upstream release receipt {path} has an invalid stable tag")
    if receipt.get("version") != tag.removeprefix("v"):
        raise UpstreamRemovalError(f"upstream release receipt {path} version does not match tag")
    expected_asset = f"api-specs-{tag}.zip"
    if receipt.get("asset_name") != expected_asset:
        raise UpstreamRemovalError(
            f"upstream release receipt {path} asset name does not match tag: {expected_asset}"
        )
    digest = receipt.get("asset_sha256")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise UpstreamRemovalError(f"upstream release receipt {path} has an invalid SHA-256")
    if not isinstance(receipt.get("asset_size"), int) or receipt["asset_size"] < 1:
        raise UpstreamRemovalError(f"upstream release receipt {path} has an invalid asset size")
    if not isinstance(receipt.get("published_at"), str):
        raise UpstreamRemovalError(f"upstream release receipt {path} has no publication time")
    return receipt


def fetch_release_by_tag(
    owner: str, repository: str, tag: str, token: str | None = None
) -> dict[str, Any]:
    """Fetch the exact GitHub release named by a receipt."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repository}/releases/tags/{quote(tag, safe='')}",
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    release = response.json()
    if not isinstance(release, dict):
        raise UpstreamRemovalError(f"GitHub release response for {tag} is malformed")
    return release


def validate_receipt_asset(receipt: dict[str, Any], release: dict[str, Any]) -> dict[str, Any]:
    """Bind a receipt to one exact GitHub release asset or fail closed."""
    tag = receipt["tag_name"]
    if release.get("tag_name") != tag:
        raise UpstreamRemovalError(f"GitHub release tag does not match receipt {tag}")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpstreamRemovalError(f"GitHub release {tag} has a malformed asset list")
    matches = [asset for asset in assets if asset.get("name") == receipt["asset_name"]]
    if len(matches) != 1:
        raise UpstreamRemovalError(
            f"GitHub release {tag} does not contain exactly one {receipt['asset_name']} asset"
        )
    asset = matches[0]
    expected_digest = f"sha256:{receipt['asset_sha256']}"
    if asset.get("digest") != expected_digest:
        raise UpstreamRemovalError(f"GitHub release {tag} asset digest does not match receipt")
    if asset.get("size") != receipt["asset_size"]:
        raise UpstreamRemovalError(f"GitHub release {tag} asset size does not match receipt")
    url = asset.get("browser_download_url")
    if not isinstance(url, str) or not url.startswith("https://github.com/"):
        raise UpstreamRemovalError(f"GitHub release {tag} asset URL is invalid")
    return asset


def _load_source_graph(directory: Path) -> dict[str, Any]:
    specs: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        if path.name == "manifest.json":
            continue
        document = json.loads(path.read_text())
        if isinstance(document, dict) and isinstance(document.get("paths"), dict):
            specs[path.name] = document
    if not specs:
        raise UpstreamRemovalError(f"no OpenAPI source documents found in {directory}")
    return canonical_merge_sources(specs).merged


def _escape(segment: object) -> str:
    return str(segment).replace("~", "~0").replace("/", "~1")


def _removed_map_entries(
    category: str, prefix: str, previous: dict[str, Any], current: dict[str, Any]
) -> list[Removal]:
    findings = []
    for key in sorted(previous.keys() - current.keys()):
        pointer = f"{prefix}/{_escape(key)}"
        value = previous[key]
        findings.append(Removal(category, pointer, value, _fingerprint(category, pointer, value)))
    return findings


def find_removals(previous: dict[str, Any], current: dict[str, Any]) -> list[Removal]:
    """Enumerate schema/property/route/method/enum/required removals."""
    findings: list[Removal] = []
    previous_schemas = previous.get("components", {}).get("schemas", {})
    current_schemas = current.get("components", {}).get("schemas", {})
    findings.extend(
        _removed_map_entries("schema", "/components/schemas", previous_schemas, current_schemas)
    )
    for schema_name in sorted(previous_schemas.keys() & current_schemas.keys()):
        before = previous_schemas[schema_name]
        after = current_schemas[schema_name]
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue
        findings.extend(
            _removed_map_entries(
                "property",
                f"/components/schemas/{_escape(schema_name)}/properties",
                before.get("properties", {})
                if isinstance(before.get("properties", {}), dict)
                else {},
                after.get("properties", {})
                if isinstance(after.get("properties", {}), dict)
                else {},
            )
        )

    previous_paths = previous.get("paths", {})
    current_paths = current.get("paths", {})
    findings.extend(_removed_map_entries("path", "/paths", previous_paths, current_paths))
    for path in sorted(previous_paths.keys() & current_paths.keys()):
        before_item = previous_paths[path]
        after_item = current_paths[path]
        if not isinstance(before_item, dict) or not isinstance(after_item, dict):
            continue
        for method in sorted((before_item.keys() - after_item.keys()) & HTTP_METHODS):
            pointer = f"/paths/{_escape(path)}/{method}"
            value = before_item[method]
            findings.append(
                Removal("method", pointer, value, _fingerprint("method", pointer, value))
            )

    def walk(before: Any, after: Any, pointer: str) -> None:
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(before.keys() & after.keys()):
                walk(before[key], after[key], f"{pointer}/{_escape(key)}")
            return
        if isinstance(before, list) and isinstance(after, list):
            terminal = pointer.rsplit("/", 1)[-1]
            if terminal not in {"enum", "required"}:
                return
            for value in before:
                if value not in after:
                    category = "enum-member" if terminal == "enum" else "required-entry"
                    member_pointer = f"{pointer}/{_escape(value)}"
                    findings.append(
                        Removal(
                            category,
                            member_pointer,
                            value,
                            _fingerprint(category, member_pointer, value),
                        )
                    )

    walk(previous, current, "")
    unique = {finding.fingerprint: finding for finding in findings}
    return sorted(unique.values(), key=lambda finding: (finding.category, finding.pointer))


def load_acknowledgements(path: Path) -> dict[str, dict[str, str]]:
    """Load and validate dated, issue-linked acknowledgements."""
    document = yaml.safe_load(path.read_text()) if path.exists() else {}
    entries = (document or {}).get("acknowledgements", [])
    if not isinstance(entries, list):
        raise UpstreamRemovalError("acknowledgements must be a list")
    result: dict[str, dict[str, str]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise UpstreamRemovalError(f"acknowledgements[{index}] must be an object")
        fingerprint = entry.get("fingerprint")
        issue = entry.get("issue")
        acknowledged = entry.get("acknowledged")
        if not isinstance(fingerprint, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", fingerprint
        ):
            raise UpstreamRemovalError(f"acknowledgements[{index}] has invalid fingerprint")
        if not isinstance(issue, str) or not ISSUE.fullmatch(issue):
            raise UpstreamRemovalError(f"acknowledgements[{index}] has invalid issue reference")
        try:
            acknowledged_date = date.fromisoformat(str(acknowledged))
        except ValueError as error:
            raise UpstreamRemovalError(
                f"acknowledgements[{index}] has invalid acknowledgement date"
            ) from error
        if acknowledged_date > datetime.now(timezone.utc).date():
            raise UpstreamRemovalError(f"acknowledgements[{index}] is future-dated")
        if fingerprint in result:
            raise UpstreamRemovalError(f"duplicate acknowledgement: {fingerprint}")
        result[fingerprint] = {"issue": issue, "acknowledged": acknowledged_date.isoformat()}
    return result


def build_report(
    previous_tag: str,
    current_tag: str,
    removals: list[Removal],
    acknowledgements: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Bind every finding to its required acknowledgement or fail."""
    missing = [
        finding.fingerprint for finding in removals if finding.fingerprint not in acknowledgements
    ]
    if missing:
        raise UpstreamRemovalError(
            f"{len(missing)} upstream contract removal(s) lack acknowledgement; first: {missing[0]}"
        )
    findings = [
        {**asdict(finding), "acknowledgement": acknowledgements[finding.fingerprint]}
        for finding in removals
    ]
    return {
        "schema_version": 1,
        "previous_release": previous_tag,
        "current_release": current_tag,
        "removal_count": len(findings),
        "removals": findings,
    }


def _receipt_identity(receipt: dict[str, Any]) -> dict[str, str]:
    return {
        "tag_name": receipt["tag_name"],
        "asset_name": receipt["asset_name"],
        "sha256": f"sha256:{receipt['asset_sha256']}",
    }


def operation_inventory(document: dict[str, Any]) -> list[dict[str, str]]:
    """Return sorted path-plus-method identities for every HTTP operation."""
    inventory: list[dict[str, str]] = []
    paths = document.get("paths", {})
    if not isinstance(paths, dict):
        return inventory
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        inventory.extend(
            {"path": path, "method": method.upper()}
            for method in HTTP_METHODS
            if isinstance(item.get(method), dict)
        )
    return sorted(inventory, key=lambda operation: (operation["path"], operation["method"]))


def _property_inventory(document: dict[str, Any]) -> dict[tuple[str, str], Any]:
    inventory: dict[tuple[str, str], Any] = {}
    schemas = document.get("components", {}).get("schemas", {})
    if not isinstance(schemas, dict):
        return inventory
    for schema_name, schema in schemas.items():
        if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
            continue
        for property_name, value in schema["properties"].items():
            inventory[(schema_name, property_name)] = value
    return inventory


def build_change_report(
    baseline_receipt: dict[str, Any],
    target_receipt: dict[str, Any],
    baseline: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    """Build a stable, complete upstream operation and schema-property diff."""
    baseline_operations = operation_inventory(baseline)
    target_operations = operation_inventory(target)
    baseline_keys = {(item["path"], item["method"]) for item in baseline_operations}
    target_keys = {(item["path"], item["method"]) for item in target_operations}

    def operation(key: tuple[str, str]) -> dict[str, str]:
        return {"path": key[0], "method": key[1]}

    before_properties = _property_inventory(baseline)
    after_properties = _property_inventory(target)
    before_keys = set(before_properties)
    after_keys = set(after_properties)
    added = [
        {"schema": schema, "property": prop, "value": after_properties[(schema, prop)]}
        for schema, prop in sorted(after_keys - before_keys)
    ]
    removed = [
        {"schema": schema, "property": prop, "value": before_properties[(schema, prop)]}
        for schema, prop in sorted(before_keys - after_keys)
    ]
    modified = [
        {
            "schema": schema,
            "property": prop,
            "before": before_properties[(schema, prop)],
            "after": after_properties[(schema, prop)],
        }
        for schema, prop in sorted(before_keys & after_keys)
        if before_properties[(schema, prop)] != after_properties[(schema, prop)]
    ]
    operation_additions = [operation(key) for key in sorted(target_keys - baseline_keys)]
    operation_removals = [operation(key) for key in sorted(baseline_keys - target_keys)]
    return {
        "schema_version": 1,
        "baseline": _receipt_identity(baseline_receipt),
        "target": _receipt_identity(target_receipt),
        "operations": {
            "baseline_count": len(baseline_operations),
            "target_count": len(target_operations),
            "addition_count": len(operation_additions),
            "removal_count": len(operation_removals),
            "baseline": baseline_operations,
            "target": target_operations,
            "additions": operation_additions,
            "removals": operation_removals,
        },
        "schema_properties": {
            "addition_count": len(added),
            "removal_count": len(removed),
            "modification_count": len(modified),
            "added": added,
            "removed": removed,
            "modified": modified,
        },
    }


def render_removals_markdown(report: dict[str, Any]) -> str:
    """Render the acknowledged-removal gate summary."""
    lines = [
        "# Upstream contract removals",
        "",
        (
            f"Compared `{report['previous_release']}` with `{report['current_release']}`: "
            f"{report['removal_count']} acknowledged removal(s)."
        ),
        "",
    ]
    counts: dict[str, int] = {}
    for removal in report["removals"]:
        counts[removal["category"]] = counts.get(removal["category"], 0) + 1
    if counts:
        lines.extend(f"- {category}: {count}" for category, count in sorted(counts.items()))
        lines.append("")
    lines.extend(["See `upstream-contract-removals.json` for the complete receipted report.", ""])
    return "\n".join(lines)


def render_change_markdown(report: dict[str, Any]) -> str:
    """Render release-note evidence while the JSON asset carries full inventories."""
    operations = report["operations"]
    properties = report["schema_properties"]
    lines = [
        "# Upstream contract changes",
        "",
        f"- Baseline: `{report['baseline']['tag_name']}` / `{report['baseline']['asset_name']}` / `{report['baseline']['sha256']}`",
        f"- Target: `{report['target']['tag_name']}` / `{report['target']['asset_name']}` / `{report['target']['sha256']}`",
        f"- Operations: {operations['baseline_count']} baseline, {operations['target_count']} target, {operations['addition_count']} added, {operations['removal_count']} removed",
        f"- Schema properties: {properties['addition_count']} added, {properties['removal_count']} removed, {properties['modification_count']} modified",
        "",
        "## Added operations",
        "",
    ]
    lines.extend(f"- `{item['method']} {item['path']}`" for item in operations["additions"])
    if not operations["additions"]:
        lines.append("- None")
    lines.extend(["", "## Removed operations", ""])
    lines.extend(f"- `{item['method']} {item['path']}`" for item in operations["removals"])
    if not operations["removals"]:
        lines.append("- None")
    lines.extend(
        [
            "",
            "The complete sorted operation inventories and schema-property additions, removals, and modifications (including full before/after values) are in `upstream-contract-changes.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    """Compare the explicit consumed and target receipts and emit evidence."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-dir", type=Path, default=Path("specs/original"))
    parser.add_argument("--previous-receipt", type=Path, required=True)
    parser.add_argument("--current-receipt", type=Path, required=True)
    parser.add_argument(
        "--acknowledgements", type=Path, default=Path("config/upstream_contract_removals.yaml")
    )
    parser.add_argument(
        "--removals-report", type=Path, default=Path("release/upstream-contract-removals.json")
    )
    parser.add_argument(
        "--removals-markdown", type=Path, default=Path("release/upstream-contract-removals.md")
    )
    parser.add_argument(
        "--changes-report", type=Path, default=Path("release/upstream-contract-changes.json")
    )
    parser.add_argument(
        "--changes-markdown", type=Path, default=Path("release/upstream-contract-changes.md")
    )
    args = parser.parse_args()

    previous_receipt = load_release_receipt(args.previous_receipt)
    current_receipt = load_release_receipt(args.current_receipt)
    token = os.getenv("GITHUB_TOKEN")
    previous_release = fetch_release_by_tag(
        "f5-sales-demo", "api-specs", previous_receipt["tag_name"], token
    )
    current_release = fetch_release_by_tag(
        "f5-sales-demo", "api-specs", current_receipt["tag_name"], token
    )
    previous_asset = validate_receipt_asset(previous_receipt, previous_release)
    validate_receipt_asset(current_receipt, current_release)

    with tempfile.TemporaryDirectory(prefix="upstream-contract-") as temporary:
        root = Path(temporary)
        archive = root / previous_receipt["asset_name"]
        if not download_release_asset(previous_asset["browser_download_url"], archive, token=token):
            raise UpstreamRemovalError("failed to securely download baseline release")
        actual_digest = verify_release_asset_digest(archive, previous_asset)
        if actual_digest != previous_receipt["asset_sha256"]:
            raise UpstreamRemovalError("downloaded baseline digest does not match receipt")
        previous_dir = root / "previous"
        extract_zip(archive, previous_dir, load_config(Path("config/download.yaml")))
        previous_graph = _load_source_graph(previous_dir)
        current_graph = _load_source_graph(args.current_dir)

    changes_report = build_change_report(
        previous_receipt, current_receipt, previous_graph, current_graph
    )
    args.changes_report.parent.mkdir(parents=True, exist_ok=True)
    args.changes_report.write_text(json.dumps(changes_report, indent=2, sort_keys=True) + "\n")
    args.changes_markdown.write_text(render_change_markdown(changes_report))
    removals_report = build_report(
        previous_receipt["tag_name"],
        current_receipt["tag_name"],
        find_removals(previous_graph, current_graph),
        load_acknowledgements(args.acknowledgements),
    )
    args.removals_report.parent.mkdir(parents=True, exist_ok=True)
    args.removals_report.write_text(json.dumps(removals_report, indent=2, sort_keys=True) + "\n")
    args.removals_markdown.write_text(render_removals_markdown(removals_report))
    print(
        f"Recorded {removals_report['removal_count']} acknowledged removals; "
        f"operations: {changes_report['operations']['addition_count']} added, "
        f"{changes_report['operations']['removal_count']} removed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
