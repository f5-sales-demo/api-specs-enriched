"""Discover namespace constraints by attempting CRUD operations against the live API.

Uses minimum configuration examples from enriched specs as payloads.
For each resource type, attempts to create in system, default, and a custom
namespace. Classifies results as namespace-restricted or any-namespace.

Requires: F5XC_API_URL/XCSH_API_URL and F5XC_API_TOKEN/XCSH_API_TOKEN env vars.

Usage:
    python scripts/discover_namespace_crud.py --custom-ns demo
    python scripts/discover_namespace_crud.py --resources dns_load_balancer,origin_pool
    python scripts/discover_namespace_crud.py --diff config/namespace_profile.yaml
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
import yaml


def get_api_client() -> tuple[str, dict[str, str]]:
    """Return (base_url, headers) for the F5 XC API."""
    url = os.environ.get("F5XC_API_URL") or os.environ.get("XCSH_API_URL", "")
    token = os.environ.get("F5XC_API_TOKEN") or os.environ.get("XCSH_API_TOKEN", "")
    if not url or not token:
        print(
            "Error: Set F5XC_API_URL/XCSH_API_URL and F5XC_API_TOKEN/XCSH_API_TOKEN",
        )
        raise SystemExit(1)
    headers = {
        "Authorization": f"APIToken {token}",
        "Content-Type": "application/json",
    }
    return url.rstrip("/"), headers


_CREATE_OPID = re.compile(r"^ves\.io\.schema\.(?:views\.)?([a-z0-9_]+)\.API\.Create$")


def build_create_path_index(specs_dir: Path) -> dict[str, dict[str, str]]:
    """Map every resource to its canonical create path by scanning all domain specs.

    A canonical create endpoint is a POST to ``.../namespaces/<ns>/<plural>`` (no
    ``{name}`` segment) whose ``operationId`` is ``ves.io.schema.<resource>.API.Create``.
    This is the deterministic resource identity — index.json's ``api_paths`` are
    incomplete (many resources have an empty list), so we derive paths from the
    full ``paths`` objects instead.

    Returns ``{resource_name: {"create_path": <path with {namespace}>, "spec_file": <file>}}``.
    """
    index: dict[str, dict[str, str]] = {}
    for spec_path in sorted(specs_dir.glob("*.json")):
        if spec_path.name in {
            "index.json",
            "namespace_profiles.json",
            "openapi.json",
            "minimal-export-defaults.json",
        }:
            continue
        try:
            with spec_path.open() as f:
                spec = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        for path, methods in spec.get("paths", {}).items():
            post = methods.get("post") if isinstance(methods, dict) else None
            if not isinstance(post, dict):
                continue
            if "{name}" in path or "{metadata.name}" in path:
                continue
            segs = path.rstrip("/").split("/")
            if "namespaces" not in segs:
                continue
            ni = segs.index("namespaces")
            # canonical create shape: .../namespaces/<ns>/<plural>
            if len(segs) != ni + 3:
                continue
            m = _CREATE_OPID.match(post.get("operationId", ""))
            if not m:
                continue
            resource = m.group(1)
            # normalise the namespace segment to the {namespace} placeholder
            segs[ni + 1] = "{namespace}"
            normalized = "/".join(segs)
            # prefer the shortest canonical path if a resource has several
            if resource not in index or len(normalized) < len(index[resource]["create_path"]):
                index[resource] = {"create_path": normalized, "spec_file": spec_path.name}
    return index


def _authoritative_resource_names(
    specs_dir: Path, path_index: dict[str, dict[str, str]] | None = None
) -> list[str]:
    """Resource universe to test.

    Union of: every resource with a canonical create path (the deterministic,
    spec-derived universe), the namespace_profile config keys, and index primaries.
    """
    names: set[str] = set(path_index.keys()) if path_index else set()
    profile_path = Path("config/namespace_profile.yaml")
    if profile_path.exists():
        with profile_path.open() as f:
            cfg = yaml.safe_load(f) or {}
        names.update((cfg.get("resources") or {}).keys())
    index_path = specs_dir / "index.json"
    if index_path.exists():
        with index_path.open() as f:
            index = json.load(f)
        specs_list = index.get("specifications", [])
        if isinstance(specs_list, dict):
            specs_list = list(specs_list.values())
        for domain_info in specs_list:
            for primary in domain_info.get("x-f5xc-primary-resources", []):
                if primary.get("name"):
                    names.add(primary["name"])
    return sorted(names)


def load_resources_from_specs(
    specs_dir: Path,
    resource_filter: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Load resource types with their create paths and example payloads.

    Driven by the authoritative resource universe (namespace_profile.yaml keys +
    index primaries) and a deterministic create-path index built from the full
    domain spec ``paths`` — not the incomplete ``api_paths`` in index.json.
    """
    path_index = build_create_path_index(specs_dir)
    spec_cache: dict[str, dict] = {}
    resources = []

    for name in _authoritative_resource_names(specs_dir, path_index):
        if resource_filter and name not in resource_filter:
            continue
        entry = path_index.get(name)
        if not entry:
            continue
        spec_file = entry["spec_file"]
        example_payload = _get_example_payload(specs_dir, spec_file, name, spec_cache)
        resources.append(
            {
                "name": name,
                "domain": spec_file.replace(".json", ""),
                "create_path": entry["create_path"],
                "example_payload": example_payload,
            }
        )

    return resources


