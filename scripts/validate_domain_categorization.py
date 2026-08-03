#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Validate domain categorization against natural identifiers in original specs.

This script compares the current regex-based domain categorization
(from config/domain_patterns.yaml) against natural identifiers found
within the original JSON spec files.

Natural identifiers examined:
- x-displayname: Human-readable name assigned by F5 engineers
- x-ves-proto-package: Package namespace hierarchy
- API path prefixes: Domain hints from endpoint paths

Usage:
    python scripts/validate_domain_categorization.py
    make validate-domains
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from utils.domain_categorizer import categorize_spec

# Path prefix to domain mappings (derived from API structure)
PATH_PREFIX_DOMAIN_MAP: dict[str, str] = {
    "/api/shape": "shape",
    "/api/infraprotect": "ddos",
    "/api/cdn": "cdn",
    "/api/secret_management": "blindfold",
    "/api/nginx": "nginx_one",
    "/api/observability": "observability",
    "/api/alert": "statistics",
    "/api/tpm": "bot_and_threat_defense",
    "/api/mobile": "bot_and_threat_defense",
    "/api/scim": "tenant_and_identity",
    "/api/bigipconnector": "bigip",
    "/api/ai_data": "ai_services",
    "/api/gen-ai": "ai_services",
    "/api/register": "ce_management",
    "/api/waf": "waf",
    "/api/object_store": "object_storage",
    "/api/report": "statistics",
}

# Package namespace to domain mappings
PACKAGE_NAMESPACE_DOMAIN_MAP: dict[str, str] = {
    "shape": "shape",
    "api_sec": "api",
    "nginx": "nginx_one",
    "tenant_management": "tenant_and_identity",
    "pbac": "marketplace",
    "observability": "observability",
    "billing": "billing_and_usage",
    "data_privacy": "data_and_privacy_security",
    "usage": "billing_and_usage",
    "operate": "support",
    "bigip": "bigip",
    "bigcne": "bigip",
    "ai_data": "ai_services",
    "user": "users",
    "was": "api",  # Web App Scanning
}

# Display name keyword to domain mappings
DISPLAYNAME_KEYWORD_MAP: dict[str, str] = {
    "firewall": "waf",
    "waf": "waf",
    "load balancer": "virtual",
    "loadbalancer": "virtual",
    "origin pool": "virtual",
    "dns": "dns",
    "cdn": "cdn",
    "bot": "bot_and_threat_defense",
    "certificate": "certificates",
    "secret": "blindfold",
    "shape": "shape",
    "infraprotect": "ddos",
    "ddos": "ddos",
    "kubernetes": "managed_kubernetes",
    "k8s": "managed_kubernetes",
    "vk8s": "container_services",
    "virtual kubernetes": "container_services",
    "workload": "container_services",
    "site": "site_management",
    "aws": "site_management",
    "azure": "site_management",
    "gcp": "site_management",
    "bgp": "network",
    "route": "network",
    "tunnel": "network",
    "rate limit": "rate_limiting",
    "policer": "rate_limiting",
    "alert": "statistics",
    "log": "statistics",
    "user": "users",
    "tenant": "tenant_and_identity",
    "rbac": "tenant_and_identity",
    "scim": "tenant_and_identity",
    "nginx": "nginx_one",
    "bigip": "bigip",
    "irule": "bigip",
    "api": "api",
    "discovery": "api",
    "crawler": "api",
    "mobile sdk": "bot_and_threat_defense",
    "registration": "ce_management",
    "module management": "ce_management",
    "synthetic": "observability",
    "monitor": "observability",
    "service mesh": "service_mesh",
    "endpoint": "service_mesh",
    "network policy": "network_security",
    "fast acl": "network_security",
    "service policy": "virtual",
    "virtual host": "virtual",
    "k8s cluster": "managed_kubernetes",
    "cluster role": "managed_kubernetes",
    "pod security": "managed_kubernetes",
    "container registry": "managed_kubernetes",
    "fleet": "ce_management",
    "virtual network": "network",
    "billing": "billing_and_usage",
    "subscription": "billing_and_usage",
    "payment": "billing_and_usage",
    "voltshare": "blindfold",
}


class NaturalIdentifiers(NamedTuple):
    """Natural identifiers extracted from a spec file."""

    displayname: str
    package: str
    path_prefix: str
    first_path: str


class ValidationResult(NamedTuple):
    """Result of validating a single spec's domain categorization."""

    filename: str
    regex_domain: str
    natural_identifiers: NaturalIdentifiers
    suggested_domains: list[str]
    status: str  # MATCH, MISMATCH, AMBIGUOUS
    confidence: float
    notes: str


