#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Automated validation of enriched API specifications against live F5 XC endpoints.

Validates endpoint availability and response schema conformance.
Fully automated - no manual intervention required.
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

# Import reporter infrastructure
sys.path.insert(0, str(Path(__file__).parent / "utils"))
try:
    from scripts.utils.path_config import PathConfig
    from scripts.utils.validation_reporter import (
        EndpointResult,
        SpecValidationResult,
        ValidationReporter,
        ValidationStats,
    )
except ModuleNotFoundError:
    from path_config import PathConfig  # type: ignore[import-not-found,no-redef,unused-ignore]
    from validation_reporter import (  # type: ignore[import-not-found,no-redef,unused-ignore]
        EndpointResult,
        SpecValidationResult,
        ValidationReporter,
        ValidationStats,
    )

console = Console()

# Precompiled regex pattern for path parameter resolution (Issue #391)
# Used in resolve_path_parameters() to handle remaining unresolved parameters
_PATH_PARAM_PATTERN = re.compile(r"\{[^}]+\}")

# Pattern caching for should_skip_endpoint (Issue #391)
_skip_patterns_cache: dict[tuple[str, ...], list[re.Pattern]] = {}
_include_patterns_cache: dict[tuple[str, ...], list[re.Pattern]] = {}


def _compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    """Compile glob patterns to regex for efficient matching (Issue #391).

    Args:
        patterns: List of glob patterns with * wildcards

    Returns:
        List of compiled regex patterns
    """
    compiled = []
    for pattern in patterns:
        regex_pattern = pattern.replace("*", ".*")
        compiled.append(re.compile(regex_pattern))
    return compiled


# Default configuration
DEFAULT_CONFIG = {
    "api": {
        "base_url": "https://console.ves.volterra.io",
        "timeout": 30,
        "max_retries": 3,
        "retry_delay": 2,
    },
    "authentication": {
        "method": "api_token",
        "env_vars": {
            "api_token": "F5XC_API_TOKEN",
            "api_url": "F5XC_API_URL",
        },
    },
    "scope": {
        "validate_methods": ["GET", "OPTIONS"],
        "skip_methods": ["POST", "PUT", "DELETE", "PATCH"],
        "max_endpoints_per_spec": 50,
        "sample_size": 5,
    },
    "filters": {
        "skip_patterns": ["/api/internal/*", "/api/debug/*", "/api/test/*"],
        "include_patterns": [],
        "skip_namespace_required": False,
    },
    "response": {
        "validate_status": True,
        "success_codes": [200, 201, 204],
        "validate_schema": True,
        "allow_additional_properties": True,
        "validate_content_type": True,
    },
    "thresholds": {
        "min_availability": 80,
        "min_schema_match": 70,
        "max_discrepancies": 50,
    },
    "concurrency": {
        "workers": 10,
        "rate_limit": 10,
        "pool_size": 20,
    },
}


def load_config(config_path: Path | None = None) -> dict:
    """Load configuration from YAML file or use defaults."""
    if config_path and config_path.exists():
        with config_path.open() as f:
            config = yaml.safe_load(f) or {}
            return _deep_merge(DEFAULT_CONFIG, config)
    return DEFAULT_CONFIG


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_auth_headers(config: dict) -> dict[str, str]:
    """Get authentication headers from environment variables."""
    auth_config = config.get("authentication", {})
    env_vars = auth_config.get("env_vars", {})

    headers = {}

    # Check for API token
    token_var = env_vars.get("api_token", "F5XC_API_TOKEN")
    token = os.environ.get(token_var)
    if token:
        headers["Authorization"] = f"APIToken {token}"

    return headers


def get_base_url(config: dict) -> str:
    """Get API base URL from config or environment."""
    auth_config = config.get("authentication", {})
    env_vars = auth_config.get("env_vars", {})

    # Check environment variable first
    url_var = env_vars.get("api_url", "F5XC_API_URL")
    url = os.environ.get(url_var)
    if url:
        return url.rstrip("/")

    # Fall back to config
    return config.get("api", {}).get("base_url", "https://console.ves.volterra.io").rstrip("/")


