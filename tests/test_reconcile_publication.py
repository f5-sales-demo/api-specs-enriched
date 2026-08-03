# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for resumable, deterministic release publication."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.release import reconcile_publication as publication
from scripts.release.source_provenance import SOURCE_PROVENANCE_PREFIX, source_provenance_marker


def _source_provenance(**overrides: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "version": "2026.07.30-19",
        "tag_name": "v2026.07.30-19",
        "published_at": "2026-08-02T08:25:35Z",
        "asset_name": "api-specs-v2026.07.30-19.zip",
        "asset_size": 5_988_559,
        "asset_digest": f"sha256:{'a' * 64}",
    }
    receipt.update(overrides)
    return receipt


def _release_body(*lines: str) -> str:
    marker = source_provenance_marker(_source_provenance())
    return "\n".join((marker, *lines))


def test_deterministic_zip_ignores_source_mtime_and_creation_order(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "b.json").write_text('{"b": 2}\n')
    (first / "a.json").write_text('{"a": 1}\n')
    (second / "a.json").write_text('{"a": 1}\n')
    (second / "b.json").write_text('{"b": 2}\n')
    os.utime(first / "a.json", (1_000_000, 1_000_000))
    os.utime(second / "a.json", (2_000_000, 2_000_000))

    first_zip = tmp_path / "first.zip"
    second_zip = tmp_path / "second.zip"
    publication.write_deterministic_zip(first, first_zip)
    publication.write_deterministic_zip(second, second_zip)

    assert first_zip.read_bytes() == second_zip.read_bytes()
    with zipfile.ZipFile(first_zip) as archive:
        assert {item.compress_type for item in archive.infolist()} == {zipfile.ZIP_STORED}


def test_five_published_assets_must_be_byte_identical_across_two_builds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    version = "2.1.208"
    commit = "a" * 40
    calls = 0

    def build(_snapshot: Path, _version: str, _commit: str, output: Path) -> dict[str, Path]:
        nonlocal calls
        calls += 1
        assets = _write_asset_set(output, version)
        if calls == 2:
            assets[f"f5xc-api-specs-v{version}.zip"].write_bytes(b"drift\n")
        return assets

    monkeypatch.setattr(publication, "build_release_assets", build)

    with pytest.raises(RuntimeError, match=r"f5xc-api-specs-v2\.1\.208\.zip"):
        publication.build_reproducible_release_assets(
            tmp_path / "snapshot",
            version,
            commit,
            tmp_path / "builds",
        )

    assert calls == 2


def test_resolve_release_commit_uses_unique_version_introduction(monkeypatch) -> None:
    commits = ["a", "b", "c", "d"]
    versions = {"a": "2.1.206", "b": "2.1.207", "c": "2.1.208", "d": "2.1.209"}

    monkeypatch.setattr(publication, "git_output", lambda *args: "\n".join(commits))
    monkeypatch.setattr(publication, "version_at_if_present", versions.__getitem__)

    assert publication.resolve_release_commit("2.1.208") == "c"


def test_resolve_release_commit_rejects_consecutive_same_version_commits(monkeypatch) -> None:
    commits = ["a", "b"]
    versions = {"a": "2.1.208", "b": "2.1.208"}

    monkeypatch.setattr(publication, "git_output", lambda *args: "\n".join(commits))
    monkeypatch.setattr(publication, "version_at_if_present", versions.__getitem__)

    with pytest.raises(RuntimeError, match="found 2"):
        publication.resolve_release_commit("2.1.208")


def test_resolve_release_commit_rejects_reintroduced_version(monkeypatch) -> None:
    commits = ["a", "b", "c", "d"]
    versions = {"a": "2.1.208", "b": "2.1.209", "c": "2.1.208", "d": "2.1.210"}

    monkeypatch.setattr(publication, "git_output", lambda *args: "\n".join(commits))
    monkeypatch.setattr(publication, "version_at_if_present", versions.__getitem__)

    with pytest.raises(RuntimeError, match="found 2"):
        publication.resolve_release_commit("2.1.208")


def test_existing_tag_must_target_release_commit(monkeypatch) -> None:
    monkeypatch.setattr(publication, "remote_tag_target", lambda _tag: "wrong-commit")

    with pytest.raises(RuntimeError, match="expected right-commit"):
        publication.ensure_tag("v2.1.208", "right-commit")


def test_target_resolution_prefers_existing_remote_tag(monkeypatch) -> None:
    tagged = "a" * 40
    monkeypatch.setattr(publication, "remote_tag_target", lambda _tag: tagged)
    monkeypatch.setattr(
        publication,
        "validate_release_commit",
        lambda version, commit, main_ref: tagged,
    )
    monkeypatch.setattr(
        publication,
        "resolve_release_commit",
        lambda *_args: pytest.fail("history inference must not run for an existing tag"),
    )

    assert publication.resolve_target_commit("2.1.206") == tagged