def extract_natural_identifiers(spec_path: Path) -> NaturalIdentifiers | None:
    """Extract natural identifiers from a spec file.

    Args:
        spec_path: Path to the OpenAPI spec JSON file

    Returns:
        NaturalIdentifiers tuple or None if extraction fails
    """
    try:
        with spec_path.open() as f:
            spec = json.load(f)

        displayname = spec.get("x-displayname", "")
        package = spec.get("x-ves-proto-package", "")

        # Extract first path and path prefix
        paths = spec.get("paths", {})
        if paths:
            first_path = next(iter(paths.keys()))
            # Extract path prefix (e.g., /api/config from /api/config/namespaces/...)
            parts = first_path.split("/")
            path_prefix = "/" + "/".join(parts[1:3]) if len(parts) >= 3 else first_path
        else:
            first_path = ""
            path_prefix = ""

        return NaturalIdentifiers(
            displayname=displayname,
            package=package,
            path_prefix=path_prefix,
            first_path=first_path,
        )
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading {spec_path}: {e}")
        return None


def infer_domains_from_identifiers(identifiers: NaturalIdentifiers) -> list[str]:
    """Infer likely domains from natural identifiers.

    Args:
        identifiers: Natural identifiers from the spec

    Returns:
        List of suggested domain names (may be empty)
    """
    suggestions: list[str] = []

    # Check path prefix mapping
    if identifiers.path_prefix in PATH_PREFIX_DOMAIN_MAP:
        suggestions.append(PATH_PREFIX_DOMAIN_MAP[identifiers.path_prefix])

    # Check package namespace
    if identifiers.package:
        # Extract first namespace after ves.io.schema.
        match = re.match(r"ves\.io\.schema\.([^.]+)", identifiers.package)
        if match:
            ns = match.group(1)
            if ns in PACKAGE_NAMESPACE_DOMAIN_MAP:
                suggestions.append(PACKAGE_NAMESPACE_DOMAIN_MAP[ns])

    # Check displayname keywords
    if identifiers.displayname:
        display_lower = identifiers.displayname.lower()
        for keyword, domain in DISPLAYNAME_KEYWORD_MAP.items():
            if keyword in display_lower:
                suggestions.append(domain)
                break  # Only take first match

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for d in suggestions:
        if d not in seen:
            seen.add(d)
            unique.append(d)

    return unique


def validate_spec(spec_path: Path) -> ValidationResult | None:
    """Validate domain categorization for a single spec.

    Args:
        spec_path: Path to the spec file

    Returns:
        ValidationResult or None if validation fails
    """
    filename = spec_path.name

    # Get current regex-based categorization
    regex_domain = categorize_spec(filename)

    # Extract natural identifiers
    identifiers = extract_natural_identifiers(spec_path)
    if identifiers is None:
        return None

    # Infer domains from natural identifiers
    suggested_domains = infer_domains_from_identifiers(identifiers)

    # Determine status and confidence
    if not suggested_domains:
        status = "AMBIGUOUS"
        confidence = 0.0
        notes = "No domain hints from natural identifiers"
    elif regex_domain in suggested_domains:
        status = "MATCH"
        confidence = 1.0 / len(suggested_domains)  # Higher if fewer suggestions
        notes = f"Regex agrees with {len(suggested_domains)} natural hint(s)"
    elif regex_domain == "other" and suggested_domains:
        status = "MISMATCH"
        confidence = 0.5
        notes = f"Regex returned 'other' but hints suggest: {', '.join(suggested_domains)}"
    else:
        status = "MISMATCH"
        confidence = 0.3
        notes = f"Regex returned '{regex_domain}' but hints suggest: {', '.join(suggested_domains)}"

    return ValidationResult(
        filename=filename,
        regex_domain=regex_domain,
        natural_identifiers=identifiers,
        suggested_domains=suggested_domains,
        status=status,
        confidence=confidence,
        notes=notes,
    )


