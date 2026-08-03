# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Exact provenance contract for the extracted corrected API specifications."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.utils.github_release import validate_release_receipt

RAW_MANIFEST_KEYS = frozenset({"release_receipt", "files"})
RAW_FILE_KEYS = frozenset({"name", "sha256"})
_DIGEST_PREFIX = "sha256:"


class RawManifestError(ValueError):
    """A raw source manifest does not satisfy the exact provenance contract."""


@dataclass(frozen=True)
class RawFile:
    """One extracted source file and its immutable byte identity."""

    name: str
    sha256: str

    def as_document(self) -> dict[str, str]:
        """Return the canonical serialized representation."""
        return {"name": self.name, "sha256": self.sha256}


@dataclass(frozen=True)
class RawManifest:
    """Validated upstream receipt and exact extracted-file byte identities."""

    release_receipt: dict[str, Any]
    entries: tuple[RawFile, ...]

    @property
    def files(self) -> tuple[str, ...]:
        """Return the exact canonical source membership."""
        return tuple(entry.name for entry in self.entries)

    def as_document(self) -> dict[str, object]:
        """Return the deterministic serialized representation."""
        return {
            "release_receipt": dict(self.release_receipt),
            "files": [entry.as_document() for entry in self.entries],
        }


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return f"{_DIGEST_PREFIX}{hasher.hexdigest()}"


def _validate_name(value: Any, position: int) -> str:
    if (
        not isinstance(value, str)
        or not value.endswith(".json")
        or value in {"manifest.json", "index.json"}
        or Path(value).name != value
    ):
        raise RawManifestError(f"manifest.json files[{position}].name is not a safe JSON filename")
    return value


def _validate_digest(value: Any, position: int) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(_DIGEST_PREFIX)
        or len(value) != len(_DIGEST_PREFIX) + 64
        or any(character not in "0123456789abcdef" for character in value[len(_DIGEST_PREFIX) :])
    ):
        raise RawManifestError(f"manifest.json files[{position}].sha256 is not a SHA-256 digest")
    return value


def validate_raw_manifest(document: Any, source_dir: Path | None = None) -> RawManifest:
    """Validate exact receipt, membership, hashes, and optionally the source bytes."""
    if not isinstance(document, dict) or set(document) != RAW_MANIFEST_KEYS:
        raise RawManifestError(f"manifest.json must contain exactly {sorted(RAW_MANIFEST_KEYS)}")
    try:
        receipt = validate_release_receipt(document["release_receipt"])
    except ValueError as exc:
        raise RawManifestError(f"manifest.json release_receipt is invalid: {exc}") from exc

    raw_entries = document["files"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise RawManifestError("manifest.json files must be a non-empty array")
    entries: list[RawFile] = []
    for position, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict) or set(raw_entry) != RAW_FILE_KEYS:
            raise RawManifestError(
                f"manifest.json files[{position}] must contain exactly {sorted(RAW_FILE_KEYS)}"
            )
        entries.append(
            RawFile(
                name=_validate_name(raw_entry["name"], position),
                sha256=_validate_digest(raw_entry["sha256"], position),
            )
        )

    names = [entry.name for entry in entries]
    if names != sorted(names):
        raise RawManifestError("manifest.json files must be sorted by name")
    duplicates = sorted(name for name in set(names) if names.count(name) > 1)
    if duplicates:
        raise RawManifestError(f"manifest.json files contains duplicate filenames: {duplicates}")

    if source_dir is not None:
        for entry in entries:
            path = source_dir / entry.name
            if path.is_symlink() or not path.is_file():
                raise RawManifestError(
                    f"manifest.json source file is missing or non-regular: {entry.name}"
                )
            actual = _file_digest(path)
            if actual != entry.sha256:
                raise RawManifestError(f"manifest.json SHA-256 mismatch for {entry.name}")

    return RawManifest(release_receipt=receipt, entries=tuple(entries))


def create_raw_manifest(
    *,
    release_receipt: dict[str, Any],
    source_dir: Path,
    files: list[str],
) -> RawManifest:
    """Build and validate one canonical byte-level source manifest."""
    names = sorted(files)
    document = {
        "release_receipt": release_receipt,
        "files": [{"name": name, "sha256": _file_digest(source_dir / name)} for name in names],
    }
    return validate_raw_manifest(document, source_dir=source_dir)