def extract_endpoints(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract endpoint definitions from an OpenAPI specification."""
    endpoints: list[dict[str, Any]] = []

    # The generated directory also contains release companions whose top-level
    # shape is not OpenAPI.  In particular, the SMSv2 parity manifest has a
    # list-valued ``paths`` field describing artifact paths.  Classify the
    # document by its specification marker before interpreting that field.
    if "openapi" not in spec and "swagger" not in spec:
        return endpoints

    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        raise TypeError("OpenAPI document 'paths' must be an object")

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        for method in ["get", "post", "put", "patch", "delete", "options", "head"]:
            if method in path_item:
                operation = path_item[method]
                endpoints.append(
                    {
                        "path": path,
                        "method": method.upper(),
                        "operation_id": operation.get("operationId", ""),
                        "summary": operation.get("summary", ""),
                        "parameters": operation.get("parameters", []),
                        "responses": operation.get("responses", {}),
                    },
                )

    return endpoints


def should_skip_endpoint(endpoint: dict[str, Any], config: dict) -> tuple[bool, str]:
    """Determine if an endpoint should be skipped based on config rules."""
    scope = config.get("scope", {})
    filters = config.get("filters", {})

    method = endpoint["method"]
    path = endpoint["path"]

    # Check if method should be validated
    validate_methods = scope.get("validate_methods", ["GET", "OPTIONS"])
    skip_methods = scope.get("skip_methods", ["POST", "PUT", "DELETE", "PATCH"])

    if method in skip_methods:
        return True, f"Method {method} is in skip list"

    if validate_methods and method not in validate_methods:
        return True, f"Method {method} not in validate list"

    # Check path patterns to skip (Issue #391: pattern caching)
    skip_patterns = filters.get("skip_patterns", [])
    if skip_patterns:
        # Cache compiled patterns using module-level cache
        skip_cache_key = tuple(skip_patterns)
        if skip_cache_key not in _skip_patterns_cache:
            _skip_patterns_cache[skip_cache_key] = _compile_patterns(skip_patterns)

        skip_compiled = _skip_patterns_cache[skip_cache_key]
        for i, pattern_obj in enumerate(skip_compiled):
            if pattern_obj.match(path):
                return True, f"Path matches skip pattern: {skip_patterns[i]}"

    # Check include patterns (Issue #391: pattern caching)
    include_patterns = filters.get("include_patterns", [])
    if include_patterns:
        # Cache compiled patterns using module-level cache
        include_cache_key = tuple(include_patterns)
        if include_cache_key not in _include_patterns_cache:
            _include_patterns_cache[include_cache_key] = _compile_patterns(
                include_patterns,
            )

        include_compiled = _include_patterns_cache[include_cache_key]
        matched = any(pattern_obj.match(path) for pattern_obj in include_compiled)
        if not matched:
            return True, "Path doesn't match any include pattern"

    # Check if namespace is required and we should skip
    if filters.get("skip_namespace_required", False) and "{namespace}" in path:
        return True, "Endpoint requires namespace parameter"

    return False, ""


def resolve_path_parameters(path: str, parameters: list[dict]) -> str:
    """Resolve path parameters with sample values for testing."""
    resolved = path

    # Default sample values for common parameter types
    sample_values = {
        "namespace": "system",
        "name": "test",
        "id": "test-id",
        "tenant": "default",
    }

    for param in parameters:
        if param.get("in") == "path":
            param_name = param.get("name", "")
            sample_value = sample_values.get(param_name, "sample")
            resolved = resolved.replace(f"{{{param_name}}}", sample_value)

    # Handle any remaining unresolved parameters (Issue #391: precompiled pattern)
    return _PATH_PARAM_PATTERN.sub("sample", resolved)


async def validate_endpoint(
    client: httpx.AsyncClient,
    base_url: str,
    endpoint: dict[str, Any],
    config: dict,
    semaphore: asyncio.Semaphore,
) -> EndpointResult:
    """Validate a single endpoint against the live API."""
    path = endpoint["path"]
    method = endpoint["method"]

    # Check if should skip
    should_skip, skip_reason = should_skip_endpoint(endpoint, config)
    if should_skip:
        return EndpointResult(
            path=path,
            method=method,
            status="skipped",
            error=skip_reason,
        )

    # Resolve path parameters
    resolved_path = resolve_path_parameters(path, endpoint.get("parameters", []))
    url = urljoin(base_url + "/", resolved_path.lstrip("/"))

    api_config = config.get("api", {})
    response_config = config.get("response", {})

    async with semaphore:
        try:
            start_time = asyncio.get_event_loop().time()

            # Make the request
            response = await client.request(
                method=method,
                url=url,
                timeout=api_config.get("timeout", 30),
            )

            response_time = (asyncio.get_event_loop().time() - start_time) * 1000

            # Check status code
            success_codes = response_config.get("success_codes", [200, 201, 204])
            is_available = response.status_code in success_codes or response.status_code in [
                401,
                403,
            ]

            # Schema validation (simplified - just check if response is valid JSON)
            schema_match = True
            discrepancies = []

            if (
                response_config.get("validate_schema", True)
                and response.status_code in success_codes
            ):
                try:
                    response_json = response.json()
                    # Basic schema validation - check for expected structure
                    if isinstance(response_json, dict):
                        # Check for common F5 XC response fields
                        expected_fields = ["metadata", "spec", "system_metadata"]
                        for field in expected_fields:
                            if field in response_json:
                                break
                        else:
                            if "items" not in response_json and "objects" not in response_json:
                                discrepancies.append("Response missing expected F5 XC structure")
                except json.JSONDecodeError:
                    schema_match = False
                    discrepancies.append("Response is not valid JSON")

            return EndpointResult(
                path=path,
                method=method,
                status="available" if is_available else "unavailable",
                status_code=response.status_code,
                schema_match=schema_match and len(discrepancies) == 0,
                response_time_ms=response_time,
                discrepancies=discrepancies,
            )

        except httpx.TimeoutException:
            return EndpointResult(
                path=path,
                method=method,
                status="error",
                error="Request timed out",
            )
        except httpx.RequestError as e:
            return EndpointResult(
                path=path,
                method=method,
                status="error",
                error=str(e),
            )
        except ValueError as e:
            # Invalid URL format, invalid timeout, etc.
            return EndpointResult(
                path=path,
                method=method,
                status="error",
                error=f"Configuration error: {e!s}",
            )
        except TypeError as e:
            # Type mismatches in config or URL construction
            return EndpointResult(
                path=path,
                method=method,
                status="error",
                error=f"Type error in endpoint validation: {e!s}",
            )


async def validate_spec(
    spec_path: Path,
    config: dict,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> SpecValidationResult:
    """Validate all endpoints in a specification file."""
    result = SpecValidationResult(filename=spec_path.name)

    try:
        with spec_path.open() as f:
            spec = json.load(f)

        endpoints = extract_endpoints(spec)
        result.endpoints_total = len(endpoints)

        # Limit endpoints per spec
        scope = config.get("scope", {})
        max_endpoints = scope.get("max_endpoints_per_spec", 50)
        sample_size = scope.get("sample_size", 5)

        # Sample endpoints if too many
        if len(endpoints) > max_endpoints:
            # Prioritize GET endpoints for sampling
            get_endpoints = [e for e in endpoints if e["method"] == "GET"]
            other_endpoints = [e for e in endpoints if e["method"] != "GET"]

            sampled = get_endpoints[:sample_size] + other_endpoints[: max_endpoints - sample_size]
            endpoints = sampled[:max_endpoints]

        base_url = get_base_url(config)

        # Validate endpoints concurrently
        tasks = [
            validate_endpoint(client, base_url, endpoint, config, semaphore)
            for endpoint in endpoints
        ]

        endpoint_results = await asyncio.gather(*tasks)

        for endpoint_result in endpoint_results:
            result.endpoint_results.append(endpoint_result)

            if endpoint_result.status == "available":
                result.endpoints_available += 1
                result.endpoints_validated += 1
                if endpoint_result.schema_match:
                    result.schema_matches += 1
                else:
                    result.schema_mismatches += 1
            elif endpoint_result.status == "unavailable":
                result.endpoints_unavailable += 1
                result.endpoints_validated += 1
            elif endpoint_result.status == "skipped":
                result.endpoints_skipped += 1
            elif endpoint_result.status == "error":
                result.errors.append(f"{endpoint_result.path}: {endpoint_result.error}")

    except (FileNotFoundError, PermissionError, IsADirectoryError) as e:
        result.errors.append(f"Cannot read spec file {spec_path.name}: {e!s}")
    except json.JSONDecodeError as e:
        result.errors.append(f"Spec file {spec_path.name} contains invalid JSON: {e!s}")
    except (KeyError, AttributeError, TypeError) as e:
        result.errors.append(f"Spec structure error in {spec_path.name}: {e!s}")
    except ValueError as e:
        result.errors.append(f"Invalid configuration for {spec_path.name}: {e!s}")

    return result


async def validate_all_specs(
    specs_dir: Path,
    config: dict,
) -> ValidationStats:
    """Validate all specification files in a directory."""
    stats = ValidationStats()

    metadata_files = {
        "index.json",
        "minimal-export-defaults.json",
        "namespace_profiles.json",
        "resource_coverage.json",
        "validation.json",
    }
    spec_files = sorted(
        path for path in specs_dir.glob("*.json") if path.name not in metadata_files
    )
    if not spec_files:
        console.print(f"[yellow]No specification files found in {specs_dir}[/yellow]")
        return stats

    console.print(f"[blue]Found {len(spec_files)} specification files to validate[/blue]")

    # Get authentication headers
    headers = get_auth_headers(config)
    if not headers.get("Authorization"):
        console.print(
            "[yellow]Warning: No API token found. Validation may fail for authenticated endpoints.[/yellow]",
        )

    concurrency = config.get("concurrency", {})
    workers = concurrency.get("workers", 10)

    semaphore = asyncio.Semaphore(workers)

    async with httpx.AsyncClient(
        headers=headers,
        verify=True,
        follow_redirects=True,
    ) as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Validating specifications...", total=len(spec_files))

            for spec_file in spec_files:
                result = await validate_spec(spec_file, config, client, semaphore)
                stats.spec_results.append(result)
                stats.specs_processed += 1
                stats.total_endpoints += result.endpoints_total
                stats.endpoints_validated += result.endpoints_validated
                stats.endpoints_available += result.endpoints_available
                stats.endpoints_unavailable += result.endpoints_unavailable
                stats.schema_matches += result.schema_matches

                # Collect discrepancies
                for er in result.endpoint_results:
                    if er.discrepancies:
                        stats.discrepancies.append(
                            {
                                "spec": result.filename,
                                "path": er.path,
                                "method": er.method,
                                "issues": er.discrepancies,
                            },
                        )

                progress.update(task, advance=1)

    return stats


def generate_report(stats: ValidationStats, output_path: Path) -> None:
    """Generate validation report."""
    report = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "summary": {
            "specs_processed": stats.specs_processed,
            "total_endpoints": stats.total_endpoints,
            "endpoints_validated": stats.endpoints_validated,
            "endpoints_available": stats.endpoints_available,
            "endpoints_unavailable": stats.endpoints_unavailable,
            "schema_matches": stats.schema_matches,
            "availability_percentage": round(
                (
                    (stats.endpoints_available / stats.endpoints_validated * 100)
                    if stats.endpoints_validated > 0
                    else 0
                ),
                2,
            ),
            "schema_match_percentage": round(
                (
                    (stats.schema_matches / stats.endpoints_available * 100)
                    if stats.endpoints_available > 0
                    else 0
                ),
                2,
            ),
        },
        "discrepancies": stats.discrepancies[:100],  # Limit to first 100
        "specs": [
            {
                "filename": r.filename,
                "endpoints_total": r.endpoints_total,
                "endpoints_validated": r.endpoints_validated,
                "endpoints_available": r.endpoints_available,
                "endpoints_skipped": r.endpoints_skipped,
                "schema_matches": r.schema_matches,
                "errors": r.errors[:5],
            }
            for r in stats.spec_results
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    console.print(f"[green]Report saved to {output_path}[/green]")


def print_summary(stats: ValidationStats, config: dict) -> None:
    """Print validation summary to console."""
    table = Table(title="Validation Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Specifications Processed", str(stats.specs_processed))
    table.add_row("Total Endpoints", str(stats.total_endpoints))
    table.add_row("Endpoints Validated", str(stats.endpoints_validated))
    table.add_row("Endpoints Available", str(stats.endpoints_available))
    table.add_row("Endpoints Unavailable", str(stats.endpoints_unavailable))
    table.add_row("Schema Matches", str(stats.schema_matches))

    if stats.endpoints_validated > 0:
        availability = stats.endpoints_available / stats.endpoints_validated * 100
        table.add_row("Availability %", f"{availability:.1f}%")

    if stats.endpoints_available > 0:
        schema_match = stats.schema_matches / stats.endpoints_available * 100
        table.add_row("Schema Match %", f"{schema_match:.1f}%")

    console.print(table)

    # Check thresholds
    thresholds = config.get("thresholds", {})
    min_availability = thresholds.get("min_availability", 80)
    min_schema_match = thresholds.get("min_schema_match", 70)
    max_discrepancies = thresholds.get("max_discrepancies", 50)

    if stats.endpoints_validated > 0:
        availability = stats.endpoints_available / stats.endpoints_validated * 100
        if availability < min_availability:
            console.print(
                f"\n[yellow]Warning: Availability {availability:.1f}% is below threshold {min_availability}%[/yellow]",
            )

    if stats.endpoints_available > 0:
        schema_match = stats.schema_matches / stats.endpoints_available * 100
        if schema_match < min_schema_match:
            console.print(
                f"[yellow]Warning: Schema match {schema_match:.1f}% is below threshold {min_schema_match}%[/yellow]",
            )

    if len(stats.discrepancies) > max_discrepancies:
        console.print(
            f"[yellow]Warning: {len(stats.discrepancies)} discrepancies exceeds threshold {max_discrepancies}[/yellow]",
        )


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate F5 XC API specifications against live endpoints",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/validation.yaml"),
        help="Path to validation configuration file",
    )
    parser.add_argument(
        "--specs-dir",
        type=Path,
        default=Path("docs/specifications/api"),
        help="Directory containing enriched specifications",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/validation-report.json"),
        help="Path for validation report output",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List endpoints without making requests",
    )

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    console.print("[bold blue]F5 XC API Specification Validation[/bold blue]")
    console.print(f"  Specs:  {args.specs_dir}")
    console.print(f"  API:    {get_base_url(config)}")

    if not args.specs_dir.exists():
        console.print(f"[red]Specifications directory not found: {args.specs_dir}[/red]")
        console.print("[yellow]Run enrichment pipeline first[/yellow]")
        return 1

    if args.dry_run:
        # Just list endpoints without validation
        console.print("\n[blue]Dry run - listing endpoints without validation[/blue]")
        metadata_files = {
            "index.json",
            "minimal-export-defaults.json",
            "namespace_profiles.json",
            "resource_coverage.json",
            "validation.json",
        }
        spec_files = sorted(
            path for path in args.specs_dir.glob("*.json") if path.name not in metadata_files
        )
        total_endpoints = 0
        for spec_file in spec_files:
            with spec_file.open() as f:
                spec = json.load(f)
            endpoints = extract_endpoints(spec)
            console.print(f"  {spec_file.name}: {len(endpoints)} endpoints")
            total_endpoints += len(endpoints)
        console.print(
            f"\n[green]Total: {total_endpoints} endpoints across {len(spec_files)} specs[/green]",
        )
        return 0

    # Run validation
    stats = asyncio.run(validate_all_specs(args.specs_dir, config))

    # Generate reports using ValidationReporter (both JSON and markdown)
    path_config = PathConfig()
    reporter = ValidationReporter(stats, path_config)

    json_report_path = args.output
    markdown_report_path = path_config.validation_report

    reporter.generate_all(markdown_report_path, json_report_path)
    console.print("[green]Reports generated:[/green]")
    console.print(f"  Markdown: {markdown_report_path}")
    console.print(f"  JSON:     {json_report_path}")

    # Print summary (keep existing console output)
    print_summary(stats, config)

    # Check if validation passed thresholds
    thresholds = config.get("thresholds", {})
    min_availability = thresholds.get("min_availability", 80)

    if stats.endpoints_validated > 0:
        availability = stats.endpoints_available / stats.endpoints_validated * 100
        if availability < min_availability:
            console.print("\n[red]Validation failed: availability below threshold[/red]")
            return 1

    console.print("\n[bold green]Validation complete![/bold green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
