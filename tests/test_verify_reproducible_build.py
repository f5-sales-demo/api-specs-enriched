from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import generate_api_viewer
from scripts.release import build_release_tree, verify_reproducible_build


def _write_release_tree(root: Path, *, domain_payload: bytes = b'{"value":1}\n') -> None:
    specs = root / "docs" / "specifications" / "api"
    api_reference = root / "docs" / "api-reference"
    release = root / "release"
    specs.mkdir(parents=True)
    api_reference.mkdir(parents=True)
    release.mkdir(parents=True)
    (root / ".github_release").write_text(
        json.dumps(
            {
                "version": "2026.08.02-1",
                "tag_name": "v2026.08.02-1",
                "published_at": "2026-08-02T00:00:00Z",
                "asset_name": "api-specs-v2026.08.02-1.zip",
                "asset_size": 1,
                "asset_digest": "sha256:" + "0" * 64,
            },
            indent=2,
        )
        + "\n"
    )
    (specs / "domain.json").write_bytes(domain_payload)
    (api_reference / "index.mdx").write_text("# API Reference\n", encoding="utf-8")
    (root / "docs" / "openapi-specs-config.json").write_text("[]\n", encoding="utf-8")
    (release / "api-catalog.json").write_text('{"version":"1.2.3"}\n', encoding="utf-8")


def test_release_manifest_is_canonical_and_covers_every_publishable_byte(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    _write_release_tree(root)

    manifest = verify_reproducible_build.release_manifest(root)

    assert manifest["schema_version"] == 1
    assert manifest["file_count"] == 5
    assert [entry["path"] for entry in manifest["files"]] == [
        ".github_release",
        "docs/api-reference/index.mdx",
        "docs/openapi-specs-config.json",
        "docs/specifications/api/domain.json",
        "release/api-catalog.json",
    ]
    assert all(set(entry) == {"path", "sha256", "size"} for entry in manifest["files"])


def test_release_manifest_rejects_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    _write_release_tree(root)
    (root / "docs" / "api-reference" / "linked.mdx").symlink_to(
        root / "docs" / "api-reference" / "index.mdx"
    )

    with pytest.raises(verify_reproducible_build.ReproducibilityError, match="symlink"):
        verify_reproducible_build.release_manifest(root)


def test_compare_release_builds_reports_exact_byte_drift(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_release_tree(first)
    _write_release_tree(second, domain_payload=b'{"value":2}\n')

    with pytest.raises(
        verify_reproducible_build.ReproducibilityError,
        match=r"docs/specifications/api/domain\.json",
    ):
        verify_reproducible_build.compare_release_builds(first, second)


def test_exact_committed_candidate_match_passes(tmp_path: Path) -> None:
    committed = tmp_path / "committed"
    candidate = tmp_path / "candidate"
    _write_release_tree(committed)
    _write_release_tree(candidate)

    manifest = verify_reproducible_build.compare_committed_candidate(committed, candidate)

    assert manifest["file_count"] == 5


@pytest.mark.parametrize(
    ("mutation", "expected_path"),
    [
        ("generated-byte", r"docs/specifications/api/domain\.json"),
        ("stale-receipt", r"\.github_release"),
    ],
)
def test_arbitrary_or_stale_committed_release_bytes_are_rejected(
    tmp_path: Path, mutation: str, expected_path: str
) -> None:
    committed = tmp_path / "committed"
    candidate = tmp_path / "candidate"
    _write_release_tree(committed)
    _write_release_tree(candidate)
    if mutation == "generated-byte":
        (committed / "docs/specifications/api/domain.json").write_text('{"hand-edited":true}\n')
    else:
        receipt = json.loads((committed / ".github_release").read_text())
        receipt["asset_digest"] = "sha256:" + "f" * 64
        (committed / ".github_release").write_text(json.dumps(receipt, indent=2) + "\n")

    with pytest.raises(
        verify_reproducible_build.ReproducibilityError,
        match=expected_path,
    ):
        verify_reproducible_build.compare_committed_candidate(committed, candidate)


def test_build_commands_exercise_the_production_biome_formatter(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    commands = verify_reproducible_build.build_commands(
        root=root,
        input_dir=Path("specs/original"),
        version="1.2.3",
        python="python",
        biome="biome",
    )

    assert commands == (
        (
            "python",
            "-m",
            "scripts.release.build_release_tree",
            "--version",
            "1.2.3",
            "--root",
            str(root),
            "--input-dir",
            "specs/original",
            "--biome",
            "biome",
        ),
    )
    canonical = build_release_tree.canonical_commands(
        root=root,
        input_dir=Path("specs/original"),
        version="1.2.3",
        python="python",
        biome="biome",
    )
    assert canonical[-1] == (
        "biome",
        "format",
        "--write",
        str(root / "docs" / "specifications" / "api"),
        str(root / "docs" / "api-reference"),
        str(root / "docs" / "openapi-specs-config.json"),
    )
    assert all(
        "API_SPECS_SKIP_BIOME" not in argument for command in canonical for argument in command
    )


def test_two_distinct_python_environments_are_required(tmp_path: Path) -> None:
    with pytest.raises(
        verify_reproducible_build.ReproducibilityError,
        match="distinct fresh Python environments",
    ):
        verify_reproducible_build.require_isolated_python_environments(
            Path(sys.executable),
            Path(sys.executable),
        )


def test_cli_requires_both_isolated_python_environments(tmp_path: Path) -> None:
    common = [
        "--version",
        "1.2.3",
        "--input-dir",
        str(tmp_path / "input"),
        "--first-root",
        str(tmp_path / "first"),
        "--second-root",
        str(tmp_path / "second"),
        "--manifest",
        str(tmp_path / "manifest.json"),
    ]

    with pytest.raises(SystemExit):
        verify_reproducible_build.parse_args(common)

    parsed = verify_reproducible_build.parse_args(
        [
            *common,
            "--first-python",
            str(tmp_path / "env-1" / "bin" / "python"),
            "--second-python",
            str(tmp_path / "env-2" / "bin" / "python"),
        ]
    )
    assert parsed.first_python != parsed.second_python


def test_generate_api_viewer_writes_only_explicit_candidate_paths(tmp_path: Path) -> None:
    specs = tmp_path / "specifications"
    api_reference = tmp_path / "api-reference"
    openapi_config = tmp_path / "openapi-specs-config.json"
    specs.mkdir()
    (specs / "index.json").write_text(
        json.dumps(
            {
                "specifications": [
                    {
                        "domain": "fixture",
                        "title": "Fixture",
                        "path_count": 1,
                        "schema_count": 0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (specs / "fixture.json").write_text('{"paths":{}}\n', encoding="utf-8")

    result = generate_api_viewer.main(
        [
            "--spec-dir",
            str(specs),
            "--mdx-dir",
            str(api_reference),
            "--openapi-config",
            str(openapi_config),
        ]
    )

    assert result == 0
    index = (api_reference / "index.mdx").read_text()
    assert "## Other\n\n<CardGrid>" in index
    assert (api_reference / "fixture-api.mdx").is_file()
    assert openapi_config.is_file()