def test_missing_tag_accepts_explicit_validated_target(monkeypatch) -> None:
    explicit = "b" * 40
    monkeypatch.setattr(publication, "remote_tag_target", lambda _tag: None)
    monkeypatch.setattr(
        publication,
        "validate_release_commit",
        lambda version, commit, main_ref: explicit,
    )
    monkeypatch.setattr(
        publication,
        "resolve_release_commit",
        lambda *_args: pytest.fail("explicit target must bypass history inference"),
    )

    assert publication.resolve_target_commit("2.1.208", target_commit=explicit) == explicit


def test_docs_commit_must_carry_every_released_generated_docs_tree(monkeypatch) -> None:
    monkeypatch.setattr(
        publication,
        "validate_release_commit",
        lambda _version, commit, _main_ref: commit,
    )

    def fake_git_output(*args: str) -> str:
        object_name = args[-1]
        if object_name.endswith(":docs/openapi-specs-config.json"):
            return (
                "release-plugin-config"
                if object_name.startswith("release:")
                else "docs-plugin-config"
            )
        return "same-generated-tree"

    monkeypatch.setattr(publication, "git_output", fake_git_output)

    with pytest.raises(RuntimeError, match="released generated documentation trees"):
        publication.validate_docs_commit("2.1.208", "release", "docs", "origin/main")


def test_completion_identity_includes_every_published_english_docs_tree(monkeypatch) -> None:
    resolved: list[str] = []

    def fake_git_output(*args: str) -> str:
        object_name = args[-1]
        resolved.append(object_name)
        return object_name

    monkeypatch.setattr(publication, "git_output", fake_git_output)

    identity = publication.completion_docs_identity("docs-commit")

    assert identity == tuple(resolved)
    assert resolved == [
        f"docs-commit:{path.as_posix()}" for path in publication.COMPLETION_DOC_PATHS
    ]
    assert "docs-commit:docs/en" in resolved


def test_explicit_target_cannot_override_existing_tag(monkeypatch) -> None:
    tagged = "a" * 40
    explicit = "b" * 40
    monkeypatch.setattr(publication, "remote_tag_target", lambda _tag: tagged)
    monkeypatch.setattr(
        publication,
        "validate_release_commit",
        lambda version, commit, main_ref: tagged,
    )
    monkeypatch.setattr(publication, "canonical_commit", lambda _commit: explicit)

    with pytest.raises(RuntimeError, match="disagrees with existing tag"):
        publication.resolve_target_commit("2.1.206", target_commit=explicit)


def test_completion_marker_is_commit_specific() -> None:
    assert publication.completion_marker("abc", "def") == "<!-- publication-complete:abc:def -->"


