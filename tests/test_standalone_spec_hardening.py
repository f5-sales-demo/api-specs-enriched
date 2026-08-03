"""Clean-break invariants for standalone normalization and enrichment."""

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

from scripts import enrich, normalize
from scripts.utils import spec_batch
from scripts.utils.raw_manifest import create_raw_manifest
from scripts.utils.source_graph_validator import (
    SourceGraphValidationError,
    SpecSelectionError,
    select_source_specs,
    source_spec_files,
    validate_source_graph,
)


def _valid_spec() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Fixture", "version": "1.0.0"},
        "paths": {
            "/api/widgets": {
                "get": {
                    "operationId": "ves.io.schema.fixture.widget.API.List",
                    "tags": ["widget"],
                    "responses": {"200": {"description": "Success"}},
                }
            }
        },
        "components": {
            "schemas": {
                "Widget": {"type": "object"},
            }
        },
    }


def _write_manifest(directory: Path, names: list[str]) -> None:
    manifest = create_raw_manifest(
        release_receipt={
            "version": "1.0.0",
            "tag_name": "v1.0.0",
            "published_at": "2026-08-01T00:00:00Z",
            "asset_name": "api-specs-v1.0.0.zip",
            "asset_size": 1,
            "asset_digest": "sha256:" + "0" * 64,
        },
        source_dir=directory,
        files=names,
    ).as_document()
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _write_index(directory: Path, names: list[str]) -> None:
    index = {
        "version": "1.0.0",
        "timestamp": "2026-08-01T00:00:00Z",
        "specifications": [{"file": name} for name in names],
    }
    (directory / "index.json").write_text(json.dumps(index), encoding="utf-8")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda spec: spec["paths"]["/api/widgets"].update({"get": {}}),
            "operation must be a non-empty object",
        ),
        (
            lambda spec: spec["paths"]["/api/widgets"]["get"].pop("operationId"),
            "has no operationId",
        ),
        (
            lambda spec: spec["paths"]["/api/widgets"]["get"].update(
                {"operationId": "ListWidgets"}
            ),
            "operationId is not fully qualified",
        ),
        (
            lambda spec: spec["paths"]["/api/widgets"]["get"].update({"tags": []}),
            "at least one non-empty tag",
        ),
        (
            lambda spec: spec["paths"]["/api/widgets"]["get"].update({"responses": {}}),
            "responses must be a non-empty object",
        ),
        (
            lambda spec: spec["paths"]["/api/widgets"]["get"].update({"requestBody": {}}),
            "requestBody must be a non-empty object",
        ),
    ],
)
def test_operation_contract_mutations_fail_closed(mutation, message):
    spec = _valid_spec()
    mutation(spec)

    with pytest.raises(SourceGraphValidationError, match=message):
        validate_source_graph(spec)


def test_unresolved_request_body_reference_fails_closed():
    spec = _valid_spec()
    spec["paths"]["/api/widgets"]["get"]["requestBody"] = {
        "$ref": "#/components/requestBodies/MissingRequest"
    }

    with pytest.raises(SourceGraphValidationError, match="unresolved local reference"):
        validate_source_graph(spec)


def test_nonlocal_reference_fails_closed():
    spec = _valid_spec()
    spec["components"]["schemas"]["Widget"] = {"$ref": "https://example.invalid/widget.json"}

    with pytest.raises(SourceGraphValidationError, match="nonlocal reference"):
        validate_source_graph(spec)


def test_reference_siblings_fail_instead_of_being_deleted():
    spec = _valid_spec()
    spec["components"]["schemas"]["WidgetList"] = {
        "$ref": "#/components/schemas/Widget",
        "description": "This field must not be silently deleted.",
    }

    with pytest.raises(SourceGraphValidationError, match="has sibling fields"):
        validate_source_graph(spec)


def test_duplicate_fully_qualified_operation_ids_fail_closed():
    spec = _valid_spec()
    duplicate = copy.deepcopy(spec["paths"]["/api/widgets"]["get"])
    spec["paths"]["/api/other-widgets"] = {"get": duplicate}

    with pytest.raises(SourceGraphValidationError, match="duplicate operationId"):
        validate_source_graph(spec)


def test_raw_manifest_is_the_only_source_membership_contract(tmp_path):
    declared = tmp_path / "declared.json"
    declared.write_text(json.dumps(_valid_spec()), encoding="utf-8")
    _write_manifest(tmp_path, [declared.name])

    assert source_spec_files(tmp_path) == [declared]

    undeclared = tmp_path / "undeclared.json"
    undeclared.write_text(json.dumps(_valid_spec()), encoding="utf-8")
    with pytest.raises(SpecSelectionError, match="undeclared OpenAPI files"):
        source_spec_files(tmp_path)


