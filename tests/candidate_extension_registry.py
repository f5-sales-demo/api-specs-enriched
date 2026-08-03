# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Production-candidate extension contract.

This file is intentionally executed only by the contract-diff job, after that
job builds its isolated release candidate. It has no committed-output fallback.
"""

from __future__ import annotations

import os
from pathlib import Path

from scripts.utils.extension_registry import assert_candidate_registry_parity


def test_generated_candidate_exactly_matches_extension_registry() -> None:
    candidate = os.environ.get("API_SPECS_ENRICHED_DIR")
    assert candidate, "API_SPECS_ENRICHED_DIR must identify the generated contract-diff candidate"

    assert_candidate_registry_parity(Path(candidate))
