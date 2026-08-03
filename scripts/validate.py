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
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urljoin, urlsplit

import httpx
import yaml
from jsonschema import Draft4Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT4
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from scripts.package_config import load_packaged_yaml
from scripts.utils.source_graph_validator import select_source_specs

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

# OpenAPI path-template parameters and the namespace value accepted for live probes.
_PATH_PARAM_PATTERN = re.compile(r"\{([^{}]+)\}")
_NAMESPACE_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?")

# Pattern caching for should_skip_endpoint (Issue #391)
_skip_patterns_cache: dict[tuple[str, ...], list[re.Pattern]] = {}
_include_patterns_cache: dict[tuple[str, ...], list[re.Pattern]] = {}

_SPEC_RESOURCE_URI = "urn:f5xc:live-validation-spec"
_SUCCESS_STATUS_CODES = frozenset({200, 201, 204})
_FORMAT_CHECKER = FormatChecker()


class LiveValidationConfigurationError(ValueError):
    """Raised when live validation cannot run safely."""


class ResponseContractError(ValueError):
    """Raised when a declared response contract cannot be evaluated."""


@dataclass(frozen=True)
class PathResolution:
    """A safely resolved path or the measured reason it cannot be requested."""

    resolved_path: str | None
    unresolved_reason: str | None = None


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


# Public for validation APIs and tests; its bytes come only from the packaged YAML.
DEFAULT_CONFIG = load_packaged_yaml("validation.yaml")

_CONFIG_SCHEMA = {
    "api": frozenset({"base_url", "timeout"}),
    "authentication": frozenset({"env_vars"}),
    "scope": frozenset({"validate_methods", "skip_methods", "namespace"}),
    "filters": frozenset({"skip_patterns", "include_patterns", "skip_namespace_required"}),
    "reporting": frozenset({"output_file", "markdown_summary", "include_details", "format"}),
    "thresholds": frozenset({"min_availability", "min_schema_match", "max_discrepancies"}),
    "concurrency": frozenset({"workers"}),
}


def load_config(config_path: Path | None = None) -> dict:
    """Load packaged validation configuration with an optional validated overlay."""
    canonical = load_packaged_yaml("validation.yaml")
    overlay: dict[str, Any] = {}
    if config_path is not None:
        if not config_path.is_file():
            raise FileNotFoundError(f"validation configuration not found: {config_path}")
        with config_path.open() as f:
            document = yaml.safe_load(f)
        if document is not None:
            if not isinstance(document, dict):
                raise TypeError("validation configuration must be an object")
            overlay = document
        _validate_config(overlay)
    config = _deep_merge(canonical, overlay)
    _validate_config(config)
    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dictionaries."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_config(config: dict[str, Any]) -> None:
    """Reject unknown validation controls instead of silently ignoring them."""
    if not isinstance(config, dict):
        raise TypeError("validation configuration must be an object")
    unknown_sections = sorted(set(config) - set(_CONFIG_SCHEMA))
    if unknown_sections:
        raise ValueError(f"unsupported validation configuration sections: {unknown_sections}")
    for section, allowed_keys in _CONFIG_SCHEMA.items():
        if section not in config:
            continue
        section_config = config[section]
        if not isinstance(section_config, dict):
            raise TypeError(f"validation configuration {section!r} must be an object")
        unknown_keys = sorted(set(section_config) - allowed_keys)
        if unknown_keys:
            raise ValueError(
                f"unsupported validation configuration keys in {section!r}: {unknown_keys}"
            )
    if "authentication" in config and "env_vars" in config["authentication"]:
        authentication = config["authentication"]
        env_vars = authentication["env_vars"]
        if not isinstance(env_vars, dict):
            raise TypeError("validation configuration 'authentication.env_vars' must be an object")
        unknown_env_vars = sorted(set(env_vars) - {"api_token", "api_url"})
        if unknown_env_vars:
            raise ValueError(
                "unsupported validation configuration keys in "
                f"'authentication.env_vars': {unknown_env_vars}"
            )


