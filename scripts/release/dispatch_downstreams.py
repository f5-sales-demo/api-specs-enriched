#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Dispatch one verified release with durable, per-target delivery receipts."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import yaml

from scripts.release.reconcile_publication import (
    PublicationReceipt,
    expected_asset_names,
    publication_receipt_from_body,
    release_asset_digests,
    repository_name,
)
from scripts.release.source_provenance import (
    SourceProvenance,
    require_source_provenance,
    source_provenance_at,
)

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
RECEIPT_PREFIX = "<!-- downstream-delivery:"
DETAIL_KINDS = frozenset({"current_pin", "publication_map", "receipt_map"})
ROOT_FIELDS = frozenset({"downstream_repositories"})
TARGET_FIELDS = frozenset(
    {
        "description",
        "detail_asset_names",
        "detail_collection",
        "detail_generated_names",
        "detail_kind",
        "detail_npm_packages",
        "detail_path",
        "detail_publication_fields",
        "detail_ref",
        "detail_spec_pin_indent",
        "enabled",
        "event_type",
        "owner",
        "publication_authority",
        "receipt_path",
        "receipt_ref",
        "repo",
        "source_asset_name",
    }
)
AUTHORITY_FIELDS = {
    "github_release": frozenset({"kind", "repository", "source_pin_path"}),
    "github_release_npm": frozenset(
        {
            "dependency_repository",
            "kind",
            "npm_registry",
            "repository",
            "source_pin_path",
        }
    ),
    "github_release_marketplaces": frozenset(
        {
            "kind",
            "marketplace_asset_url",
            "open_vsx_download_origin",
            "open_vsx_metadata_url",
            "repository",
        }
    ),
    "receiver_commit_source_pin": frozenset({"kind"}),
}
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
NETWORK_TIMEOUT_SECONDS = 30
NETWORK_PROCESS_TIMEOUT_SECONDS = NETWORK_TIMEOUT_SECONDS + 5
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_POLL_ATTEMPTS = 60
MAX_POLL_INTERVAL_SECONDS = 30
MAX_POLL_DURATION_SECONDS = 30 * 60


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    """Construct one mapping without YAML's implicit last-key-wins behavior."""
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def delivery_id(
    source: str,
    target: dict[str, Any],
    version: str,
    tag: str,
    commit: str,
) -> str:
    """Return the stable idempotency key shared with one downstream."""
    identity = {
        "commit": commit,
        "event_type": target["event_type"],
        "source": source,
        "tag": tag,
        "target": f"{target['owner']}/{target['repo']}",
        "version": version,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def receipt_marker(identifier: str) -> str:
    """Return the hidden release-body marker for a completed delivery."""
    return f"{RECEIPT_PREFIX}{identifier} -->"


def _string_list(item: dict[str, Any], field: str, index: int) -> list[str]:
    """Return one nonempty duplicate-free configuration string list."""
    values = item.get(field)
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(value, str) or not value for value in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError(f"enabled downstream target {index} has invalid {field}")
    return values


def _validated_authority(item: dict[str, Any], index: int) -> dict[str, str]:
    """Return one exact, fail-closed downstream publication authority contract."""
    raw = item.get("publication_authority")
    if not isinstance(raw, dict):
        raise TypeError(f"enabled downstream target {index} has no publication_authority")
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in AUTHORITY_FIELDS:
        raise ValueError(f"enabled downstream target {index} has invalid publication authority")
    if set(raw) != AUTHORITY_FIELDS[kind] or any(
        not isinstance(value, str) or not value for value in raw.values()
    ):
        raise ValueError(f"enabled downstream target {index} has invalid {kind} authority fields")
    authority = dict(raw)
    for field in ("repository", "dependency_repository"):
        value = authority.get(field)
        if value is not None and not REPOSITORY.fullmatch(value):
            raise ValueError(f"enabled downstream target {index} has invalid authority {field}")
    for field in ("source_pin_path",):
        value = authority.get(field)
        if value is not None and (value.startswith("/") or ".." in Path(value).parts):
            raise ValueError(f"enabled downstream target {index} has invalid authority {field}")
    if kind == "github_release_npm":
        registry = authority["npm_registry"]
        if urlsplit(registry).scheme != "https" or urlsplit(registry).path not in {"", "/"}:
            raise ValueError(f"enabled downstream target {index} has invalid npm registry")
    if kind == "github_release_marketplaces":
        metadata = authority["open_vsx_metadata_url"]
        marketplace = authority["marketplace_asset_url"]
        origin = authority["open_vsx_download_origin"]
        if (
            metadata.count("{version}") != 1
            or marketplace.count("{version}") != 1
            or urlsplit(metadata).scheme != "https"
            or urlsplit(marketplace).scheme != "https"
            or urlsplit(origin).scheme != "https"
            or not origin.endswith("/")
        ):
            raise ValueError(
                f"enabled downstream target {index} has invalid marketplace authority URLs"
            )
    return authority


def load_targets(path: Path) -> list[dict[str, Any]]:
    """Load and strictly validate enabled downstream targets."""
    try:
        document = yaml.load(
            path.read_text(encoding="utf-8"),
            Loader=_UniqueKeyLoader,  # noqa: S506 - strict SafeLoader subclass
        )
    except yaml.YAMLError as exc:
        raise ValueError(f"downstream configuration contains malformed YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise TypeError("downstream configuration must be an object")
    unknown_root_fields = set(document) - ROOT_FIELDS
    if unknown_root_fields:
        raise ValueError(
            "downstream configuration has unknown root fields: "
            f"{', '.join(sorted(str(field) for field in unknown_root_fields))}"
        )
    configured = document.get("downstream_repositories")
    if not isinstance(configured, list):
        raise TypeError("downstream_repositories must be a list")

    targets: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    for index, item in enumerate(configured):
        if not isinstance(item, dict):
            raise TypeError(f"downstream target {index} must be an object")
        unknown_target_fields = set(item) - TARGET_FIELDS
        if unknown_target_fields:
            raise ValueError(
                f"downstream target {index} has unknown fields: "
                f"{', '.join(sorted(str(field) for field in unknown_target_fields))}"
            )
        if item.get("enabled") is not True:
            continue
        target: dict[str, Any] = {}
        for field in (
            "owner",
            "repo",
            "event_type",
            "receipt_path",
            "receipt_ref",
            "detail_path",
            "detail_ref",
            "detail_kind",
        ):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"enabled downstream target {index} has no {field}")
            target[field] = value
        if target["receipt_path"] != "tools/spec-deliveries.json":
            raise ValueError("downstream receipt_path must be tools/spec-deliveries.json")
        if target["receipt_ref"] != "main":
            raise ValueError("downstream receipt_ref must be main")
        if target["detail_ref"] != "main":
            raise ValueError("downstream detail_ref must be main")
        if target["detail_path"] == target["receipt_path"]:
            raise ValueError("downstream detail_path must differ from receipt_path")
        if target["detail_kind"] not in DETAIL_KINDS:
            raise ValueError(f"unsupported downstream detail_kind: {target['detail_kind']}")
        if target["detail_kind"] in {"publication_map", "receipt_map"}:
            collection = item.get("detail_collection")
            expected_collection = (
                "publications" if target["detail_kind"] == "publication_map" else "receipts"
            )
            if collection != expected_collection:
                raise ValueError(f"enabled downstream target {index} has invalid detail_collection")
            target["detail_collection"] = collection
            target["detail_publication_fields"] = _string_list(
                item, "detail_publication_fields", index
            )
        if target["detail_kind"] == "receipt_map":
            target["detail_asset_names"] = _string_list(item, "detail_asset_names", index)
            indent = item.get("detail_spec_pin_indent")
            if indent not in {"spaces", "tabs"}:
                raise ValueError(
                    f"enabled downstream target {index} has invalid detail_spec_pin_indent"
                )
            target["detail_spec_pin_indent"] = indent
            if "generated" in target["detail_publication_fields"]:
                target["detail_generated_names"] = _string_list(
                    item, "detail_generated_names", index
                )
            if "npm" in target["detail_publication_fields"]:
                target["detail_npm_packages"] = _string_list(item, "detail_npm_packages", index)
        if target["detail_kind"] == "publication_map":
            source_asset_name = item.get("source_asset_name")
            if not isinstance(source_asset_name, str) or "{release_tag}" not in source_asset_name:
                raise ValueError(f"enabled downstream target {index} has invalid source_asset_name")
            target["source_asset_name"] = source_asset_name
        target["publication_authority"] = _validated_authority(item, index)
        authority = target["publication_authority"]
        authority_kind = authority["kind"]
        target_repository = f"{target['owner']}/{target['repo']}"
        if authority_kind != "receiver_commit_source_pin" and (
            authority.get("repository") != target_repository
        ):
            raise ValueError(
                f"enabled downstream target {index} authority repository differs from target"
            )
        if target["detail_kind"] == "current_pin":
            expected_authority = "receiver_commit_source_pin"
        elif target["detail_kind"] == "publication_map":
            expected_authority = "github_release_marketplaces"
        elif "npm" in target["detail_publication_fields"]:
            expected_authority = "github_release_npm"
        else:
            expected_authority = "github_release"
        if authority_kind != expected_authority:
            raise ValueError(
                f"enabled downstream target {index} authority does not match its detail kind"
            )
        identity = (target["owner"], target["repo"], target["event_type"])
        if identity in identities:
            raise ValueError(f"duplicate downstream target: {'/'.join(identity)}")
        identities.add(identity)
        targets.append(target)
    if not targets:
        raise ValueError("downstream configuration has no enabled targets")
    return targets


def _network_run(
    command: list[str],
    *,
    input_text: str | None = None,
    use_downstream_token: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    """Run one bounded network command with the explicitly selected credential."""
    environment = dict(os.environ)
    if use_downstream_token:
        token = environment.get("DOWNSTREAM_DISPATCH_TOKEN")
        if not token:
            raise RuntimeError("DOWNSTREAM_DISPATCH_TOKEN is required for downstream verification")
        environment["GH_TOKEN"] = token
    try:
        return subprocess.run(
            command,
            input=input_text,
            env=environment,
            capture_output=True,
            text=text,
            check=False,
            timeout=NETWORK_PROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"network command exceeded {NETWORK_PROCESS_TIMEOUT_SECONDS} seconds: {command[0]}"
        ) from exc


def _json_response(result: subprocess.CompletedProcess[Any], label: str) -> dict[str, Any]:
    """Decode one successful network response as a JSON object."""
    if result.returncode != 0:
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        raise RuntimeError(f"cannot query {label}: {stderr.strip()}")
    try:
        document = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f"{label} response is not valid JSON") from exc
    if not isinstance(document, dict):
        raise TypeError(f"{label} response is not an object")
    return document


def _github_json(endpoint: str, label: str) -> dict[str, Any]:
    """Read one authoritative downstream GitHub API object."""
    return _json_response(_network_run(["gh", "api", endpoint]), label)


def github_release(repo: str, tag: str) -> dict[str, Any] | None:
    """Read one upstream release through a bounded GitHub API request."""
    result = _network_run(
        ["gh", "api", f"repos/{repo}/releases/tags/{tag}"],
        use_downstream_token=False,
    )
    if result.returncode == 0:
        return _json_response(result, f"release {repo}@{tag}")
    if isinstance(result.stderr, str) and "HTTP 404" in result.stderr:
        return None
    raise RuntimeError(f"cannot query release {tag}: {str(result.stderr).strip()}")


def post_dispatch(target: dict[str, Any], payload: dict[str, str]) -> None:
    """Submit one repository_dispatch payload and fail on any API error."""
    request = json.dumps(
        {"event_type": target["event_type"], "client_payload": payload},
        sort_keys=True,
    )
    result = _network_run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{target['owner']}/{target['repo']}/dispatches",
            "--input",
            "-",
        ],
        input_text=request,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"dispatch to {target['owner']}/{target['repo']} failed: {result.stderr.strip()}"
        )


