# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Prevent unmeasured optimistic-concurrency claims from entering publication."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_resource_version_publication_requires_committed_evidence_inventory() -> None:
    """A concurrency enricher is forbidden until its exact wire evidence is committed."""
    publishers = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "scripts").rglob("*.py")
        if "resource_version" in path.read_text(encoding="utf-8")
    )
    evidence_inventory = ROOT / "config/resource_version_evidence.yaml"

    assert not publishers or evidence_inventory.is_file(), (
        "resource_version publication requires config/resource_version_evidence.yaml "
        f"with exact operation-bound live evidence; publishers={publishers}"
    )
