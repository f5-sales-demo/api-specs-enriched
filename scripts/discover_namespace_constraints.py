"""Discover namespace constraints by probing the live F5 XC API.

Outputs a YAML report showing which namespace types each resource
accepts or rejects. Supports both GET (list) and POST (create) probing
to distinguish between listable and creatable namespaces.

Run manually, review output, update namespace_profile.yaml.

Requires: F5XC_API_URL/XCSH_API_URL and F5XC_API_TOKEN/XCSH_API_TOKEN environment variables.

Usage:
    python scripts/discover_namespace_constraints.py [--output discovery_report.yaml]
    python scripts/discover_namespace_constraints.py --method post --resources dns_load_balancer,dns_lb_pool
    python scripts/discover_namespace_constraints.py --method both --diff config/namespace_profile.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
import yaml

NAMESPACE_TYPES_TO_TEST = ["system", "shared", "default"]


def get_api_client() -> tuple[str, dict[str, str]]:
    """Return (base_url, headers) for the F5 XC API.

    Supports both F5XC_API_* and XCSH_API_* environment variable prefixes.
    """
    url = os.environ.get("F5XC_API_URL") or os.environ.get("XCSH_API_URL", "")
    token = os.environ.get("F5XC_API_TOKEN") or os.environ.get("XCSH_API_TOKEN", "")
    if not url or not token:
        print(
            "Error: Set F5XC_API_URL/XCSH_API_URL and F5XC_API_TOKEN/XCSH_API_TOKEN",
            file=sys.stderr,
        )
        sys.exit(1)
    headers = {"Authorization": f"APIToken {token}", "Content-Type": "application/json"}
    return url.rstrip("/"), headers


def list_resource_types_from_specs(
    specs_dir: Path,
    resource_filter: list[str] | None = None,
) -> list[dict[str, str]]:
    """Extract resource types and their API paths from enriched specs."""
    resources = []
    index_path = specs_dir / "index.json"
    if not index_path.exists():
        print(f"Error: {index_path} not found", file=sys.stderr)
        sys.exit(1)

    with index_path.open() as f:
        index = json.load(f)

    specs = index.get("specifications", [])
    if isinstance(specs, dict):
        specs = specs.values()
    for domain_info in specs:
        for resource in domain_info.get("x-f5xc-primary-resources", []):
            name = resource.get("name", "")
            if resource_filter and name not in resource_filter:
                continue
            api_paths = resource.get("api_paths", [])
            list_path = _extract_list_path(api_paths)
            if name and list_path:
                resources.append({"name": name, "list_path": list_path})
    return resources


def _extract_list_path(api_paths: list[str] | dict[str, str]) -> str:
    """Extract the namespace-parameterized list path from api_paths.

    Prefers paths matching /namespaces/{namespace}/<resource_plural> that use
    the simple {namespace} placeholder (not {metadata.namespace}).
    """
    if isinstance(api_paths, dict):
        return api_paths.get("list", "")

    candidates = []
    for p in api_paths:
        if "{metadata.namespace}" in p:
            continue
        if "/namespaces/{namespace}/" in p and not _has_name_param(p):
            candidates.append(p)

    if not candidates and isinstance(api_paths, list) and api_paths:
        candidates.extend(
            p.replace("{metadata.namespace}", "{namespace}")
            for p in api_paths
            if "/namespaces/" in p and not _has_name_param(p)
        )

    if candidates:
        candidates.sort(key=len)
        return candidates[0]
    return api_paths[0] if api_paths else ""


def _has_name_param(path: str) -> bool:
    """Check if a path has a name parameter (indicating a get/update endpoint)."""
    return "{name}" in path or "{metadata.name}" in path


def probe_namespace_get(
    base_url: str,
    headers: dict[str, str],
    list_path: str,
    namespace: str,
) -> dict[str, Any]:
    """Probe a namespace via GET (list) request."""
    url = f"{base_url}{list_path}".replace("{namespace}", namespace)
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        return {
            "status": resp.status_code,
            "allowed": resp.status_code in (200, 404),
            "method": "GET",
            "error": _extract_error(resp),
        }
    except Exception as e:
        return {"status": 0, "allowed": False, "method": "GET", "error": str(e)}


def probe_namespace_post(
    base_url: str,
    headers: dict[str, str],
    list_path: str,
    namespace: str,
) -> dict[str, Any]:
    """Probe a namespace via POST (create) request with a minimal payload.

    Sends a deliberately minimal payload to trigger validation errors without
    actually creating a resource. Response classification:
      - 400 (validation error on fields) → namespace is accessible for creation
      - 403 / permission denied → namespace is restricted
      - 404 → endpoint doesn't exist for this namespace
      - 409 (conflict) → namespace is accessible (resource name collision)
      - 422 (validation) → namespace is accessible, payload rejected
    """
    url = f"{base_url}{list_path}".replace("{namespace}", namespace)
    payload = {
        "metadata": {
            "name": "ns-probe-test-00",
            "namespace": namespace,
        },
        "spec": {},
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        error_msg = _extract_error(resp)

        if resp.status_code in (200, 201):
            return {
                "status": resp.status_code,
                "allowed": True,
                "method": "POST",
                "error": "WARNING: resource may have been created",
                "note": "unexpected success — verify and clean up",
            }

        if resp.status_code == 409:
            return {
                "status": 409,
                "allowed": True,
                "method": "POST",
                "error": error_msg,
            }

        if resp.status_code in (400, 422):
            is_namespace_error = _is_namespace_restriction_error(error_msg)
            return {
                "status": resp.status_code,
                "allowed": not is_namespace_error,
                "method": "POST",
                "error": error_msg,
            }

        if resp.status_code == 403:
            return {
                "status": 403,
                "allowed": False,
                "method": "POST",
                "error": error_msg,
            }

        return {
            "status": resp.status_code,
            "allowed": resp.status_code < 400,
            "method": "POST",
            "error": error_msg,
        }
    except Exception as e:
        return {"status": 0, "allowed": False, "method": "POST", "error": str(e)}


def _extract_error(resp: requests.Response) -> str:
    """Extract error message from an API response."""
    if resp.status_code < 400:
        return ""
    try:
        body = resp.json()
        return body.get("message", "") or body.get("error", "") or str(body)[:200]
    except Exception:
        return resp.text[:200]


def _is_namespace_restriction_error(error_msg: str) -> bool:
    """Detect whether a 400/422 error is about namespace restrictions vs field validation."""
    ns_keywords = [
        "namespace",
        "not allowed",
        "restricted",
        "system namespace",
        "shared namespace",
        "cannot create",
        "not permitted",
        "invalid namespace",
    ]
    msg_lower = error_msg.lower()
    return any(kw in msg_lower for kw in ns_keywords)


def discover_all(
    specs_dir: Path,
    method: str = "get",
    resource_filter: list[str] | None = None,
    rate_limit_ms: int = 200,
) -> dict[str, Any]:
    """Probe all resources across all namespace types."""
    base_url, headers = get_api_client()
    resources = list_resource_types_from_specs(specs_dir, resource_filter)
    results: dict[str, Any] = {}

    if not resources:
        print("No resources found matching filter", file=sys.stderr)
        return results

    print(f"Discovering namespace constraints for {len(resources)} resources (method={method})...")
    for i, resource in enumerate(resources):
        name = resource["name"]
        print(f"  [{i + 1}/{len(resources)}] {name}", end=" ")
        entry: dict[str, Any] = {
            "list_path": resource["list_path"],
        }

        if method in ("get", "both"):
            get_result: dict[str, Any] = {"allowed": [], "denied": [], "errors": {}}
            for ns_type in NAMESPACE_TYPES_TO_TEST:
                probe = probe_namespace_get(base_url, headers, resource["list_path"], ns_type)
                if probe["allowed"]:
                    get_result["allowed"].append(ns_type)
                    print(f"+GET:{ns_type}", end=" ")
                else:
                    get_result["denied"].append(ns_type)
                    get_result["errors"][ns_type] = probe["error"]
                    print(f"-GET:{ns_type}", end=" ")
                time.sleep(rate_limit_ms / 1000)
            if _has_non_system(get_result["allowed"]):
                get_result["allowed"].append("custom")
            if not get_result["errors"]:
                del get_result["errors"]
            entry["get"] = get_result

        if method in ("post", "both"):
            post_result: dict[str, Any] = {
                "allowed": [],
                "denied": [],
                "errors": {},
                "notes": [],
            }
            for ns_type in NAMESPACE_TYPES_TO_TEST:
                probe = probe_namespace_post(base_url, headers, resource["list_path"], ns_type)
                if probe["allowed"]:
                    post_result["allowed"].append(ns_type)
                    print(f"+POST:{ns_type}", end=" ")
                else:
                    post_result["denied"].append(ns_type)
                    post_result["errors"][ns_type] = probe["error"]
                    print(f"-POST:{ns_type}", end=" ")
                if probe.get("note"):
                    post_result["notes"].append(f"{ns_type}: {probe['note']}")
                time.sleep(rate_limit_ms / 1000)
            if _has_non_system(post_result["allowed"]):
                post_result["allowed"].append("custom")
            if not post_result["errors"]:
                del post_result["errors"]
            if not post_result["notes"]:
                del post_result["notes"]
            entry["post"] = post_result

        if method in ("get", "both") and method != "post":
            entry["allowed"] = entry.get("get", {}).get("allowed", [])
        if method == "post":
            entry["allowed"] = entry.get("post", {}).get("allowed", [])
        if method == "both":
            get_allowed = set(entry.get("get", {}).get("allowed", []))
            post_allowed = set(entry.get("post", {}).get("allowed", []))
            entry["allowed"] = sorted(get_allowed & post_allowed)
            if get_allowed != post_allowed:
                entry["get_post_divergence"] = {
                    "get_only": sorted(get_allowed - post_allowed),
                    "post_only": sorted(post_allowed - get_allowed),
                }

        results[name] = entry
        print()

    return results


def _has_non_system(allowed: list[str]) -> bool:
    return any(ns for ns in allowed if ns != "system")


def diff_with_config(discovery: dict[str, Any], config_path: Path) -> list[dict[str, Any]]:
    """Compare discovery results against current config and report differences."""
    with config_path.open() as f:
        config = yaml.safe_load(f)

    default_allowed = config["default_profile"]["constraint"]["allowed"]
    diffs = []

    for name, result in discovery.items():
        resource_config = config.get("resources", {}).get(name, {})
        config_allowed = sorted(
            resource_config.get("constraint", {}).get("allowed", default_allowed)
        )
        discovered_allowed = sorted(result.get("allowed", []))

        if config_allowed != discovered_allowed:
            diff_entry = {
                "resource": name,
                "config_allowed": config_allowed,
                "discovered_allowed": discovered_allowed,
                "list_path": result.get("list_path", ""),
            }
            if "get_post_divergence" in result:
                diff_entry["get_post_divergence"] = result["get_post_divergence"]
            if "get" in result and result["get"].get("errors"):
                diff_entry["get_errors"] = result["get"]["errors"]
            if "post" in result and result["post"].get("errors"):
                diff_entry["post_errors"] = result["post"]["errors"]
            diffs.append(diff_entry)

    return diffs


def print_diff_report(diffs: list[dict[str, Any]]) -> None:
    """Print a human-readable drift report."""
    if not diffs:
        print("\nNo drift found — config matches discovery.")
        return

    print(f"\n{'=' * 70}")
    print(f"  DRIFT REPORT: {len(diffs)} resource(s) with namespace constraint mismatch")
    print(f"{'=' * 70}")

    for d in diffs:
        print(f"\n  {d['resource']}")
        print(f"    Config:     {d['config_allowed']}")
        print(f"    Discovered: {d['discovered_allowed']}")
        if d.get("get_post_divergence"):
            div = d["get_post_divergence"]
            if div.get("get_only"):
                print(f"    GET-only:   {div['get_only']}  (listable but NOT creatable)")
            if div.get("post_only"):
                print(f"    POST-only:  {div['post_only']}  (creatable but NOT listable)")
        if d.get("post_errors"):
            for ns, err in d["post_errors"].items():
                print(f"    POST error ({ns}): {err[:120]}")

    print(f"\n{'=' * 70}")


def main() -> None:
    """Run namespace constraint discovery and generate a report."""
    parser = argparse.ArgumentParser(description="Discover F5 XC namespace constraints")
    parser.add_argument(
        "--specs-dir", default="docs/specifications/api", help="Enriched specs directory"
    )
    parser.add_argument("--output", default="discovery_report.yaml", help="Output report path")
    parser.add_argument("--diff", metavar="CONFIG", help="Diff discovery against config file")
    parser.add_argument(
        "--method",
        choices=["get", "post", "both"],
        default="get",
        help="Probe method: get (list), post (create), or both",
    )
    parser.add_argument(
        "--resources",
        help="Comma-separated list of resource names to test (default: all)",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=200,
        help="Delay between API calls in milliseconds (default: 200)",
    )
    args = parser.parse_args()

    resource_filter = None
    if args.resources:
        resource_filter = [r.strip() for r in args.resources.split(",")]

    specs_dir = Path(args.specs_dir)
    results = discover_all(
        specs_dir,
        method=args.method,
        resource_filter=resource_filter,
        rate_limit_ms=args.rate_limit,
    )

    with Path(args.output).open("w") as f:
        yaml.dump(results, f, default_flow_style=False, sort_keys=True)
    print(f"\nReport written to {args.output}")

    if args.diff:
        diffs = diff_with_config(results, Path(args.diff))
        print_diff_report(diffs)

        diff_path = Path(args.output).with_suffix(".drift.yaml")
        with diff_path.open("w") as f:
            yaml.dump(diffs, f, default_flow_style=False, sort_keys=True)
        print(f"Drift details written to {diff_path}")


if __name__ == "__main__":
    main()
