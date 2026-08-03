# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Strict access to the published OpenAPI extension registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

REGISTRY_SECTIONS = (
    "spec_level",
    "schema_level",
    "property_level",
    "operation_level",
    "index_level",
)
DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "extension_registry.yaml"
_ALLOWED_TOP_LEVEL = frozenset(
    {"version", "extension_prefix", "preserved_native", *REGISTRY_SECTIONS}
)


class ExtensionRegistryError(ValueError):
    """The registry or a generated candidate violates its extension contract."""


def load_extension_registry(path: Path = DEFAULT_REGISTRY_PATH) -> dict[str, Any]:
    """Load the registry and reject ambiguous or historical side channels."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ExtensionRegistryError(f"cannot load extension registry {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ExtensionRegistryError("extension registry must be a mapping")

    unexpected = set(document) - _ALLOWED_TOP_LEVEL
    missing = _ALLOWED_TOP_LEVEL - set(document)
    if unexpected or missing:
        raise ExtensionRegistryError(
            f"extension registry top-level mismatch: missing={sorted(missing)}, "
            f"unexpected={sorted(unexpected)}"
        )
    prefix = document["extension_prefix"]
    if prefix != "x-f5xc-":
        raise ExtensionRegistryError("extension_prefix must be exactly 'x-f5xc-'")

    for section_name in REGISTRY_SECTIONS:
        section = document[section_name]
        if not isinstance(section, dict):
            raise ExtensionRegistryError(f"{section_name} must be a mapping")
        for extension, metadata in section.items():
            if not isinstance(extension, str) or not extension.startswith(prefix):
                raise ExtensionRegistryError(
                    f"{section_name} contains invalid extension name {extension!r}"
                )
            if not isinstance(metadata, dict) or not metadata:
                raise ExtensionRegistryError(f"{section_name}.{extension} must contain metadata")

    preserved = document["preserved_native"]
    if not isinstance(preserved, dict) or set(preserved) != {"description", "fields"}:
        raise ExtensionRegistryError("preserved_native must contain exactly description and fields")
    fields = preserved["fields"]
    if not isinstance(fields, list) or not fields or any(not isinstance(v, str) for v in fields):
        raise ExtensionRegistryError("preserved_native.fields must be a non-empty string list")
    if len(fields) != len(set(fields)):
        raise ExtensionRegistryError("preserved_native.fields contains duplicates")
    return document


def declared_extensions(registry: dict[str, Any] | None = None) -> frozenset[str]:
    """Return the exact unique extension names in the authoritative registry."""
    source = registry or load_extension_registry()
    return frozenset(
        extension for section_name in REGISTRY_SECTIONS for extension in source[section_name]
    )


def preserved_native_extensions(
    registry: dict[str, Any] | None = None,
) -> frozenset[str]:
    """Return upstream extension names that the pipeline preserves unchanged."""
    source = registry or load_extension_registry()
    return frozenset(source["preserved_native"]["fields"])


def collect_candidate_extensions(candidate_dir: Path) -> frozenset[str]:
    """Collect every ``x-f5xc-*`` key from a generated release candidate."""
    files = sorted(candidate_dir.glob("*.json"))
    if not files:
        raise ExtensionRegistryError(
            f"generated candidate contains no JSON specifications: {candidate_dir}"
        )
    extensions: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str) and key.startswith("x-f5xc-"):
                    extensions.add(key)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExtensionRegistryError(f"cannot read generated candidate {path}: {exc}") from exc
        walk(document)
    return frozenset(extensions)


def assert_candidate_registry_parity(
    candidate_dir: Path,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> None:
    """Require the generated contract and registry to name the exact same set."""
    expected = declared_extensions(load_extension_registry(registry_path))
    actual = collect_candidate_extensions(candidate_dir)
    if expected != actual:
        raise ExtensionRegistryError(
            "generated candidate extension mismatch: "
            f"undeclared={sorted(actual - expected)}, "
            f"unemitted={sorted(expected - actual)}"
        )
