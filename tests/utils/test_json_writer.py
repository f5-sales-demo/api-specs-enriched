# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for scripts/utils/json_writer.py.

Covers the Biome-gated JSON writer that landed via PR #138. The writer
is load-bearing in release CI: it is the single sanctioned producer of
generator output under ``docs/specifications/api/`` and
``docs/api-reference/``, and must match what Super-Linter's
BIOME_FORMAT check would produce. Writes to other destinations must
succeed even when Biome is absent — this suite locks down both halves.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from scripts.utils import json_writer
from scripts.utils.json_writer import (
    BiomeFormatError,
    BiomeNotFoundError,
    _format_with_biome,
    _is_maxsize_only,
    _is_publishing_path,
    write_json_file,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestIsPublishingPath:
    """_is_publishing_path gates Biome formatting on resolved path."""

    def test_matches_docs_specifications_api(self, tmp_path: Path) -> None:
        target = tmp_path / "docs" / "specifications" / "api" / "foo.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        assert _is_publishing_path(target) is True

    def test_matches_docs_api_reference(self, tmp_path: Path) -> None:
        target = tmp_path / "docs" / "api-reference" / "foo.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        assert _is_publishing_path(target) is True

    def test_rejects_tmp_path(self, tmp_path: Path) -> None:
        target = tmp_path / "foo.json"
        target.write_text("{}")
        assert _is_publishing_path(target) is False

    def test_rejects_reports_path(self, tmp_path: Path) -> None:
        target = tmp_path / "reports" / "foo.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        assert _is_publishing_path(target) is False

    def test_rejects_docs_toplevel(self, tmp_path: Path) -> None:
        target = tmp_path / "docs" / "index.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        assert _is_publishing_path(target) is False

    def test_accepts_nested_subdirs_of_publishing_root(self, tmp_path: Path) -> None:
        target = tmp_path / "docs" / "specifications" / "api" / "viewer" / "x.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        assert _is_publishing_path(target) is True


def _fake_completed(stdout: str, stderr: str, returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["biome", "format", "--write", "path"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class TestIsMaxsizeOnly:
    """Truth table over (size_warning, no_files_processed, no_other_diff)."""

    @pytest.mark.parametrize(
        ("stdout", "stderr", "expected"),
        [
            # All three conditions met → True
            (
                "",
                "file exceeds the configured maximum size\nNo files were processed",
                True,
            ),
            # size_warning missing → False
            ("", "No files were processed", False),
            # no_files_processed missing → False
            ("", "exceeds the configured maximum size", False),
            # "Formatter would have printed" means real diff → False
            (
                "Formatter would have printed the following content:\n{}",
                "exceeds the configured maximum size\nNo files were processed",
                False,
            ),
            # No relevant stderr → False
            ("", "something else", False),
        ],
    )
    def test_matrix(self, stdout: str, stderr: str, expected: bool) -> None:
        result = _fake_completed(stdout, stderr, returncode=1)
        assert _is_maxsize_only(result) is expected


class TestFormatWithBiome:
    """_format_with_biome gates on path, env var, PATH availability, and exit."""

    def test_skips_atomic_pipeline_candidate_that_publishes_to_disabled_tree(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = (
            tmp_path
            / "docs"
            / "specifications"
            / ".api-candidate-test"
            / "docs"
            / "specifications"
            / "api"
            / "x.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.delenv("API_SPECS_SKIP_BIOME", raising=False)

        with patch.object(subprocess, "run") as mock_run:
            write_json_file({"version": "7.0.3", "resources": {}}, target)

        mock_run.assert_not_called()

    def test_formats_self_describing_artifact_inside_atomic_pipeline_candidate(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = (
            tmp_path
            / "docs"
            / "specifications"
            / ".api-candidate-test"
            / "docs"
            / "specifications"
            / "api"
            / "validation.json"
        )
        monkeypatch.delenv("API_SPECS_SKIP_BIOME", raising=False)

        with (
            patch.object(json_writer.shutil, "which", return_value="/usr/bin/biome"),
            patch.object(
                subprocess,
                "run",
                return_value=_fake_completed(stdout="", stderr="", returncode=0),
            ) as mock_run,
        ):
            write_json_file({"$schema": "example", "version": "2.1.0"}, target)

        mock_run.assert_called_once()

    def test_skips_non_publishing_path(self, tmp_path: Path) -> None:
        target = tmp_path / "out.json"
        target.write_text("{}")
        with patch.object(subprocess, "run") as mock_run:
            _format_with_biome(target)
        mock_run.assert_not_called()

    def test_skips_when_env_opt_out_set(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "docs" / "specifications" / "api" / "x.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        monkeypatch.setenv("API_SPECS_SKIP_BIOME", "1")
        with patch.object(subprocess, "run") as mock_run:
            _format_with_biome(target)
        mock_run.assert_not_called()

    def test_raises_biome_not_found_when_missing_on_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "docs" / "specifications" / "api" / "x.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        monkeypatch.delenv("API_SPECS_SKIP_BIOME", raising=False)
        with (
            patch.object(json_writer.shutil, "which", return_value=None),
            pytest.raises(BiomeNotFoundError, match="biome not found on PATH"),
        ):
            _format_with_biome(target)

    def test_passes_through_on_biome_success(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "docs" / "specifications" / "api" / "x.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        monkeypatch.delenv("API_SPECS_SKIP_BIOME", raising=False)
        with (
            patch.object(json_writer.shutil, "which", return_value="/usr/bin/biome"),
            patch.object(
                subprocess,
                "run",
                return_value=_fake_completed(stdout="", stderr="", returncode=0),
            ) as mock_run,
        ):
            _format_with_biome(target)
        mock_run.assert_called_once()
        assert mock_run.call_args.args[0] == ["biome", "format", "--write", str(target)]

    def test_raises_biome_format_error_on_real_diff(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "docs" / "specifications" / "api" / "x.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        monkeypatch.delenv("API_SPECS_SKIP_BIOME", raising=False)
        diff_stdout = "Formatter would have printed the following content:\n{}"
        with (
            patch.object(json_writer.shutil, "which", return_value="/usr/bin/biome"),
            patch.object(
                subprocess,
                "run",
                return_value=_fake_completed(stdout=diff_stdout, stderr="", returncode=1),
            ),
            pytest.raises(BiomeFormatError, match="biome format failed"),
        ):
            _format_with_biome(target)

    def test_swallows_maxsize_only_non_zero_exit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "docs" / "specifications" / "api" / "huge.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}")
        monkeypatch.delenv("API_SPECS_SKIP_BIOME", raising=False)
        stderr = "file exceeds the configured maximum size\nNo files were processed"
        with (
            patch.object(json_writer.shutil, "which", return_value="/usr/bin/biome"),
            patch.object(
                subprocess,
                "run",
                return_value=_fake_completed(stdout="", stderr=stderr, returncode=1),
            ),
        ):
            # Must NOT raise
            _format_with_biome(target)

        captured = capsys.readouterr()
        assert "files.maxSize" in captured.err


class TestWriteJsonFile:
    """End-to-end tests for write_json_file."""

    def test_writes_json_with_trailing_newline_outside_publishing_path(
        self,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "out.json"
        data: dict[str, Any] = {"a": 1, "b": [2, 3]}
        with patch.object(subprocess, "run") as mock_run:
            write_json_file(data, target)
        assert target.read_text(encoding="utf-8") == json.dumps(data, indent=2) + "\n"
        mock_run.assert_not_called()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "deep" / "out.json"
        write_json_file({"x": 1}, target)
        assert target.exists()
        assert target.parent.is_dir()

    def test_respects_indent_and_sort_keys(self, tmp_path: Path) -> None:
        target = tmp_path / "out.json"
        write_json_file({"b": 2, "a": 1}, target, indent=4, sort_keys=True)
        content = target.read_text(encoding="utf-8")
        # sort_keys=True → "a" comes before "b"
        assert content.index('"a"') < content.index('"b"')
        # indent=4 → four-space indent visible
        assert '    "a": 1' in content

    def test_invokes_biome_when_destination_is_publishing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "docs" / "specifications" / "api" / "x.json"
        monkeypatch.delenv("API_SPECS_SKIP_BIOME", raising=False)
        with (
            patch.object(json_writer.shutil, "which", return_value="/usr/bin/biome"),
            patch.object(
                subprocess,
                "run",
                return_value=_fake_completed(stdout="", stderr="", returncode=0),
            ) as mock_run,
        ):
            write_json_file({"k": "v"}, target)
        mock_run.assert_called_once()
        args_list = mock_run.call_args.args[0]
        assert args_list[:3] == ["biome", "format", "--write"]
        assert args_list[3] == str(target)

    def test_publishing_path_without_biome_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "docs" / "api-reference" / "x.json"
        monkeypatch.delenv("API_SPECS_SKIP_BIOME", raising=False)
        with (
            patch.object(json_writer.shutil, "which", return_value=None),
            pytest.raises(BiomeNotFoundError),
        ):
            write_json_file({}, target)

    def test_publishing_path_with_skip_env_writes_plainly(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "docs" / "specifications" / "api" / "x.json"
        monkeypatch.setenv("API_SPECS_SKIP_BIOME", "1")
        with patch.object(subprocess, "run") as mock_run:
            write_json_file({"ok": True}, target)
        assert target.exists()
        mock_run.assert_not_called()
