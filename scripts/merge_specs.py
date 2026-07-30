#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Merge multiple OpenAPI specifications into unified documents for documentation.

Creates merged specifications organized by domain for Swagger UI and Scalar portals.
Fully automated - no manual intervention required.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from scripts.utils.build_stamp import artifact_timestamp
from scripts.utils.description_enricher import DescriptionEnricher
from scripts.utils.domain_categorizer import categorize_spec as categorize_spec_util
from scripts.utils.domain_metadata import (
    calculate_complexity,
    get_domain_icon,
    get_metadata,
    get_primary_resources_metadata,
)
from scripts.utils.extension_constants import (
    X_F5XC_CATEGORY,
    X_F5XC_CLI_DOMAIN,
    X_F5XC_CLI_METADATA,
    X_F5XC_COMPLEXITY,
    X_F5XC_CRITICAL_RESOURCES,
    X_F5XC_DESCRIPTION_MEDIUM,
    X_F5XC_DESCRIPTION_SHORT,
    X_F5XC_ENRICHED_VERSION,
    X_F5XC_ICON,
    X_F5XC_IS_PREVIEW,
    X_F5XC_LOGO_SVG,
    X_F5XC_PRIMARY_RESOURCES,
    X_F5XC_RELATED_DOMAINS,
    X_F5XC_REQUIRES_TIER,
    X_F5XC_UPSTREAM_ETAG,
    X_F5XC_UPSTREAM_TIMESTAMP,
    X_F5XC_USE_CASES,
)
from scripts.utils.external_docs_enricher import ExternalDocsEnricher
from scripts.utils.json_writer import write_json_file
from scripts.utils.server_variables import ServerVariableHelper
from scripts.utils.version_calculator import get_version_from_tags

console = Console()

# Default critical resources list (fallback if config not found)
DEFAULT_CRITICAL_RESOURCES = [
    "http_loadbalancer",
    "tcp_loadbalancer",
    "origin_pool",
    "healthcheck",
    "app_firewall",
    "service_policy",
    "network_policy",
    "dns_zone",
    "dns_load_balancer",
    "certificate",
    "namespace",
    "virtual_site",
    "aws_vpc_site",
    "azure_vnet_site",
    "gcp_vpc_site",
]


def load_critical_resources() -> list[str]:
    """Load critical resources list from configuration.

    Returns list of resource names that downstream tooling relies on
    for schema generation and validation.
    """
    config_path = Path(__file__).parent.parent / "config" / "critical_resources.yaml"

    if not config_path.exists():
        return DEFAULT_CRITICAL_RESOURCES

    try:
        with config_path.open() as f:
            config = yaml.safe_load(f) or {}
        return config.get("resources", DEFAULT_CRITICAL_RESOURCES)
    except Exception as e:
        console.print(f"[yellow]Warning: Failed to load critical resources config: {e}[/yellow]")
        return DEFAULT_CRITICAL_RESOURCES


def load_spec(spec_path: Path) -> dict[str, Any]:
    """Load an OpenAPI specification from JSON file."""
    with spec_path.open() as f:
        return json.load(f)


def save_spec(spec: dict[str, Any], output_path: Path, indent: int = 2) -> None:
    """Save an OpenAPI specification to JSON file.

    Delegates to `write_json_file`, which applies Biome formatting so
    the output satisfies Super-Linter's BIOME_FORMAT check at commit time.
    """
    write_json_file(spec, output_path, indent=indent, ensure_ascii=False)


def categorize_spec(filename: str) -> str:
    """Categorize a specification file by domain based on filename patterns.

    Delegates to centralized domain_categorizer utility for pattern matching.
    """
    return categorize_spec_util(filename)


