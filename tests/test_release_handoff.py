import json
import zipfile
from pathlib import Path

import pytest

from scripts.release_handoff import (
    HandoffError,
    apply_candidate,
    build_candidate_manifest,
    build_package_handoff,
    canonical_json,
    deterministic_archive,
    digest_bytes,
    main,
    stage_candidate,
    validate_candidate_manifest,
    validate_package_handoff,
)

COMMIT = "a" * 40
RELEASE_COMMIT = "b" * 40
DIGEST = "sha256:" + "c" * 64
FINGERPRINT = "sha256:" + "d" * 64


def candidate_tree(tmp_path: Path) -> tuple[Path, list[str]]:
    root = tmp_path / "root"
    (root / "docs/specifications/api").mkdir(parents=True)
    (root / "docs/specifications/api/openapi.json").write_text('{"openapi":"3.0.0"}\n')
    (root / "release").mkdir()
    (root / "release/api-catalog.json").write_text('{"version":"1.2.3"}\n')
    (root / "CHANGELOG.md").write_text("# Changelog\n")
    return root, ["docs/specifications/api", "release/api-catalog.json", "CHANGELOG.md"]


def candidate_manifest(tmp_path: Path) -> tuple[Path, Path, dict]:
    root, roots = candidate_tree(tmp_path)
    manifest = build_candidate_manifest(root, roots, COMMIT, DIGEST, "1.2.3", FINGERPRINT)
    stage = tmp_path / "stage"
    path = stage_candidate(root, stage, manifest)
    return stage, path, manifest


def test_candidate_manifest_is_deterministic_and_complete(tmp_path: Path) -> None:
    root, roots = candidate_tree(tmp_path)
    first = build_candidate_manifest(root, roots, COMMIT, DIGEST, "1.2.3", FINGERPRINT)
    second = build_candidate_manifest(
        root, list(reversed(roots)), COMMIT, DIGEST, "1.2.3", FINGERPRINT
    )
    assert canonical_json(first) == canonical_json(second)
    assert [item["path"] for item in first["files"]] == [
        "CHANGELOG.md",
        "docs/specifications/api/openapi.json",
        "release/api-catalog.json",
    ]
    validate_candidate_manifest(
        root,
        first,
        source_commit=COMMIT,
        upstream_digest=DIGEST,
        version="1.2.3",
        pipeline_fingerprint=FINGERPRINT,
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 2, "schema"),
        ("source_commit", "wrong", "commit"),
        ("upstream_digest", "wrong", "digest"),
        ("version", "v1.2.3", "version"),
        ("pipeline_fingerprint", "wrong", "fingerprint"),
    ],
)
def test_candidate_manifest_rejects_unsupported_identity(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    root, roots = candidate_tree(tmp_path)
    manifest = build_candidate_manifest(root, roots, COMMIT, DIGEST, "1.2.3", FINGERPRINT)
    manifest[field] = value
    with pytest.raises(HandoffError, match=message):
        validate_candidate_manifest(root, manifest)


def test_candidate_manifest_rejects_missing_unexpected_and_tampered_files(tmp_path: Path) -> None:
    root, roots = candidate_tree(tmp_path)
    manifest = build_candidate_manifest(root, roots, COMMIT, DIGEST, "1.2.3", FINGERPRINT)
    (root / "docs/specifications/api/openapi.json").write_text("tampered\n")
    with pytest.raises(HandoffError, match="missing, unexpected, or modified"):
        validate_candidate_manifest(root, manifest)
    (root / "docs/specifications/api/openapi.json").write_text('{"openapi":"3.0.0"}\n')
    (root / "docs/specifications/api/unexpected.json").write_text("{}\n")
    with pytest.raises(HandoffError, match="missing, unexpected, or modified"):
        validate_candidate_manifest(root, manifest)
    (root / "docs/specifications/api/unexpected.json").unlink()
    (root / "CHANGELOG.md").unlink()
    with pytest.raises(HandoffError, match=r"does not exist|missing, unexpected, or modified"):
        validate_candidate_manifest(root, manifest)


def test_candidate_apply_replaces_stale_generated_tree(tmp_path: Path) -> None:
    stage, manifest_path, manifest = candidate_manifest(tmp_path)
    destination = tmp_path / "destination"
    (destination / "docs/specifications/api").mkdir(parents=True)
    (destination / "docs/specifications/api/stale.json").write_text("stale\n")
    apply_candidate(stage, destination, manifest)
    assert not (destination / "docs/specifications/api/stale.json").exists()
    validate_candidate_manifest(destination, json.loads(manifest_path.read_text()))


def test_deterministic_archive_uses_sorted_entries_and_fixed_timestamp(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    (root / "z.txt").write_text("z")
    (root / "a.txt").write_text("a")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    assert deterministic_archive(root, first, 1_700_000_001) == deterministic_archive(
        root, second, 1_700_000_001
    )
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["a.txt", "z.txt"]
        assert len({item.date_time for item in archive.infolist()}) == 1


def package_handoff(tmp_path: Path) -> tuple[Path, Path, dict]:
    root = tmp_path / "payload"
    (root / ".handoff").mkdir(parents=True)
    candidate = root / ".handoff/candidate-manifest.json"
    candidate.write_bytes(canonical_json({"schema_version": 1}))
    (root / "release-package").mkdir()
    (root / "release-package/openapi.json").write_text("{}\n")
    archive = root / "bundle.zip"
    archive.write_bytes(b"zip")
    handoff = build_package_handoff(
        root,
        candidate,
        RELEASE_COMMIT,
        "release-payload-1-1",
        DIGEST,
        "bundle.zip",
        ["bundle.zip", "release-package/openapi.json"],
    )
    return root, candidate, handoff


def test_package_handoff_binds_all_identities_and_files(tmp_path: Path) -> None:
    root, candidate, handoff = package_handoff(tmp_path)
    validate_package_handoff(
        root, handoff, candidate, RELEASE_COMMIT, "release-payload-1-1", DIGEST
    )
    assert handoff["candidate_manifest_digest"] == digest_bytes(candidate.read_bytes())
    assert [item["path"] for item in handoff["assets"]] == [
        "bundle.zip",
        "release-package/openapi.json",
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(schema_version=2), "schema"),
        (lambda value: value.update(release_commit="f" * 40), "release_commit"),
        (lambda value: value.update(candidate_manifest_digest=DIGEST), "candidate manifest"),
        (lambda value: value["artifact"].update(digest=FINGERPRINT), "artifact identity"),
        (lambda value: value["archive"].update(digest=FINGERPRINT), "archive digest"),
    ],
)
def test_package_handoff_rejects_tampering(tmp_path: Path, mutation, message: str) -> None:
    root, candidate, handoff = package_handoff(tmp_path)
    mutation(handoff)
    with pytest.raises(HandoffError, match=message):
        validate_package_handoff(
            root, handoff, candidate, RELEASE_COMMIT, "release-payload-1-1", DIGEST
        )


