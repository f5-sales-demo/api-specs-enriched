# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Bind an enriched release to its exact committed upstream source receipt."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from scripts.utils.github_release import validate_release_receipt

SOURCE_PROVENANCE_PATH = Path(".github_release")
SOURCE_PROVENANCE_PREFIX = "<!-- api-specs-source-provenance:"
SOURCE_PROVENANCE_SUFFIX = " -->"

SourceProvenance = dict[str, Any]


def _validated(document: Any) -> SourceProvenance:
    """Return one canonical six-field upstream receipt."""
    try:
        return validate_release_receipt(document)
    except ValueError as exc:
        raise RuntimeError(f"source provenance receipt is invalid: {exc}") from exc


def source_provenance_marker(receipt: SourceProvenance) -> str:
    """Serialize one exact upstream receipt as a deterministic hidden line."""
    encoded = json.dumps(_validated(receipt), sort_keys=True, separators=(",", ":"))
    return f"{SOURCE_PROVENANCE_PREFIX}{encoded}{SOURCE_PROVENANCE_SUFFIX}"


def source_provenance_from_body(body: str) -> SourceProvenance | None:
    """Parse the sole canonical source-provenance marker from a release body."""
    candidates = [line.strip() for line in body.splitlines() if SOURCE_PROVENANCE_PREFIX in line]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise RuntimeError("release body contains multiple source provenance markers")

    marker = candidates[0]
    if not marker.startswith(SOURCE_PROVENANCE_PREFIX) or not marker.endswith(
        SOURCE_PROVENANCE_SUFFIX
    ):
        raise RuntimeError("source provenance marker is malformed")
    payload = marker[len(SOURCE_PROVENANCE_PREFIX) : -len(SOURCE_PROVENANCE_SUFFIX)]
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("source provenance marker is not valid JSON") from exc
    receipt = _validated(document)
    if marker != source_provenance_marker(receipt):
        raise RuntimeError("source provenance marker is not canonical")
    return receipt


def source_provenance_at(commit: str) -> SourceProvenance:
    """Read the exact upstream receipt committed at *commit*."""
    object_name = f"{commit}:{SOURCE_PROVENANCE_PATH.as_posix()}"
    result = subprocess.run(
        ["git", "show", object_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cannot read committed source provenance {object_name}: {result.stderr.strip()}"
        )
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"committed source provenance {object_name} is not valid JSON") from exc
    return _validated(document)


def require_source_provenance(body: str, expected: SourceProvenance) -> SourceProvenance:
    """Require one marker that exactly matches the authoritative committed receipt."""
    authoritative = _validated(expected)
    actual = source_provenance_from_body(body)
    if actual is None:
        raise RuntimeError("release has no source provenance marker")
    if actual != authoritative:
        mismatches = sorted(key for key in authoritative if actual[key] != authoritative[key])
        raise RuntimeError(
            f"release source provenance differs from the release commit: {', '.join(mismatches)}"
        )
    return actual


def ensure_source_provenance(body: str, expected: SourceProvenance) -> str:
    """Append a missing authoritative marker or preserve the exact existing marker."""
    authoritative = _validated(expected)
    actual = source_provenance_from_body(body)
    if actual is not None:
        require_source_provenance(body, authoritative)
        return body
    prefix = body.rstrip()
    separator = "\n\n" if prefix else ""
    return f"{prefix}{separator}{source_provenance_marker(authoritative)}\n"