def test_github_outputs_reject_line_injection_before_writing(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "github-output"
    output.write_text("existing=true\n")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    with pytest.raises(ValueError, match="line break"):
        publication.write_github_outputs(
            {"version": "2.1.208", "release_url": "safe\ninjected=true"}
        )

    assert output.read_text() == "existing=true\n"


def _write_asset_set(tmp_path: Path, version: str) -> dict[str, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    assets: dict[str, Path] = {}
    for name in sorted(publication.expected_asset_names(version)):
        path = tmp_path / name
        path.write_bytes(f"authoritative:{name}\n".encode())
        assets[name] = path
    return assets


def test_publication_receipt_round_trips_exact_five_hashes(monkeypatch, tmp_path: Path) -> None:
    version = "2.1.208"
    commit = "a" * 40
    assets = _write_asset_set(tmp_path, version)
    monkeypatch.setattr(publication, "canonical_commit", lambda value: value)

    expected = publication.receipt_for_assets(version, commit, assets)
    marker = publication.publication_receipt_marker(expected)
    parsed = publication.publication_receipt_from_body(f"release notes\n\n{marker}\n")

    assert parsed == expected
    assert set(parsed.assets) == publication.expected_asset_names(version)


def test_publication_receipt_rejects_duplicate_markers(monkeypatch, tmp_path: Path) -> None:
    version = "2.1.208"
    commit = "a" * 40
    monkeypatch.setattr(publication, "canonical_commit", lambda value: value)
    receipt = publication.receipt_for_assets(version, commit, _write_asset_set(tmp_path, version))
    marker = publication.publication_receipt_marker(receipt)

    with pytest.raises(RuntimeError, match="multiple publication receipts"):
        publication.publication_receipt_from_body(f"{marker}\n{marker}\n")


def test_package_domain_contract_includes_38_specs_and_two_auxiliaries(tmp_path: Path) -> None:
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    indexed = [f"domain_{number:02d}.json" for number in range(38)]
    for name in [
        *indexed,
        *publication.AUXILIARY_PACKAGE_ASSETS,
        *publication.STANDALONE_SPEC_ASSETS,
    ]:
        (spec_dir / name).write_text("{}\n")
    index = {"specifications": [{"file": name} for name in indexed]}

    names = publication.package_domain_names(spec_dir, index)

    assert len(names) == 40
    assert set(names) == set(indexed) | set(publication.AUXILIARY_PACKAGE_ASSETS)


def test_package_domain_contract_rejects_unreviewed_extra_file(tmp_path: Path) -> None:
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    indexed = [f"domain_{number:02d}.json" for number in range(38)]
    for name in [*indexed, *publication.AUXILIARY_PACKAGE_ASSETS, "unreviewed.json"]:
        (spec_dir / name).write_text("{}\n")
    index = {"specifications": [{"file": name} for name in indexed]}

    with pytest.raises(RuntimeError, match=r"unexpected=.*unreviewed\.json"):
        publication.package_domain_names(spec_dir, index)


def test_package_domain_contract_rejects_duplicate_index_entry(tmp_path: Path) -> None:
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    indexed = [f"domain_{number:02d}.json" for number in range(38)]
    for name in [*indexed, *publication.AUXILIARY_PACKAGE_ASSETS]:
        (spec_dir / name).write_text("{}\n")
    index = {
        "specifications": [
            *({"file": name} for name in indexed),
            {"file": indexed[0]},
        ]
    }

    with pytest.raises(RuntimeError, match="duplicate domain filename"):
        publication.package_domain_names(spec_dir, index)


def test_release_readme_uses_measured_domain_count() -> None:
    template = "version={VERSION}; date={DATE}; domains={DOMAIN_COUNT}\n"

    rendered = publication.render_release_readme(
        template,
        version="2.1.208",
        release_date="2026-08-01",
        domain_count=38,
    )

    assert rendered == "version=2.1.208; date=2026-08-01; domains=38\n"


@pytest.mark.parametrize(
    "template",
    [
        "date={DATE}; domains={DOMAIN_COUNT}",
        "version={VERSION}; date={DATE}; domains={DOMAIN_COUNT}; again={DOMAIN_COUNT}",
        "version={VERSION}; date={DATE}; domains={DOMAIN_COUNT}; unknown={UNKNOWN}",
    ],
)
def test_release_readme_rejects_missing_duplicate_or_unknown_placeholders(
    template: str,
) -> None:
    with pytest.raises(RuntimeError, match="placeholders differ"):
        publication.render_release_readme(
            template,
            version="2.1.208",
            release_date="2026-08-01",
            domain_count=38,
        )


def test_release_without_committed_catalog_fails_closed(monkeypatch, tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    (snapshot / publication.SPEC_DIR).mkdir(parents=True)
    monkeypatch.setattr(publication, "version_mismatches", lambda *_args: [])

    with pytest.raises(RuntimeError, match=r"no reviewed release/api-catalog\.json"):
        publication.build_release_assets(
            snapshot,
            "2.1.208",
            "a" * 40,
            tmp_path / "assets",
        )


class _FakeReleaseAPI:
    """Small in-memory model of the gh release operations used by reconciliation."""

    def __init__(self, *, draft: bool = True, body: str | None = None) -> None:
        self.draft = draft
        self.body = body if body is not None else _release_body("notes")
        self.title = "API Specs"
        self.remote: dict[str, bytes] = {}
        self.calls: list[tuple[str, ...]] = []

    def release(self) -> dict:
        return {
            "assets": [
                {
                    "digest": f"sha256:{hashlib.sha256(self.remote[name]).hexdigest()}",
                    "name": name,
                }
                for name in sorted(self.remote)
            ],
            "body": self.body,
            "draft": self.draft,
            "html_url": "https://example.invalid/release",
            "immutable": not self.draft,
            "prerelease": False,
            "tag_name": "v2.1.208",
            "title": self.title,
        }

    def github_release(self, _repo: str, _tag: str) -> dict:
        return deepcopy(self.release())

    def run_command(self, *args: str, **_kwargs) -> publication.CommandResult:
        self.calls.append(args)
        operation = args[2] if args[:2] == ("gh", "release") else ""
        if operation == "upload":
            path = Path(args[4])
            self.remote[path.name] = path.read_bytes()
        elif operation == "delete-asset":
            self.remote.pop(args[4], None)
        elif operation == "edit":
            if "--notes-file" in args:
                notes_index = args.index("--notes-file") + 1
                self.body = Path(args[notes_index]).read_text()
            if "--title" in args:
                title_index = args.index("--title") + 1
                self.title = args[title_index]
            if "--draft=false" in args:
                self.draft = False
        elif operation == "create":
            self.draft = True
            notes_index = args.index("--notes-file") + 1
            self.body = Path(args[notes_index]).read_text()
        return publication.CommandResult("", "", 0)

    def download_asset(
        self,
        _repo: str,
        _tag: str,
        name: str,
        destination: Path,
    ) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / name
        path.write_bytes(self.remote[name])
        return path


def _install_fake_release_api(monkeypatch, api: _FakeReleaseAPI) -> None:
    monkeypatch.setattr(publication, "github_release", api.github_release)
    monkeypatch.setattr(publication, "run_command", api.run_command)
    monkeypatch.setattr(publication, "download_asset", api.download_asset)
    monkeypatch.setattr(publication, "canonical_commit", lambda value: value)
    monkeypatch.setattr(publication, "verify_existing_tag", lambda *_args: None)
    monkeypatch.setattr(publication, "source_provenance_at", lambda _commit: _source_provenance())


@pytest.mark.parametrize(
    ("draft", "body", "message"),
    [
        (True, "notes", "no source provenance"),
        (False, "notes", "no source provenance"),
        (
            True,
            source_provenance_marker(_source_provenance(asset_size=1)),
            "asset_size",
        ),
        (
            False,
            source_provenance_marker(_source_provenance(asset_size=1)),
            "asset_size",
        ),
    ],
)
def test_reconcile_rejects_missing_or_mismatched_source_provenance_before_mutation(
    monkeypatch,
    tmp_path: Path,
    draft: bool,
    body: str,
    message: str,
) -> None:
    version = "2.1.208"
    commit = "a" * 40
    assets = _write_asset_set(tmp_path / "assets", version)
    notes = tmp_path / "CHANGELOG.md"
    notes.write_text("# Changelog\n")
    api = _FakeReleaseAPI(draft=draft, body=body)
    api.remote = {name: path.read_bytes() for name, path in assets.items()}
    _install_fake_release_api(monkeypatch, api)

    with pytest.raises(RuntimeError, match=message):
        publication.reconcile_release(
            "owner/repo",
            f"v{version}",
            version,
            commit,
            assets,
            notes,
            tmp_path / "work",
            api.release(),
        )

    assert api.calls == []


def test_draft_repair_seals_receipt_before_publish(monkeypatch, tmp_path: Path) -> None:
    version = "2.1.208"
    commit = "a" * 40
    assets = _write_asset_set(tmp_path / "assets", version)
    notes = tmp_path / "CHANGELOG.md"
    notes.write_text("# Changelog\n")
    api = _FakeReleaseAPI()
    first_name = min(assets)
    api.remote[first_name] = b"wrong\n"
    api.remote["unexpected.bin"] = b"remove me\n"
    _install_fake_release_api(monkeypatch, api)

    published = publication.reconcile_release(
        "owner/repo",
        f"v{version}",
        version,
        commit,
        assets,
        notes,
        tmp_path / "work",
        api.release(),
    )

    assert published["draft"] is False
    assert api.remote == {name: path.read_bytes() for name, path in assets.items()}
    receipt = publication.publication_receipt_from_body(api.body)
    assert receipt == publication.receipt_for_assets(version, commit, assets)
    assert (
        publication.require_source_provenance(api.body, _source_provenance())
        == _source_provenance()
    )
    assert api.body.count(SOURCE_PROVENANCE_PREFIX) == 1
    notes_edit = next(i for i, call in enumerate(api.calls) if "--notes-file" in call)
    publish_edit = next(i for i, call in enumerate(api.calls) if "--draft=false" in call)
    assert notes_edit < publish_edit


def test_new_draft_is_created_with_source_provenance_before_publication(
    monkeypatch,
    tmp_path: Path,
) -> None:
    version = "2.1.208"
    commit = "a" * 40
    assets = _write_asset_set(tmp_path / "assets", version)
    notes = tmp_path / "CHANGELOG.md"
    notes.write_text("# Changelog\n")
    api = _FakeReleaseAPI(body="")
    _install_fake_release_api(monkeypatch, api)

    def release_after_create(repo: str, tag: str) -> dict | None:
        created = any(call[:3] == ("gh", "release", "create") for call in api.calls)
        return api.github_release(repo, tag) if created else None

    monkeypatch.setattr(publication, "github_release", release_after_create)

    published = publication.reconcile_release(
        "owner/repo",
        f"v{version}",
        version,
        commit,
        assets,
        notes,
        tmp_path / "work",
    )

    assert published["draft"] is False
    assert (
        publication.require_source_provenance(api.body, _source_provenance())
        == _source_provenance()
    )
    assert api.body.count(SOURCE_PROVENANCE_PREFIX) == 1
    create_index = next(
        i for i, call in enumerate(api.calls) if call[:3] == ("gh", "release", "create")
    )
    publish_index = next(i for i, call in enumerate(api.calls) if "--draft=false" in call)
    assert create_index < publish_index


def test_sealed_draft_hash_mismatch_fails_without_mutation(monkeypatch, tmp_path: Path) -> None:
    version = "2.1.208"
    commit = "a" * 40
    assets = _write_asset_set(tmp_path / "assets", version)
    notes = tmp_path / "CHANGELOG.md"
    notes.write_text("# Changelog\n")
    api = _FakeReleaseAPI()
    _install_fake_release_api(monkeypatch, api)
    wrong_assets = _write_asset_set(tmp_path / "wrong", version)
    for path in wrong_assets.values():
        path.write_bytes(b"different\n")
    wrong_receipt = publication.receipt_for_assets(version, commit, wrong_assets)
    api.body = _release_body(publication.publication_receipt_marker(wrong_receipt))

    with pytest.raises(RuntimeError, match="asset hashes differ"):
        publication.reconcile_release(
            "owner/repo",
            f"v{version}",
            version,
            commit,
            assets,
            notes,
            tmp_path / "work",
            api.release(),
        )

    assert api.calls == []


def test_published_release_self_receipt_cannot_override_authoritative_build(
    monkeypatch, tmp_path: Path
) -> None:
    version = "2.1.208"
    commit = "a" * 40
    authoritative = _write_asset_set(tmp_path / "authoritative", version)
    forged = _write_asset_set(tmp_path / "forged", version)
    for path in forged.values():
        path.write_bytes(f"forged:{path.name}\n".encode())
    notes = tmp_path / "CHANGELOG.md"
    notes.write_text("# Changelog\n")
    api = _FakeReleaseAPI(draft=False)
    api.remote = {name: path.read_bytes() for name, path in forged.items()}
    _install_fake_release_api(monkeypatch, api)
    api.body = _release_body(
        publication.publication_receipt_marker(
            publication.receipt_for_assets(version, commit, forged)
        )
    )

    with pytest.raises(RuntimeError, match="asset hashes differ from the authoritative build"):
        publication.reconcile_release(
            "owner/repo",
            f"v{version}",
            version,
            commit,
            authoritative,
            notes,
            tmp_path / "work",
            api.release(),
        )


def test_matching_sealed_draft_publishes_without_asset_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    version = "2.1.208"
    commit = "a" * 40
    assets = _write_asset_set(tmp_path / "assets", version)
    notes = tmp_path / "CHANGELOG.md"
    notes.write_text("# Changelog\n")
    api = _FakeReleaseAPI()
    api.remote = {name: path.read_bytes() for name, path in assets.items()}
    _install_fake_release_api(monkeypatch, api)
    receipt = publication.receipt_for_assets(version, commit, assets)
    api.body = _release_body(publication.publication_receipt_marker(receipt))

    published = publication.reconcile_release(
        "owner/repo",
        f"v{version}",
        version,
        commit,
        assets,
        notes,
        tmp_path / "work",
        api.release(),
    )

    assert published["draft"] is False
    assert len(api.calls) == 1
    assert "--draft=false" in api.calls[0]
    assert published["body"].count(SOURCE_PROVENANCE_PREFIX) == 1


def test_concurrent_draft_publication_is_verified_instead_of_failing(
    monkeypatch, tmp_path: Path
) -> None:
    version = "2.1.208"
    commit = "a" * 40
    assets = _write_asset_set(tmp_path / "assets", version)
    api = _FakeReleaseAPI()
    api.remote = {name: path.read_bytes() for name, path in assets.items()}
    _install_fake_release_api(monkeypatch, api)
    receipt = publication.receipt_for_assets(version, commit, assets)
    api.body = _release_body(publication.publication_receipt_marker(receipt))
    normal_command = api.run_command

    def publish_concurrently(*args: str, **kwargs) -> publication.CommandResult:
        if args[:3] == ("gh", "release", "edit") and "--draft=false" in args:
            api.draft = False
            return publication.CommandResult("", "already published", 1)
        return normal_command(*args, **kwargs)

    monkeypatch.setattr(publication, "run_command", publish_concurrently)

    published = publication.publish_sealed_draft(
        "owner/repo",
        f"v{version}",
        version,
        commit,
        api.release(),
        tmp_path / "work",
        receipt,
    )

    assert published["draft"] is False
    assert published["immutable"] is True


def test_published_receipt_verifies_remote_bytes_without_local_assets(
    monkeypatch, tmp_path: Path
) -> None:
    version = "2.1.208"
    commit = "a" * 40
    assets = _write_asset_set(tmp_path / "assets", version)
    api = _FakeReleaseAPI(draft=False)
    api.remote = {name: path.read_bytes() for name, path in assets.items()}
    _install_fake_release_api(monkeypatch, api)
    receipt = publication.receipt_for_assets(version, commit, assets)
    api.body = _release_body(publication.publication_receipt_marker(receipt))

    verified = publication.verify_published_release(
        "owner/repo",
        f"v{version}",
        version,
        commit,
        api.release(),
        tmp_path / "downloads",
    )

    assert verified == receipt
    assert api.calls == []


def test_published_receipt_rejects_github_api_digest_disagreement(
    monkeypatch, tmp_path: Path
) -> None:
    version = "2.1.208"
    commit = "a" * 40
    assets = _write_asset_set(tmp_path / "assets", version)
    api = _FakeReleaseAPI(draft=False)
    api.remote = {name: path.read_bytes() for name, path in assets.items()}
    _install_fake_release_api(monkeypatch, api)
    receipt = publication.receipt_for_assets(version, commit, assets)
    api.body = _release_body(publication.publication_receipt_marker(receipt))
    release = api.release()
    release["assets"][0]["digest"] = f"sha256:{'0' * 64}"
    monkeypatch.setattr(
        publication,
        "download_asset",
        lambda *_args: pytest.fail("API digest mismatch must fail before download"),
    )

    with pytest.raises(RuntimeError, match="GitHub API digest differs"):
        publication.verify_published_release(
            "owner/repo",
            f"v{version}",
            version,
            commit,
            release,
            tmp_path / "downloads",
        )


def test_published_release_rechecks_remote_tag_identity(monkeypatch, tmp_path: Path) -> None:
    version = "2.1.208"
    commit = "a" * 40
    assets = _write_asset_set(tmp_path / "assets", version)
    api = _FakeReleaseAPI(draft=False)
    api.remote = {name: path.read_bytes() for name, path in assets.items()}
    _install_fake_release_api(monkeypatch, api)
    receipt = publication.receipt_for_assets(version, commit, assets)
    api.body = _release_body(publication.publication_receipt_marker(receipt))
    monkeypatch.setattr(
        publication,
        "verify_existing_tag",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("tag moved")),
    )

    with pytest.raises(RuntimeError, match="tag moved"):
        publication.verify_published_release(
            "owner/repo",
            f"v{version}",
            version,
            commit,
            api.release(),
            tmp_path / "downloads",
        )


def test_published_receipt_rejects_mutable_release(monkeypatch, tmp_path: Path) -> None:
    version = "2.1.208"
    commit = "a" * 40
    assets = _write_asset_set(tmp_path / "assets", version)
    api = _FakeReleaseAPI(draft=False)
    api.remote = {name: path.read_bytes() for name, path in assets.items()}
    _install_fake_release_api(monkeypatch, api)
    receipt = publication.receipt_for_assets(version, commit, assets)
    api.body = _release_body(publication.publication_receipt_marker(receipt))
    release = api.release()
    release["immutable"] = False

    with pytest.raises(RuntimeError, match="is not immutable"):
        publication.verify_published_release(
            "owner/repo",
            f"v{version}",
            version,
            commit,
            release,
            tmp_path / "downloads",
        )


@pytest.mark.parametrize(
    ("completed_pages_identity", "current_pages_identity", "publish_needed"),
    [
        ("same-pages", "same-pages", "false"),
        ("old-english-docs", "new-english-docs", "true"),
    ],
    ids=(
        "current-pages-completion-skips-deploy",
        "english-doc-change-requires-exact-pages-verification",
    ),
)
def test_main_completed_retry_rebuilds_authoritative_assets(
    monkeypatch,
    capsys,
    tmp_path: Path,
    completed_pages_identity: str,
    current_pages_identity: str,
    publish_needed: str,
) -> None:
    version = "2.1.208"
    commit = "a" * 40
    current_docs_commit = "b" * 40
    release = {
        "assets": [],
        "body": publication.completion_marker(commit, commit),
        "draft": False,
        "html_url": "https://example.invalid/release",
        "prerelease": False,
    }
    monkeypatch.setattr(sys, "argv", ["reconcile-publication"])
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(publication, "repository_name", lambda: "owner/repo")
    monkeypatch.setattr(publication, "version_at", lambda _ref: version)
    monkeypatch.setattr(publication, "resolve_target_commit", lambda *_args: commit)
    monkeypatch.setattr(publication, "ensure_tag", lambda *_args: None)
    monkeypatch.setattr(publication, "github_release", lambda *_args: release)
    assets = _write_asset_set(tmp_path / "authoritative", version)
    extracted: list[str] = []

    def extract(source_commit: str, destination: Path) -> None:
        extracted.append(source_commit)
        destination.mkdir(parents=True)
        (destination / "CHANGELOG.md").write_text("# Changelog\n")

    reconciled: list[dict[str, Path]] = []
    monkeypatch.setattr(publication, "extract_snapshot", extract)
    monkeypatch.setattr(publication, "build_release_assets", lambda *_args: assets)
    monkeypatch.setattr(
        publication,
        "reconcile_release",
        lambda _repo, _tag, _version, _commit, built, *_args: reconciled.append(built) or release,
    )
    monkeypatch.setattr(publication, "release_docs_identity", lambda _commit: ("same-docs",))
    monkeypatch.setattr(
        publication,
        "completion_docs_identity",
        lambda candidate: (
            current_pages_identity
            if candidate == current_docs_commit
            else completed_pages_identity,
        ),
    )
    monkeypatch.setattr(publication, "canonical_commit", lambda _value: current_docs_commit)
    monkeypatch.setattr(
        publication,
        "validate_docs_commit",
        lambda _version, _release, docs, _main: docs,
    )

    assert publication.main() == 0
    assert extracted == [commit]
    assert reconciled == [assets]
    output = capsys.readouterr().out
    assert f'"publish_needed": "{publish_needed}"' in output
    assert '"audit_needed": "true"' in output
    assert "needs_post_publish" not in output


@pytest.mark.parametrize(
    "body",
    [
        "<!-- publication-complete:not-a-commit:also-invalid -->",
        "\n".join(
            [
                publication.completion_marker("a" * 40, "b" * 40),
                publication.completion_marker("a" * 40, "c" * 40),
            ]
        ),
    ],
)
def test_completion_receipt_rejects_malformed_or_multiple_markers(body: str) -> None:
    with pytest.raises(RuntimeError, match="completion receipt"):
        publication.completed_docs_commit(body, "2.1.208", "a" * 40, "origin/main")


def test_mark_complete_replaces_stale_english_docs_completion(monkeypatch, tmp_path: Path) -> None:
    version = "2.1.208"
    release_commit = "a" * 40
    previous_docs_commit = "b" * 40
    current_docs_commit = "c" * 40
    previous_marker = publication.completion_marker(release_commit, previous_docs_commit)
    current_marker = publication.completion_marker(release_commit, current_docs_commit)
    release = {"body": _release_body("release notes", previous_marker)}
    assets = _write_asset_set(tmp_path / "assets", version)

    monkeypatch.setattr(publication, "github_release", lambda *_args: release)
    monkeypatch.setattr(publication, "extract_snapshot", lambda *_args: None)
    monkeypatch.setattr(publication, "build_release_assets", lambda *_args: assets)
    monkeypatch.setattr(publication, "verify_published_release", lambda *_args: None)
    monkeypatch.setattr(publication, "require_matching_receipt", lambda *_args: None)
    monkeypatch.setattr(publication, "source_provenance_at", lambda _commit: _source_provenance())
    monkeypatch.setattr(publication, "canonical_commit", lambda commit: commit)
    monkeypatch.setattr(
        publication,
        "validate_docs_commit",
        lambda _version, _release, docs, _main: docs,
    )
    monkeypatch.setattr(
        publication,
        "completion_docs_identity",
        lambda commit: (
            ("current-english-docs",)
            if commit == current_docs_commit
            else ("previous-english-docs",)
        ),
    )

    def edit_release(*args: str, **_kwargs) -> publication.CommandResult:
        assert args[:3] == ("gh", "release", "edit")
        release["body"] = Path(args[-1]).read_text()
        return publication.CommandResult("", "", 0)

    monkeypatch.setattr(publication, "run_command", edit_release)

    publication.mark_complete(
        "owner/repo",
        version,
        release_commit,
        current_docs_commit,
    )

    assert previous_marker not in release["body"]
    assert current_marker in release["body"]
    assert release["body"].count(publication.COMPLETE_MARKER_PREFIX) == 1


def test_main_sealed_draft_retry_rebuilds_and_requires_authoritative_receipt(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    version = "2.1.208"
    commit = "a" * 40
    assets = _write_asset_set(tmp_path / "authoritative", version)
    receipt = publication.PublicationReceipt(
        version=version,
        commit=commit,
        assets={name: publication.sha256(path) for name, path in assets.items()},
    )
    release = {
        "assets": [{"name": name} for name in sorted(receipt.assets)],
        "body": _release_body(publication.publication_receipt_marker(receipt)),
        "draft": True,
        "html_url": "https://example.invalid/release",
        "prerelease": False,
    }
    published = {**release, "draft": False}
    extracted: list[str] = []
    expected_receipts: list[publication.PublicationReceipt | None] = []
    monkeypatch.setattr(sys, "argv", ["reconcile-publication"])
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(publication, "repository_name", lambda: "owner/repo")
    monkeypatch.setattr(publication, "version_at", lambda _ref: version)
    monkeypatch.setattr(publication, "resolve_target_commit", lambda *_args: commit)
    monkeypatch.setattr(publication, "ensure_tag", lambda *_args: None)
    monkeypatch.setattr(publication, "github_release", lambda *_args: release)
    monkeypatch.setattr(publication, "source_provenance_at", lambda _commit: _source_provenance())

    def fake_publish(
        _repo,
        _tag,
        _version,
        _commit,
        _release,
        _root,
        expected=None,
    ):
        expected_receipts.append(expected)
        return published

    monkeypatch.setattr(publication, "publish_sealed_draft", fake_publish)

    def extract(source_commit: str, destination: Path) -> None:
        extracted.append(source_commit)
        destination.mkdir(parents=True)
        (destination / "CHANGELOG.md").write_text("# Changelog\n")

    monkeypatch.setattr(publication, "extract_snapshot", extract)
    monkeypatch.setattr(
        publication,
        "build_release_assets",
        lambda _snapshot, _version, _commit, _output: assets,
    )
    monkeypatch.setattr(publication, "git_output", lambda *_args: "same-index")
    monkeypatch.setattr(publication, "canonical_commit", lambda _value: commit)

    assert publication.main() == 0
    assert extracted == [commit]
    assert expected_receipts == [receipt]
    output = capsys.readouterr().out
    assert '"assets_verified": "true"' in output
    assert '"publish_needed": "true"' in output
    assert '"audit_needed": "true"' in output


def test_current_main_generated_docs_divergence_fails_instead_of_skipping(
    monkeypatch, tmp_path: Path
) -> None:
    version = "2.1.208"
    release_commit = "a" * 40
    docs_commit = "b" * 40
    assets = _write_asset_set(tmp_path / "authoritative", version)
    release = {
        "body": "",
        "draft": False,
        "html_url": "https://example.invalid/release",
        "prerelease": False,
    }
    monkeypatch.setattr(sys, "argv", ["reconcile-publication"])
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(publication, "repository_name", lambda: "owner/repo")
    monkeypatch.setattr(publication, "version_at", lambda _ref: version)
    monkeypatch.setattr(publication, "resolve_target_commit", lambda *_args: release_commit)
    monkeypatch.setattr(publication, "ensure_tag", lambda *_args: None)
    monkeypatch.setattr(publication, "github_release", lambda *_args: release)

    def extract(_commit: str, destination: Path) -> None:
        destination.mkdir(parents=True)
        (destination / "CHANGELOG.md").write_text("# Changelog\n")

    monkeypatch.setattr(publication, "extract_snapshot", extract)
    monkeypatch.setattr(publication, "build_release_assets", lambda *_args: assets)
    monkeypatch.setattr(publication, "reconcile_release", lambda *_args: release)
    monkeypatch.setattr(publication, "canonical_commit", lambda _value: docs_commit)
    monkeypatch.setattr(
        publication,
        "release_docs_identity",
        lambda commit: ("release",) if commit == release_commit else ("changed-main",),
    )

    with pytest.raises(RuntimeError, match="current origin/main generated documentation differs"):
        publication.main()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit_index(repo: Path, version: str | None) -> str:
    index = repo / publication.INDEX_PATH
    if version is None:
        index.unlink()
    else:
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps({"version": version, "specifications": []}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"index {version or 'deleted'}")
    return _git(repo, "rev-parse", "HEAD")


def test_release_resolution_crosses_historical_index_deletion(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _commit_index(repo, "1.0.0")
    _commit_index(repo, None)
    expected = _commit_index(repo, "2.0.0")
    monkeypatch.chdir(repo)

    assert publication.resolve_release_commit("2.0.0", "main") == expected


def test_release_resolution_rejects_version_reintroduced_after_deletion(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _commit_index(repo, "1.0.0")
    _commit_index(repo, None)
    _commit_index(repo, "1.0.0")
    monkeypatch.chdir(repo)

    with pytest.raises(RuntimeError, match="found 2"):
        publication.resolve_release_commit("1.0.0", "main")


def test_local_only_tag_is_published_to_remote(monkeypatch, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("test\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "tag", "v1.0.0", commit)
    monkeypatch.chdir(repo)

    publication.ensure_tag("v1.0.0", commit)

    assert _git(repo, "ls-remote", "--tags", "origin", "refs/tags/v1.0.0").split()[0] == commit


def test_concurrent_remote_tag_with_wrong_target_fails(monkeypatch, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("first\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "first")
    expected = _git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("second\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "second")
    wrong = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "tag", "v1.0.0", wrong)
    _git(repo, "push", "origin", "refs/tags/v1.0.0")
    monkeypatch.chdir(repo)

    with pytest.raises(RuntimeError, match=wrong):
        publication.ensure_tag("v1.0.0", expected)
