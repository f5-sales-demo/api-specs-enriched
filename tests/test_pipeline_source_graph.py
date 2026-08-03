"""Fail-closed source graph tests for the unified enrichment pipeline."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from scripts import pipeline
from scripts.utils.raw_manifest import create_raw_manifest


@pytest.fixture
def valid_spec() -> dict:
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0.0"},
        "paths": {
            "/api/widgets": {
                "post": {
                    "operationId": "ves.io.schema.widgets.CustomAPI.CreateWidget",
                    "tags": ["widgets"],
                    "requestBody": {"$ref": "#/components/requestBodies/CreateWidgetRequest"},
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "components": {
            "requestBodies": {
                "CreateWidgetRequest": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Widget"}}
                    }
                }
            },
            "schemas": {"Widget": {"type": "object"}},
        },
    }


def _assert_rejected_without_mutation(spec: dict, expected: str) -> None:
    original = copy.deepcopy(spec)
    with pytest.raises(pipeline.SourceGraphValidationError, match=expected):
        pipeline.validate_source_graph(spec)
    assert spec == original


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda spec: spec["components"]["requestBodies"]["CreateWidgetRequest"]["content"][
                "application/json"
            ].__setitem__("schema", {"$ref": "#/components/schemas/Absent"}),
            "unresolved local reference",
        ),
        (
            lambda spec: spec["components"]["requestBodies"]["CreateWidgetRequest"]["content"][
                "application/json"
            ].__setitem__("schema", {"$ref": "https://example.invalid/widget.json"}),
            "nonlocal reference",
        ),
        (
            lambda spec: spec["paths"]["/api/widgets"]["post"].pop("tags"),
            "at least one non-empty tag",
        ),
        (
            lambda spec: spec["paths"]["/api/widgets"].__setitem__("post", {}),
            "operation must be a non-empty object",
        ),
        (
            lambda spec: spec["paths"]["/api/widgets"].__setitem__("post", None),
            "operation must be a non-empty object",
        ),
        (
            lambda spec: spec["paths"]["/api/widgets"]["post"].pop("operationId"),
            "has no operationId",
        ),
        (
            lambda spec: spec["paths"]["/api/widgets"]["post"].pop("responses"),
            "responses must be a non-empty object",
        ),
    ],
)
def test_source_graph_mutations_fail_closed(valid_spec, mutate, expected):
    mutate(valid_spec)
    _assert_rejected_without_mutation(valid_spec, expected)


def test_valid_source_graph_passes(valid_spec):
    pipeline.validate_source_graph(valid_spec)


def test_normalization_does_not_fabricate_or_discard_contracts(valid_spec):
    operation = valid_spec["paths"]["/api/widgets"]["post"]
    operation["requestBody"] = {"$ref": "#/components/requestBodies/Absent"}
    valid_spec["paths"]["/api/empty"] = {"get": {}}
    original = copy.deepcopy(valid_spec)

    config = copy.deepcopy(pipeline.DEFAULT_CONFIG)
    config["normalization"].update(
        {
            "fix_orphan_refs": True,
            "inline_orphan_request_bodies": True,
            "remove_empty_objects": True,
        }
    )
    normalized, stats = pipeline.normalize_spec(valid_spec, config)

    assert normalized == original
    assert stats == {"ref_siblings_removed": 0, "types_normalized": 0}


def test_current_source_graph_is_complete():
    source_files = pipeline.source_spec_files(Path("specs/original"))
    assert source_files
    assert pipeline.validate_source_files(source_files) == []


def _write_manifest(input_dir: Path, names: list[str]) -> None:
    manifest = create_raw_manifest(
        release_receipt={
            "version": "1.2.3",
            "tag_name": "v1.2.3",
            "published_at": "2026-08-01T00:00:00Z",
            "asset_name": "api-specs-v1.2.3.zip",
            "asset_size": 1,
            "asset_digest": "sha256:" + "0" * 64,
        },
        source_dir=input_dir,
        files=names,
    ).as_document()
    (input_dir / "manifest.json").write_text(json.dumps(manifest))


def test_invalid_source_preserves_existing_output(tmp_path: Path, valid_spec):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    report_dir = tmp_path / "reports"
    input_dir.mkdir()
    output_dir.mkdir()
    invalid = copy.deepcopy(valid_spec)
    invalid["paths"]["/api/widgets"]["post"].pop("operationId")
    (input_dir / "invalid.json").write_text(json.dumps(invalid))
    _write_manifest(input_dir, ["invalid.json"])
    sentinel = output_dir / "existing.json"
    sentinel.write_text("preserve me")

    stats = pipeline.run_pipeline(
        input_dir,
        output_dir,
        report_dir,
        config=copy.deepcopy(pipeline.DEFAULT_CONFIG),
        version="1.2.3",
    )

    assert stats.files_failed == 1
    assert stats.errors[0]["file"] == "invalid.json"
    assert sentinel.read_text() == "preserve me"


class _OmittingBatchProcessor:
    cleaned = False

    def __init__(self, batch_size):
        self.batch_size = batch_size

    def process_batch(self, *_args, **_kwargs):
        return {}

    def get_stats(self):
        return {"specs_processed": 0, "batches_processed": 1}

    def cleanup_cache(self):
        type(self).cleaned = True


def test_silent_batch_omission_becomes_fatal_error(
    tmp_path: Path, valid_spec, monkeypatch: pytest.MonkeyPatch
):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "widget.json").write_text(json.dumps(valid_spec))
    _write_manifest(input_dir, ["widget.json"])
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    sentinel = output_dir / "existing.json"
    sentinel.write_text("preserve me")
    _OmittingBatchProcessor.cleaned = False
    monkeypatch.setattr(pipeline, "BatchSpecProcessor", _OmittingBatchProcessor)

    stats = pipeline.run_pipeline(
        input_dir,
        output_dir,
        tmp_path / "reports",
        config=copy.deepcopy(pipeline.DEFAULT_CONFIG),
        version="1.2.3",
    )

    assert stats.files_processed == 1
    assert stats.files_succeeded == 0
    assert stats.files_failed == 1
    assert stats.errors == [
        {
            "file": "widget.json",
            "error": "batch processing failed to produce a cached specification",
        }
    ]
    assert _OmittingBatchProcessor.cleaned
    assert sentinel.read_text() == "preserve me"


class _UnreadableCacheBatchProcessor(_OmittingBatchProcessor):
    def process_batch(self, spec_files, *_args, **_kwargs):
        return {spec_files[0].name: Path("unreadable-cache.json")}

    def get_stats(self):
        return {"specs_processed": 1, "batches_processed": 1}

    def load_cached_spec(self, _cache_path):
        raise ValueError("cache is unreadable")


def test_cache_load_failure_becomes_fatal_error_before_output_cleanup(
    tmp_path: Path, valid_spec, monkeypatch: pytest.MonkeyPatch
):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "widget.json").write_text(json.dumps(valid_spec))
    _write_manifest(input_dir, ["widget.json"])
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    sentinel = output_dir / "existing.json"
    sentinel.write_text("preserve me")
    _UnreadableCacheBatchProcessor.cleaned = False
    monkeypatch.setattr(pipeline, "BatchSpecProcessor", _UnreadableCacheBatchProcessor)

    stats = pipeline.run_pipeline(
        input_dir,
        output_dir,
        tmp_path / "reports",
        config=copy.deepcopy(pipeline.DEFAULT_CONFIG),
        version="1.2.3",
    )

    assert stats.files_succeeded == 0
    assert stats.files_failed == 1
    assert stats.errors == [
        {
            "file": "widget.json",
            "error": "failed to load cached specification: cache is unreadable",
        }
    ]
    assert _UnreadableCacheBatchProcessor.cleaned
    assert sentinel.read_text() == "preserve me"


def test_late_exporter_failure_preserves_existing_artifact_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output_dir = tmp_path / "api"
    output_dir.mkdir()
    sentinel = output_dir / "existing.json"
    sentinel.write_text("preserve me")

    monkeypatch.setattr(
        pipeline,
        "save_spec",
        lambda _spec, path, indent: path.write_text("{}"),
    )
    monkeypatch.setattr(pipeline, "create_spec_index", lambda _specs, _version: {})

    class ValidationExporter:
        def export(self, path):
            path.write_text("{}")

        def get_stats(self):
            return {
                "resources_processed": 0,
                "required_fields_exported": 0,
                "enum_values_exported": 0,
            }

    class MinimalDefaultsExporter:
        @staticmethod
        def collect_schemas(_specs):
            return {}

        def export(self, _schemas, path, *, version):
            path.write_text("{}")
            return {"resources": {}, "version": version}

    class FailingNamespaceProfilesExporter:
        def export(self, _path, *, version):
            raise RuntimeError(f"late exporter failure for {version}")

    monkeypatch.setattr(pipeline, "ValidationExporter", ValidationExporter)
    monkeypatch.setattr(pipeline, "MinimalDefaultsExporter", MinimalDefaultsExporter)
    monkeypatch.setattr(
        pipeline,
        "NamespaceProfilesExporter",
        FailingNamespaceProfilesExporter,
    )

    with pytest.raises(RuntimeError, match="late exporter failure"):
        pipeline._publish_generated_outputs(
            {"widgets": {}},
            {"components": {"schemas": {}}},
            output_dir,
            version="1.2.3",
            indent=2,
        )

    assert sentinel.read_text() == "preserve me"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["api"]


def test_main_returns_nonzero_for_stats_errors_without_failed_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        pipeline,
        "run_pipeline",
        lambda **_kwargs: pipeline.PipelineStats(
            errors=[{"file": "widget.json", "error": "measured failure"}]
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pipeline",
            "--version",
            "1.2.3",
            "--input-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "output"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--dry-run",
        ],
    )

    assert pipeline.main() == 1