@pytest.mark.parametrize("defect", ["missing", "duplicate", "unsafe", "unknown-key"])
def test_malformed_raw_manifest_fails_closed(tmp_path, defect):
    selected = tmp_path / "selected.json"
    selected.write_text(json.dumps(_valid_spec()), encoding="utf-8")
    _write_manifest(tmp_path, [selected.name])
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if defect == "missing":
        selected.unlink()
    elif defect == "duplicate":
        manifest["files"].append(dict(manifest["files"][0]))
    elif defect == "unsafe":
        manifest["files"][0]["name"] = "../selected.json"
    else:
        manifest["compatibility_mode"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SpecSelectionError):
        select_source_specs(tmp_path)


def test_generated_index_selects_domains_plus_canonical_and_ignores_support(tmp_path):
    domain = tmp_path / "domain.json"
    canonical = tmp_path / "openapi.json"
    domain.write_text(json.dumps(_valid_spec()), encoding="utf-8")
    canonical.write_text(json.dumps(_valid_spec()), encoding="utf-8")
    (tmp_path / "support.json").write_text(json.dumps({"kind": "support"}), encoding="utf-8")
    _write_index(tmp_path, [domain.name])

    assert source_spec_files(tmp_path) == [domain, canonical]


def test_generated_index_requires_unique_entries_and_canonical_asset(tmp_path):
    domain = tmp_path / "domain.json"
    domain.write_text(json.dumps(_valid_spec()), encoding="utf-8")
    _write_index(tmp_path, [domain.name, domain.name])
    with pytest.raises(SpecSelectionError, match="duplicate filenames"):
        source_spec_files(tmp_path)

    _write_index(tmp_path, [domain.name])
    with pytest.raises(SpecSelectionError, match=r"openapi\.json"):
        source_spec_files(tmp_path)


def test_missing_or_ambiguous_selector_fails_closed(tmp_path):
    with pytest.raises(SpecSelectionError, match="exactly one selector"):
        source_spec_files(tmp_path)
    (tmp_path / "missing.json").write_text("{}")
    _write_manifest(tmp_path, ["missing.json"])
    (tmp_path / "missing.json").unlink()
    _write_index(tmp_path, ["also-missing.json"])
    with pytest.raises(SpecSelectionError, match="exactly one selector"):
        source_spec_files(tmp_path)


def test_raw_source_mutation_under_an_unchanged_manifest_fails_closed(tmp_path):
    selected = tmp_path / "selected.json"
    selected.write_text(json.dumps(_valid_spec()), encoding="utf-8")
    _write_manifest(tmp_path, [selected.name])

    selected.write_text(json.dumps({**_valid_spec(), "x-mutated": True}), encoding="utf-8")

    with pytest.raises(SpecSelectionError, match="SHA-256 mismatch"):
        select_source_specs(tmp_path)


def test_normalize_does_not_fabricate_missing_reference(tmp_path):
    spec = _valid_spec()
    spec["paths"]["/api/widgets"]["get"]["requestBody"] = {
        "$ref": "#/components/requestBodies/MissingRequest"
    }
    source = tmp_path / "source.json"
    output = tmp_path / "output.json"
    source.write_text(json.dumps(spec), encoding="utf-8")

    result = normalize.normalize_spec_file(source, output, normalize.DEFAULT_CONFIG)

    assert result.success is False
    assert "unresolved local reference" in (result.error or "")
    assert not output.exists()


def test_normalize_does_not_delete_empty_operation(tmp_path):
    spec = _valid_spec()
    spec["paths"]["/api/widgets"]["get"] = {}
    source = tmp_path / "source.json"
    output = tmp_path / "output.json"
    source.write_text(json.dumps(spec), encoding="utf-8")

    result = normalize.normalize_spec_file(source, output, normalize.DEFAULT_CONFIG)

    assert result.success is False
    assert "operation must be a non-empty object" in (result.error or "")
    assert not output.exists()


def test_enrich_rejects_bad_contract_before_transforming(tmp_path):
    spec = _valid_spec()
    del spec["paths"]["/api/widgets"]["get"]["operationId"]
    source = tmp_path / "source.json"
    output = tmp_path / "output.json"
    source.write_text(json.dumps(spec), encoding="utf-8")

    result = enrich.enrich_spec_file(source, output, enrich.DEFAULT_CONFIG)

    assert result.success is False
    assert "has no operationId" in (result.error or "")
    assert not output.exists()


