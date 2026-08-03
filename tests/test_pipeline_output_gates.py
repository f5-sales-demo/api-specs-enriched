# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Fail-closed tests for generated artifact validation and contract accounting."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from scripts import pipeline


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_invalid_staged_openapi_rejects_entire_set_before_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    sentinel = output_dir / "existing.json"
    sentinel.write_text("preserve me", encoding="utf-8")
    promoted = False

    def write_invalid_set(
        domain_specs: dict,
        master_spec: dict,
        staging_dir: Path,
        version: str,
        indent: int,
    ) -> None:
        del domain_specs, master_spec, version, indent
        for name in pipeline._SUPPORT_ARTIFACTS:
            _write_json(staging_dir / name, {})
        _write_json(
            staging_dir / "alpha.json",
            {
                "openapi": "3.0.0",
                "info": {"title": "Valid staged domain", "version": "1.0.0"},
                "paths": {},
            },
        )

    def record_promotion(staging_dir: Path, destination: Path) -> None:
        del staging_dir, destination
        nonlocal promoted
        promoted = True

    monkeypatch.setattr(pipeline, "_write_staged_outputs", write_invalid_set)
    monkeypatch.setattr(pipeline, "_promote_staged_outputs", record_promotion)

    with pytest.raises(
        RuntimeError,
        match=r"staged OpenAPI artifact openapi\.json is invalid",
    ):
        pipeline._publish_generated_outputs(
            {"alpha": {}},
            {},
            output_dir,
            "1.2.3",
            2,
        )

    assert promoted is False
    assert sentinel.read_text(encoding="utf-8") == "preserve me"
    assert sorted(path.name for path in output_dir.iterdir()) == ["existing.json"]
    assert list(tmp_path.glob(".generated.staging-*")) == []


