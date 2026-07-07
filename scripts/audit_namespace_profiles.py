"""Audit namespace profile classifications against available signals.

Combines multiple signal sources to assess confidence in each resource's
namespace profile classification:

  1. Spec descriptions — explicit "always system" / "only system" language
  2. Path patterns — hardcoded /namespaces/system/ vs parameterized {namespace}
  3. Example values — x-ves-example: "System" vs "Ns1"
  4. Suggest-values paths — hardcoded namespace in suggest-values endpoints
  5. Existing config — current namespace_profile.yaml classification

Outputs a confidence-scored report showing which classifications are well-supported
by signals and which need manual verification.

Usage:
    python scripts/audit_namespace_profiles.py
    python scripts/audit_namespace_profiles.py --output audit_report.yaml
    python scripts/audit_namespace_profiles.py --flag-low-confidence
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

SYSTEM_DESCRIPTION_PATTERNS = [
    re.compile(r"namespace is always system", re.IGNORECASE),
    re.compile(r"always in system namespace", re.IGNORECASE),
    re.compile(r"supports only system namespace", re.IGNORECASE),
    re.compile(r"only system namespace", re.IGNORECASE),
    re.compile(r"must be system namespace", re.IGNORECASE),
    re.compile(r"restricted to system", re.IGNORECASE),
]


def load_config(config_path: Path) -> dict[str, Any]:
    """Load namespace_profile.yaml."""
    with config_path.open() as f:
        return yaml.safe_load(f)


def load_index(specs_dir: Path) -> list[dict[str, Any]]:
    """Load the specification index."""
    index_path = specs_dir / "index.json"
    with index_path.open() as f:
        index = json.load(f)
    specs = index.get("specifications", [])
    if isinstance(specs, dict):
        return list(specs.values())
    return specs


def mine_description_signals(specs_dir: Path) -> dict[str, list[dict[str, str]]]:
    """Scan all spec files for namespace constraint language in descriptions."""
    results: dict[str, list[dict[str, str]]] = {}

    for entry in sorted(specs_dir.iterdir()):
        fname = entry.name
        if not fname.endswith(".json") or fname in ("index.json", "namespace_profiles.json"):
            continue
        with (specs_dir / fname).open() as f:
            spec = json.load(f)

        domain = fname.replace(".json", "")
        schemas = spec.get("components", {}).get("schemas", {})
        for schema_name, schema_def in schemas.items():
            props = schema_def.get("properties", {})
            ns_prop = props.get("namespace", {})
            desc = ns_prop.get("description", "")

            for pattern in SYSTEM_DESCRIPTION_PATTERNS:
                if pattern.search(desc):
                    resource = _schema_to_resource(schema_name)
                    if resource not in results:
                        results[resource] = []
                    results[resource].append(
                        {
                            "signal": "description",
                            "schema": schema_name,
                            "text": desc[:150],
                            "domain": domain,
                            "inferred": "system",
                        }
                    )
                    break

    return results


def mine_path_signals(specs: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Analyze API paths for hardcoded vs parameterized namespaces."""
    results: dict[str, list[dict[str, str]]] = {}

    for domain_info in specs:
        domain = domain_info.get("domain", "")
        for resource in domain_info.get("x-f5xc-primary-resources", []):
            name = resource.get("name", "")
            api_paths = resource.get("api_paths", [])

            system_hardcoded = [p for p in api_paths if "/namespaces/system/" in p]
            parameterized = [
                p for p in api_paths if "{namespace}" in p or "{metadata.namespace}" in p
            ]

            if system_hardcoded and not parameterized:
                if name not in results:
                    results[name] = []
                results[name].append(
                    {
                        "signal": "paths_system_only",
                        "domain": domain,
                        "detail": f"{len(system_hardcoded)} system-only paths, 0 parameterized",
                        "inferred": "system",
                    }
                )
            elif system_hardcoded and parameterized:
                if name not in results:
                    results[name] = []
                results[name].append(
                    {
                        "signal": "paths_mixed",
                        "domain": domain,
                        "detail": (
                            f"{len(system_hardcoded)} system paths + "
                            f"{len(parameterized)} parameterized paths"
                        ),
                        "inferred": "mixed",
                    }
                )

    return results


