#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Reconcile one committed API-spec build into a verified GitHub release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from scripts.release.source_provenance import (
    SourceProvenance,
    ensure_source_provenance,
    require_source_provenance,
    source_provenance_at,
)
from scripts.stamp_release_version import version_mismatches

INDEX_PATH = Path("docs/specifications/api/index.json")
SPEC_DIR = INDEX_PATH.parent
RELEASE_DOC_PATHS = (
    Path("docs/specifications"),
    Path("docs/api-reference"),
    Path("docs/openapi-specs-config.json"),
)
COMPLETION_DOC_PATHS = (*RELEASE_DOC_PATHS, Path("docs/en"))
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
COMPLETE_MARKER_PREFIX = "<!-- publication-complete:"
PUBLICATION_RECEIPT_PREFIX = "<!-- publication-receipt:"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
STANDALONE_SPEC_ASSETS = frozenset({"openapi.json", "index.json", "minimal-export-defaults.json"})
AUXILIARY_PACKAGE_ASSETS = frozenset({"namespace_profiles.json", "validation.json"})
EXPECTED_PACKAGE_DOMAIN_COUNT = 40
RELEASE_README_PLACEHOLDERS = frozenset({"DATE", "DOMAIN_COUNT", "VERSION"})
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
COMMIT_HEX = re.compile(r"^[0-9a-f]{40,64}$")


@dataclass(frozen=True)
class CommandResult:
    """Captured subprocess result."""

    stdout: str
    stderr: str
    returncode: int


@dataclass(frozen=True)
class PublicationReceipt:
    """Immutable identity of one published release's exact asset bytes."""

    version: str
    commit: str
    assets: dict[str, str]


def run_command(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> CommandResult:
    """Run one command without a shell and return its captured output."""
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        command = " ".join(args)
        raise RuntimeError(f"command failed ({result.returncode}): {command}\n{result.stderr}")
    return CommandResult(result.stdout, result.stderr, result.returncode)


def git_output(*args: str) -> str:
    """Return stripped output from Git."""
    return run_command("git", *args).stdout.strip()


def version_at(commit: str) -> str:
    """Return the strict build version committed at *commit*."""
    version = version_at_if_present(commit)
    if version is None:
        raise RuntimeError(f"{commit}:{INDEX_PATH} is absent")
    return version


def _version_from_index(raw: str, commit: str) -> str:
    """Parse one committed index document's strict semantic version."""
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{commit}:{INDEX_PATH} is not valid JSON") from exc
    version = document.get("version") if isinstance(document, dict) else None
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise RuntimeError(f"{commit}:{INDEX_PATH} has no semantic build version")
    return version


def version_at_if_present(commit: str) -> str | None:
    """Return a commit's build version, or ``None`` when the index is absent."""
    result = run_command(
        "git",
        "show",
        f"{commit}:{INDEX_PATH.as_posix()}",
        check=False,
    )
    if result.returncode != 0:
        return None
    return _version_from_index(result.stdout, commit)


def resolve_release_commit(version: str, main_ref: str = "origin/main") -> str:
    """Resolve the unique first-parent commit carrying *version*.

    Consecutive commits with the same version are not one identity: their
    generated trees can differ.  A missing tag is recoverable only when history
    names exactly one candidate, unless the operator supplies a target commit.
    """
    if not SEMVER.fullmatch(version):
        raise ValueError(f"invalid semantic version: {version!r}")
    commits = git_output(
        "rev-list",
        "--first-parent",
        "--reverse",
        main_ref,
        "--",
        INDEX_PATH.as_posix(),
    ).splitlines()
    matches: list[str] = []
    for commit in commits:
        current_version = version_at_if_present(commit)
        if current_version == version:
            matches.append(commit)
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one commit carrying {version} on {main_ref}, found {len(matches)}"
        )
    return matches[0]


def canonical_commit(commit: str) -> str:
    """Resolve *commit* to its full commit object ID."""
    return git_output("rev-parse", "--verify", f"{commit}^{{commit}}")


def validate_release_commit(version: str, commit: str, main_ref: str) -> str:
    """Validate one explicit or tagged release commit against main history."""
    resolved = canonical_commit(commit)
    ancestry = run_command(
        "git",
        "merge-base",
        "--is-ancestor",
        resolved,
        main_ref,
        check=False,
    )
    if ancestry.returncode != 0:
        if ancestry.returncode == 1:
            raise RuntimeError(f"release commit {resolved} is not reachable from {main_ref}")
        raise RuntimeError(f"could not verify release commit ancestry: {ancestry.stderr}")
    actual = version_at(resolved)
    if actual != version:
        raise RuntimeError(f"release commit {resolved} carries {actual}, expected {version}")
    return resolved


