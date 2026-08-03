# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Transactional publication helpers for deterministic specification batches."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp

from scripts.utils.raw_manifest import create_raw_manifest, validate_raw_manifest
from scripts.utils.source_graph_validator import SpecSelection, select_source_specs


class SpecBatchRollbackError(RuntimeError):
    """Raised when publication fails and the previous output cannot be restored."""

    def __init__(self, message: str, recovery_dir: Path) -> None:
        """Record the durable directory containing the previous output bytes."""
        super().__init__(message)
        self.recovery_dir = recovery_dir


def _replace_path(source: Path, destination: Path) -> None:
    source.replace(destination)


def _remove_path(path: Path) -> str | None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        return f"remove {path}: {error}"
    return None


def _restore_path(backup_path: Path, output_path: Path) -> str | None:
    try:
        # Keep the backup bytes intact until every rollback operation succeeds.
        # A move would consume successful restores and leave an incomplete recovery
        # set if a later restore failed.
        shutil.copy2(backup_path, output_path)
    except OSError as error:
        return f"restore {output_path}: {error}"
    return None


def publish_spec_batch(
    staging_dir: Path,
    output_dir: Path,
    selection: SpecSelection,
) -> None:
    """Replace the complete output spec set, rolling back any publication error."""
    expected = selection.names
    contract_path = staging_dir / selection.contract_name
    if selection.contract_name == "manifest.json":
        upstream = validate_raw_manifest(json.loads(selection.contract_bytes))
        derived = create_raw_manifest(
            release_receipt=upstream.release_receipt,
            source_dir=staging_dir,
            files=list(expected),
        )
        contract_path.write_text(json.dumps(derived.as_document(), indent=2) + "\n")
    else:
        contract_path.write_bytes(selection.contract_bytes)
    staged_selection = select_source_specs(staging_dir)
    if staged_selection.names != expected:
        raise ValueError(
            "staged specification set does not match the selected input contract: "
            f"expected {expected!r}, got {staged_selection.names!r}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_json = sorted(output_dir.glob("*.json"))
    existing_selection = select_source_specs(output_dir) if output_json else None
    existing = list(existing_selection.files) if existing_selection else []
    if existing_selection is not None:
        existing.append(output_dir / existing_selection.contract_name)
    output_parent = output_dir.parent
    backed_up: list[tuple[Path, Path]] = []
    published: list[Path] = []

    backup_dir = Path(mkdtemp(prefix=f".{output_dir.name}-backup-", dir=output_parent)).resolve()
    try:
        for output_path in existing:
            backup_path = backup_dir / output_path.name
            _replace_path(output_path, backup_path)
            backed_up.append((backup_path, output_path))

        for name in expected:
            output_path = output_dir / name
            _replace_path(staging_dir / name, output_path)
            published.append(output_path)
        contract_output = output_dir / selection.contract_name
        _replace_path(staging_dir / selection.contract_name, contract_output)
        published.append(contract_output)
    except BaseException as publication_error:
        rollback_errors = [
            rollback_error
            for output_path in reversed(published)
            if (rollback_error := _remove_path(output_path)) is not None
        ]
        rollback_errors.extend(
            rollback_error
            for backup_path, output_path in reversed(backed_up)
            if (rollback_error := _restore_path(backup_path, output_path)) is not None
        )
        if rollback_errors:
            details = "; ".join(rollback_errors)
            message = (
                "specification publication failed and rollback was incomplete: "
                f"{details}. Previous output bytes are preserved in {backup_dir}. "
                "After resolving the rollback error, copy every file from that "
                f"directory into {output_dir.resolve()} before retrying publication."
            )
            raise SpecBatchRollbackError(message, backup_dir) from publication_error
        shutil.rmtree(backup_dir)
        raise
    shutil.rmtree(backup_dir)
