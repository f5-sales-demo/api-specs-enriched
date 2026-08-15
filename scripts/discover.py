#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""F5 XC API Discovery Script.

Systematically explores the live authenticated F5 XC API to discover
undocumented behavior, default values, constraints, and schema differences
from published specifications.

Usage:
    python -m scripts.discover                    # Full discovery
    python -m scripts.discover --namespace system # Single namespace
    python -m scripts.discover --endpoint /path   # Single endpoint
    python -m scripts.discover --dry-run          # List endpoints only
    python -m scripts.discover --cli-only         # Use only xcsh CLI
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import yaml
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from .discovery import CLIExplorer, RateLimiter, ReportGenerator, SchemaInferrer
from .discovery.report_generator import DiscoverySession, EndpointDiscovery
from .discovery.schema_inferrer import InferredSchema

console = Console()

# Precompiled regex pattern for path parameter resolution (Issue #391)
# This pattern is used in hot paths (called ~300+ times during discovery)
_PATH_PARAM_PATTERN = re.compile(r"\{[^}]+\}")


def load_config(config_path: Path) -> dict:
    """Load discovery configuration from YAML file."""
    if not config_path.exists():
        console.print(f"[yellow]Config not found: {config_path}, using defaults[/yellow]")
        return get_default_config()

    with config_path.open() as f:
        config = yaml.safe_load(f) or {}
        return config.get("discovery", config)


def get_default_config() -> dict:
    """Get default discovery configuration."""
    return {
        "api_url": os.environ.get("F5XC_API_URL", ""),
        "auth_token": os.environ.get("F5XC_API_TOKEN", ""),
        "rate_limit": {
            "requests_per_second": 5,
            "burst_limit": 10,
            "backoff_base": 1.0,
            "backoff_max": 60.0,
            "retry_attempts": 3,
        },
        "exploration": {
            "namespaces": ["system", "shared"],
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            "timeout_seconds": 30,
            "max_endpoints_per_run": 500,
        },
        "output": {
            "base_dir": "specs/discovered",
            "format": "json",
            "pretty_print": True,
        },
    }


def get_api_url(config: dict) -> str:
    """Get API URL from config or environment."""
    url = os.environ.get("F5XC_API_URL") or config.get("api_url", "")
    return url.rstrip("/")


def get_auth_headers(config: dict) -> dict[str, str]:
    """Get authentication headers."""
    token = os.environ.get("F5XC_API_TOKEN") or config.get("auth_token", "")
    if token:
        return {"Authorization": f"APIToken {token}"}
    return {}


def extract_endpoints_from_specs(specs_dir: Path) -> list[dict[str, Any]]:
    """Extract endpoints from published OpenAPI specs.

    Args:
        specs_dir: Directory containing OpenAPI spec files

    Returns:
        List of endpoint definitions
    """
    endpoints: list[dict[str, Any]] = []

    if not specs_dir.exists():
        console.print(f"[yellow]Specs directory not found: {specs_dir}[/yellow]")
        return endpoints

    for spec_file in sorted(specs_dir.glob("*.json")):
        if spec_file.name in (
            "index.json",
            "namespace_profiles.json",
            "resource_coverage.json",
            "validation.json",
            "openapi.json",
        ):
            continue

        try:
            with spec_file.open() as f:
                spec = json.load(f)

            paths = spec.get("paths", {})
            for path, path_item in paths.items():
                if not isinstance(path_item, dict):
                    continue

                for method in ["get", "options", "post", "put", "delete", "patch"]:
                    if method in path_item:
                        operation = path_item[method]
                        endpoints.append(
                            {
                                "path": path,
                                "method": method.upper(),
                                "operation_id": operation.get("operationId", ""),
                                "parameters": operation.get("parameters", []),
                                "responses": operation.get("responses", {}),
                                "source_file": spec_file.name,
                            },
                        )
        except Exception as e:
            console.print(f"[red]Error reading {spec_file}: {e}[/red]")

    return endpoints


