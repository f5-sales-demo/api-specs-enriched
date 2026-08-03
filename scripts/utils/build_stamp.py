# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""A timestamp for values written into committed artifacts.

Enrichers stamp things like ``validatedAt`` into the generated specs. Using
``datetime.now()`` for that makes every rebuild rewrite ~43 files whose content is
otherwise identical, which has two costs and no benefit:

* a real change is invisible in the diff, and a reviewer cannot tell a two-line
  correction from a full regeneration;
* "is the committed tree what the pipeline produces?" becomes unanswerable, so
  nothing can check it — which is how a stale seed came to silently revert the
  upstream misspelled-``persistence`` rename the F5 server depends on
  (``api-specs#686``), and how ``index.json`` came to report 2.1.200 while
  ``namespace_profiles.json`` reported 2.1.199.

A wall-clock stamp also carries no information. Regenerated on every run, it records
when the pipeline last ran, not when anything was validated.

So the stamp is derived from the INPUT instead: the upstream download's own timestamp
from ``specs/original/manifest.json``. It changes when the specs change and not
otherwise, which is what the field claims to mean, and it makes a rebuild from an
unchanged seed byte-identical.

See issue #1152.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from scripts.utils.raw_manifest import RawManifestError, validate_raw_manifest

logger = logging.getLogger(__name__)

_MANIFEST = Path("specs/original/manifest.json")


@lru_cache(maxsize=1)
def artifact_timestamp() -> str:
    """ISO-8601 timestamp for a value written into a committed artifact.

    Derived from the upstream seed so the same seed always yields the same output.
    A missing, malformed, or incomplete manifest fails closed because there is no
    reproducible timestamp in that state.
    """
    remedy = "Run `make download-force` to refresh specs/original/."

    if not _MANIFEST.exists():
        raise RuntimeError(f"No {_MANIFEST} — artifacts cannot be stamped reproducibly. {remedy}")

    try:
        manifest = json.loads(_MANIFEST.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{_MANIFEST} is unreadable ({exc}). {remedy}") from exc

    try:
        contract = validate_raw_manifest(manifest, source_dir=_MANIFEST.parent)
    except RawManifestError as exc:
        raise RuntimeError(
            f"{_MANIFEST} violates the source provenance contract ({exc}). {remedy}"
        ) from exc
    published = contract.release_receipt["published_at"]
    return datetime.fromisoformat(published).astimezone(UTC).isoformat()


def reset_cache() -> None:
    """Clear the memoised stamp. For tests only."""
    artifact_timestamp.cache_clear()
