"""Build and validate the immutable AWS SMSv2 contract release assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from scripts.utils.interface_contract_enricher import (
    InterfaceContractEnricher,
    InterfaceContractValidationError,
    validate_aws_telemetry_intake,
)

CONTRACT_FILE = "smsv2-contract.json"
EVIDENCE_FILE = "smsv2-evidence-receipt.json"
MANIFEST_FILE = "smsv2-contract-manifest.json"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_VERSION = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
_FORBIDDEN_EVIDENCE = re.compile(
    r"(?i)(?:\b(?:bearer|authorization|password|secret|cookie)\b|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|\b(?:i|eni|subnet|vpc|tgw)-[0-9a-f]{8,}\b|\b(?:\d{1,3}\.){3}\d{1,3}\b)"
)


class Smsv2ReleaseValidationError(ValueError):
    """Raised when a SMSv2 contract release cannot be safely consumed."""


def canonical_json(value: Any) -> bytes:
    """Encode a release value deterministically."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def digest(value: bytes) -> str:
    """Return the algorithm-qualified digest used by release receipts."""
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise Smsv2ReleaseValidationError(f"{field} must be a UTC RFC3339 timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise Smsv2ReleaseValidationError(f"{field} is malformed") from error


def _load_contract(config_path: Path) -> dict[str, Any]:
    try:
        InterfaceContractEnricher(config_path)
    except InterfaceContractValidationError as error:
        raise Smsv2ReleaseValidationError(str(error)) from error
    config = yaml.safe_load(config_path.read_text())
    try:
        contract = config["contracts"]["securemesh_site_v2"]["contract"]
    except (KeyError, TypeError) as error:
        raise Smsv2ReleaseValidationError("SMSv2 contract configuration is missing") from error
    if not isinstance(contract, dict):
        raise Smsv2ReleaseValidationError("SMSv2 contract must be an object")
    return contract


def _evidence(contract: dict[str, Any]) -> dict[str, Any]:
    try:
        evidence = contract["providers"]["aws"]["evidence"]
    except (KeyError, TypeError) as error:
        raise Smsv2ReleaseValidationError("AWS evidence is missing") from error
    if not isinstance(evidence, dict) or not isinstance(evidence.get("receipts"), list):
        raise Smsv2ReleaseValidationError("AWS evidence is malformed")
    receipt = {
        "contract_id": contract.get("contract_id"),
        "provenance": evidence.get("provenance"),
        "observed_at": evidence.get("observed_at"),
        "profiles": evidence.get("profiles"),
        "receipts": evidence.get("receipts"),
    }
    _assert_sanitized_evidence(receipt)
    return receipt


def _assert_sanitized_evidence(value: object) -> None:
    encoded = canonical_json(value).decode("ascii")
    if _FORBIDDEN_EVIDENCE.search(encoded):
        raise Smsv2ReleaseValidationError(
            "evidence receipt contains sensitive or identifying material"
        )
    if '"sanitized":true' not in encoded:
        raise Smsv2ReleaseValidationError("evidence receipt is not explicitly sanitized")


def build_release_assets(
    config_path: Path, output_dir: Path, version: str, commit: str
) -> dict[str, bytes]:
    """Build the three checksummed release assets from the validated config."""
    if not _VERSION.fullmatch(version):
        raise Smsv2ReleaseValidationError("release tag must be a stable vMAJOR.MINOR.PATCH tag")
    if not _COMMIT.fullmatch(commit):
        raise Smsv2ReleaseValidationError("release commit must be a full lowercase SHA-1")
    contract = _load_contract(config_path)
    evidence = _evidence(contract)
    contract_bytes = canonical_json(contract)
    evidence_bytes = canonical_json(evidence)
    manifest = {
        "schema_version": 1,
        "contract_id": contract.get("contract_id"),
        "contract_version": contract.get("version"),
        "release": {"tag": version, "commit": commit},
        "assets": {CONTRACT_FILE: digest(contract_bytes), EVIDENCE_FILE: digest(evidence_bytes)},
    }
    manifest_bytes = canonical_json(manifest)
    output_dir.mkdir(parents=True, exist_ok=True)
    assets = {
        CONTRACT_FILE: contract_bytes,
        EVIDENCE_FILE: evidence_bytes,
        MANIFEST_FILE: manifest_bytes,
    }
    for name, content in assets.items():
        (output_dir / name).write_bytes(content)
    return assets


def validate_release_assets(
    manifest_bytes: bytes,
    assets: dict[str, bytes],
    release: dict[str, Any],
    receipt: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(days=180),
) -> dict[str, Any]:
    """Validate a stable GitHub release, manifest, receipt, and asset checksums."""
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as error:
        raise Smsv2ReleaseValidationError("contract manifest is malformed") from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise Smsv2ReleaseValidationError("contract manifest schema is unsupported")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not _VERSION.fullmatch(tag):
        raise Smsv2ReleaseValidationError("release must use a stable semantic version tag")
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise Smsv2ReleaseValidationError("draft or prerelease contract releases are unavailable")
    if release.get("immutable") is not True:
        raise Smsv2ReleaseValidationError("mutable contract releases are unavailable")
    published = _parse_timestamp(release.get("published_at"), "release.published_at")
    current = now or datetime.now(UTC)
    if published > current or current - published > max_age:
        raise Smsv2ReleaseValidationError("contract release is stale")
    release_identity = manifest.get("release")
    if not isinstance(release_identity, dict) or release_identity.get("tag") != tag:
        raise Smsv2ReleaseValidationError("release tag does not match contract manifest")
    commit = release.get("target_commitish")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise Smsv2ReleaseValidationError("release commit identity is malformed")
    if release_identity.get("commit") != commit or receipt.get("commit") != commit:
        raise Smsv2ReleaseValidationError(
            "release commit identity does not match manifest and receipt"
        )
    expected = manifest.get("assets")
    if not isinstance(expected, dict) or set(expected) != {CONTRACT_FILE, EVIDENCE_FILE}:
        raise Smsv2ReleaseValidationError("contract manifest asset set is invalid")
    for name, expected_digest in expected.items():
        content = assets.get(name)
        if (
            not isinstance(expected_digest, str)
            or not _SHA256.fullmatch(expected_digest)
            or content is None
        ):
            raise Smsv2ReleaseValidationError("contract release asset is missing or malformed")
        if (
            digest(content) != expected_digest
            or receipt.get("assets", {}).get(name) != expected_digest
        ):
            raise Smsv2ReleaseValidationError(
                "contract release checksum does not match manifest and receipt"
            )
    try:
        contract = json.loads(assets[CONTRACT_FILE])
        evidence = json.loads(assets[EVIDENCE_FILE])
    except json.JSONDecodeError as error:
        raise Smsv2ReleaseValidationError("contract release asset JSON is malformed") from error
    _assert_sanitized_evidence(evidence)
    if contract.get("contract_id") != manifest.get("contract_id") or contract.get(
        "version"
    ) != manifest.get("contract_version"):
        raise Smsv2ReleaseValidationError("contract identity does not match manifest")
    aws = contract.get("providers", {}).get("aws", {})
    capabilities = aws.get("capabilities")
    if capabilities != {
        "aws_ce_create": "available",
        "runtime_status": "unavailable",
        "tgw_connect": "unavailable",
    }:
        raise Smsv2ReleaseValidationError("AWS SMSv2 capability model is unavailable or unproven")
    try:
        validate_aws_telemetry_intake(aws.get("telemetry_intake"))
    except InterfaceContractValidationError as error:
        raise Smsv2ReleaseValidationError(str(error)) from error
    return contract


def main() -> None:
    """Build deterministic contract assets for the release workflow."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/interface_contracts.yaml"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    build_release_assets(args.config, args.output_dir, args.version, args.commit)


if __name__ == "__main__":
    main()
