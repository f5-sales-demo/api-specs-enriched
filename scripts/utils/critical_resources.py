# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Strict critical-resource contract loading for release indexes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class CriticalResourcesConfigError(ValueError):
    """The critical-resource contract is missing or ambiguous."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def load_critical_resources(config_path: Path | None = None) -> list[str]:
    """Load the single required, exact critical-resource configuration."""
    path = config_path or Path("config/critical_resources.yaml")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CriticalResourcesConfigError(
            f"critical resources configuration {path} is unreadable: {exc}"
        ) from exc
    loader = _UniqueKeyLoader(text)
    try:
        document = loader.get_single_data()
    except yaml.YAMLError as exc:
        raise CriticalResourcesConfigError(
            f"critical resources configuration {path} is unreadable: {exc}"
        ) from exc
    finally:
        loader.dispose()
    if not isinstance(document, dict) or set(document) != {
        "version",
        "description",
        "resources",
    }:
        raise CriticalResourcesConfigError(
            "critical resources configuration must contain exactly "
            "description, resources, and version"
        )
    for field in ("version", "description"):
        if not isinstance(document[field], str) or not document[field].strip():
            raise CriticalResourcesConfigError(f"critical resources {field} must be non-empty")
    resources = document["resources"]
    if (
        not isinstance(resources, list)
        or not resources
        or any(not isinstance(value, str) or not value.strip() for value in resources)
    ):
        raise CriticalResourcesConfigError(
            "critical resources must be a non-empty array of non-empty strings"
        )
    duplicates = sorted(value for value in set(resources) if resources.count(value) > 1)
    if duplicates:
        raise CriticalResourcesConfigError(
            f"critical resources contains duplicate entries: {duplicates}"
        )
    return list(resources)