def should_skip_endpoint(endpoint: dict, config: dict) -> tuple[bool, str]:
    """Check if endpoint should be skipped."""
    exploration = config.get("exploration", {})
    valid_methods = exploration.get("methods", ["GET", "OPTIONS"])
    skip_patterns = exploration.get("skip_patterns", [])

    method = endpoint["method"]
    path = endpoint["path"]

    if method not in valid_methods:
        return True, f"Method {method} not in allowed methods"

    for pattern in skip_patterns:
        if pattern in path:
            return True, f"Path matches skip pattern: {pattern}"

    return False, ""


def resolve_path_params(path: str, namespace: str = "system") -> str:
    """Resolve path parameters with sample values."""
    resolved = path.replace("{namespace}", namespace)
    resolved = resolved.replace("{name}", "sample-name")
    resolved = resolved.replace("{id}", "sample-id")

    # Handle any remaining parameters
    resolved = _PATH_PARAM_PATTERN.sub("sample", resolved)

    return resolved


def is_list_endpoint(path: str) -> bool:
    """Return True if path ends with a collection (no trailing {name})."""
    segments = path.rstrip("/").split("/")
    last = segments[-1] if segments else ""
    return not (last.startswith("{") and last.endswith("}"))


def is_item_endpoint(path: str) -> bool:
    """Return True if path ends with a named item placeholder."""
    segments = path.rstrip("/").split("/")
    last = segments[-1] if segments else ""
    return last.startswith("{") and last.endswith("}")


def generate_crud_variants(path: str) -> list[dict[str, Any]]:
    """Generate CRUD endpoint variants from a list or item path.

    For list paths (ending in resource collection):
        POST   /path           — create
        GET    /path/{name}    — get by name
        PUT    /path/{name}    — replace
        DELETE /path/{name}    — delete

    For item paths (ending in {name}):
        PUT    /path           — replace
        PATCH  /path           — partial update
        DELETE /path           — delete
    """
    variants: list[dict[str, Any]] = []

    if is_list_endpoint(path):
        item_path = path + "/{name}"
        variants.append(
            {"method": "POST", "path": path, "operation_id": "", "parameters": [], "responses": {}}
        )
        variants.append(
            {
                "method": "GET",
                "path": item_path,
                "operation_id": "",
                "parameters": [],
                "responses": {},
            }
        )
        variants.append(
            {
                "method": "PUT",
                "path": item_path,
                "operation_id": "",
                "parameters": [],
                "responses": {},
            }
        )
        variants.append(
            {
                "method": "DELETE",
                "path": item_path,
                "operation_id": "",
                "parameters": [],
                "responses": {},
            }
        )
    else:
        variants.append(
            {"method": "PUT", "path": path, "operation_id": "", "parameters": [], "responses": {}}
        )
        variants.append(
            {"method": "PATCH", "path": path, "operation_id": "", "parameters": [], "responses": {}}
        )
        variants.append(
            {
                "method": "DELETE",
                "path": path,
                "operation_id": "",
                "parameters": [],
                "responses": {},
            }
        )

    return variants


async def discover_endpoint(
    client: httpx.AsyncClient,
    base_url: str,
    endpoint: dict,
    config: dict,
    rate_limiter: RateLimiter,
    schema_inferrer: SchemaInferrer,
    namespace: str = "system",
) -> EndpointDiscovery:
    """Discover actual behavior of a single endpoint.

    Args:
        client: HTTP client
        base_url: API base URL
        endpoint: Endpoint definition
        config: Discovery config
        rate_limiter: Rate limiter instance
        schema_inferrer: Schema inferrer instance
        namespace: Namespace to use

    Returns:
        EndpointDiscovery result
    """
    path = endpoint["path"]
    method = endpoint["method"]

    # Check if should skip
    should_skip, skip_reason = should_skip_endpoint(endpoint, config)
    if should_skip:
        return EndpointDiscovery(
            path=path,
            method=method,
            error=f"Skipped: {skip_reason}",
        )

    # Resolve path parameters
    resolved_path = resolve_path_params(path, namespace)
    url = urljoin(base_url + "/", resolved_path.lstrip("/"))

    exploration = config.get("exploration", {})
    timeout = exploration.get("timeout_seconds", 30)

    async with rate_limiter:
        try:
            start_time = asyncio.get_event_loop().time()

            response = await client.request(
                method=method,
                url=url,
                timeout=timeout,
            )

            response_time = (asyncio.get_event_loop().time() - start_time) * 1000

            discovery = EndpointDiscovery(
                path=path,
                method=method,
                status_code=response.status_code,
                response_time_ms=response_time,
            )

            # Try to parse and infer schema from response
            if response.status_code in (200, 201):
                try:
                    response_json = response.json()
                    discovery.inferred_schema = schema_inferrer.infer(response_json)
                    discovery.examples = [response_json] if isinstance(response_json, dict) else []
                except json.JSONDecodeError:
                    pass

            return discovery

        except httpx.TimeoutException:
            return EndpointDiscovery(
                path=path,
                method=method,
                error="Request timed out",
            )
        except httpx.RequestError as e:
            return EndpointDiscovery(
                path=path,
                method=method,
                error=str(e),
            )
        except Exception as e:
            return EndpointDiscovery(
                path=path,
                method=method,
                error=str(e),
            )