def _get_example_payload(
    specs_dir: Path,
    spec_file: str,
    resource_name: str,
    cache: dict[str, dict],
) -> dict[str, Any]:
    """Extract the minimum configuration example JSON for a resource."""
    if spec_file not in cache:
        spec_path = specs_dir / spec_file
        if spec_path.exists():
            with spec_path.open() as f:
                cache[spec_file] = json.load(f)
        else:
            cache[spec_file] = {}

    spec = cache[spec_file]
    schemas = spec.get("components", {}).get("schemas", {})

    target_schemas = [
        f"{resource_name}CreateRequest",
        f"schema{resource_name}CreateSpecType",
        f"{resource_name}CreateSpecType",
    ]

    for schema_name in target_schemas:
        schema = schemas.get(schema_name, {})
        minconf = schema.get("x-f5xc-minimum-configuration", {})
        example_json = minconf.get("example_json", "")
        if example_json:
            try:
                return json.loads(example_json)
            except json.JSONDecodeError:
                continue

    return {"metadata": {"name": "ns-crud-probe"}, "spec": {}}


NS_RESTRICTION_PATTERNS = [
    "not permitted",
    "outside system namespace",
    "cannot be created",
    "restricted to",
    "invalid namespace",
    "namespace not allowed",
    "allowed to be created only in",
]

# The F5 XC API rejects a create in the wrong namespace with a message like
# "ves.io.schema.X.Object allowed to be created only in shared or app, got demo".
# Parse the allow-list out of that message. "app" is an internal alias for the
# shared tenant scope, so it maps to "shared".
_ONLY_IN_RE = re.compile(r"allowed to be created only in ([a-z ,or]+?),? got", re.IGNORECASE)
_NS_ALIAS = {"app": "shared"}


def restriction_allowed(error_msg: str) -> list[str]:
    """Extract the allowed namespace set from an API namespace-restriction error.

    Returns e.g. ["system"] or ["shared"]; empty if the message names none
    explicitly (e.g. the generic DNS "outside system namespace not permitted",
    which implies system).
    """
    m = _ONLY_IN_RE.search(error_msg)
    if m:
        tokens = re.split(r"[,\s]+or\s+|\s*,\s*|\s+or\s+", m.group(1).strip())
        allowed = {_NS_ALIAS.get(t.strip(), t.strip()) for t in tokens if t.strip()}
        allowed.discard("")
        if allowed:
            return sorted(allowed)
    low = error_msg.lower()
    if "outside system namespace" in low or "only system namespace" in low:
        return ["system"]
    return []


def classify_response(status_code: int, error_msg: str) -> str:
    """Classify an API response into a namespace constraint signal."""
    if status_code in (200, 201):
        return "created"
    if status_code == 409:
        return "conflict"

    error_lower = error_msg.lower()
    for pattern in NS_RESTRICTION_PATTERNS:
        if pattern in error_lower:
            return "ns_restricted"

    if status_code == 403:
        return "forbidden"
    if status_code == 404:
        return "not_found"
    if status_code in (400, 422):
        return "validation_error"
    if status_code >= 500:
        return "server_error"
    return f"other_{status_code}"