def test_failed_promotion_restores_prior_tree_and_removes_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    sentinel = output_dir / "existing.json"
    sentinel.write_text("preserve me", encoding="utf-8")
    original_rename = Path.rename

    def write_candidate(
        domain_specs: dict,
        master_spec: dict,
        staging_dir: Path,
        version: str,
        indent: int,
    ) -> None:
        del domain_specs, master_spec, version, indent
        (staging_dir / "candidate.json").write_text("new tree", encoding="utf-8")

    def fail_candidate_promotion(source: Path, destination: Path) -> Path:
        if source.name.startswith(".generated.staging-") and destination == output_dir:
            raise OSError("injected candidate promotion failure")
        return original_rename(source, destination)

    monkeypatch.setattr(pipeline, "_write_staged_outputs", write_candidate)
    monkeypatch.setattr(pipeline, "_validate_staged_outputs", lambda *_args: None)
    monkeypatch.setattr(Path, "rename", fail_candidate_promotion)

    with pytest.raises(OSError, match="injected candidate promotion failure"):
        pipeline._publish_generated_outputs({}, {}, output_dir, "1.2.3", 2)

    assert sentinel.read_text(encoding="utf-8") == "preserve me"
    assert sorted(path.name for path in output_dir.iterdir()) == ["existing.json"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["generated"]


def test_backup_cleanup_failure_reports_success_with_promoted_tree_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    output_dir = tmp_path / "generated"
    output_dir.mkdir()
    (output_dir / "existing.json").write_text("preserve me", encoding="utf-8")
    staging_dir = tmp_path / ".generated.staging-fixture"
    staging_dir.mkdir()
    (staging_dir / "candidate.json").write_text("new tree", encoding="utf-8")
    original_rmtree = pipeline.shutil.rmtree

    def fail_backup_cleanup(path: Path) -> None:
        if path.name.startswith(".generated.backup-"):
            raise OSError("injected backup cleanup failure")
        original_rmtree(path)

    monkeypatch.setattr(pipeline.shutil, "rmtree", fail_backup_cleanup)

    with caplog.at_level(logging.WARNING, logger=pipeline.__name__):
        pipeline._promote_staged_outputs(staging_dir, output_dir)

    assert (output_dir / "candidate.json").read_text(encoding="utf-8") == "new tree"
    assert not (output_dir / "existing.json").exists()
    backups = list(tmp_path.glob(".generated.backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "existing.json").read_text(encoding="utf-8") == "preserve me"
    assert not staging_dir.exists()
    assert "promoted successfully" in caplog.text
    assert "injected backup cleanup failure" in caplog.text


def test_release_findings_are_measured_and_block_publication(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple[str, dict]] = []
    spec = {"fixture": True}

    class FindingConsistencyValidator:
        def validate(self, value: dict) -> list[dict]:
            calls.append(("consistency", value))
            return [
                {
                    "severity": "warning",
                    "category": "parameter",
                    "message": "measured consistency defect",
                    "location": "global",
                }
            ]

    class FindingBrandingValidator:
        def validate_spec(self, value: dict, _target_fields: list[str]) -> list[dict]:
            calls.append(("branding", value))
            return [
                {
                    "term": "Volterra",
                    "position": 7,
                    "context": "legacy Volterra wording",
                    "path": "info.description",
                }
            ]

    monkeypatch.setattr(pipeline, "ConsistencyValidator", FindingConsistencyValidator)
    monkeypatch.setattr(pipeline, "BrandingValidator", FindingBrandingValidator)

    with pytest.raises(RuntimeError) as error:
        pipeline._validate_release_findings({"openapi.json": spec}, ["description"])

    assert calls == [("consistency", spec), ("branding", spec)]
    assert "consistency validation found 1 configured finding(s)" in str(error.value)
    assert "branding validation found 1 finding(s)" in str(error.value)
    assert "measured consistency defect" in str(error.value)
    assert "legacy Volterra wording" in str(error.value)


def test_clean_release_findings_pass_both_validators(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[str] = []

    class CleanConsistencyValidator:
        def validate(self, _spec: dict) -> list[dict]:
            calls.append("consistency")
            return []

    class CleanBrandingValidator:
        def validate_spec(self, _spec: dict, _target_fields: list[str]) -> list[dict]:
            calls.append("branding")
            return []

    monkeypatch.setattr(pipeline, "ConsistencyValidator", CleanConsistencyValidator)
    monkeypatch.setattr(pipeline, "BrandingValidator", CleanBrandingValidator)

    pipeline._validate_release_findings({"openapi.json": {}}, ["description"])

    assert calls == ["consistency", "branding"]


def _operation(metadata: dict | None = None, **flat: object) -> dict:
    wrapper = metadata or {
        "purpose": "Read the resource",
        "required_fields": [],
        "optional_fields": ["description"],
        "field_docs": {"description": "Resource description"},
        "conditions": {"prerequisites": [], "postconditions": []},
        "side_effects": {},
        "danger_level": "low",
        "confirmation_required": False,
        "common_errors": [],
        "performance_impact": {"latency": "low", "resource_usage": "low"},
    }
    return {
        "operationId": "ves.io.schema.fixture.API.Get",
        "responses": {"200": {"description": "Success"}},
        "x-f5xc-operation-metadata": wrapper,
        **flat,
    }


def test_complete_operation_metadata_gate_checks_every_candidate_operation() -> None:
    specs = {
        "alpha.json": {"paths": {"/alpha": {"get": _operation()}}},
        "omega.json": {"paths": {"/omega": {"post": _operation()}}},
    }

    assert pipeline._require_complete_operation_metadata(specs) == 2


def test_operation_metadata_gate_rejects_missing_field_in_any_candidate() -> None:
    incomplete = _operation()["x-f5xc-operation-metadata"]
    incomplete.pop("performance_impact")
    specs = {
        "alpha.json": {"paths": {"/alpha": {"get": _operation()}}},
        "omega.json": {"paths": {"/omega": {"post": _operation(incomplete)}}},
    }

    with pytest.raises(RuntimeError, match=r"omega\.json POST /omega.*performance_impact"):
        pipeline._require_complete_operation_metadata(specs)


def test_operation_metadata_gate_rejects_every_flat_compatibility_field() -> None:
    specs = {
        "alpha.json": {
            "paths": {
                "/alpha": {
                    "get": _operation(**{"x-f5xc-danger-level": "low"}),
                }
            }
        }
    }

    with pytest.raises(RuntimeError, match=r"x-f5xc-danger-level"):
        pipeline._require_complete_operation_metadata(specs)


def test_operation_metadata_gate_reports_unhashable_array_entries_cleanly() -> None:
    malformed = _operation()["x-f5xc-operation-metadata"]
    malformed["required_fields"] = [{"field": "description"}]
    specs = {"alpha.json": {"paths": {"/alpha": {"get": _operation(malformed)}}}}

    with pytest.raises(
        RuntimeError,
        match=r"alpha\.json GET /alpha.*required_fields is not a unique non-empty string array",
    ):
        pipeline._require_complete_operation_metadata(specs)


def test_release_findings_validate_master_and_every_domain(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple[str, str]] = []

    class RecordingConsistencyValidator:
        def validate(self, spec: dict) -> list[dict]:
            calls.append(("consistency", spec["name"]))
            return []

    class RecordingBrandingValidator:
        def validate_spec(self, spec: dict, target_fields: list[str]) -> list[dict]:
            assert target_fields == ["description"]
            calls.append(("branding", spec["name"]))
            return []

    monkeypatch.setattr(pipeline, "ConsistencyValidator", RecordingConsistencyValidator)
    monkeypatch.setattr(pipeline, "BrandingValidator", RecordingBrandingValidator)

    pipeline._validate_release_findings(
        {
            "openapi.json": {"name": "master"},
            "virtual.json": {"name": "virtual"},
        },
        ["description", "x-f5xc-example"],
    )

    assert calls == [
        ("consistency", "master"),
        ("branding", "master"),
        ("consistency", "virtual"),
        ("branding", "virtual"),
    ]