async def discover_with_cli(
    cli: CLIExplorer,
    namespace: str,
    config: dict,
    rate_limiter: RateLimiter,
    schema_inferrer: SchemaInferrer,
) -> list[EndpointDiscovery]:
    """Discover API using xcsh CLI.

    Args:
        cli: CLI explorer instance
        namespace: Namespace to explore
        config: Discovery config
        rate_limiter: Rate limiter
        schema_inferrer: Schema inferrer

    Returns:
        List of endpoint discoveries
    """
    discoveries: list[EndpointDiscovery] = []

    # Get resource types from CLI
    resource_types = await cli.discover_resource_types()

    if not resource_types:
        console.print("[yellow]No resource types discovered from CLI[/yellow]")
        return discoveries

    console.print(f"[blue]Discovered {len(resource_types)} resource types from CLI[/blue]")

    # Get max individual resources to fetch per type
    max_individual_resources = config.get("exploration", {}).get("max_individual_resources", 5)

    for resource_type in resource_types[:50]:  # Limit
        async with rate_limiter:
            result = await cli.list_resources(resource_type, namespace)

            path = f"/api/config/namespaces/{namespace}/{resource_type}"

            if result.success and result.data:
                schema = schema_inferrer.infer(result.data)
                examples = [result.data] if isinstance(result.data, dict) else []

                # Extract individual resource names from list response
                items = []
                if isinstance(result.data, dict):
                    items = result.data.get("items", [])
                elif isinstance(result.data, list):
                    items = result.data

                # Fetch individual resources for richer data (defaults, enum values)
                individual_examples = []
                for item in items[:max_individual_resources]:
                    if isinstance(item, dict) and "name" in item:
                        resource_name = item["name"]
                        async with rate_limiter:
                            individual_result = await cli.get_resource(
                                resource_type.rstrip("s"),  # Singular form
                                resource_name,
                                namespace,
                            )
                            if individual_result.success and individual_result.data:
                                individual_examples.append(individual_result.data)
                                # Merge inferred schema from individual resource
                                individual_schema = schema_inferrer.infer(individual_result.data)
                                # Merge schemas to capture all fields
                                if individual_schema:
                                    merged = _merge_schemas(schema, individual_schema)
                                    if merged:
                                        schema = merged

                # Combine examples
                if individual_examples:
                    examples.extend(individual_examples)

                discoveries.append(
                    EndpointDiscovery(
                        path=path,
                        method="GET",
                        status_code=200,
                        inferred_schema=schema,
                        examples=examples[:10],  # Limit stored examples
                    ),
                )
            else:
                discoveries.append(
                    EndpointDiscovery(
                        path=path,
                        method="GET",
                        error=result.error or "Unknown error",
                    ),
                )

    return discoveries