def validate_docs_commit(
    version: str,
    release_commit: str,
    docs_commit: str,
    main_ref: str,
) -> str:
    """Require docs to carry every release-coupled generated Pages tree."""
    resolved = validate_release_commit(version, docs_commit, main_ref)
    release_identity = release_docs_identity(release_commit)
    docs_identity = release_docs_identity(resolved)
    if docs_identity != release_identity:
        raise RuntimeError(
            f"docs commit {resolved} does not carry the released generated documentation "
            f"trees from {release_commit}"
        )
    return resolved


def release_docs_identity(commit: str) -> tuple[str, ...]:
    """Return Git tree identities for all generated documentation bound to a release."""
    return tuple(
        git_output("rev-parse", f"{commit}:{path.as_posix()}") for path in RELEASE_DOC_PATHS
    )


def completion_docs_identity(commit: str) -> tuple[str, ...]:
    """Return Git identities for every documentation source published by Pages."""
    return tuple(
        git_output("rev-parse", f"{commit}:{path.as_posix()}") for path in COMPLETION_DOC_PATHS
    )


def resolve_target_commit(
    version: str,
    main_ref: str = "origin/main",
    target_commit: str | None = None,
) -> str:
    """Resolve a release target, preferring an existing immutable remote tag."""
    if not SEMVER.fullmatch(version):
        raise ValueError(f"invalid semantic version: {version!r}")
    tag = f"v{version}"
    tagged = remote_tag_target(tag)
    if tagged is not None:
        resolved = validate_release_commit(version, tagged, main_ref)
        if target_commit is not None and canonical_commit(target_commit) != resolved:
            raise RuntimeError(
                f"explicit target {target_commit} disagrees with existing tag {tag} at {resolved}"
            )
        return resolved
    if target_commit is not None:
        return validate_release_commit(version, target_commit, main_ref)
    return resolve_release_commit(version, main_ref)


def sha256(path: Path) -> str:
    """Return the lowercase SHA-256 hex digest for *path*."""
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def expected_asset_names(version: str) -> set[str]:
    """Return the exact five release asset names for *version*."""
    if not SEMVER.fullmatch(version):
        raise ValueError(f"invalid semantic version: {version!r}")
    return {
        "openapi.json",
        "index.json",
        "minimal-export-defaults.json",
        "api-catalog.json",
        f"f5xc-api-specs-v{version}.zip",
    }


def receipt_for_assets(
    version: str,
    commit: str,
    assets: dict[str, Path],
) -> PublicationReceipt:
    """Create a strict receipt for the five locally built release assets."""
    names = set(assets)
    expected = expected_asset_names(version)
    if names != expected:
        raise RuntimeError(
            f"release asset set differs: expected {sorted(expected)}, found {sorted(names)}"
        )
    resolved_commit = canonical_commit(commit)
    return PublicationReceipt(
        version=version,
        commit=resolved_commit,
        assets={name: sha256(assets[name]) for name in sorted(assets)},
    )


