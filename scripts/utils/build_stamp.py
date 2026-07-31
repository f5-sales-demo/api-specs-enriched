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
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_MANIFEST = Path("specs/original/manifest.json")


@lru_cache(maxsize=1)
def artifact_timestamp() -> str:
    """ISO-8601 timestamp for a value written into a committed artifact.

    Derived from the upstream seed so the same seed always yields the same output.
    Falls back to the current time only when the seed carries no manifest, which
    means the specs were not downloaded through ``scripts.download``; that case is
    logged, because it silently reintroduces per-run churn.
    """
    remedy = "Run `make download-force` to refresh specs/original/."

    if not _MANIFEST.exists():
        raise RuntimeError(f"No {_MANIFEST} — artifacts cannot be stamped reproducibly. {remedy}")

    try:
        manifest = json.loads(_MANIFEST.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{_MANIFEST} is unreadable ({exc}). {remedy}") from exc

    published = manifest.get("release_published_at")
    if not published:
        raise RuntimeError(
            f"{_MANIFEST} has no release_published_at. Its `timestamp` field records when "
            f"THIS machine downloaded, so deriving the stamp from it makes every generated "
            f"spec differ between a local rebuild and CI's. {remedy}",
        )

    try:
        # Normalise so the value is stable regardless of how the manifest spells its
        # timezone.
        parsed = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"{_MANIFEST} has an unparseable release_published_at ({exc}). {remedy}"
        ) from exc

    return parsed.astimezone(timezone.utc).isoformat()


def reset_cache() -> None:
    """Clear the memoised stamp. For tests only."""
    artifact_timestamp.cache_clear()
