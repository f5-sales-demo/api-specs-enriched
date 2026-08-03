# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Artifact-integrity and atomicity tests for upstream release downloads."""

from __future__ import annotations

import hashlib
import json
import zipfile
from typing import TYPE_CHECKING

import pytest

from scripts import download
from scripts.utils.source_graph_validator import select_source_specs

if TYPE_CHECKING:
    from pathlib import Path


def _write_archive(path: Path, members: list[tuple[str, bytes]]) -> bytes:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members:
            archive.writestr(name, payload)
    return path.read_bytes()


def _release(archive_bytes: bytes) -> dict:
    digest = "sha256:" + hashlib.sha256(archive_bytes).hexdigest()
    asset = {
        "name": "api-specs-v2026.07.30-16.zip",
        "size": len(archive_bytes),
        "digest": digest,
        "browser_download_url": "https://github.com/example/releases/asset.zip",
    }
    return {
        "tag_name": "v2026.07.30-16",
        "published_at": "2026-08-01T15:41:03Z",
        "assets": [asset],
        "immutable": True,
    }


def _receipt(release: dict) -> dict:
    asset = release["assets"][0]
    return {
        "version": release["tag_name"].removeprefix("v"),
        "tag_name": release["tag_name"],
        "published_at": release["published_at"],
        "asset_name": asset["name"],
        "asset_size": asset["size"],
        "asset_digest": asset["digest"],
    }


def _config(tmp_path: Path) -> dict:
    return {
        "source": {
            "repository": {"owner": "owner", "name": "repo"},
            "asset_pattern": "api-specs-v*.zip",
        },
        "paths": {
            "version_file": str(tmp_path / ".github_release"),
            "original": str(tmp_path / "specs" / "original"),
        },
        "extraction": {
            "include_patterns": ["domains/*.json"],
            "exclude_patterns": [],
            "max_file_size": 1024 * 1024,
            "max_total_size": 10 * 1024 * 1024,
            "max_compression_ratio": 100,
            "max_file_count": 100,
        },
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (field, value)
        for field in ("max_file_size", "max_total_size", "max_compression_ratio", "max_file_count")
        for value in (True, 0, -1, 1.5, "1")
    ],
)
def test_download_config_rejects_invalid_integer_limits(
    tmp_path: Path, field: str, value: object
) -> None:
    config = _config(tmp_path)
    config["extraction"][field] = value

    with pytest.raises(ValueError, match=rf"extraction\.{field} must be a positive integer"):
        download._validate_download_config(config, config["source"])


def _install_download_fakes(
    monkeypatch: pytest.MonkeyPatch,
    archive_bytes: bytes,
    release: dict,
) -> None:
    monkeypatch.setattr(
        download, "load_release_receipt", lambda *_args, **_kwargs: _receipt(release)
    )
    monkeypatch.setattr(
        download,
        "resolve_release_receipt",
        lambda *_args, **_kwargs: (release, release["assets"][0]),
    )

    def fake_download(_url: str, output_path: Path, **_kwargs: object) -> bool:
        output_path.write_bytes(archive_bytes)
        return True

    monkeypatch.setattr(download, "download_release_asset", fake_download)


def _write_old_tree(tmp_path: Path) -> tuple[Path, Path]:
    output = tmp_path / "specs" / "original"
    output.mkdir(parents=True)
    (output / "old.json").write_text('{"old": true}\n')
    download.generate_manifest(
        output,
        ["old.json"],
        {
            "version": "2026.07.30-15",
            "tag_name": "v2026.07.30-15",
            "published_at": "2026-08-01T08:21:47Z",
            "asset_name": "api-specs-v2026.07.30-15.zip",
            "asset_size": 1,
            "asset_digest": "sha256:" + "0" * 64,
        },
    )
    metadata = tmp_path / ".github_release"
    metadata.write_bytes(b"old release metadata\n")
    return output, metadata


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_digest_mismatch_fails_before_extraction_or_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_bytes = b"actual"
    release = _release(b"expected")
    monkeypatch.setattr(
        download, "load_release_receipt", lambda *_args, **_kwargs: _receipt(release)
    )
    monkeypatch.setattr(
        download,
        "resolve_release_receipt",
        lambda *_args, **_kwargs: (release, release["assets"][0]),
    )

    def fake_download(_url: str, output_path: Path, **_kwargs: object) -> bool:
        output_path.write_bytes(archive_bytes)
        return True

    monkeypatch.setattr(download, "download_release_asset", fake_download)
    extraction_called = False

    def fake_extract(*_args: object, **_kwargs: object) -> list[str]:
        nonlocal extraction_called
        extraction_called = True
        return []

    monkeypatch.setattr(download, "extract_zip", fake_extract)

    success, returned_release = download.download_from_github_release(_config(tmp_path), force=True)

    assert success is False
    assert returned_release is None
    assert extraction_called is False
    assert not (tmp_path / ".github_release").exists()