def publication_receipt_marker(receipt: PublicationReceipt) -> str:
    """Serialize a release receipt as one deterministic hidden Markdown line."""
    payload = {
        "assets": dict(sorted(receipt.assets.items())),
        "commit": receipt.commit,
        "version": receipt.version,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{PUBLICATION_RECEIPT_PREFIX}{encoded} -->"


def _validated_receipt(document: Any) -> PublicationReceipt:
    """Validate and materialize one decoded publication receipt."""
    if not isinstance(document, dict) or set(document) != {"assets", "commit", "version"}:
        raise RuntimeError("publication receipt has invalid fields")
    version = document["version"]
    commit = document["commit"]
    assets = document["assets"]
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise RuntimeError("publication receipt has an invalid version")
    if not isinstance(commit, str) or not COMMIT_HEX.fullmatch(commit):
        raise RuntimeError("publication receipt has an invalid commit")
    if not isinstance(assets, dict) or set(assets) != expected_asset_names(version):
        raise RuntimeError("publication receipt does not name the exact five release assets")
    for name, digest in assets.items():
        if (
            not isinstance(name, str)
            or not isinstance(digest, str)
            or not SHA256_HEX.fullmatch(digest)
        ):
            raise RuntimeError("publication receipt has an invalid asset digest")
    return PublicationReceipt(version=version, commit=commit, assets=dict(sorted(assets.items())))


def publication_receipt_from_body(body: str) -> PublicationReceipt | None:
    """Parse the sole hidden publication receipt from a release body."""
    markers = [
        line.strip()
        for line in body.splitlines()
        if line.strip().startswith(PUBLICATION_RECEIPT_PREFIX)
    ]
    if not markers:
        return None
    if len(markers) != 1:
        raise RuntimeError("release body contains multiple publication receipts")
    marker = markers[0]
    if not marker.endswith(" -->"):
        raise RuntimeError("publication receipt marker is malformed")
    payload = marker[len(PUBLICATION_RECEIPT_PREFIX) : -len(" -->")]
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("publication receipt is not valid JSON") from exc
    return _validated_receipt(document)


def require_matching_receipt(
    release: dict[str, Any],
    version: str,
    commit: str,
    expected: PublicationReceipt | None = None,
) -> PublicationReceipt:
    """Require a release's durable receipt to match its resolved identity."""
    receipt = publication_receipt_from_body(str(release.get("body") or ""))
    if receipt is None:
        raise RuntimeError("release has no publication receipt")
    resolved_commit = canonical_commit(commit)
    if receipt.version != version or receipt.commit != resolved_commit:
        raise RuntimeError(
            "publication receipt identity differs: "
            f"found {receipt.version}@{receipt.commit}, expected {version}@{resolved_commit}"
        )
    if expected is not None and receipt != expected:
        raise RuntimeError("publication receipt asset hashes differ from the authoritative build")
    return receipt


def extract_snapshot(commit: str, destination: Path) -> None:
    """Extract a repository snapshot without changing the current worktree."""
    archive = destination.parent / "snapshot.tar"
    with archive.open("wb") as stream:
        result = subprocess.run(
            ["git", "archive", "--format=tar", commit],
            stdout=stream,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(f"git archive failed: {result.stderr.decode()}")
    destination.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter="data")


def write_deterministic_zip(source: Path, destination: Path) -> None:
    """Write a byte-reproducible ZIP without runtime-dependent compression."""
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(relative, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, path.read_bytes())


def package_domain_names(spec_dir: Path, index: dict[str, Any]) -> list[str]:
    """Return the exact reviewed domain/auxiliary member set for the ZIP."""
    specifications = index.get("specifications")
    if not isinstance(specifications, list):
        raise TypeError("index.json has no specifications list")
    indexed_names: list[str] = []
    for entry in specifications:
        name = entry.get("file") if isinstance(entry, dict) else None
        if not isinstance(name, str) or Path(name).name != name:
            raise RuntimeError(f"index.json contains an invalid domain filename: {name!r}")
        indexed_names.append(name)
    indexed = set(indexed_names)
    if len(indexed_names) != len(indexed):
        raise RuntimeError("index.json contains a duplicate domain filename")
    expected = indexed | set(AUXILIARY_PACKAGE_ASSETS)
    present = {
        path.name for path in spec_dir.glob("*.json") if path.name not in STANDALONE_SPEC_ASSETS
    }
    if present != expected:
        missing = sorted(expected - present)
        unexpected = sorted(present - expected)
        raise RuntimeError(
            "package domain contract differs"
            f"; missing={missing or 'none'}; unexpected={unexpected or 'none'}"
        )
    if len(expected) != EXPECTED_PACKAGE_DOMAIN_COUNT:
        raise RuntimeError(
            f"package domain contract requires {EXPECTED_PACKAGE_DOMAIN_COUNT} files, "
            f"found {len(expected)}"
        )
    return sorted(expected)


def render_release_readme(
    template: str,
    *,
    version: str,
    release_date: str,
    domain_count: int,
) -> str:
    """Render the release README only when its measured placeholders are exact."""
    placeholder_names = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template)
    if set(placeholder_names) != RELEASE_README_PLACEHOLDERS or any(
        placeholder_names.count(name) != 1 for name in RELEASE_README_PLACEHOLDERS
    ):
        raise RuntimeError(
            "release README placeholders differ: "
            f"expected={sorted(RELEASE_README_PLACEHOLDERS)}, "
            f"measured={placeholder_names}"
        )
    values = {
        "DATE": release_date,
        "DOMAIN_COUNT": str(domain_count),
        "VERSION": version,
    }
    rendered = template
    for name, value in values.items():
        rendered = rendered.replace(f"{{{name}}}", value)
    return rendered