def get_auth_headers(config: dict) -> dict[str, str]:
    """Get authentication headers from environment variables."""
    env_vars = config["authentication"]["env_vars"]

    headers = {}

    # Check for API token
    token_var = env_vars["api_token"]
    token = os.environ.get(token_var)
    if token:
        headers["Authorization"] = f"APIToken {token}"

    return headers


def get_base_url(config: dict) -> str:
    """Get API base URL from config or environment."""
    env_vars = config["authentication"]["env_vars"]

    # Check environment variable first
    url_var = env_vars["api_url"]
    url = os.environ.get(url_var)
    if url:
        return url.rstrip("/")

    # Fall back to config
    return config["api"]["base_url"].rstrip("/")


def require_live_credentials(config: dict) -> tuple[str, dict[str, str]]:
    """Return the configured live endpoint and authentication header or fail closed."""
    env_vars = config["authentication"]["env_vars"]
    token_var = env_vars["api_token"]
    url_var = env_vars["api_url"]
    token = os.environ.get(token_var, "").strip()
    base_url = os.environ.get(url_var, "").strip().rstrip("/")

    missing = [name for name, value in ((url_var, base_url), (token_var, token)) if not value]
    if missing:
        raise LiveValidationConfigurationError(
            f"live validation requires environment variable(s): {', '.join(missing)}"
        )
    try:
        parsed_url = urlsplit(base_url)
        port = parsed_url.port
    except ValueError as exc:
        raise LiveValidationConfigurationError(
            f"live validation environment variable {url_var} must be a credential-free HTTPS URL"
        ) from exc
    has_https_authority = parsed_url.scheme == "https" and bool(parsed_url.hostname)
    has_credentials = parsed_url.username is not None or parsed_url.password is not None
    has_suffix = bool(parsed_url.query or parsed_url.fragment)
    has_valid_port = port is None or 1 <= port <= 65535
    if not has_https_authority or has_credentials or has_suffix or not has_valid_port:
        raise LiveValidationConfigurationError(
            f"live validation environment variable {url_var} must be a credential-free HTTPS URL"
        )
    return base_url, {"Authorization": f"APIToken {token}"}