def test_downloaded_size_mismatch_fails_before_extraction_or_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_bytes = b"actual archive bytes"
    release = _release(archive_bytes)
    release["assets"][0]["size"] = len(archive_bytes) + 1
    _install_download_fakes(monkeypatch, archive_bytes, release)
    extraction_called = False

    def fake_extract(*_args: object, **_kwargs: object) -> list[str]:
        nonlocal extraction_called
        extraction_called = True
        return []

    monkeypatch.setattr(download, "extract_zip", fake_extract)

    success, returned_release = download.download_from_github_release(_config(tmp_path), force=True)

    assert success is False
    assert returned_release is None
    assert extraction_called is False
    assert not (tmp_path / ".github_release").exists()


def test_cache_reuse_requires_tree_manifest_to_match_selected_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "specs" / "original"
    output.mkdir(parents=True)
    (output / "old.json").write_text('{"old": true}\n')
    old_receipt = {
        "version": "2026.07.30-15",
        "tag_name": "v2026.07.30-15",
        "published_at": "2026-08-01T08:21:47Z",
        "asset_name": "api-specs-v2026.07.30-15.zip",
        "asset_size": 1,
        "asset_digest": "sha256:" + "0" * 64,
    }
    download.generate_manifest(output, ["old.json"], old_receipt)

    release = _release(b"new release")
    selected_receipt = _receipt(release)
    metadata = tmp_path / ".github_release"
    metadata.write_text(json.dumps(selected_receipt))
    monkeypatch.setattr(
        download,
        "resolve_release_receipt",
        lambda *_args, **_kwargs: (release, release["assets"][0]),
    )
    download_attempted = False

    def fail_download(*_args: object, **_kwargs: object) -> bool:
        nonlocal download_attempted
        download_attempted = True
        return False

    monkeypatch.setattr(download, "download_release_asset", fail_download)

    success, returned_release = download.download_from_github_release(_config(tmp_path))

    assert success is False
    assert returned_release is None
    assert download_attempted is True
    assert json.loads((output / "manifest.json").read_text())["release_receipt"] == old_receipt


def test_valid_candidate_promotes_tree_before_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output, metadata = _write_old_tree(tmp_path)
    archive_bytes = _write_archive(
        tmp_path / "release.zip",
        [("domains/new.json", b'{"openapi":"3.0.3"}\n')],
    )
    release = _release(archive_bytes)
    _install_download_fakes(monkeypatch, archive_bytes, release)

    replacements: list[Path] = []
    real_replace = download._replace_path

    def recording_replace(source: Path, destination: Path) -> None:
        replacements.append(destination)
        real_replace(source, destination)

    monkeypatch.setattr(download, "_replace_path", recording_replace)

    success, returned_release = download.download_from_github_release(_config(tmp_path), force=True)

    assert success is True
    assert returned_release == release
    assert sorted(path.name for path in output.iterdir()) == ["manifest.json", "new.json"]
    assert not (output / "old.json").exists()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["release_receipt"] == _receipt(release)
    assert manifest["files"] == [
        {
            "name": "new.json",
            "sha256": "sha256:" + hashlib.sha256(b'{"openapi":"3.0.3"}\n').hexdigest(),
        }
    ]
    assert json.loads(metadata.read_text())["asset_digest"] == release["assets"][0]["digest"]
    assert replacements.index(output) < replacements.index(metadata)


@pytest.mark.parametrize(
    ("members", "expected_message"),
    [
        (
            [
                ("domains/good.json", b'{"good":true}'),
                ("domains/../evil.json", b'{"evil":true}'),
            ],
            "Unsafe included archive member",
        ),
        ([("domains/bad.json", b"{not-json")], "not valid JSON"),
    ],
)
def test_invalid_included_member_preserves_old_tree_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    members: list[tuple[str, bytes]],
    expected_message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output, metadata = _write_old_tree(tmp_path)
    old_tree = _tree_bytes(output)
    old_metadata = metadata.read_bytes()
    archive_bytes = _write_archive(tmp_path / "release.zip", members)
    release = _release(archive_bytes)
    _install_download_fakes(monkeypatch, archive_bytes, release)

    success, returned_release = download.download_from_github_release(_config(tmp_path), force=True)

    assert success is False
    assert returned_release is None
    assert expected_message in " ".join(capsys.readouterr().out.split())
    assert _tree_bytes(output) == old_tree
    assert metadata.read_bytes() == old_metadata


def test_unknown_operator_file_blocks_replacement_before_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output, metadata = _write_old_tree(tmp_path)
    operator_file = output / "operator-notes.txt"
    operator_file.write_text("do not delete\n")
    old_tree = _tree_bytes(output)
    old_metadata = metadata.read_bytes()
    selection_called = False

    def fake_selection(*_args: object, **_kwargs: object) -> tuple[dict, dict]:
        nonlocal selection_called
        selection_called = True
        raise AssertionError("release selection must happen after destination ownership validation")

    monkeypatch.setattr(download, "resolve_release_receipt", fake_selection)

    success, returned_release = download.download_from_github_release(_config(tmp_path), force=True)

    assert success is False
    assert returned_release is None
    assert selection_called is False
    assert _tree_bytes(output) == old_tree
    assert operator_file.read_text() == "do not delete\n"
    assert metadata.read_bytes() == old_metadata


