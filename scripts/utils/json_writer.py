"""Biome-compliant JSON writer for generator output.

Super-Linter's BIOME_FORMAT check runs on every `pull_request` to main,
and the release pipeline force-adds auto-generated JSON under
`docs/specifications/api/` and `docs/api-reference/` into its commits.
Generator output must therefore already match what Biome would produce,
otherwise Super-Linter fails and the release PR cannot auto-merge.

This helper is the single source of truth for writing such files. It
fails loudly if Biome is missing (or fails) so the pipeline never
silently emits non-compliant output.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING, Any

from scripts.stamp_release_version import carries_build_version

if TYPE_CHECKING:
    from pathlib import Path

_SKIP_BIOME_ENV = "API_SPECS_SKIP_BIOME"

# Paths that Super-Linter scans with BIOME_FORMAT / CHECKOV on the release PR.
# Only writes landing inside one of these prefixes are required to match what
# Biome would produce. Writes to other destinations (reports, /tmp, ad-hoc
# ``--output-dir`` / ``--output`` CLI targets) are left untouched so callers
# without Biome on PATH still work.
_PUBLISHING_PATH_MARKERS: tuple[str, ...] = (
    "/docs/specifications/api/",
    "/docs/api-reference/",
)
_ATOMIC_PIPELINE_CANDIDATE_PREFIX = ".api-candidate-"


class BiomeNotFoundError(RuntimeError):
    """Raised when biome is not on PATH while writing generator output."""


class BiomeFormatError(RuntimeError):
    """Raised when `biome format --write` exits non-zero."""


def _is_maxsize_only(result: subprocess.CompletedProcess[str]) -> bool:
    """True iff biome's only complaint is the oversize warning.

    When `biome format --write <path>` is passed a single file that
    exceeds `files.maxSize` in biome.json, biome emits two messages
    on stderr: the "exceeds the configured maximum" size warning and
    a "No files were processed in the specified paths" line. The
    latter flips the exit code to non-zero. In Super-Linter's
    multi-file mode biome processes the other files and the same
    oversize is just a warning. Treating the single-file oversize
    case as non-fatal here mirrors Super-Linter's behaviour.
    """
    stderr = result.stderr
    stdout = result.stdout
    size_warning = "exceeds the configured maximum" in stderr
    no_files_processed = "No files were processed" in stderr
    no_other_diff = "Formatter would have printed" not in stdout
    return size_warning and no_files_processed and no_other_diff


def _is_publishing_path(output_path: Path) -> bool:
    """True iff the output lands in a Super-Linter-scanned docs path.

    Super-Linter's BIOME_FORMAT and CHECKOV sub-checks run on JSON
    under ``docs/specifications/api/`` and ``docs/api-reference/``.
    Only those destinations are required to be Biome-compliant at
    write time; ad-hoc CLI outputs (``--output /tmp/...`` on
    ``validation_exporter``, ``--output-dir`` on enrich/normalize)
    must still succeed in environments that do not carry Biome.
    """
    resolved = output_path.resolve().as_posix()
    return any(marker in resolved for marker in _PUBLISHING_PATH_MARKERS)


def _is_atomic_pipeline_candidate(output_path: Path) -> bool:
    """Return whether *output_path* is inside the pipeline's staging tree.

    The candidate deliberately contains a nested ``docs/specifications/api``
    suffix, but its temporary prefix means Biome does not apply the repository
    override that disables formatting for generated specifications. Formatting
    there would therefore produce different bytes from the final published path.
    """
    return any(
        part.startswith(_ATOMIC_PIPELINE_CANDIDATE_PREFIX) for part in output_path.resolve().parts
    )


def _is_formatter_disabled(result: subprocess.CompletedProcess[str]) -> bool:
    """True iff biome skipped the file because formatter is disabled for its path."""
    stderr = result.stderr
    no_files = "No files were processed" in stderr
    not_oversize = "exceeds the configured maximum" not in stderr
    return no_files and not_oversize


def _format_with_biome(output_path: Path, *, release_versioned: bool = False) -> None:
    if not _is_publishing_path(output_path):
        # Writes outside the release-committed docs tree do not need
        # Biome formatting and must not fail when Biome is missing.
        return

    if _is_atomic_pipeline_candidate(output_path) and release_versioned:
        # A release stamp rewrites build-versioned artifacts with json.dumps
        # after the candidate is published. Match those final bytes now instead
        # of letting the staging prefix bypass the final path's disabled rule.
        return

    if os.environ.get(_SKIP_BIOME_ENV):
        # Opt-out for tests / local dev envs that don't carry Biome.
        # Not intended for CI — the workflow installs a pinned Biome.
        print(
            f"[json_writer] {_SKIP_BIOME_ENV}=1 set; skipping biome format for {output_path}",
            file=sys.stderr,
        )
        return

    if shutil.which("biome") is None:
        msg = (
            f"biome not found on PATH while writing {output_path}. "
            "Install it via `npm install -g @biomejs/biome` (the release "
            "workflow pins a specific version — see sync-and-enrich.yml). "
            f"Set {_SKIP_BIOME_ENV}=1 to bypass only for local/test runs "
            "that will not commit the output."
        )
        raise BiomeNotFoundError(msg)

    result = subprocess.run(
        ["biome", "format", "--write", str(output_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return

    if _is_maxsize_only(result):
        # Super-Linter will emit the same warning in multi-file mode and
        # pass the file through unformatted. Matching that behaviour here
        # keeps the generator output consistent with what Super-Linter
        # accepts on the release PR.
        print(
            f"[json_writer] biome skipped {output_path} (exceeds files.maxSize in biome.json); "
            "Super-Linter will treat this as a warning, not an error.",
            file=sys.stderr,
        )
        return

    if _is_formatter_disabled(result):
        return

    msg = (
        f"biome format failed for {output_path} (exit {result.returncode}):\n"
        f"stdout: {result.stdout.strip()}\n"
        f"stderr: {result.stderr.strip()}"
    )
    raise BiomeFormatError(msg)


def write_json_file(
    data: Any,
    output_path: Path,
    *,
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
) -> None:
    """Serialise `data` to `output_path` and Biome-format the result.

    Biome formatting is applied only when the resolved path lands
    inside the Super-Linter-scanned docs tree
    (``docs/specifications/api/**`` or ``docs/api-reference/**``).
    Writes to other destinations — reports, cache, release artifacts,
    ad-hoc ``--output`` / ``--output-dir`` CLI targets — are serialised
    plainly, so callers without Biome on PATH still work.

    Raises:
        BiomeNotFoundError: biome is not on PATH and the skip-env is
            unset, and the output path is in the publishing tree.
        BiomeFormatError: biome format returned a non-zero exit code.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii)
        f.write("\n")

    _format_with_biome(
        output_path,
        release_versioned=carries_build_version(data),
    )