def _receiver_document(
    target: dict[str, Any],
    document_path: str,
    document_ref: str,
    label: str,
) -> Any | None:
    """Read one receiver JSON document, returning ``None`` only for HTTP 404."""
    endpoint = (
        f"repos/{target['owner']}/{target['repo']}/contents/{document_path}?ref={document_ref}"
    )
    result = _network_run(
        [
            "gh",
            "api",
            "--header",
            "Accept: application/vnd.github.raw+json",
            endpoint,
        ],
    )
    if result.returncode != 0:
        if "HTTP 404" in result.stderr:
            return None
        raise RuntimeError(
            f"cannot read {label} for {target['owner']}/{target['repo']}: {result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"receiver {label} for {target['owner']}/{target['repo']} is not valid JSON"
        ) from exc


def _receiver_ref_commit(target: dict[str, Any]) -> str:
    """Resolve the receiver branch once so both evidence files use one snapshot."""
    result = _network_run(
        [
            "gh",
            "api",
            f"repos/{target['owner']}/{target['repo']}/git/ref/heads/{target['receipt_ref']}",
        ],
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cannot resolve receiver ref for {target['owner']}/{target['repo']}: "
            f"{result.stderr.strip()}"
        )
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"receiver ref for {target['owner']}/{target['repo']} is not valid JSON"
        ) from exc
    git_object = document.get("object") if isinstance(document, dict) else None
    if (
        not isinstance(git_object, dict)
        or git_object.get("type") != "commit"
        or not isinstance(git_object.get("sha"), str)
        or not COMMIT.fullmatch(git_object["sha"])
    ):
        raise RuntimeError(f"receiver ref for {target['owner']}/{target['repo']} is malformed")
    return git_object["sha"]


