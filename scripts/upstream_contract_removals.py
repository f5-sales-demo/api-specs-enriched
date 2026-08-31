"""Gate and publish removals between consecutive stable upstream releases."""

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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

import requests
import yaml

from scripts.download import extract_zip, load_config, verify_release_asset_digest
from scripts.utils.canonical_merge import canonical_merge_sources
from scripts.utils.github_release import download_release_asset, find_release_asset

STABLE_TAG = re.compile(r"^v\d{4}\.\d{2}\.\d{2}-\d+$")
ISSUE = re.compile(r"^(?:[\w.-]+/[\w.-]+)?#\d+$")


class UpstreamRemovalError(ValueError):
    """Raised when upstream release history or removal acknowledgement is unsafe."""


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


def select_previous_stable_release(
    releases: Iterable[dict[str, Any]], current_tag: str
) -> dict[str, Any]:
    """Select the stable release immediately preceding the pinned release."""
    stable = [
        release
        for release in releases
        if release.get("draft") is False
        and release.get("prerelease") is False
        and isinstance(release.get("tag_name"), str)
        and STABLE_TAG.fullmatch(release["tag_name"])
        and isinstance(release.get("published_at"), str)
    ]
    current = next((release for release in stable if release["tag_name"] == current_tag), None)
    if current is None:
        raise UpstreamRemovalError(f"pinned stable upstream release not found: {current_tag}")
    earlier = [release for release in stable if release["published_at"] < current["published_at"]]
    if not earlier:
        raise UpstreamRemovalError(f"no stable release precedes {current_tag}")
    return max(earlier, key=lambda release: (release["published_at"], release["tag_name"]))


def fetch_releases(owner: str, repository: str, token: str | None = None) -> list[dict[str, Any]]:
    """Fetch all release pages needed to resolve a pinned release predecessor."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    releases: list[dict[str, Any]] = []
    page = 1
    while True:
        response = requests.get(
            f"https://api.github.com/repos/{owner}/{repository}/releases",
            headers=headers,
            params={"per_page": 100, "page": page},
            timeout=30,
        )
        response.raise_for_status()
        batch = response.json()
        if not isinstance(batch, list):
            raise UpstreamRemovalError("GitHub releases response is malformed")
        releases.extend(batch)
        if len(batch) < 100:
            return releases
        page += 1


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
    methods = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    for path in sorted(previous_paths.keys() & current_paths.keys()):
        before_item = previous_paths[path]
        after_item = current_paths[path]
        if not isinstance(before_item, dict) or not isinstance(after_item, dict):
            continue
        for method in sorted((before_item.keys() - after_item.keys()) & methods):
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


def render_markdown(report: dict[str, Any]) -> str:
    """Render the release-note summary for the complete JSON asset."""
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


def main() -> int:
    """Resolve the predecessor, compare it with the pinned source, and emit reports."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-dir", type=Path, default=Path("specs/original"))
    parser.add_argument("--release-receipt", type=Path, default=Path(".github_release"))
    parser.add_argument(
        "--acknowledgements", type=Path, default=Path("config/upstream_contract_removals.yaml")
    )
    parser.add_argument(
        "--report", type=Path, default=Path("release/upstream-contract-removals.json")
    )
    parser.add_argument(
        "--markdown", type=Path, default=Path("release/upstream-contract-removals.md")
    )
    args = parser.parse_args()
    receipt = json.loads(args.release_receipt.read_text())
    current_tag = receipt.get("tag_name")
    if not isinstance(current_tag, str) or not STABLE_TAG.fullmatch(current_tag):
        raise UpstreamRemovalError("upstream release receipt has an invalid stable tag")
    token = os.getenv("GITHUB_TOKEN")
    releases = fetch_releases("f5-sales-demo", "api-specs", token)
    previous = select_previous_stable_release(releases, current_tag)
    asset = find_release_asset(previous, "api-specs-v*.zip")
    if not asset:
        raise UpstreamRemovalError(f"{previous['tag_name']} has no API specification asset")
    with tempfile.TemporaryDirectory(prefix="upstream-removals-") as temporary:
        root = Path(temporary)
        archive = root / "previous.zip"
        if not download_release_asset(asset["browser_download_url"], archive, token=token):
            raise UpstreamRemovalError("failed to securely download previous release")
        verify_release_asset_digest(archive, asset)
        previous_dir = root / "previous"
        extract_zip(archive, previous_dir, load_config(Path("config/download.yaml")))
        removals = find_removals(
            _load_source_graph(previous_dir), _load_source_graph(args.current_dir)
        )
    report = build_report(
        previous["tag_name"],
        current_tag,
        removals,
        load_acknowledgements(args.acknowledgements),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    args.markdown.write_text(render_markdown(report))
    print(f"Recorded {report['removal_count']} acknowledged upstream contract removals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