def test_package_handoff_rejects_asset_tampering(tmp_path: Path) -> None:
    root, candidate, handoff = package_handoff(tmp_path)
    (root / "release-package/openapi.json").write_text("tampered\n")
    with pytest.raises(HandoffError, match="missing, unexpected, or modified"):
        validate_package_handoff(
            root, handoff, candidate, RELEASE_COMMIT, "release-payload-1-1", DIGEST
        )


def test_package_handoff_rejects_unexpected_file(tmp_path: Path) -> None:
    root, candidate, handoff = package_handoff(tmp_path)
    (root / "unexpected.txt").write_text("unexpected\n")
    with pytest.raises(HandoffError, match="missing or unexpected"):
        validate_package_handoff(
            root, handoff, candidate, RELEASE_COMMIT, "release-payload-1-1", DIGEST
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(candidate_manifest_digest="invalid"), "SHA-256"),
        (lambda value: value["artifact"].update(name=""), "non-empty"),
        (lambda value: value.update(assets=["not-a-record"]), "file records"),
        (
            lambda value: value.update(
                assets=[item for item in value["assets"] if item["path"] != "bundle.zip"]
            ),
            "archive must be included",
        ),
    ],
)
def test_package_handoff_rejects_malformed_records(tmp_path: Path, mutation, message: str) -> None:
    root, candidate, handoff = package_handoff(tmp_path)
    mutation(handoff)
    with pytest.raises(HandoffError, match=message):
        validate_package_handoff(
            root, handoff, candidate, RELEASE_COMMIT, "release-payload-1-1", DIGEST
        )


def test_package_handoff_requires_archive_in_assets(tmp_path: Path) -> None:
    root, candidate, _ = package_handoff(tmp_path)
    with pytest.raises(HandoffError, match="archive must be included"):
        build_package_handoff(
            root,
            candidate,
            RELEASE_COMMIT,
            "release-payload-1-1",
            DIGEST,
            "bundle.zip",
            ["release-package/openapi.json"],
        )


def test_package_handoff_rejects_duplicate_assets_and_empty_artifact_name(tmp_path: Path) -> None:
    root, candidate, _ = package_handoff(tmp_path)
    with pytest.raises(HandoffError, match="unique"):
        build_package_handoff(
            root,
            candidate,
            RELEASE_COMMIT,
            "release-payload-1-1",
            DIGEST,
            "bundle.zip",
            ["bundle.zip", "bundle.zip"],
        )
    with pytest.raises(HandoffError, match="non-empty"):
        build_package_handoff(
            root,
            candidate,
            RELEASE_COMMIT,
            "",
            DIGEST,
            "bundle.zip",
            ["bundle.zip"],
        )


def test_candidate_verify_cli_rejects_manifest_digest_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage, manifest_path, _ = candidate_manifest(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "release_handoff",
            "candidate-verify",
            "--root",
            str(stage),
            "--manifest",
            str(manifest_path),
            "--manifest-digest",
            "sha256:" + "e" * 64,
        ],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