def generate_report(results: list[ValidationResult], output_path: Path) -> dict[str, Any]:
    """Generate markdown validation report.

    Args:
        results: List of validation results
        output_path: Path to write the markdown report

    Returns:
        Summary statistics dictionary
    """
    matches = [r for r in results if r.status == "MATCH"]
    mismatches = [r for r in results if r.status == "MISMATCH"]
    ambiguous = [r for r in results if r.status == "AMBIGUOUS"]

    total = len(results)
    stats = {
        "total": total,
        "matches": len(matches),
        "mismatches": len(mismatches),
        "ambiguous": len(ambiguous),
        "match_rate": len(matches) / total * 100 if total > 0 else 0,
    }

    lines = [
        "# Domain Categorization Validation Report",
        "",
        f"Generated: {datetime.now(tz=UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "## Summary",
        "",
        f"- **Total specs analyzed**: {stats['total']}",
        f"- **Matches**: {stats['matches']} ({stats['match_rate']:.1f}%)",
        f"- **Mismatches**: {stats['mismatches']} ({stats['mismatches'] / total * 100:.1f}%)"
        if total > 0
        else "",
        f"- **Ambiguous**: {stats['ambiguous']} ({stats['ambiguous'] / total * 100:.1f}%)"
        if total > 0
        else "",
        "",
        "## Natural Identifiers Used",
        "",
        "| Identifier | Source | Purpose |",
        "|------------|--------|---------|",
        "| `x-displayname` | Top-level extension | Human-readable name |",
        "| `x-ves-proto-package` | Top-level extension | Package namespace hierarchy |",
        "| API path prefix | First path in spec | Domain routing hint |",
        "",
    ]

    # Mismatches section
    if mismatches:
        lines.extend(
            [
                "## Mismatches (Review Needed)",
                "",
                "These specs have a mismatch between regex categorization and natural identifier hints.",
                "",
                "| Filename | Regex Result | Natural Hints | Displayname | Package |",
                "|----------|--------------|---------------|-------------|---------|",
            ],
        )
        for r in sorted(mismatches, key=lambda x: x.regex_domain):
            hints = ", ".join(r.suggested_domains) if r.suggested_domains else "-"
            # Truncate long values
            displayname = (
                r.natural_identifiers.displayname[:40] + "..."
                if len(r.natural_identifiers.displayname) > 40
                else r.natural_identifiers.displayname
            )
            package = r.natural_identifiers.package.replace("ves.io.schema.", "")[:30]
            lines.append(
                f"| `{r.filename[:60]}...` | {r.regex_domain} | {hints} | {displayname} | {package} |",
            )
        lines.append("")

    # Ambiguous section
    if ambiguous:
        lines.extend(
            [
                "## Ambiguous (Manual Review)",
                "",
                "These specs have no clear domain hints from natural identifiers.",
                "",
                "| Filename | Regex Result | Displayname | Package | Path Prefix |",
                "|----------|--------------|-------------|---------|-------------|",
            ],
        )
        for r in sorted(ambiguous, key=lambda x: x.regex_domain):
            displayname = r.natural_identifiers.displayname[:30] or "-"
            package = r.natural_identifiers.package.replace("ves.io.schema.", "")[:25] or "-"
            path_prefix = r.natural_identifiers.path_prefix or "-"
            lines.append(
                f"| `{r.filename[:50]}...` | {r.regex_domain} | {displayname} | {package} | {path_prefix} |",
            )
        lines.append("")

    # Matches section (summary only)
    if matches:
        lines.extend(
            [
                "## Matches (Confirmed)",
                "",
                f"**{len(matches)} specs** have regex categorization matching natural identifier hints.",
                "",
                "### Domain Distribution",
                "",
                "| Domain | Count | Example |",
                "|--------|-------|---------|",
            ],
        )
        # Group by domain
        domain_groups: dict[str, list[ValidationResult]] = {}
        for r in matches:
            if r.regex_domain not in domain_groups:
                domain_groups[r.regex_domain] = []
            domain_groups[r.regex_domain].append(r)

        for domain in sorted(domain_groups.keys()):
            group = domain_groups[domain]
            example = group[0].natural_identifiers.displayname[:40] or group[0].filename[:40]
            lines.append(f"| {domain} | {len(group)} | {example} |")
        lines.append("")

    # Path prefix analysis
    lines.extend(
        [
            "## Path Prefix Analysis",
            "",
            "Distribution of API path prefixes across specs:",
            "",
            "| Path Prefix | Count | Mapped Domain |",
            "|-------------|-------|---------------|",
        ],
    )
    prefix_counts: dict[str, int] = {}
    for r in results:
        prefix = r.natural_identifiers.path_prefix
        prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1

    for prefix, count in sorted(prefix_counts.items(), key=lambda x: -x[1]):
        mapped = PATH_PREFIX_DOMAIN_MAP.get(prefix, "(no mapping)")
        lines.append(f"| `{prefix}` | {count} | {mapped} |")
    lines.append("")

    # Write report
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))

    print(f"Report written to: {output_path}")
    return stats


def main() -> None:
    """Main entry point."""
    # Paths
    project_root = Path(__file__).parent.parent
    specs_dir = project_root / "specs" / "original"
    report_path = project_root / "reports" / "domain-validation-report.md"

    if not specs_dir.exists():
        print(f"Error: Specs directory not found: {specs_dir}")
        print("Run 'make download' first to fetch original specs.")
        return

    # Find all spec files
    spec_files = sorted(specs_dir.glob("*.json"))
    if not spec_files:
        print(f"No spec files found in {specs_dir}")
        return

    print(f"Analyzing {len(spec_files)} spec files...")

    # Validate each spec
    results: list[ValidationResult] = []
    for spec_path in spec_files:
        result = validate_spec(spec_path)
        if result:
            results.append(result)

    # Generate report
    stats = generate_report(results, report_path)

    # Print summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    print(f"Total specs: {stats['total']}")
    print(f"Matches:     {stats['matches']} ({stats['match_rate']:.1f}%)")
    print(f"Mismatches:  {stats['mismatches']}")
    print(f"Ambiguous:   {stats['ambiguous']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