def _merge_schemas(
    base: InferredSchema | None,
    new: InferredSchema | None,
) -> InferredSchema | None:
    """Merge two inferred schemas to capture all discovered fields.

    Args:
        base: Base schema
        new: New schema to merge in

    Returns:
        Merged schema with combined properties
    """
    if not base:
        return new
    if not new:
        return base

    # Merge properties
    for prop_name, prop_schema in new.properties.items():
        if prop_name not in base.properties:
            base.properties[prop_name] = prop_schema
        else:
            # Merge constraints - keep tightest
            base_prop = base.properties[prop_name]
            new_constraints = prop_schema.constraints
            base_constraints = base_prop.constraints

            # Update lengths if new values are more restrictive
            if new_constraints.min_length is not None and (
                base_constraints.min_length is None
                or new_constraints.min_length > base_constraints.min_length
            ):
                base_constraints.min_length = new_constraints.min_length
            if new_constraints.max_length is not None and (
                base_constraints.max_length is None
                or new_constraints.max_length < base_constraints.max_length
            ):
                base_constraints.max_length = new_constraints.max_length

            # Merge enum values
            if new_constraints.enum_values:
                existing_enums = set(base_constraints.enum_values or [])
                for val in new_constraints.enum_values:
                    existing_enums.add(val)
                base_constraints.enum_values = list(existing_enums)

            # Update format if not set
            if not base_prop.format and prop_schema.format:
                base_prop.format = prop_schema.format

    return base


async def fetch_namespaces(client: httpx.AsyncClient, base_url: str) -> list[str]:
    """Fetch available namespaces from the F5XC API."""
    try:
        response = await client.get(f"{base_url.rstrip('/')}/api/web/namespaces", timeout=10)
        if response.status_code == 200:
            data = response.json()
            items = data.get("items", [])
            names = [item.get("name") for item in items if item.get("name")]
            if names:
                console.print(
                    f"[green]Auto-discovered {len(names)} namespaces: {', '.join(names[:5])}{'...' if len(names) > 5 else ''}[/green]"
                )
                return names
    except Exception as e:
        console.print(
            f"[yellow]Namespace auto-discovery failed: {e} — using config defaults[/yellow]"
        )
    return []


async def run_discovery(
    config: dict,
    namespace: str | None = None,
    endpoint: str | None = None,
    cli_only: bool = False,
    dry_run: bool = False,
) -> DiscoverySession:
    """Run API discovery.

    Args:
        config: Discovery configuration
        namespace: Optional namespace filter
        endpoint: Optional endpoint filter
        cli_only: Use only CLI for discovery
        dry_run: Just list endpoints without making requests

    Returns:
        DiscoverySession with results
    """
    session = DiscoverySession(
        api_url=get_api_url(config),
        namespaces=[namespace]
        if namespace
        else config.get("exploration", {}).get("namespaces", ["system"]),
    )

    if not session.api_url:
        console.print("[red]Error: F5XC_API_URL not set[/red]")
        session.errors.append("API URL not configured")
        return session

    # Initialize components
    rate_limiter = RateLimiter(config.get("rate_limit", {}))
    schema_inferrer = SchemaInferrer(
        detect_patterns=True,
        detect_constraints=True,
    )
    cli = CLIExplorer()

    # Get endpoints to discover
    specs_dir = Path("docs/specifications/api")
    endpoints = extract_endpoints_from_specs(specs_dir)

    # Expand GET list endpoints into full CRUD variants
    crud_additions: list[dict[str, Any]] = []
    existing_keys = {(e["method"], e["path"]) for e in endpoints}
    for ep in list(endpoints):
        if ep["method"] == "GET":
            for variant in generate_crud_variants(ep["path"]):
                key = (variant["method"], variant["path"])
                if key not in existing_keys:
                    crud_additions.append(variant)
                    existing_keys.add(key)
    endpoints = endpoints + crud_additions
    console.print(f"[blue]After CRUD expansion: {len(endpoints)} endpoints[/blue]")

    # Filter if needed
    exploration = config.get("exploration", {})
    max_endpoints = exploration.get("max_endpoints_per_run", 500)

    if endpoint:
        endpoints = [e for e in endpoints if endpoint in e["path"]]

    if len(endpoints) > max_endpoints:
        endpoints = endpoints[:max_endpoints]

    console.print(f"[blue]Found {len(endpoints)} endpoints to explore[/blue]")

    if dry_run:
        console.print("\n[yellow]Dry run - listing endpoints without discovery[/yellow]")
        for ep in endpoints[:50]:
            console.print(f"  {ep['method']} {ep['path']}")
        if len(endpoints) > 50:
            console.print(f"  ... and {len(endpoints) - 50} more")
        return session

    # Run discovery
    headers = get_auth_headers(config)

    if not headers.get("Authorization"):
        console.print("[yellow]Warning: No API token found[/yellow]")

    if cli_only:
        # CLI-only discovery
        if not cli.is_available():
            console.print("[red]Error: xcsh CLI not available[/red]")
            session.errors.append("xcsh not found")
            return session

        for ns in session.namespaces:
            cli_discoveries = await discover_with_cli(
                cli,
                ns,
                config,
                rate_limiter,
                schema_inferrer,
            )
            session.endpoints.extend(cli_discoveries)
    else:
        # HTTP-based discovery
        async with httpx.AsyncClient(
            headers=headers,
            verify=True,
            follow_redirects=True,
        ) as client:
            # Auto-discover namespaces if not specified
            if not namespace:
                auto_namespaces = await fetch_namespaces(client, session.api_url)
                if auto_namespaces:
                    session.namespaces = auto_namespaces

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Discovering endpoints...", total=len(endpoints))

                for ep in endpoints:
                    for ns in session.namespaces:
                        discovery = await discover_endpoint(
                            client,
                            session.api_url,
                            ep,
                            config,
                            rate_limiter,
                            schema_inferrer,
                            namespace=ns,
                        )
                        session.endpoints.append(discovery)

                    progress.update(task, advance=1)

    session.completed_at = datetime.now(timezone.utc)
    session.rate_limiter_stats = rate_limiter.get_stats()

    return session