def build_release_assets(
    snapshot: Path, version: str, commit: str, output: Path
) -> dict[str, Path]:
    """Build the five authoritative release assets from a historical snapshot."""
    spec_dir = snapshot / SPEC_DIR
    mismatches = version_mismatches(spec_dir, version)
    if mismatches:
        details = ", ".join(f"{path.name}={actual}" for path, actual in mismatches)
        raise RuntimeError(f"snapshot build versions disagree with {version}: {details}")

    output.mkdir(parents=True)
    catalog = output / "api-catalog.json"
    committed_catalog = snapshot / "release" / "api-catalog.json"
    if not committed_catalog.is_file():
        raise RuntimeError("release commit has no reviewed release/api-catalog.json")
    catalog_document = json.loads(committed_catalog.read_text())
    if catalog_document.get("version") != version:
        raise RuntimeError("committed API catalog version disagrees with release version")
    shutil.copyfile(committed_catalog, catalog)

    raw_assets = {
        "openapi.json": spec_dir / "openapi.json",
        "index.json": spec_dir / "index.json",
        "minimal-export-defaults.json": spec_dir / "minimal-export-defaults.json",
        "api-catalog.json": catalog,
    }
    for name, source in raw_assets.items():
        if source.parent != output:
            shutil.copyfile(source, output / name)

    package = output / "package"
    domains = package / "domains"
    domains.mkdir(parents=True)
    index = json.loads((spec_dir / "index.json").read_text())
    domain_names = package_domain_names(spec_dir, index)
    for name in domain_names:
        shutil.copyfile(spec_dir / name, domains / name)
    shutil.copyfile(spec_dir / "openapi.json", package / "openapi.json")
    shutil.copyfile(spec_dir / "index.json", package / "index.json")
    changelog = snapshot / "CHANGELOG.md"
    if changelog.exists():
        shutil.copyfile(changelog, package / "CHANGELOG.md")

    openapi = json.loads((spec_dir / "openapi.json").read_text())
    (package / "openapi.yaml").write_text(
        yaml.safe_dump(openapi, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    commit_time = git_output("show", "-s", "--format=%cI", commit)
    release_date = datetime.fromisoformat(commit_time).astimezone(UTC).date().isoformat()
    readme = render_release_readme(
        (snapshot / "release" / "README.md").read_text(),
        version=version,
        release_date=release_date,
        domain_count=len(index["specifications"]),
    )
    (package / "README.md").write_text(readme)

    zip_name = f"f5xc-api-specs-v{version}.zip"
    write_deterministic_zip(package, output / zip_name)
    assets = {name: output / name for name in raw_assets}
    assets[zip_name] = output / zip_name
    return assets


def build_reproducible_release_assets(
    snapshot: Path,
    version: str,
    commit: str,
    output_root: Path,
) -> dict[str, Path]:
    """Build all five public assets twice and require identical bytes."""
    first = build_release_assets(snapshot, version, commit, output_root / "first")
    second = build_release_assets(snapshot, version, commit, output_root / "second")
    expected = expected_asset_names(version)
    if set(first) != expected or set(second) != expected:
        raise RuntimeError("reproducible build did not produce the exact five release assets")
    different = sorted(
        name
        for name in expected
        if first[name].stat().st_size != second[name].stat().st_size
        or sha256(first[name]) != sha256(second[name])
        or first[name].read_bytes() != second[name].read_bytes()
    )
    if different:
        raise RuntimeError(
            "two fresh release-asset builds differ byte-for-byte: " + ", ".join(different)
        )
    return first


def repository_name() -> str:
    """Resolve the owner/repository used for GitHub release calls."""
    configured = os.environ.get("GITHUB_REPOSITORY", "")
    if configured:
        return configured
    return run_command(
        "gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"
    ).stdout.strip()


def ensure_tag(tag: str, commit: str) -> None:
    """Create a missing remote tag or verify its immutable target."""
    remote_target = remote_tag_target(tag)
    if remote_target is not None:
        if remote_target != commit:
            raise RuntimeError(f"remote tag {tag} targets {remote_target}, expected {commit}")
        return

    local = run_command(
        "git",
        "rev-parse",
        "--verify",
        f"refs/tags/{tag}^{{commit}}",
        check=False,
    )
    if local.returncode == 0 and local.stdout.strip() != commit:
        raise RuntimeError(f"local tag {tag} targets {local.stdout.strip()}, expected {commit}")
    if local.returncode != 0:
        run_command("git", "tag", tag, commit)
    pushed = run_command("git", "push", "origin", f"refs/tags/{tag}", check=False)
    remote_target = remote_tag_target(tag)
    if remote_target != commit:
        detail = pushed.stderr.strip() or "remote tag is still absent"
        raise RuntimeError(f"could not publish tag {tag} at {commit}: {detail}")


def remote_tag_target(tag: str) -> str | None:
    """Return the remote tag's peeled commit, without trusting local refs."""
    result = run_command(
        "git",
        "ls-remote",
        "--tags",
        "origin",
        f"refs/tags/{tag}",
        f"refs/tags/{tag}^{{}}",
    )
    direct: str | None = None
    peeled: str | None = None
    for line in result.stdout.splitlines():
        target, ref = line.split("\t", 1)
        if ref.endswith("^{}"):
            peeled = target
        else:
            direct = target
    return peeled or direct


def verify_existing_tag(tag: str, commit: str) -> None:
    """Require an existing remote tag to dereference to *commit*."""
    target = remote_tag_target(tag)
    if target != commit:
        raise RuntimeError(f"tag {tag} targets {target or 'nothing'}, expected {commit}")


def github_release(repo: str, tag: str) -> dict[str, Any] | None:
    """Return a release object, distinguishing absence from API failure."""
    result = run_command("gh", "api", f"repos/{repo}/releases/tags/{tag}", check=False)
    if result.returncode == 0:
        document = json.loads(result.stdout)
        if not isinstance(document, dict):
            raise RuntimeError("GitHub release response is not an object")
        return document
    if "HTTP 404" in result.stderr:
        return None
    raise RuntimeError(f"cannot query release {tag}: {result.stderr}")


def download_asset(repo: str, tag: str, name: str, destination: Path) -> Path:
    """Download one named release asset into an empty directory."""
    destination.mkdir(parents=True, exist_ok=True)
    run_command(
        "gh",
        "release",
        "download",
        tag,
        "--repo",
        repo,
        "--pattern",
        name,
        "--dir",
        str(destination),
    )
    path = destination / name
    if not path.is_file():
        raise RuntimeError(f"GitHub did not download expected release asset {name}")
    return path


def verify_remote_assets(repo: str, tag: str, expected: dict[str, Path], root: Path) -> None:
    """Download and hash every expected release asset."""
    release = github_release(repo, tag)
    if release is None:
        raise RuntimeError(f"cannot verify absent release {tag}")
    verify_remote_asset_digests(
        repo,
        tag,
        release,
        {name: sha256(path) for name, path in expected.items()},
        root,
    )


def release_asset_names(release: dict[str, Any]) -> set[str]:
    """Return a release's strict, duplicate-free asset name set."""
    raw_assets = release.get("assets", [])
    if not isinstance(raw_assets, list):
        raise TypeError("GitHub release assets are not a list")
    names: list[str] = []
    for asset in raw_assets:
        name = asset.get("name") if isinstance(asset, dict) else None
        if not isinstance(name, str) or not name:
            raise RuntimeError("GitHub release contains an invalid asset name")
        names.append(name)
    if len(names) != len(set(names)):
        raise RuntimeError("GitHub release contains duplicate asset names")
    return set(names)


def release_asset_digests(release: dict[str, Any]) -> dict[str, str]:
    """Return strict GitHub API SHA-256 digests keyed by unique asset name."""
    raw_assets = release.get("assets", [])
    if not isinstance(raw_assets, list):
        raise TypeError("GitHub release assets are not a list")
    digests: dict[str, str] = {}
    for asset in raw_assets:
        if not isinstance(asset, dict):
            raise TypeError("GitHub release contains an invalid asset")
        name = asset.get("name")
        digest = asset.get("digest")
        if not isinstance(name, str) or not name or name in digests:
            raise RuntimeError("GitHub release contains duplicate or invalid asset names")
        if (
            not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or not SHA256_HEX.fullmatch(digest.removeprefix("sha256:"))
        ):
            raise RuntimeError(f"GitHub release asset has no immutable SHA-256 digest: {name}")
        digests[name] = digest.removeprefix("sha256:")
    return digests


def verify_remote_asset_digests(
    repo: str,
    tag: str,
    release: dict[str, Any],
    expected: dict[str, str],
    root: Path,
) -> None:
    """Verify the exact remote asset set against durable SHA-256 receipts."""
    remote_names = release_asset_names(release)
    expected_names = set(expected)
    if remote_names != expected_names:
        raise RuntimeError(
            f"release asset set differs: expected {sorted(expected_names)}, "
            f"found {sorted(remote_names)}"
        )
    api_digests = release_asset_digests(release)
    for name, expected_digest in expected.items():
        if api_digests[name] != expected_digest:
            raise RuntimeError(f"GitHub API digest differs from publication receipt: {name}")
        downloaded = download_asset(repo, tag, name, root / name)
        if sha256(downloaded) != expected_digest:
            raise RuntimeError(f"published release asset differs from its receipt: {name}")


def verify_published_release(
    repo: str,
    tag: str,
    version: str,
    commit: str,
    release: dict[str, Any],
    root: Path,
    expected: PublicationReceipt | None = None,
) -> PublicationReceipt:
    """Verify a sealed published release without rebuilding historical assets."""
    if bool(release.get("draft")):
        raise RuntimeError(f"release {tag} is still a draft")
    if bool(release.get("prerelease")):
        raise RuntimeError(f"release {tag} is unexpectedly a prerelease")
    if release.get("immutable") is not True:
        raise RuntimeError(f"release {tag} is not immutable")
    if release.get("tag_name") != tag:
        raise RuntimeError(f"release API identity differs from requested tag {tag}")
    verify_existing_tag(tag, canonical_commit(commit))
    require_source_provenance(
        str(release.get("body") or ""),
        source_provenance_at(commit),
    )
    receipt = require_matching_receipt(release, version, commit, expected)
    verify_remote_asset_digests(repo, tag, release, receipt.assets, root)
    return receipt


def _require_mutable_draft(
    repo: str,
    tag: str,
    source_provenance: SourceProvenance,
) -> dict[str, Any]:
    """Refetch and require an unsealed draft immediately before mutation."""
    release = github_release(repo, tag)
    if release is None:
        raise RuntimeError(f"release {tag} disappeared during reconciliation")
    if not bool(release.get("draft")):
        raise RuntimeError(f"release {tag} was published before draft reconciliation completed")
    require_source_provenance(str(release.get("body") or ""), source_provenance)
    if publication_receipt_from_body(str(release.get("body") or "")) is not None:
        raise RuntimeError(f"draft release {tag} is already sealed by a publication receipt")
    return release


def _reconcile_mutable_draft_assets(
    repo: str,
    tag: str,
    assets: dict[str, Path],
    temp_root: Path,
    source_provenance: SourceProvenance,
) -> dict[str, Any]:
    """Repair an unsealed draft, refusing every mutation after it is sealed."""
    expected_names = set(assets)
    release = _require_mutable_draft(repo, tag, source_provenance)
    unexpected = sorted(release_asset_names(release) - expected_names)
    for name in unexpected:
        _require_mutable_draft(repo, tag, source_provenance)
        result = run_command(
            "gh",
            "release",
            "delete-asset",
            tag,
            name,
            "--repo",
            repo,
            "--yes",
            check=False,
        )
        if result.returncode != 0:
            refreshed = _require_mutable_draft(repo, tag, source_provenance)
            if name in release_asset_names(refreshed):
                raise RuntimeError(
                    f"could not delete unexpected draft asset {name}: {result.stderr}"
                )

    for name, local_path in assets.items():
        release = _require_mutable_draft(repo, tag, source_provenance)
        remote_names = release_asset_names(release)
        if name not in remote_names:
            result = run_command(
                "gh",
                "release",
                "upload",
                tag,
                str(local_path),
                "--repo",
                repo,
                check=False,
            )
            if result.returncode != 0:
                refreshed = _require_mutable_draft(repo, tag, source_provenance)
                if name not in release_asset_names(refreshed):
                    raise RuntimeError(f"could not upload draft asset {name}: {result.stderr}")
            continue

        downloaded = download_asset(repo, tag, name, temp_root / "existing" / name)
        if sha256(downloaded) == sha256(local_path):
            continue
        _require_mutable_draft(repo, tag, source_provenance)
        result = run_command(
            "gh",
            "release",
            "upload",
            tag,
            str(local_path),
            "--repo",
            repo,
            "--clobber",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"could not replace mismatched draft asset {name}: {result.stderr}")

    refreshed = _require_mutable_draft(repo, tag, source_provenance)
    verify_remote_asset_digests(
        repo,
        tag,
        refreshed,
        {name: sha256(path) for name, path in assets.items()},
        temp_root / "verified-draft",
    )
    return refreshed


def _write_draft_receipt(
    repo: str,
    tag: str,
    version: str,
    receipt: PublicationReceipt,
    notes: Path,
    source_provenance: SourceProvenance,
) -> dict[str, Any]:
    """Seal a fully verified draft with its immutable publication receipt."""
    draft = _require_mutable_draft(repo, tag, source_provenance)
    existing_body = str(draft.get("body") or notes.read_text())
    body = ensure_source_provenance(existing_body, source_provenance)
    body = f"{body.rstrip()}\n\n{publication_receipt_marker(receipt)}\n"
    with tempfile.TemporaryDirectory(prefix="publication-receipt-") as temp:
        notes_with_receipt = Path(temp) / "notes.md"
        notes_with_receipt.write_text(body)
        run_command(
            "gh",
            "release",
            "edit",
            tag,
            "--repo",
            repo,
            "--title",
            f"API Specs v{version}",
            "--notes-file",
            str(notes_with_receipt),
        )
    refreshed = github_release(repo, tag)
    if refreshed is None or not bool(refreshed.get("draft")):
        raise RuntimeError(f"release {tag} was not a draft after receipt sealing")
    require_matching_receipt(refreshed, version, receipt.commit, receipt)
    require_source_provenance(str(refreshed.get("body") or ""), source_provenance)
    return refreshed


def publish_sealed_draft(
    repo: str,
    tag: str,
    version: str,
    commit: str,
    release: dict[str, Any],
    root: Path,
    expected: PublicationReceipt | None = None,
) -> dict[str, Any]:
    """Verify and publish a receipt-bearing draft without rebuilding its assets."""
    if not bool(release.get("draft")):
        raise RuntimeError(f"release {tag} is not a draft")
    source_provenance = source_provenance_at(commit)
    require_source_provenance(str(release.get("body") or ""), source_provenance)
    receipt = require_matching_receipt(release, version, commit, expected)
    verify_remote_asset_digests(repo, tag, release, receipt.assets, root / "sealed-draft")

    current = github_release(repo, tag)
    if current is None:
        raise RuntimeError(f"release {tag} disappeared before publication")
    if not bool(current.get("draft")):
        verify_published_release(
            repo,
            tag,
            version,
            commit,
            current,
            root / "concurrent-published",
            expected,
        )
        return current
    require_source_provenance(str(current.get("body") or ""), source_provenance)
    require_matching_receipt(current, version, commit, expected)
    publish = run_command(
        "gh",
        "release",
        "edit",
        tag,
        "--repo",
        repo,
        "--draft=false",
        check=False,
    )

    published = github_release(repo, tag)
    if published is None or bool(published.get("draft")):
        detail = publish.stderr.strip() or "release is still a draft"
        raise RuntimeError(f"release {tag} was not published: {detail}")
    verify_published_release(
        repo,
        tag,
        version,
        commit,
        published,
        root / "published",
        expected,
    )
    return published


def reconcile_release(
    repo: str,
    tag: str,
    version: str,
    commit: str,
    assets: dict[str, Path],
    notes: Path,
    temp_root: Path,
    release: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create, seal, publish, or verify one exact release state."""
    expected_receipt = receipt_for_assets(version, commit, assets)
    source_provenance = source_provenance_at(commit)
    if release is None:
        release = github_release(repo, tag)
    if release is None:
        temp_root.mkdir(parents=True, exist_ok=True)
        notes_with_source = temp_root / "release-notes-with-source-provenance.md"
        notes_with_source.write_text(
            ensure_source_provenance(notes.read_text(), source_provenance),
        )
        created = run_command(
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            repo,
            "--verify-tag",
            "--draft",
            "--title",
            f"API Specs v{version}",
            "--notes-file",
            str(notes_with_source),
            check=False,
        )
        release = github_release(repo, tag)
        if release is None:
            detail = created.stderr.strip() or "release remains absent"
            raise RuntimeError(f"could not create release {tag}: {detail}")

    require_source_provenance(str(release.get("body") or ""), source_provenance)
    if bool(release.get("prerelease")):
        raise RuntimeError(f"release {tag} is unexpectedly a prerelease")
    if not bool(release.get("draft")):
        verify_published_release(
            repo,
            tag,
            version,
            commit,
            release,
            temp_root / "verified-published",
            expected_receipt,
        )
        return release

    existing_receipt = publication_receipt_from_body(str(release.get("body") or ""))
    if existing_receipt is not None:
        return publish_sealed_draft(
            repo,
            tag,
            version,
            commit,
            release,
            temp_root,
            expected_receipt,
        )

    _reconcile_mutable_draft_assets(repo, tag, assets, temp_root, source_provenance)
    release = _write_draft_receipt(
        repo,
        tag,
        version,
        expected_receipt,
        notes,
        source_provenance,
    )
    return publish_sealed_draft(
        repo,
        tag,
        version,
        commit,
        release,
        temp_root,
        expected_receipt,
    )


def completion_marker(release_commit: str, docs_commit: str) -> str:
    """Return the exact release/docs deployment completion receipt."""
    return f"{COMPLETE_MARKER_PREFIX}{release_commit}:{docs_commit} -->"


def completed_docs_commit(
    body: str,
    version: str,
    release_commit: str,
    main_ref: str,
) -> str | None:
    """Return the validated docs commit from the sole completion receipt."""
    markers = [line for line in body.splitlines() if COMPLETE_MARKER_PREFIX in line]
    if not markers:
        return None
    if len(markers) != 1:
        raise RuntimeError("release body must contain exactly one completion receipt")
    match = re.fullmatch(
        r"<!-- publication-complete:([^:]+):([^ ]+) -->",
        markers[0],
    )
    if match is None:
        raise RuntimeError("release body contains a malformed completion receipt")
    recorded_release, recorded_docs = match.groups()
    if (
        not COMMIT_HEX.fullmatch(recorded_release)
        or not COMMIT_HEX.fullmatch(recorded_docs)
        or recorded_release != release_commit
    ):
        raise RuntimeError("completion receipt identity differs from the release")
    return validate_docs_commit(version, release_commit, recorded_docs, main_ref)


def mark_complete(
    repo: str,
    version: str,
    commit: str,
    docs_commit: str,
    main_ref: str = "origin/main",
) -> None:
    """Record successful Pages verification and downstream dispatch."""
    tag = f"v{version}"
    release = github_release(repo, tag)
    if release is None:
        raise RuntimeError(f"cannot mark absent release {tag} complete")
    with tempfile.TemporaryDirectory(prefix="completion-verification-") as temp:
        root = Path(temp)
        snapshot = root / "snapshot"
        extract_snapshot(commit, snapshot)
        assets = build_reproducible_release_assets(
            snapshot,
            version,
            commit,
            root / "asset-builds",
        )
        expected = receipt_for_assets(version, commit, assets)
        verify_published_release(
            repo,
            tag,
            version,
            commit,
            release,
            root / "published",
            expected,
        )
    marker = completion_marker(commit, docs_commit)
    body = release.get("body") or ""
    completed_commit = completed_docs_commit(body, version, commit, main_ref)
    if completed_commit is not None:
        if completion_docs_identity(completed_commit) == completion_docs_identity(docs_commit):
            return
        body = "\n".join(
            line for line in body.splitlines() if COMPLETE_MARKER_PREFIX not in line
        ).rstrip()
    with tempfile.TemporaryDirectory(prefix="publication-marker-") as temp:
        notes = Path(temp) / "notes.md"
        notes.write_text(f"{body.rstrip()}\n\n{marker}\n")
        run_command("gh", "release", "edit", tag, "--repo", repo, "--notes-file", str(notes))
    verified = github_release(repo, tag)
    if verified is None or marker not in (verified.get("body") or ""):
        raise RuntimeError(f"release {tag} completion marker was not persisted")
    if (
        completed_docs_commit(str(verified.get("body") or ""), version, commit, main_ref)
        != docs_commit
    ):
        raise RuntimeError(f"release {tag} completion identity was not persisted")
    require_source_provenance(
        str(verified.get("body") or ""),
        source_provenance_at(commit),
    )
    require_matching_receipt(verified, version, commit, expected)


def write_github_outputs(values: dict[str, str]) -> None:
    """Append outputs when running under GitHub Actions."""
    destination = os.environ.get("GITHUB_OUTPUT")
    if not destination:
        return
    for key, value in values.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"invalid GitHub output name: {key!r}")
        if "\n" in value or "\r" in value:
            raise ValueError(f"GitHub output {key!r} contains a line break")
    with Path(destination).open("a") as stream:
        stream.writelines(f"{key}={value}\n" for key, value in values.items())


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Version to reconcile; defaults to origin/main")
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--mark-complete", action="store_true")
    parser.add_argument("--target-commit")
    parser.add_argument("--docs-commit")
    args = parser.parse_args()

    repo = repository_name()
    if args.mark_complete:
        if not args.version or not args.target_commit or not args.docs_commit:
            parser.error("--mark-complete requires --version, --target-commit, and --docs-commit")
        expected_commit = resolve_target_commit(
            args.version,
            args.main_ref,
            args.target_commit,
        )
        verify_existing_tag(f"v{args.version}", expected_commit)
        resolved_docs_commit = validate_docs_commit(
            args.version,
            expected_commit,
            args.docs_commit,
            args.main_ref,
        )
        mark_complete(repo, args.version, expected_commit, resolved_docs_commit, args.main_ref)
        return 0

    current_version = version_at(args.main_ref)
    version = args.version or current_version
    commit = resolve_target_commit(version, args.main_ref, args.target_commit)
    tag = f"v{version}"
    docs_commit = canonical_commit(args.main_ref)
    docs_match_release = release_docs_identity(docs_commit) == release_docs_identity(commit)
    historical_request = args.version is not None and version != current_version
    if not historical_request and not docs_match_release:
        raise RuntimeError(
            f"current {args.main_ref} generated documentation differs from release {tag} at {commit}"
        )
    ensure_tag(tag, commit)

    with tempfile.TemporaryDirectory(prefix=f"publish-{tag}-") as temp:
        root = Path(temp)
        snapshot = root / "snapshot"
        extract_snapshot(commit, snapshot)
        assets = build_reproducible_release_assets(
            snapshot,
            version,
            commit,
            root / "asset-builds",
        )
        notes = snapshot / "CHANGELOG.md"
        if not notes.is_file():
            raise RuntimeError(f"{commit} does not contain CHANGELOG.md")
        release = reconcile_release(
            repo,
            tag,
            version,
            commit,
            assets,
            notes,
            root,
            github_release(repo, tag),
        )

    is_current = not historical_request and docs_match_release
    completed_commit = completed_docs_commit(
        str(release.get("body") or ""),
        version,
        commit,
        args.main_ref,
    )
    completion_is_current = completed_commit is not None and completion_docs_identity(
        completed_commit
    ) == completion_docs_identity(docs_commit)
    outputs = {
        "version": version,
        "tag": tag,
        "target_commit": commit,
        "docs_commit": docs_commit,
        "release_url": str(release.get("html_url", "")),
        "assets_verified": "true",
        "is_current": str(is_current).lower(),
        # Publication completion is historical evidence, not continuing proof
        # that downstream authorities still serve the receipted bytes. Keep
        # mutation and audit decisions independent so every current
        # reconciliation can re-verify external truth without republishing.
        "publish_needed": str(is_current and not completion_is_current).lower(),
        "audit_needed": str(is_current).lower(),
    }
    write_github_outputs(outputs)
    print(json.dumps(outputs, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