def _validated_common_ledger(
    target: dict[str, Any], document: Any, source: str
) -> dict[str, dict[str, str]]:
    """Validate every common-ledger entry and its canonical delivery ID."""
    if (
        not isinstance(document, dict)
        or set(document) != {"deliveries", "version"}
        or document.get("version") != 1
        or not isinstance(document.get("deliveries"), dict)
    ):
        raise RuntimeError(f"receiver ledger for {target['owner']}/{target['repo']} is malformed")
    deliveries: dict[str, dict[str, str]] = {}
    tags: set[str] = set()
    for identifier, raw_entry in document["deliveries"].items():
        if not isinstance(identifier, str) or not SHA256.fullmatch(identifier):
            raise RuntimeError("receiver ledger contains an invalid delivery ID")
        if not isinstance(raw_entry, dict) or set(raw_entry) != {
            "release_tag",
            "target_commit",
            "version",
        }:
            raise RuntimeError("receiver ledger contains a malformed delivery entry")
        entry = raw_entry
        version = entry.get("version")
        tag = entry.get("release_tag")
        commit = entry.get("target_commit")
        if (
            not isinstance(version, str)
            or not SEMVER.fullmatch(version)
            or tag != f"v{version}"
            or not isinstance(commit, str)
            or not COMMIT.fullmatch(commit)
        ):
            raise RuntimeError("receiver ledger contains a malformed delivery identity")
        if tag in tags:
            raise RuntimeError("receiver ledger contains a duplicate release tag")
        tags.add(tag)
        if identifier != delivery_id(source, target, version, tag, commit):
            raise RuntimeError("receiver ledger contains a noncanonical delivery ID")
        deliveries[identifier] = entry
    return deliveries


def _validate_digest_map(value: Any, label: str, *, allow_empty: bool = False) -> dict[str, str]:
    """Validate and return a string-to-SHA256 map."""
    if (
        not isinstance(value, dict)
        or (not value and not allow_empty)
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(digest, str)
            or not SHA256.fullmatch(digest)
            for name, digest in value.items()
        )
    ):
        raise RuntimeError(f"{label} is not a valid SHA-256 map")
    return value


def _source_pin_sha256(
    receipt: PublicationReceipt,
    delivery_entry: dict[str, str],
    indentation: str,
) -> str:
    """Hash the receiver's canonical source-release pin representation."""
    if (
        receipt.version != delivery_entry["version"]
        or receipt.commit != delivery_entry["target_commit"]
    ):
        raise RuntimeError("source receipt conflicts with the detailed delivery identity")
    pin = {
        "assets": dict(sorted(receipt.assets.items())),
        "release_tag": delivery_entry["release_tag"],
        "target_commit": delivery_entry["target_commit"],
        "version": delivery_entry["version"],
    }
    indent: int | str = "\t" if indentation == "tabs" else 2
    canonical = f"{json.dumps(pin, indent=indent, sort_keys=True)}\n"
    return hashlib.sha256(canonical.encode()).hexdigest()


def _configured_names(
    target: dict[str, Any], field: str, *, version: str | None = None
) -> set[str]:
    """Expand one exact configured evidence-name set."""
    try:
        return {
            name.format(version=version) if version is not None else name for name in target[field]
        }
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"downstream {field} contains an invalid template") from exc


