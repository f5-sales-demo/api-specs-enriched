"""Parity test between docs/en/extensions/catalog.md and extension_constants.py.

Prevents drift between:
  1. Every extension name declared in VALID_X_F5XC_EXTENSIONS / PRESERVED_NATIVE_EXTENSIONS
     has a `### <name>` section in the catalog.
  2. No dead catalog entries (every header maps back to a known constant).
  3. Every "Injected here" catalog entry is actually emitted at least once in
     docs/specifications/api/ (skipped when the enriched output has not been
     built yet).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.utils.extension_constants import (
    PRESERVED_NATIVE_EXTENSIONS,
    VALID_X_F5XC_EXTENSIONS,
)

REPO_ROOT = Path(__file__).parent.parent
CATALOG = REPO_ROOT / "docs" / "en" / "extensions" / "catalog.md"
ENRICHED_DIR = REPO_ROOT / "docs" / "specifications" / "api"


def _catalog_entries() -> set[str]:
    """Extract every `### <x-name>` header from catalog.md."""
    text = CATALOG.read_text()
    return set(re.findall(r"^### (x-[a-z0-9-]+)\s*$", text, re.MULTILINE))


def _walk_x_keys(obj, acc: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.startswith("x-"):
                acc.add(k)
            _walk_x_keys(v, acc)
    elif isinstance(obj, list):
        for item in obj:
            _walk_x_keys(item, acc)


def _enriched_x_keys() -> set[str]:
    keys: set[str] = set()
    for p in ENRICHED_DIR.glob("*.json"):
        if p.name in {
            "index.json",
            "namespace_profiles.json",
            "resource_coverage.json",
            "validation.json",
        }:
            continue
        _walk_x_keys(json.loads(p.read_text()), keys)
    return keys


@pytest.fixture(scope="module")
def catalog() -> set[str]:
    return _catalog_entries()


def test_catalog_file_exists():
    assert CATALOG.exists(), f"Catalog not found at {CATALOG}"


def test_every_valid_extension_has_a_catalog_entry(catalog):
    missing = VALID_X_F5XC_EXTENSIONS - catalog
    assert not missing, f"Missing catalog entries: {sorted(missing)}"


def test_every_preserved_native_has_a_catalog_entry(catalog):
    missing = PRESERVED_NATIVE_EXTENSIONS - catalog
    assert not missing, f"Missing upstream pass-through entries: {sorted(missing)}"


def test_no_dead_catalog_entries(catalog):
    known = VALID_X_F5XC_EXTENSIONS | PRESERVED_NATIVE_EXTENSIONS
    extra = {c for c in catalog if c not in known}
    # Allow x-ves-oneof-field-* pattern entries (F5 native OneOf field extensions).
    extra = {c for c in extra if not c.startswith("x-ves-oneof-field-")}
    assert not extra, f"Catalog entries not in extension_constants.py: {sorted(extra)}"


def test_every_emitted_x_f5xc_key_has_a_catalog_entry(catalog):
    """Anti-drift invariant: every ``x-f5xc-*`` key that actually appears
    in the enriched output must be documented in the catalog AND live in
    ``VALID_X_F5XC_EXTENSIONS``.

    This is the REVERSE direction of the aspirational
    ``test_every_valid_extension_is_emitted`` below and is the more urgent
    guard: it catches a new enricher that starts emitting a key without
    documenting it.
    """
    actual = {k for k in _enriched_x_keys() if k.startswith("x-f5xc-")}
    undocumented = actual - VALID_X_F5XC_EXTENSIONS
    assert not undocumented, (
        f"Enriched output emits x-f5xc-* keys that are not declared in "
        f"VALID_X_F5XC_EXTENSIONS (and therefore have no catalog entry): "
        f"{sorted(undocumented)}. Either declare them in extension_constants "
        f"+ catalog.md or stop emitting them."
    )


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Aspirational invariant: 35 of 54 declared VALID_X_F5XC_EXTENSIONS "
        "are catalog/constants-declared but not yet emitted by any enricher "
        "in the current pipeline run. Xfail until the enrichers catch up "
        "with the declared namespace. Remove this marker when the declared "
        "set matches what the pipeline actually emits."
    ),
)
def test_every_valid_extension_is_emitted(catalog):
    """Aspirational: every declared VALID_X_F5XC_EXTENSIONS entry should
    be emitted at least once in the enriched output. Today many are
    constants-only (pre-wired for future enrichers)."""
    actual = _enriched_x_keys()
    missing = VALID_X_F5XC_EXTENSIONS - actual
    assert not missing, f"Declared-but-not-emitted extensions: {sorted(missing)}"
