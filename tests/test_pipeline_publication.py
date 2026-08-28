"""Atomic publication tests for the unified enrichment pipeline."""

from pathlib import Path

import pytest

from scripts import pipeline


def test_failed_candidate_does_not_replace_published_output(tmp_path, monkeypatch):
    """Collected per-file failures must leave the published tree untouched."""
    published = tmp_path / "published"
    published.mkdir()
    (published / "existing.json").write_text("existing\n")

    def failed_run(input_dir: Path, output_dir: Path, config: dict, dry_run: bool):
        (output_dir / "partial.json").write_text("partial\n")
        return pipeline.PipelineStats(files_processed=2, files_succeeded=1, files_failed=1)

    monkeypatch.setattr(pipeline, "_run_pipeline", failed_run)
    stats = pipeline.run_pipeline(tmp_path / "input", published, {})

    assert stats.files_failed == 1
    assert (published / "existing.json").read_text() == "existing\n"
    assert not (published / "partial.json").exists()


def test_successful_candidate_replaces_published_output(tmp_path, monkeypatch):
    """A complete candidate replaces the previous publication as one tree."""
    published = tmp_path / "published"
    published.mkdir()
    (published / "stale.json").write_text("stale\n")

    def successful_run(input_dir: Path, output_dir: Path, config: dict, dry_run: bool):
        (output_dir / "fresh.json").write_text("fresh\n")
        return pipeline.PipelineStats(files_processed=1, files_succeeded=1)

    monkeypatch.setattr(pipeline, "_run_pipeline", successful_run)
    stats = pipeline.run_pipeline(tmp_path / "input", published, {})

    assert stats.files_failed == 0
    assert not (published / "stale.json").exists()
    assert (published / "fresh.json").read_text() == "fresh\n"


def test_candidate_directory_is_removed_after_exception(tmp_path, monkeypatch):
    """Temporary output is removed even when candidate generation raises."""
    published = tmp_path / "published"
    published.mkdir()

    def exploding_run(input_dir: Path, output_dir: Path, config: dict, dry_run: bool):
        (output_dir / "partial.json").write_text("partial\n")
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "_run_pipeline", exploding_run)
    with pytest.raises(RuntimeError, match="boom"):
        pipeline.run_pipeline(tmp_path / "input", published, {})

    assert not list(tmp_path.glob(".published-candidate-*"))


def test_explicit_worker_override_preserves_default_when_absent():
    """Benchmark worker input changes only the configured independent worker count."""
    config = {"processing": {"parallel_workers": 4}, "unrelated": "kept"}

    pipeline.configure_parallel_workers(config, None)
    assert config == {"processing": {"parallel_workers": 4}, "unrelated": "kept"}

    pipeline.configure_parallel_workers(config, 8)
    assert config == {"processing": {"parallel_workers": 8}, "unrelated": "kept"}
