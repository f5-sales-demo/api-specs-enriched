# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Repository-level clean-break contracts for the production release path."""

import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import download

ROOT = Path(__file__).resolve().parents[1]


def test_canonical_grammar_transform_has_no_optional_language_tool_fallback() -> None:
    grammar = (ROOT / "scripts/utils/grammar.py").read_text()
    project = (ROOT / "pyproject.toml").read_text()

    assert "language_tool" not in grammar
    assert "language-tool-python" not in project
    assert "use_language_tool" not in grammar


def test_download_source_is_only_the_immutable_api_specs_release() -> None:
    config = yaml.safe_load((ROOT / "config/download.yaml").read_text())

    assert config["source"]["type"] == "github_release"
    assert config["source"]["repository"] == {
        "owner": "f5-sales-demo",
        "name": "api-specs",
    }
    assert "url" not in config["source"]
    assert "etag_file" not in config["source"]


def test_download_cli_has_no_mutable_latest_comparison_compatibility_mode() -> None:
    source = Path(download.__file__).read_text()

    assert "--check-only" not in source
    assert "check_for_updates" not in source
    assert not hasattr(download, "check_for_updates")


def test_explicit_download_config_cannot_change_or_inherit_the_source(tmp_path: Path) -> None:
    canonical = yaml.safe_load((ROOT / "config/download.yaml").read_text())
    for name, candidate in (
        ("missing", {"paths": canonical["paths"], "extraction": canonical["extraction"]}),
        (
            "alternate",
            {
                **canonical,
                "source": {
                    **canonical["source"],
                    "repository": {"owner": "f5-sales-demo", "name": "api-specs-enriched"},
                },
            },
        ),
    ):
        path = tmp_path / f"{name}.yaml"
        path.write_text(yaml.safe_dump(candidate))
        with pytest.raises((TypeError, ValueError), match="source"):
            download.load_config(path)

    assert "DEFAULT_CONFIG" not in Path(download.__file__).read_text()


def test_enrichment_has_no_second_mutable_source_contract() -> None:
    config = yaml.safe_load((ROOT / "config/enrichment.yaml").read_text())
    paths = yaml.safe_load((ROOT / "config/paths.yaml").read_text())

    assert "source" not in config
    assert "etag_file" not in paths["project"]
    assert not (ROOT / ".etag").exists()
    repository_text = "\n".join(
        path.read_text()
        for path in (
            ROOT / "config/enrichment.yaml",
            ROOT / "config/paths.yaml",
            ROOT / "config/extension_registry.yaml",
            ROOT / "scripts/utils/server_variables.py",
        )
    )
    assert "etag" not in repository_text.lower()


def test_api_reference_has_one_canonical_generated_source() -> None:
    canonical = ROOT / "docs" / "api-reference"

    assert canonical.is_dir()
    assert any(canonical.glob("*.mdx"))
    assert not (ROOT / "docs" / "en" / "api-reference").exists()


def test_production_workflow_cannot_consume_discovery_snapshots() -> None:
    workflow = (ROOT / ".github/workflows/sync-and-enrich.yml").read_text()

    assert "specs/discovered" not in workflow
    assert "DISCOVERY_ENRICHMENT_ENABLED" not in workflow
    assert "check-discovery" not in workflow
    assert "discovery-summary" not in workflow


def test_discovery_snapshots_cannot_be_committed_by_a_make_target() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "push-discovery" not in makefile
    assert "discover-and-push" not in makefile
    assert "enrich-with-discovery" not in makefile
    assert "pipeline-enriched" not in makefile
    assert "build-enriched" not in makefile
    assert "git add specs/discovered" not in makefile
    result = subprocess.run(
        [
            "git",
            "check-ignore",
            "--no-index",
            "--quiet",
            "specs/discovered/openapi.json",
        ],
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0