def print_summary(session: DiscoverySession) -> None:
    """Print discovery summary to console."""
    table = Table(title="Discovery Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("API URL", session.api_url)
    table.add_row("Namespaces", ", ".join(session.namespaces))
    table.add_row("Duration", f"{session.duration_seconds:.1f}s")
    table.add_row("Endpoints Explored", str(len(session.endpoints)))

    successful = len([e for e in session.endpoints if e.error is None])
    table.add_row("Successful", str(successful))
    table.add_row("Failed", str(len(session.endpoints) - successful))
    table.add_row("Success Rate", f"{session.success_rate:.1f}%")

    if session.rate_limiter_stats:
        table.add_row("Requests Made", str(session.rate_limiter_stats.get("requests_made", 0)))
        table.add_row("Rate Limit Hits", str(session.rate_limiter_stats.get("rate_limit_hits", 0)))

    console.print(table)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Discover F5 XC API behavior from live endpoints",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/discovery.yaml"),
        help="Path to discovery configuration",
    )
    parser.add_argument(
        "--namespace",
        "-n",
        type=str,
        help="Namespace to explore (default: from config)",
    )
    parser.add_argument(
        "--endpoint",
        "-e",
        type=str,
        help="Filter to specific endpoint path",
    )
    parser.add_argument(
        "--cli-only",
        action="store_true",
        help="Use only xcsh CLI for discovery",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List endpoints without making requests",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("specs/discovered"),
        help="Output directory for discovered specs",
    )

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    console.print("[bold blue]F5 XC API Discovery[/bold blue]")
    console.print(f"  API:    {get_api_url(config)}")
    console.print(f"  Config: {args.config}")

    # Run discovery
    session = asyncio.run(
        run_discovery(
            config,
            namespace=args.namespace,
            endpoint=args.endpoint,
            cli_only=args.cli_only,
            dry_run=args.dry_run,
        ),
    )

    if args.dry_run:
        return 0

    # Generate reports
    report_gen = ReportGenerator(
        output_dir=args.output_dir,
        include_examples=True,
    )

    console.print("\n[blue]Generating reports...[/blue]")
    generated = report_gen.generate_all(session)

    for report_type, path in generated.items():
        console.print(f"  {report_type}: {path}")

    # Print summary
    print_summary(session)

    if session.errors:
        console.print(f"\n[yellow]Completed with {len(session.errors)} errors[/yellow]")
        return 1

    console.print("\n[bold green]Discovery complete![/bold green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
