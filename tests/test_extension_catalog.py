"""Parity tests for the authoritative extension registry and runtime constants.

Prevents drift between:
  1. The authoritative registry and runtime validation constants.
  2. The authoritative preserved-native registry and runtime constants.

Generated-artifact parity is a separate test executed against the isolated
contract-diff candidate in ``tests/candidate_extension_registry.py``. It never
falls back to stale committed output.
"""

from __future__ import annotations

import pytest

from scripts.utils.extension_constants import (
    PRESERVED_NATIVE_EXTENSIONS,
    VALID_X_F5XC_EXTENSIONS,
)
from scripts.utils.extension_registry import (
    declared_extensions,
    load_extension_registry,
    preserved_native_extensions,
)


@pytest.fixture(scope="module")
def registry() -> set[str]:
    return set(declared_extensions(load_extension_registry()))


def test_runtime_extensions_exactly_match_registry(registry):
    assert set(VALID_X_F5XC_EXTENSIONS) == registry


def test_preserved_native_extensions_exactly_match_registry():
    assert preserved_native_extensions() == PRESERVED_NATIVE_EXTENSIONS
