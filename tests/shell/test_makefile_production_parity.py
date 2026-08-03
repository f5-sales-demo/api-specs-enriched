# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Contracts keeping local generation byte-equivalent to production."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"


def test_pipeline_runs_every_production_generation_and_formatting_stage() -> None:
    makefile = MAKEFILE.read_text()
    pipeline = makefile[makefile.index("pipeline:") : makefile.index("\n# Individual steps")]
    producer = (ROOT / ".github/workflows/sync-and-enrich.yml").read_text()
    verifier = (ROOT / "scripts/release/verify_reproducible_build.py").read_text()

    assert "pipeline: check-deps" in pipeline
    assert "scripts.release.build_release_tree" in pipeline
    assert producer.count("scripts.release.build_release_tree") == 2
    assert "scripts.release.build_release_tree" in verifier
    for command in (
        "scripts.pipeline --version",
        "scripts.compile_catalog --version",
        "scripts.generate_api_viewer",
        "biome format --write",
    ):
        assert command not in pipeline


def test_dependency_gate_proves_the_locked_builder_tools() -> None:
    makefile = MAKEFILE.read_text()
    gate = makefile[makefile.index("check-deps:") : makefile.index("\n# Install all dependencies")]

    assert "$(UV) --version" in gate
    assert "$(UV) sync --frozen --check" in gate
    assert "$(PYTHON) --version" in gate
    assert "$(BIOME) --version" in gate
    assert "BIOME_VERSION" in makefile