def probe_create(
    base_url: str,
    headers: dict[str, str],
    create_path: str,
    namespace: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Attempt to create a resource in a given namespace."""
    url = f"{base_url}{create_path}".replace("{namespace}", namespace)
    p = json.loads(json.dumps(payload))
    if "metadata" in p:
        p["metadata"]["namespace"] = namespace
        p["metadata"]["name"] = f"ns-crud-probe-{namespace[:4]}"

    try:
        resp = requests.post(url, json=p, headers=headers, timeout=20)
        error_msg = ""
        if resp.status_code >= 400:
            try:
                body = resp.json()
                error_msg = body.get("message", str(body))[:300]
            except Exception:
                error_msg = resp.text[:300]

        result_class = classify_response(resp.status_code, error_msg)

        result = {
            "status": resp.status_code,
            "classification": result_class,
            "error": error_msg or None,
        }
        if result_class == "ns_restricted":
            result["restriction_allowed"] = restriction_allowed(error_msg)

        if result_class in ("created", "conflict"):
            _cleanup(
                base_url,
                headers,
                create_path,
                namespace,
                p["metadata"]["name"],
            )

        return result
    except Exception as e:
        return {"status": 0, "classification": "error", "error": str(e)[:200]}


def _cleanup(
    base_url: str,
    headers: dict[str, str],
    create_path: str,
    namespace: str,
    name: str,
) -> None:
    """Delete a created resource."""
    url = f"{base_url}{create_path}/{name}".replace("{namespace}", namespace)
    with contextlib.suppress(Exception):
        requests.delete(url, headers=headers, timeout=10)


def _load_payload_overrides(overrides_path: Path | None) -> dict[str, Any]:
    """Load payload overrides from a YAML file."""
    if not overrides_path or not overrides_path.exists():
        return {}
    with overrides_path.open() as f:
        return yaml.safe_load(f) or {}


def _prepare_payload(
    resource_name: str,
    example_payload: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Return the override payload if available, otherwise the spec example."""
    override = overrides.get(resource_name)
    if override:
        return {k: v for k, v in override.items() if not k.startswith("_")}
    return example_payload


def _setup_prereqs(
    base_url: str,
    headers: dict[str, str],
    overrides: dict[str, Any],
    resource_name: str,
    namespace: str,
) -> list[str]:
    """Create prerequisite resources for a CRUD probe. Returns cleanup paths."""
    override = overrides.get(resource_name, {})
    prereqs = override.get("_prereqs", [])
    cleanup_paths: list[str] = []

    for prereq in prereqs:
        path = prereq["path"].replace("{namespace}", namespace)
        payload = json.loads(json.dumps(prereq["payload"]))
        if "metadata" in payload:
            payload["metadata"]["namespace"] = namespace
        url = f"{base_url}{path}"
        with contextlib.suppress(Exception):
            requests.post(url, json=payload, headers=headers, timeout=15)
            name = payload.get("metadata", {}).get("name", "")
            if name:
                cleanup_paths.append(f"{path}/{name}")

    return cleanup_paths


def _cleanup_prereqs(
    base_url: str,
    headers: dict[str, str],
    cleanup_paths: list[str],
) -> None:
    """Delete prerequisite resources."""
    for path in cleanup_paths:
        with contextlib.suppress(Exception):
            requests.delete(f"{base_url}{path}", headers=headers, timeout=10)


def _inject_cert_placeholders(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace {CERT_B64}/{KEY_B64} placeholders with a dummy self-signed cert."""
    payload_str = json.dumps(payload)
    if "{CERT_B64}" not in payload_str and "{KEY_B64}" not in payload_str:
        return payload

    with tempfile.TemporaryDirectory() as td:
        key_path = Path(td) / "key.pem"
        cert_path = Path(td) / "cert.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(key_path),
                "-out",
                str(cert_path),
                "-days",
                "1",
                "-nodes",
                "-subj",
                "/CN=crud-probe.example.com",
            ],
            capture_output=True,
            check=True,
        )
        cert_b64 = base64.b64encode(cert_path.read_bytes()).decode()
        key_b64 = base64.b64encode(key_path.read_bytes()).decode()

    payload_str = payload_str.replace("{CERT_B64}", cert_b64).replace("{KEY_B64}", key_b64)
    return json.loads(payload_str)


def discover_all(
    specs_dir: Path,
    custom_ns: str,
    resource_filter: list[str] | None = None,
    rate_limit_ms: int = 300,
    payload_overrides_path: Path | None = None,
) -> dict[str, Any]:
    """Run CRUD namespace discovery for all resources."""
    base_url, headers = get_api_client()
    resources = load_resources_from_specs(specs_dir, resource_filter)
    overrides = _load_payload_overrides(payload_overrides_path)
    namespaces_to_test = ["system", "default", custom_ns]

    if not resources:
        print("No resources found matching filter")
        return {}

    override_count = sum(1 for r in resources if r["name"] in overrides)
    print(
        f"CRUD testing {len(resources)} resources across {namespaces_to_test}"
        f" ({override_count} with payload overrides)...\n"
    )
    results: dict[str, Any] = {}

    for i, resource in enumerate(resources):
        name = resource["name"]
        has_override = name in overrides
        label = f"  [{i + 1}/{len(resources)}] {name}"
        if has_override:
            label += " (override)"
        print(label, end=" ", flush=True)

        payload = _prepare_payload(name, resource["example_payload"], overrides)
        payload = _inject_cert_placeholders(payload)

        entry: dict[str, Any] = {
            "domain": resource["domain"],
            "create_path": resource["create_path"],
            "probes": {},
        }

        created_in: list[str] = []
        restricted_from: list[str] = []
        validation_errors: list[str] = []
        other_results: list[str] = []
        restriction_allowed_set: list[str] = []

        for ns in namespaces_to_test:
            cleanup_paths = _setup_prereqs(base_url, headers, overrides, name, ns)

            ns_payload = json.loads(json.dumps(payload).replace('"{namespace}"', f'"{ns}"'))

            probe = probe_create(
                base_url,
                headers,
                resource["create_path"],
                ns,
                ns_payload,
            )
            entry["probes"][ns] = probe

            _cleanup_prereqs(base_url, headers, cleanup_paths)

            c = probe["classification"]
            if c in ("created", "conflict"):
                created_in.append(ns)
                print(f"+{ns}", end=" ", flush=True)
            elif c == "ns_restricted":
                restricted_from.append(ns)
                for a in probe.get("restriction_allowed", []):
                    if a not in restriction_allowed_set:
                        restriction_allowed_set.append(a)
                print(f"!{ns}", end=" ", flush=True)
            elif c == "validation_error":
                validation_errors.append(ns)
                print(f"~{ns}", end=" ", flush=True)
            else:
                other_results.append(ns)
                print(f"?{ns}", end=" ", flush=True)

            time.sleep(rate_limit_ms / 1000)

        if restricted_from:
            # The API named the allowed namespace(s) in its rejection. "shared" and
            # "system" are NOT custom namespaces, so a shared-only/system-only
            # resource must be scoped precisely, not lumped into the tenant bucket.
            allowed = sorted(restriction_allowed_set) or ["system"]
            if allowed == ["shared"]:
                entry["verdict"] = "shared_only"
            elif allowed == ["system"]:
                entry["verdict"] = "system_only"
            else:
                entry["verdict"] = "restricted"
            entry["confidence"] = "high"
            entry["discovered_allowed"] = allowed
        elif created_in:
            non_system = [ns for ns in created_in if ns != "system"]
            if non_system:
                entry["verdict"] = "any_namespace"
                entry["confidence"] = "high"
                entry["discovered_allowed"] = ["custom", "default", "shared", "system"]
            else:
                entry["verdict"] = "system_confirmed"
                entry["confidence"] = "medium"
                entry["discovered_allowed"] = ["system"]
        elif validation_errors:
            entry["verdict"] = "inconclusive"
            entry["confidence"] = "low"
            entry["note"] = (
                "All namespaces returned validation errors — namespace check may happen after field validation"
            )
        else:
            entry["verdict"] = "inconclusive"
            entry["confidence"] = "low"

        print(f"→ {entry['verdict']}", flush=True)
        results[name] = entry

    return results


def diff_with_config(discovery: dict[str, Any], config_path: Path) -> list[dict[str, Any]]:
    """Compare discovery results against namespace_profile.yaml."""
    with config_path.open() as f:
        config = yaml.safe_load(f)

    default_allowed = sorted(config["default_profile"]["constraint"]["allowed"])
    resources_config = config.get("resources", {})
    diffs = []

    for name, result in discovery.items():
        if result.get("confidence") == "low":
            continue

        discovered = sorted(result.get("discovered_allowed", []))
        resource_conf = resources_config.get(name, {})
        config_allowed = sorted(resource_conf.get("constraint", {}).get("allowed", default_allowed))

        if config_allowed != discovered:
            diffs.append(
                {
                    "resource": name,
                    "domain": result.get("domain", ""),
                    "config_allowed": config_allowed,
                    "discovered_allowed": discovered,
                    "verdict": result["verdict"],
                    "confidence": result["confidence"],
                }
            )

    return diffs


def print_summary(results: dict[str, Any], diffs: list[dict[str, Any]] | None) -> None:
    """Print a summary report."""
    verdicts: dict[str, int] = {}
    for r in results.values():
        v = r.get("verdict", "unknown")
        verdicts[v] = verdicts.get(v, 0) + 1

    print(f"\n{'=' * 60}")
    print("  CRUD NAMESPACE DISCOVERY SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total resources tested: {len(results)}")
    for v, count in sorted(verdicts.items()):
        print(f"  {v}: {count}")

    if diffs:
        print(f"\n{'─' * 60}")
        print(f"  DRIFT: {len(diffs)} resources differ from config")
        print(f"{'─' * 60}")
        for d in diffs:
            print(f"\n  {d['resource']} ({d['domain']})")
            print(f"    Config:     {d['config_allowed']}")
            print(f"    Discovered: {d['discovered_allowed']}")
            print(f"    Verdict:    {d['verdict']} (confidence: {d['confidence']})")

    print(f"\n{'=' * 60}")


def main() -> None:
    """Run CRUD-based namespace discovery."""
    parser = argparse.ArgumentParser(description="Discover namespace constraints via CRUD testing")
    parser.add_argument(
        "--specs-dir",
        default="docs/specifications/api",
        help="Enriched specs directory",
    )
    parser.add_argument(
        "--output",
        default="namespace_crud_report.yaml",
        help="Output report path",
    )
    parser.add_argument(
        "--diff",
        metavar="CONFIG",
        help="Diff discovery against config file",
    )
    parser.add_argument(
        "--resources",
        help="Comma-separated resource names to test (default: all)",
    )
    parser.add_argument(
        "--custom-ns",
        default="demo",
        help="Custom namespace to test in (default: demo)",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=300,
        help="Delay between API calls in ms (default: 300)",
    )
    parser.add_argument(
        "--payloads",
        help="YAML file with payload overrides for resources with complex schemas",
    )
    args = parser.parse_args()

    resource_filter = None
    if args.resources:
        resource_filter = [r.strip() for r in args.resources.split(",")]

    payload_overrides_path = Path(args.payloads) if args.payloads else None

    specs_dir = Path(args.specs_dir)
    results = discover_all(
        specs_dir,
        custom_ns=args.custom_ns,
        resource_filter=resource_filter,
        rate_limit_ms=args.rate_limit,
        payload_overrides_path=payload_overrides_path,
    )

    with Path(args.output).open("w") as f:
        yaml.dump(results, f, default_flow_style=False, sort_keys=True)
    print(f"\nReport written to {args.output}")

    diffs = None
    if args.diff:
        diffs = diff_with_config(results, Path(args.diff))

    print_summary(results, diffs)

    if diffs:
        drift_path = Path(args.output).with_suffix(".drift.yaml")
        with drift_path.open("w") as f:
            yaml.dump(diffs, f, default_flow_style=False, sort_keys=True)
        print(f"Drift details written to {drift_path}")


if __name__ == "__main__":
    main()
