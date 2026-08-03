# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Installed CLI defaults must not depend on a repository checkout CWD."""

import sys
from pathlib import Path

import pytest

from scripts import download, enrich, lint, normalize, pipeline, validate
from scripts.package_config import load_packaged_yaml


def test_all_cli_defaults_load_packaged_configuration_outside_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    download_config = download.load_config()
    enrichment_config = enrich.load_config()
    lint_config = lint.load_config()
    normalization_config = normalize.load_config()
    pipeline_config = pipeline.load_config()
    validation_config = validate.load_config()

    assert download_config["source"] == load_packaged_yaml("download.yaml")["source"]
    assert enrichment_config["branding"] == load_packaged_yaml("enrichment.yaml")["branding"]
    assert lint_config == load_packaged_yaml("lint.yaml")
    assert normalization_config == load_packaged_yaml("normalization.yaml")
    assert pipeline_config["branding"] == load_packaged_yaml("enrichment.yaml")["branding"]
    assert (
        pipeline_config["normalization"]
        == load_packaged_yaml("normalization.yaml")["normalization"]
    )
    assert validation_config["reporting"] == load_packaged_yaml("validation.yaml")["reporting"]


def test_packaged_default_output_paths_remain_cwd_relative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    download_output = Path(download.load_config()["paths"]["original"])
    enrichment_output = Path(enrich.load_config()["paths"]["enriched"])
    lint_output = Path(lint.load_config()["paths"]["normalized"])
    normalization_output = Path(normalize.load_config()["paths"]["normalized"])
    pipeline_output = Path(pipeline.load_config()["paths"]["enriched"])

    for output in (
        download_output,
        enrichment_output,
        lint_output,
        normalization_output,
        pipeline_output,
    ):
        assert not output.is_absolute()
        assert output.resolve().is_relative_to(tmp_path)


@pytest.mark.parametrize(
    ("loader", "message"),
    [
        (download.load_config, "download configuration not found"),
        (enrich.load_config, "enrichment configuration not found"),
        (lint.load_config, "lint configuration not found"),
        (normalize.load_config, "normalization configuration not found"),
        (pipeline.load_config, "pipeline configuration not found"),
        (validate.load_config, "validation configuration not found"),
    ],
)
def test_explicit_missing_cli_config_fails_closed(
    loader: object,
    message: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError, match=message):
        loader(tmp_path / "missing.yaml")  # type: ignore[operator]


@pytest.mark.parametrize(
    ("loader", "section", "field", "value"),
    [
        (enrich.load_config, "processing", "parallel_workers", 7),
        (lint.load_config, "linting", "max_errors_per_file", 7),
        (normalize.load_config, "processing", "parallel_workers", 7),
        (pipeline.load_config, "processing", "parallel_workers", 7),
        (validate.load_config, "concurrency", "workers", 7),
    ],
)
def test_explicit_config_is_a_validated_overlay_on_packaged_defaults(
    loader: object,
    section: str,
    field: str,
    value: int,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "overlay.yaml"
    config_path.write_text(f"{section}:\n  {field}: {value}\n", encoding="utf-8")

    config = loader(config_path)  # type: ignore[operator]

    assert config[section][field] == value
    assert len(config) > 1


@pytest.mark.parametrize(
    "loader",
    [
        enrich.load_config,
        lint.load_config,
        normalize.load_config,
        pipeline.load_config,
        validate.load_config,
    ],
)
def test_explicit_config_rejects_unknown_controls(
    loader: object,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "overlay.yaml"
    config_path.write_text("unknown_control: true\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"unsupported|unknown"):
        loader(config_path)  # type: ignore[operator]


def test_default_configs_are_fresh_copies() -> None:
    first = enrich.load_config()
    first["paths"]["enriched"] = "mutated"

    assert enrich.load_config()["paths"]["enriched"] != "mutated"


def test_pipeline_support_configs_load_outside_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    aliases = pipeline.load_operation_aliases()
    spectral = load_packaged_yaml("spectral.yaml")

    assert aliases
    assert spectral["extends"] == ["spectral:oas"]


def test_enrich_validate_only_does_not_require_raw_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "published"
    output_dir.mkdir()
    config = enrich.load_config()
    config["paths"] = {
        "original": str(tmp_path / "missing-raw-input"),
        "enriched": str(output_dir),
        "reports": str(tmp_path / "reports"),
    }
    monkeypatch.setattr(enrich, "load_config", lambda _path=None: config)
    monkeypatch.setattr(enrich, "source_spec_files", lambda _path: [output_dir / "one.json"])
    monkeypatch.setattr(enrich, "_validate_single_spec_file", lambda _path: (True, None))
    monkeypatch.setattr(sys, "argv", ["f5xc-enrich", "--validate-only"])

    assert enrich.main() == 0