def mine_example_signals(specs_dir: Path) -> dict[str, list[dict[str, str]]]:
    """Check x-ves-example and x-f5xc-example values for namespace fields."""
    results: dict[str, list[dict[str, str]]] = {}

    for entry in sorted(specs_dir.iterdir()):
        fname = entry.name
        if not fname.endswith(".json") or fname in ("index.json", "namespace_profiles.json"):
            continue
        with (specs_dir / fname).open() as f:
            spec = json.load(f)

        domain = fname.replace(".json", "")
        schemas = spec.get("components", {}).get("schemas", {})
        for schema_name, schema_def in schemas.items():
            props = schema_def.get("properties", {})
            ns_prop = props.get("namespace", {})
            ves_example = ns_prop.get("x-ves-example", "")
            f5_example = ns_prop.get("x-f5xc-example", "")

            if ves_example.lower() == "system" or f5_example.lower() == "system":
                resource = _schema_to_resource(schema_name)
                if resource not in results:
                    results[resource] = []
                results[resource].append(
                    {
                        "signal": "example_system",
                        "schema": schema_name,
                        "x-ves-example": ves_example,
                        "x-f5xc-example": f5_example,
                        "domain": domain,
                        "inferred": "system",
                    }
                )

    return results


def _schema_to_resource(schema_name: str) -> str:
    """Extract base resource name from a schema name.

    Handles patterns like:
      - dns_zoneCreateSpecType → dns_zone
      - dns_zoneDnsZoneMetricsRequest → dns_zone
      - dns_domainVerifyDnsDomainRequest → dns_domain
      - oidc_providerReplaceRequest → oidc_provider
      - siteSiteStatusMetricsRequest → site
      - schemaoidc_providerDeleteRequest → oidc_provider
    """
    result = schema_name
    result = re.sub(r"^(views|schema)", "", result)

    suffixes = [
        "CreateSpecType",
        "ReplaceSpecType",
        "GetSpecType",
        "DeleteRequest",
        "ListResponseItem",
        "ListRequest",
        "GetRequest",
        "MetricsRequest",
        "RequestLogRequest",
        "ReplaceRequest",
        "CreateRequest",
        "Object",
        "StatusObject",
    ]
    for suffix in suffixes:
        if result.endswith(suffix):
            result = result[: -len(suffix)]
            break

    # Handle CamelCase compound names like dns_zoneDnsZone → dns_zone
    # or siteSiteStatus → site
    # Strategy: find the longest known resource prefix that uses underscores
    parts = re.split(r"(?<=[a-z_])(?=[A-Z])", result, maxsplit=1)
    if len(parts) > 1:
        candidate = parts[0]
        if "_" in candidate or candidate[0].islower():
            result = candidate

    return result


