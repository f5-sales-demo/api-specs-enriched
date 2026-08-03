# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Contracts for the documented correction/enrichment/consumer boundary."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_defines_the_repository_supply_chain() -> None:
    readme = (ROOT / "README.md").read_text()

    assert "`api-specs` release" in readme
    assert "canonical enriched specification bundle" in readme
    assert "The specification leads provider implementation" in readme


def test_validation_consumers_are_not_sent_to_mutable_publications() -> None:
    documentation = (ROOT / "docs/en/validation-spec.mdx").read_text()

    assert "releases/download/v<VERSION>/f5xc-api-specs-v<VERSION>.zip" in documentation
    assert "domains/validation.json" in documentation
    assert "raw.githubusercontent.com" not in documentation
    assert "github.io/api-specs-enriched/specifications" not in documentation
