"""Fetch and embed F5's current network allowlist inventory in the master OpenAPI."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from scripts.utils.json_writer import write_json_file

SOURCE_URL = (
    "https://docs.cloud.f5.com/docs-v2/downloads/platform/reference/"
    "network-cloud-ref/ips-domains.json"
)
EXTENSION = "x-f5xc-network-allowlist"
DEFAULT_OPENAPI = Path("docs/specifications/api/openapi.json")
MAX_BYTES = 1024 * 1024
MAX_SECONDS = 15
ADDRESS_FIELDS = frozenset({"ipv4_cidrs", "ipv4_ips", "dns_servers", "ntp_servers"})
LIST_FIELDS = ADDRESS_FIELDS | {"domains"}
SERVICE_TYPES = frozenset(
    {
        "regional_edges",
        "cdn",
        "secondary_dns_zone_transfer",
        "global_log_receiver",
        "dnslb_health_checks",
        "global_controller_sso_egress",
        "bot_defense",
        "data_intelligence",
    }
)
SITE_TYPES = frozenset({"secure_mesh_v2", "legacy"})
SERVICE_FIELDS = {
    "regional_edges": "regions",
    "cdn": "ipv4_cidrs",
    "secondary_dns_zone_transfer": "ipv4_ips",
    "global_log_receiver": "ipv4_cidrs",
    "dnslb_health_checks": "ipv4_ips",
    "global_controller_sso_egress": "ipv4_ips",
    "bot_defense": "domains",
    "data_intelligence": "regions",
}
DOMAIN_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class AllowlistError(ValueError):
    """The live feed or target document is unavailable or invalid."""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AllowlistError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _valid_domain(value: str) -> bool:
    name = value.removeprefix(".")
    return (
        len(name) <= 253
        and "." in name
        and all(DOMAIN_LABEL.fullmatch(label) for label in name.split("."))
    )


def _validate_tree(node: Any, path: str) -> int:
    if not isinstance(node, dict) or not node:
        raise AllowlistError(f"{path}: expected nonempty object")
    count = 0
    for key, value in node.items():
        child = f"{path}.{key}"
        if key in LIST_FIELDS:
            if not isinstance(value, list) or not value:
                raise AllowlistError(f"{child}: expected nonempty list")
            for entry in value:
                if not isinstance(entry, str) or not entry or entry != entry.strip():
                    raise AllowlistError(f"{child}: invalid entry")
                if key == "domains":
                    if not _valid_domain(entry):
                        raise AllowlistError(f"{child}: invalid entry {entry!r}")
                    continue
                try:
                    if key == "ipv4_cidrs":
                        # F5 also publishes individual addresses in this field.
                        ipaddress.IPv4Network(entry, strict=False)
                    else:
                        ipaddress.IPv4Address(entry)
                except ValueError as exc:
                    raise AllowlistError(f"{child}: invalid entry {entry!r}") from exc
            count += len(value)
        elif isinstance(value, dict):
            count += _validate_tree(value, child)
        else:
            raise AllowlistError(f"{child}: unsupported manifest shape")
    return count


def validate(manifest: Any) -> dict[str, Any]:
    """Validate the documented manifest envelope and every address leaf."""
    expected = {
        "manifest_type",
        "schema_version",
        "manifest_version",
        "generated_at",
        "reference",
        "services",
        "customer_edge",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected:
        raise AllowlistError("unsupported manifest envelope")
    if manifest["manifest_type"] != "firewall_proxy_allowlist":
        raise AllowlistError("unsupported manifest type")
    if manifest["schema_version"] != "1.0.0":
        raise AllowlistError("unsupported manifest schema version")
    if not isinstance(manifest["manifest_version"], str) or not VERSION.fullmatch(
        manifest["manifest_version"]
    ):
        raise AllowlistError("invalid manifest version")
    if not isinstance(manifest["generated_at"], str) or not TIMESTAMP.fullmatch(
        manifest["generated_at"]
    ):
        raise AllowlistError("invalid generated_at")
    if (
        manifest["reference"]
        != "https://docs.cloud.f5.com/docs-v2/platform/reference/network-cloud-ref"
    ):
        raise AllowlistError("unexpected manifest reference")
    services = manifest["services"]
    edge = manifest["customer_edge"]
    if not isinstance(services, dict) or not services or not set(services) <= SERVICE_TYPES:
        raise AllowlistError("unsupported services shape")
    for name, section in services.items():
        if not isinstance(section, dict) or set(section) != {SERVICE_FIELDS[name]}:
            raise AllowlistError(f"unsupported {name} shape")
    if not isinstance(edge, dict) or set(edge) != {"defaults", "site_types"}:
        raise AllowlistError("unsupported customer_edge shape")
    site_types = edge["site_types"]
    if not isinstance(site_types, dict) or not site_types or not set(site_types) <= SITE_TYPES:
        raise AllowlistError("unsupported Customer Edge site type")
    count = _validate_tree(services, "services")
    count += _validate_tree(edge, "customer_edge")
    if count == 0:
        raise AllowlistError("manifest contains no addresses or domains")
    return manifest


def digest(manifest: dict[str, Any]) -> str:
    """SHA-256 of sorted-key, compact UTF-8 JSON; array order is preserved."""
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extension(manifest: dict[str, Any]) -> dict[str, Any]:
    """Build the documented metadata wrapper around the unmodified manifest."""
    validate(manifest)
    return {"source_url": SOURCE_URL, "sha256": digest(manifest), "manifest": manifest}


def fetch() -> dict[str, Any]:
    """Fetch only HTTPS bytes, with curl-enforced total time and size bounds."""
    command = [
        "curl",
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--max-time",
        str(MAX_SECONDS),
        "--max-filesize",
        str(MAX_BYTES),
        SOURCE_URL,
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=MAX_SECONDS + 5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AllowlistError("F5 network allowlist retrieval failed") from exc
    if result.returncode != 0:
        raise AllowlistError(
            f"F5 network allowlist HTTP retrieval failed (curl exit {result.returncode})"
        )
    if not result.stdout or len(result.stdout) > MAX_BYTES:
        raise AllowlistError("F5 network allowlist response is empty or too large")
    try:
        manifest = json.loads(result.stdout, object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AllowlistError("F5 network allowlist is malformed JSON") from exc
    return validate(manifest)


def _load_openapi(path: Path) -> dict[str, Any]:
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AllowlistError(f"cannot read OpenAPI document: {path}") from exc
    if not isinstance(spec, dict) or not isinstance(spec.get("info"), dict):
        raise AllowlistError(f"invalid OpenAPI document: {path}")
    return spec


def check(path: Path = DEFAULT_OPENAPI) -> bool:
    """Return whether the live digest differs from the checked-out document."""
    manifest = fetch()
    current = _load_openapi(path)["info"].get(EXTENSION)
    return not isinstance(current, dict) or current.get("sha256") != digest(manifest)


def update(path: Path = DEFAULT_OPENAPI) -> bool:
    """Atomically replace the OpenAPI file only when the live inventory changes."""
    manifest = fetch()
    spec = _load_openapi(path)
    value = extension(manifest)
    if spec["info"].get(EXTENSION) == value:
        return False
    spec["info"][EXTENSION] = value
    fd, temporary = tempfile.mkstemp(prefix=".network-allowlist-", suffix=".json", dir=path.parent)
    os.close(fd)
    staged = Path(temporary)
    try:
        write_json_file(spec, staged, indent=2, ensure_ascii=False)
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)
    return True


def main(argv: list[str] | None = None) -> int:
    """Run the live check or atomic merge command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="return 1 when live feed differs")
    parser.add_argument("--openapi", type=Path, default=DEFAULT_OPENAPI)
    args = parser.parse_args(argv)
    try:
        changed = check(args.openapi) if args.check else update(args.openapi)
    except AllowlistError as exc:
        print(f"network allowlist: {exc}", file=sys.stderr)
        return 2
    print(f"network allowlist: {'changed' if changed else 'unchanged'}")
    return int(changed) if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