def classify_resources(
    config: dict[str, Any],
    specs: list[dict[str, Any]],
    desc_signals: dict[str, list[dict[str, str]]],
    path_signals: dict[str, list[dict[str, str]]],
    example_signals: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """Classify each resource's namespace profile confidence."""
    default_allowed = config["default_profile"]["constraint"]["allowed"]
    resources_config = config.get("resources", {})
    all_resource_names = set()

    for domain_info in specs:
        for resource in domain_info.get("x-f5xc-primary-resources", []):
            all_resource_names.add(resource["name"])

    for name in resources_config:
        all_resource_names.add(name)

    audit_entries = []

    for name in sorted(all_resource_names):
        resource_config = resources_config.get(name, {})
        config_allowed = resource_config.get("constraint", {}).get("allowed", default_allowed)
        config_category = resource_config.get("classification", {}).get("category", "")
        is_system_only = sorted(config_allowed) == ["system"]
        is_default_profile = name not in resources_config or "constraint" not in resource_config

        signals = []
        signals.extend(desc_signals.get(name, []))
        signals.extend(path_signals.get(name, []))
        signals.extend(example_signals.get(name, []))

        system_signal_count = sum(1 for s in signals if s.get("inferred") == "system")
        has_system_signals = system_signal_count > 0

        if is_system_only and has_system_signals:
            confidence = "high"
            status = "confirmed"
        elif is_system_only and not has_system_signals:
            confidence = "low"
            status = "needs_verification"
        elif not is_system_only and has_system_signals:
            confidence = "low"
            status = "potential_misclassification"
        elif is_default_profile:
            confidence = "medium"
            status = "uses_default"
        else:
            confidence = "high"
            status = "explicitly_configured"

        entry: dict[str, Any] = {
            "resource": name,
            "config_allowed": config_allowed,
            "is_system_only": is_system_only,
            "uses_default_profile": is_default_profile,
            "confidence": confidence,
            "status": status,
            "signal_count": len(signals),
        }
        if config_category:
            entry["category"] = config_category
        if signals:
            entry["signals"] = signals

        audit_entries.append(entry)

    return audit_entries


def print_audit_report(entries: list[dict[str, Any]], flag_low_confidence: bool = False) -> None:
    """Print a human-readable audit report."""
    stats = {"high": 0, "medium": 0, "low": 0}
    for e in entries:
        stats[e["confidence"]] += 1

    print(f"\n{'=' * 70}")
    print("  NAMESPACE PROFILE AUDIT REPORT")
    print(f"{'=' * 70}")
    print(f"  Total resources: {len(entries)}")
    print(f"  High confidence: {stats['high']}")
    print(f"  Medium confidence: {stats['medium']}")
    print(f"  Low confidence (needs review): {stats['low']}")

    low_entries = [e for e in entries if e["confidence"] == "low"]
    if low_entries:
        print(f"\n{'─' * 70}")
        print("  LOW CONFIDENCE — Needs manual verification:")
        print(f"{'─' * 70}")
        for e in low_entries:
            label = (
                "SYSTEM-ONLY (no signals)"
                if e["is_system_only"]
                else "TENANT-SCOPED (has system signals)"
            )
            print(f"\n  {e['resource']} — {label}")
            print(f"    Config: {e['config_allowed']}")
            if e.get("category"):
                print(f"    Category: {e['category']}")
            if e.get("signals"):
                for s in e["signals"][:3]:
                    print(f"    Signal: {s.get('signal', '?')} → {s.get('inferred', '?')}")

    if flag_low_confidence:
        medium_entries = [e for e in entries if e["confidence"] == "medium"]
        if medium_entries:
            print(f"\n{'─' * 70}")
            print("  MEDIUM CONFIDENCE — Uses default profile (no explicit override):")
            print(f"{'─' * 70}")
            for e in medium_entries:
                print(f"  {e['resource']}: {e['config_allowed']}")

    print(f"\n{'=' * 70}")


def main() -> None:
    """Run the namespace profile audit."""
    parser = argparse.ArgumentParser(description="Audit namespace profile classifications")
    parser.add_argument(
        "--specs-dir",
        default="docs/specifications/api",
        help="Enriched specs directory",
    )
    parser.add_argument(
        "--config",
        default="config/namespace_profile.yaml",
        help="Namespace profile config file",
    )
    parser.add_argument(
        "--output",
        default="namespace_audit_report.yaml",
        help="Output report path",
    )
    parser.add_argument(
        "--flag-low-confidence",
        action="store_true",
        help="Also flag medium-confidence resources",
    )
    args = parser.parse_args()

    specs_dir = Path(args.specs_dir)
    config = load_config(Path(args.config))
    specs = load_index(specs_dir)

    print("Mining signals from spec descriptions...")
    desc_signals = mine_description_signals(specs_dir)
    print(f"  Found {len(desc_signals)} resources with description signals")

    print("Mining signals from API path patterns...")
    path_signals = mine_path_signals(specs)
    print(f"  Found {len(path_signals)} resources with path signals")

    print("Mining signals from example values...")
    example_signals = mine_example_signals(specs_dir)
    print(f"  Found {len(example_signals)} resources with example signals")

    entries = classify_resources(config, specs, desc_signals, path_signals, example_signals)
    print_audit_report(entries, flag_low_confidence=args.flag_low_confidence)

    with Path(args.output).open("w") as f:
        yaml.dump(entries, f, default_flow_style=False, sort_keys=False)
    print(f"\nFull report written to {args.output}")


if __name__ == "__main__":
    main()