def create_base_spec(
    title: str,
    description: str,
    version: str,
    upstream_info: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a base OpenAPI specification structure.

    Args:
        title: API title
        description: API description
        version: Full version string (upstream-enriched format)
        upstream_info: Optional dict with upstream_timestamp, upstream_etag, enriched_version

    Delegates to ServerVariableHelper for centralized server variable management.
    """
    helper = ServerVariableHelper()
    return helper.create_base_spec(title, description, version, upstream_info)


def merge_components(
    target: dict[str, Any],
    source: dict[str, Any],
    prefix: str = "",
) -> dict[str, int]:
    """Merge components from source into target with conflict resolution."""
    stats = {"schemas": 0, "responses": 0, "parameters": 0, "requestBodies": 0}

    source_components = source.get("components", {})
    target_components = target.setdefault("components", {})

    # Merge schemas
    source_schemas = source_components.get("schemas", {})
    target_schemas = target_components.setdefault("schemas", {})
    for name, schema in source_schemas.items():
        prefixed_name = f"{prefix}{name}" if prefix else name
        if prefixed_name not in target_schemas:
            target_schemas[prefixed_name] = schema
            stats["schemas"] += 1

    # Merge responses
    source_responses = source_components.get("responses", {})
    target_responses = target_components.setdefault("responses", {})
    for name, response in source_responses.items():
        prefixed_name = f"{prefix}{name}" if prefix else name
        if prefixed_name not in target_responses:
            target_responses[prefixed_name] = response
            stats["responses"] += 1

    # Merge parameters
    source_params = source_components.get("parameters", {})
    target_params = target_components.setdefault("parameters", {})
    for name, param in source_params.items():
        prefixed_name = f"{prefix}{name}" if prefix else name
        if prefixed_name not in target_params:
            target_params[prefixed_name] = param
            stats["parameters"] += 1

    # Merge requestBodies (critical for Scalar/Swagger UI compatibility)
    source_request_bodies = source_components.get("requestBodies", {})
    target_request_bodies = target_components.setdefault("requestBodies", {})
    for name, request_body in source_request_bodies.items():
        prefixed_name = f"{prefix}{name}" if prefix else name
        if prefixed_name not in target_request_bodies:
            target_request_bodies[prefixed_name] = request_body
            stats["requestBodies"] += 1

    return stats


def merge_paths(target: dict[str, Any], source: dict[str, Any], domain: str = "") -> int:
    """Merge paths from source into target, filtering by domain.

    Filters out domain-specific paths when not in their respective domains,
    using pattern-based detection to prevent endpoint contamination.
    """
    source_paths = source.get("paths", {})
    target_paths = target.setdefault("paths", {})

    paths_added = 0
    is_cdn_domain = domain == "cdn_and_content_delivery"
    is_data_intelligence_domain = domain == "data_intelligence"
    is_user_mgmt_domain = domain == "user_and_account_management"
    is_threat_campaign_domain = domain == "threat_campaign"

    for path, path_item in source_paths.items():
        # Skip CDN paths if not merging into CDN domain
        if not is_cdn_domain and ("/api/cdn/" in path or "/cdn_loadbalancers/" in path):
            continue

        # Skip threat_campaign/threat_mesh paths if not merging into threat_campaign domain
        if not is_threat_campaign_domain and (
            "/api/waf/threat_campaign" in path or "/threat_mesh" in path
        ):
            continue

        # Skip data-intelligence paths if not merging into data_intelligence domain
        if not is_data_intelligence_domain and "/api/data-intelligence/" in path:
            continue

        # Skip credential management paths if not merging into user_and_account_management
        # Pattern-based: /api/web/ + (api_credentials|service_credentials|scim_token)
        is_credential_path = "/api/web/" in path and (
            "/api_credentials" in path or "/service_credentials" in path or "/scim_token" in path
        )
        if not is_user_mgmt_domain and is_credential_path:
            continue

        if path not in target_paths:
            target_paths[path] = path_item
            paths_added += 1
        else:
            # Merge methods if path already exists
            for method, operation in path_item.items():
                if method not in target_paths[path]:
                    target_paths[path][method] = operation
                    paths_added += 1

    return paths_added


def add_domain_metadata_to_spec(spec: dict[str, Any], domain: str) -> None:
    """Add domain classification metadata to spec (idempotent).

    Adds x-f5xc-cli-domain extension to the spec's info section.
    Preserves existing values if already present (idempotent behavior).

    Args:
        spec: OpenAPI specification to enhance
        domain: Domain classification (e.g., "virtual", "cdn_and_content_delivery")
    """
    if "info" not in spec:
        spec["info"] = {}

    info = spec["info"]

    # Idempotent: preserve existing x-f5xc-cli-domain
    if X_F5XC_CLI_DOMAIN not in info:
        info[X_F5XC_CLI_DOMAIN] = domain


def extract_tags(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Extract unique tags from a specification."""
    tags = []
    seen_names = set()

    # Get explicit tags
    for tag in spec.get("tags", []):
        if tag.get("name") and tag["name"] not in seen_names:
            tags.append(tag)
            seen_names.add(tag["name"])

    # Extract tags from paths
    for path_item in spec.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                for tag_name in operation.get("tags", []):
                    if tag_name not in seen_names:
                        tags.append({"name": tag_name})
                        seen_names.add(tag_name)

    return tags


def _process_single_spec_file(
    spec_file: Path,
    merged: dict[str, Any],
    domain: str = "",
) -> tuple[bool, int, dict[str, int], list[dict[str, str]], str]:
    """Process a single spec file for merging.

    Args:
        spec_file: Path to the specification file to process
        merged: Target merged specification to merge into
        domain: Domain category for filtering (e.g., "cdn_and_content_delivery")

    Returns:
        Tuple of (success, paths_added, comp_stats, tags, error_message).
    """
    try:
        spec = load_spec(spec_file)
        paths_added = merge_paths(merged, spec, domain=domain)
        comp_stats = merge_components(merged, spec)
        tags = extract_tags(spec)
        return True, paths_added, comp_stats, tags, ""
    except Exception as e:
        return False, 0, {"schemas": 0, "requestBodies": 0}, [], str(e)


def merge_specs_by_domain(
    specs_dir: Path,
    output_dir: Path,
    version: str,
    upstream_info: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Merge specifications grouped by domain."""
    spec_files = sorted(specs_dir.glob("*.json"))
    if not spec_files:
        console.print(f"[yellow]No specification files found in {specs_dir}[/yellow]")
        return {}

    # Group specs by domain
    domain_specs: dict[str, list[Path]] = defaultdict(list)
    for spec_file in spec_files:
        domain = categorize_spec(spec_file.name)
        domain_specs[domain].append(spec_file)

    console.print(f"[blue]Found {len(spec_files)} specs across {len(domain_specs)} domains[/blue]")

    merged_specs = {}
    stats = {"domains": 0, "specs": 0, "paths": 0, "schemas": 0, "requestBodies": 0}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Merging specifications...", total=len(domain_specs))

        for domain, domain_files in sorted(domain_specs.items()):
            domain_title = domain.replace("_", " ").title()
            merged = create_base_spec(
                title=f"F5 XC {domain_title} API",
                description=f"F5 Distributed Cloud {domain_title} API specifications",
                version=version,
                upstream_info=upstream_info,
            )

            all_tags = []
            for spec_file in domain_files:
                success, paths_added, comp_stats, tags, error = _process_single_spec_file(
                    spec_file,
                    merged,
                    domain=domain,
                )
                if success:
                    stats["paths"] += paths_added
                    stats["schemas"] += comp_stats["schemas"]
                    stats["requestBodies"] += comp_stats["requestBodies"]
                    all_tags.extend(tags)
                    stats["specs"] += 1
                else:
                    console.print(
                        f"[yellow]Warning: Failed to merge {spec_file.name}: {error}[/yellow]",
                    )

            # Deduplicate and sort tags
            seen_tag_names = set()
            unique_tags = []
            for tag in all_tags:
                if tag.get("name") and tag["name"] not in seen_tag_names:
                    unique_tags.append(tag)
                    seen_tag_names.add(tag["name"])
            merged["tags"] = sorted(unique_tags, key=lambda t: t.get("name", ""))

            # Add spec-level domain metadata (idempotent)
            add_domain_metadata_to_spec(merged, domain)

            # Rewrite upstream API reference links to our published site
            external_docs_enricher = ExternalDocsEnricher()
            external_docs_enricher.enrich_spec(merged, filename=f"{domain}.json")

            # Save domain-specific merged spec
            output_path = output_dir / f"{domain}.json"
            save_spec(merged, output_path)
            merged_specs[domain] = merged
            stats["domains"] += 1

            progress.update(task, advance=1)

    # Print stats
    table = Table(title="Merge Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Domains Created", str(stats["domains"]))
    table.add_row("Specs Processed", str(stats["specs"]))
    table.add_row("Paths Merged", str(stats["paths"]))
    table.add_row("Schemas Merged", str(stats["schemas"]))
    table.add_row("RequestBodies Merged", str(stats["requestBodies"]))
    console.print(table)

    return merged_specs


def create_master_spec(
    merged_specs: dict[str, dict[str, Any]],
    output_path: Path,
    version: str,
    upstream_info: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a master specification combining all domains."""
    # Load enriched description for root/master spec
    enricher = DescriptionEnricher()
    root_desc = enricher.get_description("root", tier="long")

    master = create_base_spec(
        title="F5 Distributed Cloud API",
        description=root_desc or "Complete F5 Distributed Cloud API specification",
        version=version,
        upstream_info=upstream_info,
    )

    all_tags = []
    for spec in merged_specs.values():
        # Merge paths with domain prefix in tags
        for path, path_item in spec.get("paths", {}).items():
            if path not in master["paths"]:
                master["paths"][path] = path_item

        # Merge components
        merge_components(master, spec)

        # Collect tags
        all_tags.extend(spec.get("tags", []))

    # Deduplicate tags
    seen_tag_names = set()
    unique_tags = []
    for tag in all_tags:
        if tag.get("name") and tag["name"] not in seen_tag_names:
            unique_tags.append(tag)
            seen_tag_names.add(tag["name"])
    master["tags"] = sorted(unique_tags, key=lambda t: t.get("name", ""))

    save_spec(master, output_path)

    console.print(f"[green]Created master spec with {len(master['paths'])} paths[/green]")

    return master


def create_spec_index(
    merged_specs: dict[str, dict[str, Any]],
    output_path: Path,
    version: str,
    upstream_info: dict[str, str] | None = None,
) -> None:
    """Create an index file listing all available specifications."""
    index: dict[str, Any] = {
        "version": version,
        # Derived from the upstream seed, not wall-clock: index.json is a committed
        # artifact, and a per-run timestamp here was the last thing making two
        # consecutive rebuilds differ (#1152). Everything else converged once the
        # enrichers stopped stamping now() into every schema.
        "timestamp": artifact_timestamp(),
        "specifications": [],
    }

    # Add upstream tracking info if available (Issue #292: x-f5xc-* namespace)
    if upstream_info:
        index[X_F5XC_UPSTREAM_TIMESTAMP] = upstream_info.get("upstream_timestamp", "unknown")
        index[X_F5XC_UPSTREAM_ETAG] = upstream_info.get("upstream_etag", "unknown")
        index[X_F5XC_ENRICHED_VERSION] = upstream_info.get("enriched_version", version)

    # Add critical resources list for downstream tooling (e.g., xcsh CLI)
    index[X_F5XC_CRITICAL_RESOURCES] = load_critical_resources()

    # Load description enricher for multi-tier descriptions
    description_enricher = DescriptionEnricher()

    for domain, spec in sorted(merged_specs.items()):
        info = spec.get("info", {})
        paths = spec.get("paths", {})
        path_count = len(paths)
        schema_count = len(spec.get("components", {}).get("schemas", {}))
        metadata = get_metadata(domain)

        # Get icon and primary resources for the domain
        icon_info = get_domain_icon(domain)
        # Rich metadata format for IDE tooling (Issues #267-270)
        primary_resources_metadata = get_primary_resources_metadata(domain)

        # Get multi-tier descriptions (short/medium for index, long already in spec)
        domain_title = domain.replace("_", " ").title()
        description_short = description_enricher.get_description(domain, tier="short")
        description_medium = description_enricher.get_description(domain, tier="medium")

        # Build specification entry with x-f5xc-* namespace (Issue #292)
        spec_entry = {
            "domain": domain,
            "title": info.get("title", ""),
            "description": info.get("description", ""),
            X_F5XC_DESCRIPTION_SHORT: description_short or f"{domain_title} API",
            X_F5XC_DESCRIPTION_MEDIUM: description_medium
            or f"F5 Distributed Cloud {domain_title} API specifications",
            "file": f"{domain}.json",
            "path_count": path_count,
            "schema_count": schema_count,
            X_F5XC_COMPLEXITY: calculate_complexity(path_count, schema_count),
            X_F5XC_IS_PREVIEW: metadata.get("is_preview", False),
            X_F5XC_REQUIRES_TIER: metadata.get("requires_tier", "Standard"),
            # Single category field for CLI, UI, docs, and Terraform grouping (DRY)
            X_F5XC_CATEGORY: metadata.get("category", "Other"),
            X_F5XC_USE_CASES: metadata.get("use_cases", []),
            X_F5XC_RELATED_DOMAINS: metadata.get("related_domains", []),
            # Visual identity and resource metadata (Issue #184)
            X_F5XC_ICON: icon_info["icon"],
            X_F5XC_LOGO_SVG: icon_info["logo_svg"],
            # Rich resource metadata for IDE tooling (Issues #267-270)
            X_F5XC_PRIMARY_RESOURCES: primary_resources_metadata,
        }

        # Add spec-level CLI domain metadata if available
        spec_cli_domain = info.get(X_F5XC_CLI_DOMAIN)
        if spec_cli_domain:
            spec_entry[X_F5XC_CLI_DOMAIN] = spec_cli_domain

        # Add CLI metadata if available
        cli_metadata = metadata.get("cli_metadata")
        if cli_metadata:
            spec_entry[X_F5XC_CLI_METADATA] = cli_metadata

        index["specifications"].append(spec_entry)

    write_json_file(index, output_path, indent=2)

    console.print(f"[green]Created spec index at {output_path}[/green]")


def get_upstream_info() -> dict[str, str]:
    """Get upstream source information from manifest.json.

    Returns dict with:
        - upstream_timestamp: YYYYMMDDHHmm format
        - upstream_etag: ETag from source
        - enriched_version: Semantic version from .version file
        - full_version: upstream_timestamp-enriched_version
    """
    enriched_version = get_enriched_version()

    # Read manifest for upstream info
    manifest_path = Path("specs/original/manifest.json")
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            upstream_ts = manifest.get("timestamp", "")
            etag = manifest.get("etag", "unknown")

            # Convert ISO timestamp to YYYYMMDDHHmm format
            if upstream_ts:
                # Handle both 'Z' suffix and explicit timezone
                ts = upstream_ts.replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts)
                upstream_timestamp = dt.strftime("%Y%m%d%H%M")
            else:
                upstream_timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M")
        except (json.JSONDecodeError, ValueError):
            upstream_timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M")
            etag = "unknown"
    else:
        upstream_timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M")
        etag = "unknown"

    return {
        "upstream_timestamp": upstream_timestamp,
        "upstream_etag": etag,
        "enriched_version": enriched_version,
        "full_version": f"{upstream_timestamp}-{enriched_version}",
    }


def get_enriched_version() -> str:
    """Get enriched version from git tags or generate date-based version.

    Uses tag-based versioning to eliminate race conditions from file-based versioning.
    """
    version = get_version_from_tags()
    if version == "0.0.0":
        # Fallback to date-based version if no tags exist
        return datetime.now(tz=timezone.utc).strftime("%Y.%m.%d")
    return version


def get_version() -> str:
    """Get full version string (upstream-enriched format).

    Returns version in format: YYYYMMDDHHmm-X.Y.Z
    Example: 202512200813-1.0.12
    """
    info = get_upstream_info()
    return info["full_version"]


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Merge F5 XC API specifications into unified documents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("docs/specifications/api"),
        help="Directory containing processed specifications",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/specifications/api"),
        help="Directory for merged specifications",
    )
    parser.add_argument(
        "--version",
        type=str,
        help="Version string for merged specifications",
    )
    parser.add_argument(
        "--no-master",
        action="store_true",
        help="Skip creating master combined specification",
    )

    args = parser.parse_args()

    # Get upstream info and version
    upstream_info = get_upstream_info()
    version = args.version or upstream_info["full_version"]

    console.print("[bold blue]F5 XC API Specification Merge[/bold blue]")
    console.print(f"  Input:   {args.input_dir}")
    console.print(f"  Output:  {args.output_dir}")
    console.print(f"  Version: {version}")
    console.print(
        f"  Upstream: {upstream_info['upstream_timestamp']} (ETag: {upstream_info['upstream_etag'][:12]}...)",
    )
    console.print(f"  Enriched: {upstream_info['enriched_version']}")

    if not args.input_dir.exists():
        console.print(f"[red]Input directory not found: {args.input_dir}[/red]")
        console.print("[yellow]Run enrichment pipeline first[/yellow]")
        return 1

    # Merge specs by domain
    merged_specs = merge_specs_by_domain(
        args.input_dir,
        args.output_dir,
        version,
        upstream_info,
    )

    if not merged_specs:
        console.print("[red]No specifications were merged[/red]")
        return 1

    # Create master spec unless disabled
    if not args.no_master:
        master_path = args.output_dir / "openapi.json"
        create_master_spec(merged_specs, master_path, version, upstream_info)

    # Create index file
    index_path = args.output_dir / "index.json"
    create_spec_index(merged_specs, index_path, version, upstream_info)

    console.print("\n[bold green]Successfully merged specifications![/bold green]")
    console.print(f"  Domains: {len(merged_specs)}")
    console.print(f"  Output:  {args.output_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