def test_enrich_save_rejects_invalid_final_graph(tmp_path):
    spec = _valid_spec()
    spec["components"]["schemas"]["Broken"] = {"$ref": "#/components/schemas/Missing"}
    output = tmp_path / "output.json"

    with pytest.raises(SourceGraphValidationError, match="unresolved local reference"):
        enrich.save_spec(spec, output)

    assert not output.exists()


def test_configs_expose_no_repair_or_continue_on_error_switches():
    normalization = yaml.safe_load(Path("config/normalization.yaml").read_text(encoding="utf-8"))
    enrichment = yaml.safe_load(Path("config/enrichment.yaml").read_text(encoding="utf-8"))

    assert set(normalization["normalization"]) == {"type_standardization"}
    assert "stub_templates" not in normalization
    assert "continue_on_error" not in normalization["processing"]
    assert "continue_on_error" not in enrichment["processing"]
    assert "validation" not in enrichment
    assert "continue_on_error" not in normalize.DEFAULT_CONFIG["processing"]
    assert "continue_on_error" not in enrich.DEFAULT_CONFIG["processing"]


def test_removed_normalize_compatibility_helpers_do_not_exist():
    for name in (
        "create_stub_component",
        "fix_orphan_refs",
        "inline_orphan_request_bodies",
        "remove_empty_operations",
        "remove_ref_siblings",
    ):
        assert not hasattr(normalize, name)


def test_obsolete_and_unknown_execution_controls_are_rejected():
    normalization = copy.deepcopy(normalize.DEFAULT_CONFIG)
    normalization["processing"]["continue_on_error"] = True
    with pytest.raises(ValueError, match="unsupported normalization configuration keys"):
        normalize._validate_config(normalization)

    enrichment = copy.deepcopy(enrich.DEFAULT_CONFIG)
    enrichment["processing"]["parallel_workerz"] = 4
    with pytest.raises(ValueError, match="unsupported enrichment configuration keys"):
        enrich._validate_config(enrichment)

    enrichment = copy.deepcopy(enrich.DEFAULT_CONFIG)
    enrichment["discovery_enrichment"] = {"continue_on_error": True}
    with pytest.raises(ValueError, match="obsolete enrichment configuration keys"):
        enrich._validate_config(enrichment)


@pytest.mark.parametrize("module", [normalize, enrich])
def test_explicit_missing_config_does_not_silently_use_defaults(module, tmp_path):
    with pytest.raises(FileNotFoundError, match="configuration not found"):
        module.load_config(tmp_path / "missing.yaml")


def test_normalize_rejects_malformed_openapi_before_writing(tmp_path):
    spec = _valid_spec()
    spec["openapi"] = "not-an-openapi-version"
    source = tmp_path / "source.json"
    output = tmp_path / "output.json"
    source.write_text(json.dumps(spec), encoding="utf-8")

    result = normalize.normalize_spec_file(source, output, normalize.DEFAULT_CONFIG)

    assert result.success is False
    assert not output.exists()


def test_configured_validation_findings_are_release_blocking():
    class FindingValidator:
        @staticmethod
        def validate(_spec):
            return [
                {
                    "severity": "warning",
                    "category": "fixture",
                    "message": "measured inconsistency",
                    "location": "paths./api/widgets.get",
                }
            ]

    with pytest.raises(ValueError, match="consistency validation found 1 issue"):
        enrich._validate_enrichment_findings(_valid_spec(), ["description"], FindingValidator())

    spec = _valid_spec()
    spec["info"]["description"] = "Legacy Volterra wording"
    with pytest.raises(ValueError, match="branding validation found"):
        enrich._validate_enrichment_findings(spec, ["description"], enrich.ConsistencyValidator())