def _validate_vscode_publication_identity(
    evidence: dict[str, Any], delivery_entry: dict[str, str], identifier: str
) -> None:
    """Derive VS Code's tag and marketplace version from source version plus epoch."""
    epoch = evidence["publication_epoch"]
    try:
        published = datetime.fromtimestamp(int(epoch), UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise RuntimeError(
            f"detailed publication evidence {identifier} has an invalid publication epoch"
        ) from exc
    timestamp = published.strftime("%y%m%d%H%M%S")
    expected_tag = f"v{delivery_entry['version']}-{timestamp}"
    major = delivery_entry["version"].split(".", maxsplit=1)[0]
    expected_marketplace = f"{major}.{int(timestamp[:4])}.{int(timestamp[4:])}"
    if (
        evidence["publication_tag"] != expected_tag
        or evidence["marketplace_version"] != expected_marketplace
    ):
        raise RuntimeError(
            f"detailed publication evidence {identifier} disagrees with its source identity"
        )


def _validate_publication_evidence(
    target: dict[str, Any],
    evidence: Any,
    identifier: str,
    delivery_entry: dict[str, str],
    source_receipt: PublicationReceipt,
) -> None:
    """Validate the configured exact evidence shape and generic integrity fields."""
    expected_fields = set(target["detail_publication_fields"])
    if not isinstance(evidence, dict) or set(evidence) != expected_fields:
        raise RuntimeError(f"detailed publication evidence {identifier} has an invalid shape")
    digest_maps: dict[str, dict[str, str]] = {}
    for field, value in evidence.items():
        if field.endswith("sha256"):
            if not isinstance(value, str) or not SHA256.fullmatch(value):
                raise RuntimeError(
                    f"detailed publication evidence {identifier} has invalid {field}"
                )
        elif field in {"assets", "generated"}:
            digest_maps[field] = _validate_digest_map(
                value, f"detailed publication evidence {identifier}.{field}"
            )
    if "commit" in evidence and (
        not isinstance(evidence["commit"], str) or not COMMIT.fullmatch(evidence["commit"])
    ):
        raise RuntimeError(f"detailed publication evidence {identifier} has invalid commit")
    if "version" in evidence:
        version = evidence["version"]
        if (
            not isinstance(version, str)
            or not SEMVER.fullmatch(version)
            or evidence.get("tag") != f"v{version}"
        ):
            raise RuntimeError(f"detailed publication evidence {identifier} has invalid release")
        expected_assets = _configured_names(
            target, "detail_asset_names", version=evidence["version"]
        )
        if set(digest_maps["assets"]) != expected_assets:
            raise RuntimeError(
                f"detailed publication evidence {identifier} has the wrong asset set"
            )
        if evidence["spec_release_sha256"] != _source_pin_sha256(
            source_receipt, delivery_entry, target["detail_spec_pin_indent"]
        ):
            raise RuntimeError(
                f"detailed publication evidence {identifier} is not bound to the source pin"
            )
    if "generated" in evidence and set(digest_maps["generated"]) != _configured_names(
        target, "detail_generated_names"
    ):
        raise RuntimeError(
            f"detailed publication evidence {identifier} has the wrong generated set"
        )
    if "provider" in evidence:
        provider = evidence["provider"]
        if (
            not isinstance(provider, dict)
            or set(provider) != {"commit", "tag"}
            or not isinstance(provider["commit"], str)
            or not COMMIT.fullmatch(provider["commit"])
            or not isinstance(provider["tag"], str)
            or not re.fullmatch(r"v\d+\.\d+\.\d+", provider["tag"])
        ):
            raise RuntimeError(f"detailed publication evidence {identifier} has invalid provider")
    if "npm" in evidence:
        npm = evidence["npm"]
        commit = evidence.get("commit")
        if not isinstance(npm, dict) or not npm:
            raise RuntimeError(f"detailed publication evidence {identifier} has invalid npm data")
        if set(npm) != _configured_names(target, "detail_npm_packages"):
            raise RuntimeError(
                f"detailed publication evidence {identifier} has the wrong npm package set"
            )
        for package in npm.values():
            if (
                not isinstance(package, dict)
                or set(package) != {"git_head", "integrity"}
                or package.get("git_head") != commit
                or not isinstance(package.get("integrity"), str)
                or not re.fullmatch(r"sha512-[A-Za-z0-9+/]+={0,2}", package["integrity"])
            ):
                raise RuntimeError(
                    f"detailed publication evidence {identifier} has invalid npm data"
                )
    if "marketplace_version" in evidence:
        marketplace_version = evidence["marketplace_version"]
        if (
            not isinstance(marketplace_version, str)
            or not SEMVER.fullmatch(marketplace_version)
            or evidence.get("open_vsx_version") != marketplace_version
            or evidence.get("marketplace_sha256") != evidence.get("vsix_sha256")
            or evidence.get("open_vsx_sha256") != evidence.get("vsix_sha256")
        ):
            raise RuntimeError(
                f"detailed publication evidence {identifier} has inconsistent marketplaces"
            )
        epoch = evidence.get("publication_epoch")
        publication_tag = evidence.get("publication_tag")
        vsix_name = evidence.get("vsix_name")
        if (
            not isinstance(epoch, str)
            or not re.fullmatch(r"\d{1,12}", epoch)
            or not isinstance(publication_tag, str)
            or not re.fullmatch(r"v\d+\.\d+\.\d+-\d{12}(?:-BETA)?", publication_tag)
            or not isinstance(vsix_name, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]+\.vsix", vsix_name)
        ):
            raise RuntimeError(
                f"detailed publication evidence {identifier} has invalid marketplace identity"
            )
        _validate_vscode_publication_identity(evidence, delivery_entry, identifier)


def _github_raw(endpoint: str, label: str) -> bytes:
    """Read exact bytes from one authoritative downstream GitHub object."""
    result = _network_run(
        [
            "gh",
            "api",
            "--header",
            "Accept: application/vnd.github.raw+json",
            endpoint,
        ],
        text=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else ""
        raise RuntimeError(f"cannot query {label}: {stderr.strip()}")
    if not isinstance(result.stdout, bytes):
        raise TypeError(f"{label} did not return bytes")
    if len(result.stdout) > MAX_DOWNLOAD_BYTES:
        raise RuntimeError(f"{label} exceeds the download size limit")
    return result.stdout


def _download_bytes(url: str, label: str) -> bytes:
    """Download one HTTPS artifact with hard time and size bounds."""
    if urlsplit(url).scheme != "https":
        raise RuntimeError(f"{label} URL is not HTTPS")
    result = _network_run(
        [
            "curl",
            "--fail",
            "--location",
            "--max-filesize",
            str(MAX_DOWNLOAD_BYTES),
            "--max-time",
            str(NETWORK_TIMEOUT_SECONDS),
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--silent",
            "--show-error",
            url,
        ],
        use_downstream_token=False,
        text=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else ""
        raise RuntimeError(f"cannot download {label}: {stderr.strip()}")
    if not isinstance(result.stdout, bytes) or len(result.stdout) > MAX_DOWNLOAD_BYTES:
        raise RuntimeError(f"{label} exceeds the download size limit")
    return result.stdout


def _http_json(url: str, label: str) -> dict[str, Any]:
    """Download and decode one bounded public HTTPS JSON document."""
    body = _download_bytes(url, label)
    try:
        document = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} response is not valid JSON") from exc
    if not isinstance(document, dict):
        raise TypeError(f"{label} response is not an object")
    return document


def _gunzip_bounded(content: bytes, label: str) -> bytes:
    """Decompress one gzip response without permitting unbounded expansion."""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as stream:
            expanded = stream.read(MAX_DOWNLOAD_BYTES + 1)
    except (EOFError, gzip.BadGzipFile, OSError) as exc:
        raise RuntimeError(f"{label} returned malformed gzip content") from exc
    if len(expanded) > MAX_DOWNLOAD_BYTES:
        raise RuntimeError(f"{label} decompressed bytes exceed the download size limit")
    return expanded


def _authoritative_release(
    repository: str,
    tag: str,
    expected_assets: dict[str, str],
    *,
    commit: str | None,
) -> None:
    """Require one immutable final GitHub release with exact assets and optional tag commit."""
    release = _github_json(
        f"repos/{repository}/releases/tags/{tag}",
        f"authoritative release {repository}@{tag}",
    )
    if (
        release.get("tag_name") != tag
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or release.get("immutable") is not True
    ):
        raise RuntimeError(f"authoritative release {repository}@{tag} is not immutable and final")
    if release_asset_digests(release) != expected_assets:
        raise RuntimeError(
            f"authoritative release {repository}@{tag} assets differ from receiver evidence"
        )
    if commit is not None:
        tagged = _github_json(
            f"repos/{repository}/commits/{tag}",
            f"authoritative tag {repository}@{tag}",
        ).get("sha")
        if tagged != commit:
            raise RuntimeError(f"authoritative release {repository}@{tag} targets another commit")


def _verify_source_pin_and_generated(authority: dict[str, str], evidence: dict[str, Any]) -> None:
    """Bind receipted source and generated hashes to exact downstream tag bytes."""
    repository = authority["repository"]
    commit = evidence["commit"]
    pin = _github_raw(
        f"repos/{repository}/contents/{authority['source_pin_path']}?ref={commit}",
        f"authoritative source pin {repository}@{commit}",
    )
    if hashlib.sha256(pin).hexdigest() != evidence["spec_release_sha256"]:
        raise RuntimeError("authoritative downstream source pin differs from receiver evidence")
    for path, expected in evidence.get("generated", {}).items():
        content = _github_raw(
            f"repos/{repository}/contents/{path}?ref={commit}",
            f"authoritative generated file {repository}@{commit}:{path}",
        )
        if hashlib.sha256(content).hexdigest() != expected:
            raise RuntimeError(
                f"authoritative generated file {path} differs from receiver evidence"
            )


def _verify_npm(authority: dict[str, str], evidence: dict[str, Any]) -> None:
    """Bind each npm receipt to registry metadata and exact tarball bytes."""
    registry = authority["npm_registry"].rstrip("/")
    registry_host = urlsplit(registry).netloc
    for package_name, expected in evidence["npm"].items():
        metadata_url = f"{registry}/{quote(package_name, safe='')}/{evidence['version']}"
        metadata = _http_json(metadata_url, f"npm package {package_name}@{evidence['version']}")
        dist = metadata.get("dist")
        if (
            metadata.get("version") != evidence["version"]
            or metadata.get("gitHead") != expected["git_head"]
            or not isinstance(dist, dict)
            or dist.get("integrity") != expected["integrity"]
            or not isinstance(dist.get("tarball"), str)
        ):
            raise RuntimeError(f"npm authority disagrees for {package_name}@{evidence['version']}")
        tarball_url = dist["tarball"]
        parsed_tarball = urlsplit(tarball_url)
        if parsed_tarball.scheme != "https" or parsed_tarball.netloc != registry_host:
            raise RuntimeError(
                f"npm authority returned an unexpected tarball origin for {package_name}"
            )
        algorithm, encoded = expected["integrity"].split("-", maxsplit=1)
        if algorithm != "sha512":
            raise RuntimeError(f"npm authority returned unsupported integrity for {package_name}")
        actual = base64.b64encode(
            hashlib.sha512(_download_bytes(tarball_url, package_name)).digest()
        )
        if actual.decode() != encoded:
            raise RuntimeError(
                f"npm tarball bytes disagree for {package_name}@{evidence['version']}"
            )


def _verify_marketplaces(authority: dict[str, str], evidence: dict[str, Any]) -> None:
    """Bind VSIX evidence to GitHub, Open VSX, and Visual Studio Marketplace bytes."""
    _authoritative_release(
        authority["repository"],
        evidence["publication_tag"],
        {evidence["vsix_name"]: evidence["vsix_sha256"]},
        commit=None,
    )
    version = evidence["marketplace_version"]
    metadata_url = authority["open_vsx_metadata_url"].format(version=version)
    metadata = _http_json(metadata_url, f"Open VSX metadata {version}")
    files = metadata.get("files")
    download_url = files.get("download") if isinstance(files, dict) else None
    if not isinstance(download_url, str) or not download_url.startswith(
        authority["open_vsx_download_origin"]
    ):
        raise RuntimeError("Open VSX returned an unexpected download origin")
    open_vsx = _download_bytes(download_url, f"Open VSX VSIX {version}")
    if hashlib.sha256(open_vsx).hexdigest() != evidence["open_vsx_sha256"]:
        raise RuntimeError("Open VSX artifact bytes differ from receiver evidence")
    marketplace_url = authority["marketplace_asset_url"].format(version=version)
    marketplace = _download_bytes(marketplace_url, f"Marketplace VSIX {version}")
    if marketplace.startswith(b"\x1f\x8b"):
        marketplace = _gunzip_bounded(marketplace, "Marketplace")
    if hashlib.sha256(marketplace).hexdigest() != evidence["marketplace_sha256"]:
        raise RuntimeError("Marketplace artifact bytes differ from receiver evidence")


def _verify_authoritative_publication(
    target: dict[str, Any], evidence: dict[str, Any], cache: set[str]
) -> None:
    """Require receiver evidence to match independent downstream publication APIs."""
    authority = target["publication_authority"]
    cache_document = {"authority": authority, "evidence": evidence}
    cache_key = hashlib.sha256(
        json.dumps(cache_document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if cache_key in cache:
        return
    kind = authority["kind"]
    if kind in {"github_release", "github_release_npm"}:
        _authoritative_release(
            authority["repository"],
            evidence["tag"],
            evidence["assets"],
            commit=evidence["commit"],
        )
        _verify_source_pin_and_generated(authority, evidence)
        if kind == "github_release_npm":
            provider = evidence["provider"]
            dependency_commit = _github_json(
                f"repos/{authority['dependency_repository']}/commits/{provider['tag']}",
                "authoritative provider dependency tag",
            ).get("sha")
            if dependency_commit != provider["commit"]:
                raise RuntimeError("authoritative provider dependency targets another commit")
            _verify_npm(authority, evidence)
    elif kind == "github_release_marketplaces":
        _verify_marketplaces(authority, evidence)
    elif kind != "receiver_commit_source_pin":
        raise RuntimeError(f"unsupported publication authority: {kind}")
    cache.add(cache_key)


def _validate_map_detail(
    target: dict[str, Any],
    document: Any,
    common: dict[str, dict[str, str]],
    payload: dict[str, str],
    source_receipt: PublicationReceipt,
    receipt_cache: dict[tuple[str, str, str], PublicationReceipt],
) -> dict[str, dict[str, Any]]:
    """Validate one parallel detailed evidence map and cross-bind its entries."""
    collection = target["detail_collection"]
    if (
        not isinstance(document, dict)
        or set(document) != {collection, "version"}
        or document.get("version") != 1
        or not isinstance(document.get(collection), dict)
    ):
        raise RuntimeError(
            f"receiver detailed ledger for {target['owner']}/{target['repo']} is malformed"
        )
    details = document[collection]
    if set(details) != set(common):
        raise RuntimeError("receiver common and detailed ledgers have different delivery keys")
    evidence_by_identifier: dict[str, dict[str, Any]] = {}
    for identifier, raw_detail in details.items():
        if target["detail_kind"] == "receipt_map":
            if not isinstance(raw_detail, dict) or set(raw_detail) != {"delivery", "publication"}:
                raise RuntimeError(f"detailed receipt {identifier} has an invalid shape")
            if raw_detail["delivery"] != common[identifier]:
                raise RuntimeError(
                    f"detailed receipt {identifier} conflicts with the common ledger"
                )
            evidence = raw_detail["publication"]
        else:
            evidence = raw_detail
        delivery_entry = common[identifier]
        entry_receipt = _receipt_for_delivery(
            payload["trigger_source"],
            delivery_entry,
            payload,
            source_receipt,
            receipt_cache,
        )
        _validate_publication_evidence(
            target,
            evidence,
            identifier,
            delivery_entry,
            entry_receipt,
        )
        evidence_by_identifier[identifier] = evidence
        if target["detail_kind"] == "publication_map":
            asset_name = target["source_asset_name"].format(
                release_tag=delivery_entry["release_tag"]
            )
            if evidence.get("bundle_sha256") != entry_receipt.assets.get(asset_name):
                raise RuntimeError(
                    "receiver publication evidence conflicts with the source release bytes"
                )
    return evidence_by_identifier


def _receipt_for_delivery(
    source: str,
    entry: dict[str, str],
    current_payload: dict[str, str],
    current_receipt: PublicationReceipt,
    cache: dict[tuple[str, str, str], PublicationReceipt],
) -> PublicationReceipt:
    """Return the immutable source receipt for one current or historical entry."""
    key = (entry["version"], entry["release_tag"], entry["target_commit"])
    if (
        entry["version"] == current_payload["version"]
        and entry["release_tag"] == current_payload["release_tag"]
        and entry["target_commit"] == current_payload["target_commit"]
    ):
        cache.setdefault(key, current_receipt)
        return current_receipt
    cached = cache.get(key)
    if cached is not None:
        return cached
    release = github_release(source, entry["release_tag"])
    if release is None:
        raise RuntimeError(
            f"cannot verify historical delivery against absent release {entry['release_tag']}"
        )
    receipt = _source_receipt(
        release,
        {
            "release_tag": entry["release_tag"],
            "target_commit": entry["target_commit"],
            "version": entry["version"],
        },
    )
    cache[key] = receipt
    return receipt


def _version_key(entry: dict[str, str]) -> tuple[int, int, int]:
    """Return the numeric semantic-version ordering key for one delivery."""
    return tuple(int(part) for part in entry["version"].split("."))  # type: ignore[return-value]


def _validate_current_pin(
    document: Any | None,
    common: dict[str, dict[str, str]],
    payload: dict[str, str],
    source_receipt: PublicationReceipt,
    receipt_cache: dict[tuple[str, str, str], PublicationReceipt],
) -> None:
    """Validate console's singleton pin against the newest common delivery."""
    if not common:
        if document is not None:
            raise RuntimeError("receiver current pin exists without a common delivery")
        return
    if document is None:
        raise RuntimeError("receiver current pin is absent for a nonempty common ledger")
    if not isinstance(document, dict) or set(document) != {
        "assets",
        "release_tag",
        "target_commit",
        "version",
    }:
        raise RuntimeError("receiver current pin is malformed")
    _, current = max(common.items(), key=lambda item: _version_key(item[1]))
    if {field: document.get(field) for field in current} != current:
        raise RuntimeError("receiver current pin is not bound to the newest delivery")
    assets = _validate_digest_map(document.get("assets"), "receiver current pin assets")
    if set(assets) != expected_asset_names(current["version"]):
        raise RuntimeError("receiver current pin has the wrong source asset set")
    expected_receipt = _receipt_for_delivery(
        payload["trigger_source"],
        current,
        payload,
        source_receipt,
        receipt_cache,
    )
    if assets != expected_receipt.assets:
        raise RuntimeError("receiver current pin conflicts with the source release bytes")


def receiver_has_delivery(
    target: dict[str, Any],
    payload: dict[str, str],
    source_receipt: PublicationReceipt,
    *,
    receipt_cache: dict[tuple[str, str, str], PublicationReceipt] | None = None,
    snapshot_cache: dict[tuple[str, str, str, str, str], bool] | None = None,
    authority_cache: set[str] | None = None,
) -> bool:
    """Verify common and detailed receiver evidence for this exact delivery."""
    receiver_commit = _receiver_ref_commit(target)
    receipt_identity = hashlib.sha256(
        json.dumps(
            {
                "assets": source_receipt.assets,
                "commit": source_receipt.commit,
                "version": source_receipt.version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    snapshot_key = (
        target["owner"],
        target["repo"],
        receiver_commit,
        payload["delivery_id"],
        receipt_identity,
    )
    snapshots = snapshot_cache if snapshot_cache is not None else {}
    cached = snapshots.get(snapshot_key)
    if cached is not None:
        return cached
    ledger = _receiver_document(target, target["receipt_path"], receiver_commit, "delivery ledger")
    if ledger is None:
        snapshots[snapshot_key] = False
        return False
    common = _validated_common_ledger(target, ledger, payload["trigger_source"])
    receipts = receipt_cache if receipt_cache is not None else {}
    authorities = authority_cache if authority_cache is not None else set()
    detail = _receiver_document(
        target, target["detail_path"], receiver_commit, "detailed delivery evidence"
    )
    evidence_by_identifier: dict[str, dict[str, Any]] = {}
    if target["detail_kind"] == "current_pin":
        _validate_current_pin(detail, common, payload, source_receipt, receipts)
    else:
        if detail is None:
            raise RuntimeError(
                f"receiver detailed ledger for {target['owner']}/{target['repo']} is absent"
            )
        evidence_by_identifier = _validate_map_detail(
            target, detail, common, payload, source_receipt, receipts
        )
    present = payload["delivery_id"] in common
    if present and target["detail_kind"] != "current_pin":
        _verify_authoritative_publication(
            target,
            evidence_by_identifier[payload["delivery_id"]],
            authorities,
        )
    snapshots[snapshot_key] = present
    return present


def _source_receipt(release: dict[str, Any], payload: dict[str, str]) -> PublicationReceipt:
    """Require the upstream release's immutable receipt to match the dispatch."""
    tag = payload["release_tag"]
    if bool(release.get("draft")):
        raise RuntimeError(f"source release {tag} is still a draft")
    if bool(release.get("prerelease")):
        raise RuntimeError(f"source release {tag} is unexpectedly a prerelease")
    if release.get("immutable") is not True:
        raise RuntimeError(f"source release {tag} is not immutable")
    if release.get("tag_name") != tag:
        raise RuntimeError("source release API identity differs from the dispatch tag")
    release_url = payload.get("release_url")
    if release_url is not None and release.get("html_url") != release_url:
        raise RuntimeError("source release URL differs from the dispatch URL")
    body = str(release.get("body") or "")
    require_source_provenance(
        body,
        source_provenance_at(payload["target_commit"]),
    )
    receipt = publication_receipt_from_body(body)
    if receipt is None:
        raise RuntimeError("source release has no publication receipt")
    if receipt.version != payload["version"] or receipt.commit != payload["target_commit"]:
        raise RuntimeError("source publication receipt conflicts with the dispatch identity")
    if release_asset_digests(release) != receipt.assets:
        raise RuntimeError("source release API asset digests differ from its publication receipt")
    return receipt


def append_receipt(
    repo: str,
    tag: str,
    marker: str,
    source_provenance: SourceProvenance,
) -> None:
    """Append and verify one delivery receipt without losing existing notes."""
    release = github_release(repo, tag)
    if release is None:
        raise RuntimeError(f"cannot receipt absent release {tag}")
    body = release.get("body") or ""
    require_source_provenance(str(body), source_provenance)
    if marker in body:
        return
    with tempfile.TemporaryDirectory(prefix="downstream-receipt-") as temp:
        notes = Path(temp) / "notes.md"
        notes.write_text(f"{body.rstrip()}\n\n{marker}\n", encoding="utf-8")
        result = _network_run(
            ["gh", "release", "edit", tag, "--repo", repo, "--notes-file", str(notes)],
            use_downstream_token=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"cannot append release receipt: {str(result.stderr).strip()}")
    refreshed = github_release(repo, tag)
    if refreshed is None or marker not in (refreshed.get("body") or ""):
        raise RuntimeError(f"release {tag} did not persist downstream receipt")
    require_source_provenance(str(refreshed.get("body") or ""), source_provenance)


def dispatch_all(
    repo: str,
    targets: list[dict[str, Any]],
    version: str,
    tag: str,
    release_url: str,
    commit: str,
    *,
    poll_attempts: int = 60,
    poll_interval: float = 30,
    verify_only: bool = False,
) -> None:
    """Reconcile delivery or non-mutatingly audit every durable receipt."""
    if not SEMVER.fullmatch(version) or tag != f"v{version}":
        raise ValueError("release tag and semantic version disagree")
    if not COMMIT.fullmatch(commit):
        raise ValueError("target commit must be a full lowercase Git SHA")
    if not verify_only and (
        poll_attempts < 1
        or poll_attempts > MAX_POLL_ATTEMPTS
        or poll_interval < 0
        or poll_interval > MAX_POLL_INTERVAL_SECONDS
        or poll_attempts * poll_interval > MAX_POLL_DURATION_SECONDS
    ):
        raise ValueError("delivery polling configuration is invalid")

    pending: list[tuple[dict[str, Any], dict[str, str], str, PublicationReceipt]] = []
    receipt_cache: dict[tuple[str, str, str], PublicationReceipt] = {}
    snapshot_cache: dict[tuple[str, str, str, str, str], bool] = {}
    authority_cache: set[str] = set()
    release = github_release(repo, tag)
    if release is None:
        raise RuntimeError(f"cannot dispatch absent release {tag}")
    source_receipt = _source_receipt(
        release,
        {
            "release_tag": tag,
            "release_url": release_url,
            "target_commit": commit,
            "version": version,
        },
    )
    source_provenance = source_provenance_at(commit)
    for target in targets:
        identifier = delivery_id(repo, target, version, tag, commit)
        marker = receipt_marker(identifier)
        payload = {
            "delivery_id": identifier,
            "release_tag": tag,
            "release_url": release_url,
            "target_commit": commit,
            "trigger_source": repo,
            "version": version,
        }
        source_receipted = marker in (release.get("body") or "")
        if verify_only and not source_receipted:
            raise RuntimeError(
                f"verify-only target {target['owner']}/{target['repo']} "
                "has no durable source receipt"
            )
        receiver_receipted = receiver_has_delivery(
            target,
            payload,
            source_receipt,
            receipt_cache=receipt_cache,
            snapshot_cache=snapshot_cache,
            authority_cache=authority_cache,
        )
        if verify_only:
            if not receiver_receipted:
                raise RuntimeError(
                    f"verify-only authority audit failed for {target['owner']}/{target['repo']}"
                )
            continue
        if source_receipted:
            if not receiver_receipted:
                raise RuntimeError(
                    f"source receipt exists without receiver completion for "
                    f"{target['owner']}/{target['repo']}"
                )
            continue
        if receiver_receipted:
            append_receipt(repo, tag, marker, source_provenance)
            continue
        post_dispatch(target, payload)
        pending.append((target, payload, marker, source_receipt))

    if verify_only:
        return

    for attempt in range(1, poll_attempts + 1):
        remaining: list[tuple[dict[str, Any], dict[str, str], str, PublicationReceipt]] = []
        for target, payload, marker, source_receipt in pending:
            if receiver_has_delivery(
                target,
                payload,
                source_receipt,
                receipt_cache=receipt_cache,
                snapshot_cache=snapshot_cache,
                authority_cache=authority_cache,
            ):
                append_receipt(repo, tag, marker, source_provenance)
            else:
                remaining.append((target, payload, marker, source_receipt))
        pending = remaining
        if not pending:
            return
        if attempt < poll_attempts:
            time.sleep(poll_interval)

    names = ", ".join(f"{target['owner']}/{target['repo']}" for target, _, _, _ in pending)
    raise RuntimeError(
        f"downstream completion was not receipted after {poll_attempts} checks: {names}"
    )


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--release-url", required=True)
    parser.add_argument("--target-commit", required=True)
    parser.add_argument("--config", type=Path, default=Path("config/downstream_repos.yaml"))
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="audit every durable downstream receipt without dispatching or writing receipts",
    )
    args = parser.parse_args()

    for variable in ("GH_TOKEN", "DOWNSTREAM_DISPATCH_TOKEN"):
        if not os.environ.get(variable):
            raise RuntimeError(f"{variable} is required for downstream delivery")
    dispatch_all(
        repository_name(),
        load_targets(args.config),
        args.version,
        args.tag,
        args.release_url,
        args.target_commit,
        poll_attempts=int(os.environ.get("DELIVERY_POLL_ATTEMPTS", "60")),
        poll_interval=float(os.environ.get("DELIVERY_POLL_INTERVAL", "30")),
        verify_only=args.verify_only,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