def test_metadata_persist_failure_rolls_back_promoted_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output, metadata = _write_old_tree(tmp_path)
    old_tree = _tree_bytes(output)
    old_metadata = metadata.read_bytes()
    archive_bytes = _write_archive(
        tmp_path / "release.zip",
        [("domains/new.json", b'{"openapi":"3.0.3"}\n')],
    )
    release = _release(archive_bytes)
    _install_download_fakes(monkeypatch, archive_bytes, release)
    real_replace = download._replace_path

    def fail_metadata_replace(source: Path, destination: Path) -> None:
        if destination == metadata:
            raise OSError("injected metadata failure")
        real_replace(source, destination)

    monkeypatch.setattr(download, "_replace_path", fail_metadata_replace)

    success, returned_release = download.download_from_github_release(_config(tmp_path), force=True)

    assert success is False
    assert returned_release is None
    assert _tree_bytes(output) == old_tree
    assert metadata.read_bytes() == old_metadata


def test_manifest_is_deterministic_and_has_no_wall_clock_fields(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    for output in (first, second):
        (output / "a.json").write_text('{"openapi":"3.0.3","name":"a"}\n')
        (output / "b.json").write_text('{"openapi":"3.0.3","name":"b"}\n')
        download.generate_manifest(
            output,
            ["b.json", "a.json"],
            {
                "version": "2026.07.30-16",
                "tag_name": "v2026.07.30-16",
                "published_at": "2026-08-01T15:41:03Z",
                "asset_name": "api-specs-v2026.07.30-16.zip",
                "asset_size": 1,
                "asset_digest": "sha256:" + "0" * 64,
            },
        )

    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
    manifest = json.loads((first / "manifest.json").read_text())
    assert set(manifest) == {
        "files",
        "release_receipt",
    }
    assert [entry["name"] for entry in manifest["files"]] == ["a.json", "b.json"]
    assert all(entry["sha256"].startswith("sha256:") for entry in manifest["files"])
    assert "timestamp" not in manifest
    assert "downloaded_at" not in manifest


def test_generated_manifest_is_accepted_by_source_selection(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name in ("b.json", "a.json"):
        (source / name).write_text('{"openapi":"3.0.3"}\n')
    download.generate_manifest(
        source,
        ["b.json", "a.json"],
        {
            "version": "2026.07.30-16",
            "tag_name": "v2026.07.30-16",
            "published_at": "2026-08-01T15:41:03Z",
            "asset_name": "api-specs-v2026.07.30-16.zip",
            "asset_size": 1,
            "asset_digest": "sha256:" + "0" * 64,
        },
    )

    selection = select_source_specs(source)

    assert selection.names == ("a.json", "b.json")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-key", "must contain exactly"),
        ("extra-key", "must contain exactly"),
        ("field-type", "asset_size"),
        ("file-hash", "SHA-256"),
        ("membership", "missing|incomplete"),
    ],
)
def test_staging_and_selection_reject_the_same_manifest_contract_defects(
    tmp_path: Path, mutation: str, message: str
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "present.json").write_text('{"openapi":"3.0.3"}\n')
    download.generate_manifest(
        staging,
        ["present.json"],
        {
            "version": "2026.07.30-16",
            "tag_name": "v2026.07.30-16",
            "published_at": "2026-08-01T15:41:03Z",
            "asset_name": "api-specs-v2026.07.30-16.zip",
            "asset_size": 1,
            "asset_digest": "sha256:" + "0" * 64,
        },
    )
    manifest_path = staging / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if mutation == "missing-key":
        manifest.pop("release_receipt")
    elif mutation == "extra-key":
        manifest["timestamp"] = "2026-08-01T16:00:00Z"
    elif mutation == "field-type":
        manifest["release_receipt"]["asset_size"] = "1"
    elif mutation == "file-hash":
        manifest["files"][0]["sha256"] = "not-a-digest"
    else:
        manifest["files"][0]["name"] = "other.json"
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match=message):
        download.validate_staged_tree(staging, ["present.json"])
    with pytest.raises(ValueError, match=message):
        select_source_specs(staging)


def test_staged_tree_validation_rejects_a_manifest_with_a_missing_file(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "present.json").write_text('{"openapi":"3.0.3"}\n')
    (staging / "manifest.json").write_text(
        json.dumps(
            {
                "release_receipt": {
                    "version": "2026.07.30-16",
                    "tag_name": "v2026.07.30-16",
                    "published_at": "2026-08-01T15:41:03Z",
                    "asset_name": "api-specs-v2026.07.30-16.zip",
                    "asset_size": 1,
                    "asset_digest": "sha256:" + "0" * 64,
                },
                "files": [
                    {"name": "missing.json", "sha256": "sha256:" + "0" * 64},
                    {"name": "present.json", "sha256": "sha256:" + "0" * 64},
                ],
            }
        )
    )

    with pytest.raises(ValueError, match="incomplete"):
        download.validate_staged_tree(staging, ["missing.json", "present.json"])
