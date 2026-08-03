# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Read configuration shipped inside the installed Python package."""

from __future__ import annotations

from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

import yaml


def _config_resource(filename: str) -> Any:
    """Return one validated top-level resource from the ``config`` package."""
    if Path(filename).name != filename or not filename.endswith((".yaml", ".json")):
        raise ValueError(f"invalid packaged configuration filename: {filename!r}")
    resource = files("config").joinpath(filename)
    if not resource.is_file():
        raise FileNotFoundError(f"packaged configuration not found: {filename}")
    return resource


def load_packaged_yaml(filename: str) -> dict[str, Any]:
    """Load one packaged YAML mapping without depending on the caller's CWD."""
    resource = _config_resource(filename)
    with resource.open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise TypeError(f"packaged configuration must be a mapping: {filename}")
    return document


@contextmanager
def packaged_config_path(filename: str) -> Iterator[Path]:
    """Materialize one packaged resource for an API that requires a file path."""
    with as_file(_config_resource(filename)) as path:
        yield path