@pytest.mark.parametrize("module", [normalize, enrich])
def test_batch_failure_preserves_all_existing_outputs(module, tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    for name in ("good.json", "bad.json"):
        (input_dir / name).write_text(json.dumps(_valid_spec()), encoding="utf-8")
    _write_manifest(input_dir, ["good.json", "bad.json"])
    sentinel = output_dir / "existing.json"
    sentinel.write_text("preserve exactly", encoding="utf-8")

    def fake_process(args):
        source, staged_output, _config = args
        if source.name == "bad.json":
            if module is normalize:
                return normalize.NormalizationResult(source.name, success=False, error="invalid")
            return enrich.EnrichmentResult(
                source.name,
                success=False,
                validation_passed=False,
                error="invalid",
            )
        staged_output.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        if module is normalize:
            return normalize.NormalizationResult(source.name, success=True)
        return enrich.EnrichmentResult(source.name, success=True)

    monkeypatch.setattr(module, "process_spec_wrapper", fake_process)
    config = copy.deepcopy(module.DEFAULT_CONFIG)
    stats = (
        module.normalize_all_specs(input_dir, output_dir, config, parallel=False)
        if module is normalize
        else module.enrich_all_specs(input_dir, output_dir, config, parallel=False)
    )

    assert stats.files_succeeded == 0
    assert stats.files_failed == 2
    assert sentinel.read_text(encoding="utf-8") == "preserve exactly"
    assert sorted(path.name for path in output_dir.iterdir()) == ["existing.json"]


def test_successful_enrichment_reconciles_stale_output_membership(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    source = input_dir / "current.json"
    source.write_text(json.dumps(_valid_spec()), encoding="utf-8")
    _write_manifest(input_dir, [source.name])
    (output_dir / "stale.json").write_text(json.dumps(_valid_spec()), encoding="utf-8")
    _write_manifest(output_dir, ["stale.json"])
    support = {"kind": "support"}
    (output_dir / "support.json").write_text(json.dumps(support), encoding="utf-8")

    def fake_process(args):
        input_path, staged_output, _config = args
        staged_output.write_text(input_path.read_text(encoding="utf-8"), encoding="utf-8")
        return enrich.EnrichmentResult(input_path.name, success=True)

    monkeypatch.setattr(enrich, "process_spec_wrapper", fake_process)
    stats = enrich.enrich_all_specs(
        input_dir,
        output_dir,
        copy.deepcopy(enrich.DEFAULT_CONFIG),
        parallel=False,
    )

    assert stats.files_succeeded == 1
    assert stats.files_failed == 0
    assert (output_dir / "current.json").exists()
    assert not (output_dir / "stale.json").exists()
    assert json.loads((output_dir / "support.json").read_text(encoding="utf-8")) == support
    assert source_spec_files(output_dir) == [output_dir / "current.json"]


def test_required_discovery_failure_preserves_existing_outputs(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "current.json").write_text(json.dumps(_valid_spec()), encoding="utf-8")
    _write_manifest(input_dir, ["current.json"])
    sentinel = output_dir / "existing.json"
    sentinel.write_text("preserve exactly", encoding="utf-8")
    config = copy.deepcopy(enrich.DEFAULT_CONFIG)
    config["discovery_enrichment"] = {
        "enabled": True,
        "discovered_specs_dir": str(tmp_path / "missing-discovery"),
    }

    stats = enrich.enrich_all_specs(input_dir, output_dir, config, parallel=False)

    assert stats.files_succeeded == 0
    assert stats.files_failed == 1
    assert any(error["file"] == "<discovery>" for error in stats.errors)
    assert sentinel.read_text(encoding="utf-8") == "preserve exactly"


def test_publication_error_rolls_back_existing_spec_set(tmp_path, monkeypatch):
    staging_dir = tmp_path / "staging"
    output_dir = tmp_path / "output"
    staging_dir.mkdir()
    output_dir.mkdir()
    current_spec = _valid_spec()
    current_spec["info"]["title"] = "Current"
    (staging_dir / "current.json").write_text(json.dumps(current_spec), encoding="utf-8")
    _write_manifest(staging_dir, ["current.json"])
    selection = select_source_specs(staging_dir)
    existing = output_dir / "existing.json"
    existing_text = json.dumps(_valid_spec(), sort_keys=True)
    existing.write_text(existing_text, encoding="utf-8")
    _write_manifest(output_dir, ["existing.json"])
    real_replace = spec_batch._replace_path
    call_count = 0

    def fail_first_publication(source, destination):
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise OSError("measured publication failure")
        return real_replace(source, destination)

    monkeypatch.setattr(spec_batch, "_replace_path", fail_first_publication)

    with pytest.raises(OSError, match="measured publication failure"):
        spec_batch.publish_spec_batch(staging_dir, output_dir, selection)

    assert existing.read_text(encoding="utf-8") == existing_text
    assert not (output_dir / "current.json").exists()
    assert list(tmp_path.glob(".output-backup-*")) == []


def test_publication_and_rollback_failure_preserves_recovery_copy(tmp_path, monkeypatch):
    staging_dir = tmp_path / "staging"
    output_dir = tmp_path / "output"
    staging_dir.mkdir()
    output_dir.mkdir()
    current_spec = _valid_spec()
    current_spec["info"]["title"] = "Current"
    (staging_dir / "current.json").write_text(json.dumps(current_spec), encoding="utf-8")
    _write_manifest(staging_dir, ["current.json"])
    selection = select_source_specs(staging_dir)
    existing = output_dir / "existing.json"
    existing_text = json.dumps(_valid_spec(), sort_keys=True)
    existing.write_text(existing_text, encoding="utf-8")
    _write_manifest(output_dir, ["existing.json"])
    existing_manifest = (output_dir / "manifest.json").read_bytes()
    real_replace = spec_batch._replace_path
    call_count = 0

    def fail_first_publication(source, destination):
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise OSError("measured publication failure")
        return real_replace(source, destination)

    monkeypatch.setattr(spec_batch, "_replace_path", fail_first_publication)
    monkeypatch.setattr(
        spec_batch,
        "_restore_path",
        lambda _backup, output: f"measured restore failure for {output}",
    )

    with pytest.raises(spec_batch.SpecBatchRollbackError) as caught:
        spec_batch.publish_spec_batch(staging_dir, output_dir, selection)

    error = caught.value
    assert isinstance(error.__cause__, OSError)
    assert "copy every file" in str(error)
    assert str(error.recovery_dir) in str(error)
    assert error.recovery_dir.parent == tmp_path
    assert error.recovery_dir.is_dir()
    assert (error.recovery_dir / "existing.json").read_text(encoding="utf-8") == existing_text
    assert (error.recovery_dir / "manifest.json").read_bytes() == existing_manifest


def test_successful_publication_removes_backup_directory(tmp_path):
    staging_dir = tmp_path / "staging"
    output_dir = tmp_path / "output"
    staging_dir.mkdir()
    output_dir.mkdir()
    current_spec = _valid_spec()
    current_spec["info"]["title"] = "Current"
    current_text = json.dumps(current_spec, sort_keys=True)
    (staging_dir / "current.json").write_text(current_text, encoding="utf-8")
    _write_manifest(staging_dir, ["current.json"])
    selection = select_source_specs(staging_dir)
    (output_dir / "existing.json").write_text(json.dumps(_valid_spec()), encoding="utf-8")
    _write_manifest(output_dir, ["existing.json"])

    spec_batch.publish_spec_batch(staging_dir, output_dir, selection)

    assert (output_dir / "current.json").read_text(encoding="utf-8") == current_text
    assert not (output_dir / "existing.json").exists()
    assert list(tmp_path.glob(".output-backup-*")) == []


def test_normalize_main_returns_nonzero_for_any_recorded_error(monkeypatch, tmp_path, capsys):
    stats = normalize.NormalizationStats(
        files_processed=1,
        files_succeeded=1,
        errors=[{"file": "fixture.json", "error": "recorded error"}],
    )
    config = copy.deepcopy(normalize.DEFAULT_CONFIG)
    config["paths"] = {
        "enriched": str(tmp_path),
        "normalized": str(tmp_path / "out"),
        "reports": str(tmp_path / "reports"),
    }
    monkeypatch.setattr(normalize, "load_config", lambda _path: config)
    monkeypatch.setattr(normalize, "normalize_all_specs", lambda **_kwargs: stats)
    monkeypatch.setattr(normalize, "generate_report", lambda *_args: None)
    monkeypatch.setattr(normalize, "print_summary", lambda *_args: None)
    monkeypatch.setattr(sys, "argv", ["normalize"])

    assert normalize.main() == 1
    assert "Successfully normalized" not in capsys.readouterr().out


def test_enrich_main_returns_nonzero_for_any_recorded_error(monkeypatch, tmp_path, capsys):
    stats = enrich.EnrichmentStats(
        files_processed=1,
        files_succeeded=1,
        validation_passed=1,
        errors=[{"file": "fixture.json", "error": "recorded error"}],
    )
    config = copy.deepcopy(enrich.DEFAULT_CONFIG)
    config["paths"] = {
        "original": str(tmp_path),
        "enriched": str(tmp_path / "out"),
        "reports": str(tmp_path / "reports"),
        "discovered": str(tmp_path / "discovered"),
    }
    monkeypatch.setattr(enrich, "load_config", lambda _path: config)
    monkeypatch.setattr(enrich, "enrich_all_specs", lambda **_kwargs: stats)
    monkeypatch.setattr(enrich, "generate_report", lambda *_args: None)
    monkeypatch.setattr(enrich, "print_summary", lambda *_args: None)
    monkeypatch.setattr(sys, "argv", ["enrich"])

    assert enrich.main() == 1
    assert "Successfully enriched" not in capsys.readouterr().out


def test_all_original_openapi_inputs_satisfy_clean_contract():
    spec_files = source_spec_files(Path("specs/original"))

    assert len(spec_files) == 283
    for spec_file in spec_files:
        validate_source_graph(json.loads(spec_file.read_text(encoding="utf-8")))