def extract_endpoints(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract endpoint definitions from an OpenAPI specification."""
    endpoints = []

    paths = spec.get("paths", {})
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

    return sorted(endpoints, key=lambda endpoint: (endpoint["path"], endpoint["method"]))


def _resolve_local_object(document: dict[str, Any], value: Any) -> Any:
    """Resolve a local OpenAPI Reference Object without permitting remote input."""
    seen: set[str] = set()
    while isinstance(value, dict) and "$ref" in value:
        if set(value) != {"$ref"}:
            raise ResponseContractError("response reference object contains unsupported siblings")
        reference = value["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise ResponseContractError("response contract contains a non-local reference")
        if reference in seen:
            raise ResponseContractError("response contract contains a reference cycle")
        seen.add(reference)

        current: Any = document
        try:
            for raw_segment in reference[2:].split("/"):
                segment = raw_segment.replace("~1", "/").replace("~0", "~")
                current = current[int(segment)] if isinstance(current, list) else current[segment]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ResponseContractError(
                "response contract contains an unresolved local reference"
            ) from error
        value = current
    return value


def _normalise_openapi_schema(value: Any) -> Any:
    """Translate the OpenAPI 3.0 nullable keyword to JSON Schema Draft 4."""
    if isinstance(value, list):
        return [_normalise_openapi_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    nullable = value.get("nullable") is True
    normalised = {
        key: _normalise_openapi_schema(item) for key, item in value.items() if key != "nullable"
    }
    if nullable:
        return {"anyOf": [normalised, {"type": "null"}]}
    return normalised


def _qualify_local_schema_refs(value: Any) -> Any:
    """Bind response-schema references to the complete OpenAPI document resource."""
    if isinstance(value, list):
        return [_qualify_local_schema_refs(item) for item in value]
    if not isinstance(value, dict):
        return value

    qualified: dict[str, Any] = {}
    for key, item in value.items():
        if key == "$ref":
            if not isinstance(item, str) or not item.startswith("#/"):
                raise ResponseContractError("response schema contains a non-local reference")
            qualified[key] = f"{_SPEC_RESOURCE_URI}{item}"
        else:
            qualified[key] = _qualify_local_schema_refs(item)
    return qualified


def _response_definition(
    document: dict[str, Any], endpoint: dict[str, Any], status_code: int
) -> dict[str, Any] | None:
    """Select and resolve the response object declared for an observed status."""
    responses = endpoint.get("responses", {})
    if not isinstance(responses, dict):
        raise ResponseContractError("operation responses must be an object")

    status = str(status_code)
    candidates = (status, f"{status[0]}XX", f"{status[0]}xx", "default")
    for candidate in candidates:
        if candidate in responses:
            resolved = _resolve_local_object(document, responses[candidate])
            if not isinstance(resolved, dict):
                raise ResponseContractError("declared response must be an object")
            return resolved
    return None


def _matching_media_type(
    actual: str, declared: dict[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    """Return the most specific declared media type matching the response."""
    actual_type, separator, actual_subtype = actual.partition("/")
    if not separator:
        return None

    matches: list[tuple[int, str, dict[str, Any]]] = []
    for media_type, media_definition in declared.items():
        declared_type, declared_separator, declared_subtype = media_type.lower().partition("/")
        if not declared_separator or not isinstance(media_definition, dict):
            continue
        type_matches = declared_type in {"*", actual_type}
        subtype_matches = declared_subtype == "*" or (
            declared_subtype.startswith("*")
            and actual_subtype.endswith(declared_subtype.removeprefix("*"))
        )
        if type_matches and (declared_subtype == actual_subtype or subtype_matches):
            specificity = int(declared_type != "*") + int("*" not in declared_subtype)
            matches.append((specificity, media_type, media_definition))
    if not matches:
        return None
    _, media_type, media_definition = max(matches, key=lambda candidate: candidate[0])
    return media_type, media_definition


def _schema_discrepancies(
    document: dict[str, Any], schema: dict[str, Any], instance: Any
) -> list[str]:
    """Validate an instance against an OpenAPI response schema, including local refs."""
    normalised_document = _normalise_openapi_schema(document)
    normalised_schema = _qualify_local_schema_refs(_normalise_openapi_schema(schema))
    try:
        Draft4Validator.check_schema(normalised_schema)
        registry = Registry().with_resource(
            _SPEC_RESOURCE_URI,
            Resource.from_contents(normalised_document, default_specification=DRAFT4),
        )
        errors = sorted(
            Draft4Validator(
                normalised_schema,
                registry=registry,
                format_checker=_FORMAT_CHECKER,
            ).iter_errors(instance),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except Exception as schema_error:
        raise ResponseContractError(
            f"response schema evaluation failed ({type(schema_error).__name__})"
        ) from schema_error

    discrepancies = []
    for validation_error in errors:
        location = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}"
            for part in validation_error.absolute_path
        )
        discrepancies.append(
            f"Response schema mismatch at {location} ({validation_error.validator})"
        )
    return discrepancies


def validate_response_contract(
    response: httpx.Response,
    endpoint: dict[str, Any],
    document: dict[str, Any],
) -> list[str]:
    """Validate an observed response against its operation's declared contract."""
    response_definition = _response_definition(document, endpoint, response.status_code)
    if response_definition is None:
        return [f"No response contract declared for HTTP {response.status_code}"]

    declared_content = response_definition.get("content", {})
    if not isinstance(declared_content, dict):
        raise ResponseContractError("declared response content must be an object")

    body = response.content
    if not declared_content:
        return [] if not body else ["Response body has no declared content contract"]

    actual_content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not actual_content_type:
        return ["Response Content-Type is missing"]
    match = _matching_media_type(actual_content_type, declared_content)
    if match is None:
        return ["Response Content-Type is not declared by the operation"]

    _, media_definition = match
    schema = media_definition.get("schema")
    if schema is None:
        raise ResponseContractError("declared response content has no schema")
    if not isinstance(schema, dict):
        raise ResponseContractError("declared response schema must be an object")

    if actual_content_type == "application/json" or actual_content_type.endswith("+json"):
        try:
            instance = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return ["Response body is not valid JSON"]
    else:
        instance = response.text
    return _schema_discrepancies(document, schema, instance)


def should_skip_endpoint(endpoint: dict[str, Any], config: dict) -> tuple[bool, str]:
    """Determine if an endpoint should be skipped based on config rules."""
    scope = config["scope"]
    filters = config["filters"]

    method = endpoint["method"]
    path = endpoint["path"]

    # Check if method should be validated
    validate_methods = scope["validate_methods"]
    skip_methods = scope["skip_methods"]

    if method in skip_methods:
        return True, f"Method {method} is in skip list"

    if validate_methods and method not in validate_methods:
        return True, f"Method {method} not in validate list"

    # Check path patterns to skip (Issue #391: pattern caching)
    skip_patterns = filters["skip_patterns"]
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
    include_patterns = filters["include_patterns"]
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
    if filters["skip_namespace_required"] and "{namespace}" in path:
        return True, "Endpoint requires namespace parameter"

    return False, ""


def _parse_safe_relative_path(path: str) -> SplitResult | None:
    if not path.startswith("/") or path.startswith("//"):
        return None
    try:
        return urlsplit(path)
    except ValueError:
        return None


def resolve_path_parameters(path: str, namespace: Any) -> PathResolution:
    """Resolve only namespace placeholders using the configured stable scope."""
    parsed_path = _parse_safe_relative_path(path)
    if parsed_path is None:
        return PathResolution(None, "path is not a safe API-relative path")
    has_authority = bool(parsed_path.scheme or parsed_path.netloc)
    has_suffix = bool(parsed_path.query or parsed_path.fragment)
    if has_authority or has_suffix:
        return PathResolution(None, "path is not a safe API-relative path")

    placeholders = tuple(dict.fromkeys(_PATH_PARAM_PATTERN.findall(path)))
    unsupported = sorted(set(placeholders) - {"namespace"})
    if unsupported:
        names = ", ".join(unsupported)
        return PathResolution(None, f"unsupported path parameter(s): {names}")

    if "{" in path or "}" in path:
        if not placeholders:
            return PathResolution(None, "malformed path parameter template")
        if namespace is None or not isinstance(namespace, str) or not namespace:
            return PathResolution(
                None, "namespace path parameter has no configured scope.namespace"
            )
        if _NAMESPACE_PATTERN.fullmatch(namespace) is None:
            return PathResolution(None, "configured scope.namespace is not a safe path segment")

    resolved_path = path.replace("{namespace}", namespace) if placeholders else path
    if "{" in resolved_path or "}" in resolved_path:
        return PathResolution(None, "malformed path parameter template")
    return PathResolution(resolved_path)


async def validate_endpoint(
    client: httpx.AsyncClient,
    base_url: str,
    endpoint: dict[str, Any],
    config: dict,
    semaphore: asyncio.Semaphore,
    *,
    resolved_path: str,
    spec_document: dict[str, Any] | None = None,
) -> EndpointResult:
    """Execute one endpoint whose path was already proven safely resolvable."""
    path = endpoint["path"]
    method = endpoint["method"]
    url = urljoin(base_url + "/", resolved_path.lstrip("/"))

    async with semaphore:
        try:
            start_time = asyncio.get_event_loop().time()

            # Make the request
            response = await client.request(
                method=method,
                url=url,
                timeout=config["api"]["timeout"],
            )

            response_time = (asyncio.get_event_loop().time() - start_time) * 1000

            # Check status code
            is_available = response.status_code in _SUCCESS_STATUS_CODES
            document = spec_document or {"components": {}}
            discrepancies = validate_response_contract(response, endpoint, document)

            return EndpointResult(
                path=path,
                method=method,
                status="available" if is_available else "unavailable",
                status_code=response.status_code,
                schema_match=not discrepancies,
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
        except httpx.RequestError as error:
            return EndpointResult(
                path=path,
                method=method,
                status="error",
                error=f"Request failed ({type(error).__name__})",
            )
        except ResponseContractError as e:
            return EndpointResult(
                path=path,
                method=method,
                status="error",
                error=str(e),
            )
        except ValueError as error:
            # Invalid URL format, invalid timeout, etc.
            return EndpointResult(
                path=path,
                method=method,
                status="error",
                error=f"Configuration error ({type(error).__name__})",
            )
        except TypeError as error:
            # Type mismatches in config or URL construction
            return EndpointResult(
                path=path,
                method=method,
                status="error",
                error=f"Type error in endpoint validation ({type(error).__name__})",
            )
        except Exception as error:
            return EndpointResult(
                path=path,
                method=method,
                status="error",
                error=f"Unexpected endpoint validation failure ({type(error).__name__})",
            )


async def validate_spec(
    spec_path: Path,
    config: dict,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    *,
    base_url: str | None = None,
) -> SpecValidationResult:
    """Validate all endpoints in a specification file."""
    result = SpecValidationResult(filename=spec_path.name)

    try:
        with spec_path.open() as f:
            spec = json.load(f)

        endpoints = extract_endpoints(spec)
        result.endpoints_total = len(endpoints)
        namespace = config["scope"]["namespace"]
        executable: list[tuple[dict[str, Any], str]] = []

        for endpoint in endpoints:
            should_skip, skip_reason = should_skip_endpoint(endpoint, config)
            if should_skip:
                result.endpoints_skipped += 1
                result.endpoint_results.append(
                    EndpointResult(
                        path=endpoint["path"],
                        method=endpoint["method"],
                        status="skipped",
                        error=skip_reason,
                    )
                )
                continue

            result.endpoints_eligible += 1
            resolution = resolve_path_parameters(endpoint["path"], namespace)
            if resolution.resolved_path is None:
                reason = resolution.unresolved_reason or "path could not be safely resolved"
                result.endpoints_unresolved += 1
                result.unresolved_endpoints.append(
                    {
                        "method": endpoint["method"],
                        "path": endpoint["path"],
                        "reason": reason,
                    }
                )
                result.endpoint_results.append(
                    EndpointResult(
                        path=endpoint["path"],
                        method=endpoint["method"],
                        status="unresolved",
                        error=reason,
                    )
                )
                continue

            result.endpoints_safely_resolved += 1
            executable.append((endpoint, resolution.resolved_path))

        base_url = base_url or get_base_url(config)

        # Validate endpoints concurrently
        tasks = [
            validate_endpoint(
                client,
                base_url,
                endpoint,
                config,
                semaphore,
                resolved_path=resolved_path,
                spec_document=spec,
            )
            for endpoint, resolved_path in executable
        ]

        endpoint_results = await asyncio.gather(*tasks)
        result.endpoints_executed = len(endpoint_results)
        if result.endpoints_executed != result.endpoints_safely_resolved:
            result.errors.append(
                "endpoint execution invariant failed: "
                f"executed={result.endpoints_executed}, "
                f"safely_resolved={result.endpoints_safely_resolved}"
            )

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
    base_url, headers = require_live_credentials(config)

    spec_files = _validation_spec_files(specs_dir)
    if not spec_files:
        console.print(f"[yellow]No specification files found in {specs_dir}[/yellow]")
        return stats

    console.print(f"[blue]Found {len(spec_files)} specification files to validate[/blue]")

    workers = config["concurrency"]["workers"]

    semaphore = asyncio.Semaphore(workers)

    async with httpx.AsyncClient(
        headers=headers,
        verify=True,
        follow_redirects=False,
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
                result = await validate_spec(
                    spec_file, config, client, semaphore, base_url=base_url
                )
                stats.spec_results.append(result)
                stats.specs_processed += 1
                stats.total_endpoints += result.endpoints_total
                stats.endpoints_eligible += result.endpoints_eligible
                stats.endpoints_safely_resolved += result.endpoints_safely_resolved
                stats.endpoints_unresolved += result.endpoints_unresolved
                stats.endpoints_executed += result.endpoints_executed
                stats.unresolved_endpoints.extend(
                    {"spec": result.filename, **unresolved}
                    for unresolved in result.unresolved_endpoints
                )
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


def _validation_spec_files(specs_dir: Path) -> list[Path]:
    """Select each executable contract once from the canonical directory graph."""
    selection = select_source_specs(specs_dir)
    if selection.contract_name == "index.json":
        return [path for path in selection.files if path.name != "openapi.json"]
    return list(selection.files)


def generate_report(stats: ValidationStats, output_path: Path) -> None:
    """Generate the same complete, deterministic JSON as ValidationReporter."""
    ValidationReporter(stats).generate_json(output_path)

    console.print(f"[green]Report saved to {output_path}[/green]")


def print_summary(stats: ValidationStats, config: dict) -> None:
    """Print validation summary to console."""
    table = Table(title="Validation Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Specifications Processed", str(stats.specs_processed))
    table.add_row("Endpoints Extracted", str(stats.total_endpoints))
    table.add_row("Endpoints Eligible", str(stats.endpoints_eligible))
    table.add_row("Endpoints Safely Resolved", str(stats.endpoints_safely_resolved))
    table.add_row("Endpoints Unresolved", str(stats.endpoints_unresolved))
    table.add_row("Endpoints Executed", str(stats.endpoints_executed))
    table.add_row("Response Results", str(stats.endpoints_validated))
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
    thresholds = config["thresholds"]
    min_availability = thresholds["min_availability"]
    min_schema_match = thresholds["min_schema_match"]
    max_discrepancies = thresholds["max_discrepancies"]

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


def validation_failures(stats: ValidationStats, config: dict) -> list[str]:
    """Return every production-gate failure without short-circuiting evidence."""
    failures: list[str] = []
    validation_errors = [
        f"{result.filename}: {error}" for result in stats.spec_results for error in result.errors
    ]
    if validation_errors:
        failures.append(f"{len(validation_errors)} validation error(s) occurred")

    if stats.endpoints_eligible != (stats.endpoints_safely_resolved + stats.endpoints_unresolved):
        failures.append(
            "endpoint resolution invariant failed: "
            f"eligible={stats.endpoints_eligible}, "
            f"safely_resolved={stats.endpoints_safely_resolved}, "
            f"unresolved={stats.endpoints_unresolved}"
        )
    if stats.endpoints_executed != stats.endpoints_safely_resolved:
        failures.append(
            "endpoint execution invariant failed: "
            f"executed={stats.endpoints_executed}, "
            f"safely_resolved={stats.endpoints_safely_resolved}"
        )

    if stats.endpoints_validated == 0:
        failures.append("zero endpoints were validated")

    thresholds = config["thresholds"]
    min_availability = thresholds["min_availability"]
    min_schema_match = thresholds["min_schema_match"]
    max_discrepancies = thresholds["max_discrepancies"]

    availability = (
        stats.endpoints_available / stats.endpoints_validated * 100
        if stats.endpoints_validated
        else 0
    )
    if availability < min_availability:
        failures.append(f"availability {availability:.1f}% is below threshold {min_availability}%")

    schema_match = (
        stats.schema_matches / stats.endpoints_available * 100 if stats.endpoints_available else 0
    )
    if schema_match < min_schema_match:
        failures.append(f"schema match {schema_match:.1f}% is below threshold {min_schema_match}%")

    if len(stats.discrepancies) > max_discrepancies:
        failures.append(
            f"{len(stats.discrepancies)} discrepancies exceed threshold {max_discrepancies}"
        )
    return failures


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate F5 XC API specifications against live endpoints",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to validation configuration file (default: packaged config/validation.yaml)",
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
    console.print("  API:    configured live endpoint")

    if not args.specs_dir.exists():
        console.print(f"[red]Specifications directory not found: {args.specs_dir}[/red]")
        console.print("[yellow]Run enrichment pipeline first[/yellow]")
        return 1

    if args.dry_run:
        # Just list endpoints without validation
        console.print("\n[blue]Dry run - listing endpoints without validation[/blue]")
        spec_files = _validation_spec_files(args.specs_dir)
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

    try:
        require_live_credentials(config)
    except LiveValidationConfigurationError as error:
        console.print(f"[red]Validation configuration error: {error}[/red]")
        return 1

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

    failures = validation_failures(stats, config)
    if failures:
        console.print("\n[red]Validation failed:[/red]")
        for failure in failures:
            console.print(f"  - {failure}")
        return 1

    console.print("\n[bold green]Validation complete![/bold green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
