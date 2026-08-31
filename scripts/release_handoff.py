#!/usr/bin/env python3
"""Create and verify immutable release handoffs between runner trust zones."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
CANDIDATE_KEYS = {
    "schema_version",
    "kind",
    "source_commit",
    "upstream_digest",
    "version",
    "pipeline_fingerprint",
    "roots",
    "files",
    "tree_digest",
}
PACKAGE_KEYS = {
    "schema_version",
    "kind",
    "release_commit",
    "candidate_manifest_digest",
    "artifact",
    "archive",
    "assets",
    "asset_set_digest",
}


class HandoffError(ValueError):
    """Raised when a release handoff is incomplete or has been modified."""


def canonical_json(value: Any) -> bytes:
    """Encode JSON deterministically."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest_bytes(value: bytes) -> str:
    """Return an algorithm-qualified SHA-256 digest."""
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def digest_file(path: Path) -> str:
    """Return the SHA-256 digest for one regular file."""
    return digest_bytes(path.read_bytes())


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise HandoffError(f"unsafe relative path: {value}")
    return path


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise HandoffError(f"{field} must be a SHA-256 digest")
    return value


def _require_commit(value: object, field: str) -> str:
    if not isinstance(value, str) or not COMMIT_RE.fullmatch(value):
        raise HandoffError(f"{field} must be a full lowercase commit SHA")
    return value


def _require_version(value: object) -> str:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise HandoffError("version must be MAJOR.MINOR.PATCH")
    return value


def _files_for_roots(root: Path, roots: list[str]) -> list[dict[str, Any]]:
    paths: list[Path] = []
    for raw in roots:
        relative = _safe_relative(raw)
        target = root.joinpath(*relative.parts)
        if target.is_symlink():
            raise HandoffError(f"symlinks are not allowed in handoffs: {raw}")
        if target.is_file():
            paths.append(target)
        elif target.is_dir():
            for path in target.rglob("*"):
                if path.is_symlink():
                    raise HandoffError(
                        f"symlinks are not allowed in handoffs: {path.relative_to(root)}"
                    )
                if path.is_file():
                    paths.append(path)
        else:
            raise HandoffError(f"handoff root does not exist: {raw}")
    unique = sorted(set(paths), key=lambda item: item.relative_to(root).as_posix())
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": digest_file(path),
        }
        for path in unique
    ]


def _validate_files(root: Path, roots: list[str], files: object, tree_digest: object) -> None:
    if not isinstance(files, list) or not files:
        raise HandoffError("files must be a non-empty array")
    if any(not isinstance(item, dict) or set(item) != {"path", "size", "sha256"} for item in files):
        raise HandoffError("file entries use an unsupported schema")
    expected_paths = [str(item["path"]) for item in files]
    if expected_paths != sorted(expected_paths) or len(expected_paths) != len(set(expected_paths)):
        raise HandoffError("file paths must be unique and sorted")
    for item in files:
        _safe_relative(str(item["path"]))
        if isinstance(item["size"], bool) or not isinstance(item["size"], int) or item["size"] < 0:
            raise HandoffError("file size must be a non-negative integer")
        _require_digest(item["sha256"], "file sha256")
    actual = _files_for_roots(root, roots)
    if actual != files:
        raise HandoffError("handoff files are missing, unexpected, or modified")
    if digest_bytes(canonical_json(files)) != _require_digest(tree_digest, "tree_digest"):
        raise HandoffError("handoff tree digest does not match")


def build_candidate_manifest(
    root: Path,
    roots: list[str],
    source_commit: str,
    upstream_digest: str,
    version: str,
    pipeline_fingerprint: str,
) -> dict[str, Any]:
    """Build a schema-v1 manifest over every generated candidate file."""
    _require_commit(source_commit, "source_commit")
    _require_digest(upstream_digest, "upstream_digest")
    _require_digest(pipeline_fingerprint, "pipeline_fingerprint")
    _require_version(version)
    normalized_roots = sorted({_safe_relative(item).as_posix() for item in roots})
    files = _files_for_roots(root, normalized_roots)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "release-candidate",
        "source_commit": source_commit,
        "upstream_digest": upstream_digest,
        "version": version,
        "pipeline_fingerprint": pipeline_fingerprint,
        "roots": normalized_roots,
        "files": files,
        "tree_digest": digest_bytes(canonical_json(files)),
    }


def validate_candidate_manifest(
    root: Path,
    manifest: dict[str, Any],
    *,
    source_commit: str | None = None,
    upstream_digest: str | None = None,
    version: str | None = None,
    pipeline_fingerprint: str | None = None,
) -> None:
    """Fail closed unless a candidate and all expected identities match."""
    if set(manifest) != CANDIDATE_KEYS or manifest.get("schema_version") != SCHEMA_VERSION:
        raise HandoffError("unsupported candidate manifest schema")
    if manifest.get("kind") != "release-candidate":
        raise HandoffError("candidate manifest kind is invalid")
    _require_commit(manifest.get("source_commit"), "source_commit")
    _require_digest(manifest.get("upstream_digest"), "upstream_digest")
    _require_digest(manifest.get("pipeline_fingerprint"), "pipeline_fingerprint")
    _require_version(manifest.get("version"))
    expected = {
        "source_commit": source_commit,
        "upstream_digest": upstream_digest,
        "version": version,
        "pipeline_fingerprint": pipeline_fingerprint,
    }
    for field, value in expected.items():
        if value is not None and manifest.get(field) != value:
            raise HandoffError(f"candidate {field} does not match")
    roots = manifest.get("roots")
    if not isinstance(roots, list) or not roots or any(not isinstance(item, str) for item in roots):
        raise HandoffError("candidate roots must be a non-empty string array")
    normalized = sorted({_safe_relative(item).as_posix() for item in roots})
    if roots != normalized:
        raise HandoffError("candidate roots must be unique and sorted")
    _validate_files(root, normalized, manifest.get("files"), manifest.get("tree_digest"))


def stage_candidate(root: Path, stage: Path, manifest: dict[str, Any]) -> Path:
    """Copy only manifest-covered candidate files into a fresh staging tree."""
    if stage.exists() and any(stage.iterdir()):
        raise HandoffError("candidate staging directory must be empty")
    stage.mkdir(parents=True, exist_ok=True)
    for item in manifest["files"]:
        relative = _safe_relative(item["path"])
        destination = stage.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root.joinpath(*relative.parts), destination)
    metadata = stage / ".handoff" / "candidate-manifest.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_bytes(canonical_json(manifest))
    return metadata


def apply_candidate(stage: Path, destination: Path, manifest: dict[str, Any]) -> None:
    """Replace generated roots only after the staged candidate validates."""
    validate_candidate_manifest(stage, manifest)
    for raw in manifest["roots"]:
        relative = _safe_relative(raw)
        source = stage.joinpath(*relative.parts)
        target = destination.joinpath(*relative.parts)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copyfile(source, target)
    _validate_files(destination, manifest["roots"], manifest["files"], manifest["tree_digest"])


def deterministic_archive(root: Path, output: Path, timestamp: int) -> str:
    """Create a byte-reproducible ZIP using sorted entries and fixed metadata."""
    moment = datetime.fromtimestamp(timestamp, timezone.utc)
    if moment.year < 1980:
        moment = datetime(1980, 1, 1, tzinfo=timezone.utc)
    date_time = (
        moment.year,
        moment.month,
        moment.day,
        moment.hour,
        moment.minute,
        moment.second // 2 * 2,
    )
    files = _files_for_roots(root, [path.name for path in root.iterdir()])
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for item in files:
            info = zipfile.ZipInfo(item["path"], date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, (root / item["path"]).read_bytes())
    return digest_file(output)


def build_package_handoff(
    root: Path,
    candidate_manifest: Path,
    release_commit: str,
    artifact_name: str,
    artifact_digest: str,
    archive: str,
    assets: list[str],
) -> dict[str, Any]:
    """Bind the release commit, candidate, package artifact, archive, and assets."""
    _require_commit(release_commit, "release_commit")
    if not artifact_name:
        raise HandoffError("artifact.name must be non-empty")
    _require_digest(artifact_digest, "artifact.digest")
    archive_path = _safe_relative(archive).as_posix()
    normalized_assets = sorted(_safe_relative(item).as_posix() for item in assets)
    if len(normalized_assets) != len(set(normalized_assets)):
        raise HandoffError("asset paths must be unique")
    if archive_path not in normalized_assets:
        raise HandoffError("archive must be included in the asset set")
    entries = _files_for_roots(root, normalized_assets)
    candidate_digest = digest_file(candidate_manifest)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "release-package",
        "release_commit": release_commit,
        "candidate_manifest_digest": candidate_digest,
        "artifact": {"name": artifact_name, "digest": artifact_digest},
        "archive": {"path": archive_path, "digest": digest_file(root / archive_path)},
        "assets": entries,
        "asset_set_digest": digest_bytes(canonical_json(entries)),
    }


def validate_package_handoff(
    root: Path,
    handoff: dict[str, Any],
    candidate_manifest: Path,
    release_commit: str,
    artifact_name: str,
    artifact_digest: str,
) -> None:
    """Verify the complete package handoff before any publication write."""
    if set(handoff) != PACKAGE_KEYS or handoff.get("schema_version") != SCHEMA_VERSION:
        raise HandoffError("unsupported package handoff schema")
    if handoff.get("kind") != "release-package":
        raise HandoffError("package handoff kind is invalid")
    _require_commit(handoff.get("release_commit"), "release_commit")
    if handoff["release_commit"] != release_commit:
        raise HandoffError("package release_commit does not match")
    _require_digest(handoff.get("candidate_manifest_digest"), "candidate_manifest_digest")
    if handoff.get("candidate_manifest_digest") != digest_file(candidate_manifest):
        raise HandoffError("candidate manifest digest does not match")
    artifact = handoff.get("artifact")
    if not isinstance(artifact, dict) or set(artifact) != {"name", "digest"}:
        raise HandoffError("artifact identity is malformed")
    if not isinstance(artifact.get("name"), str) or not artifact["name"]:
        raise HandoffError("artifact.name must be non-empty")
    _require_digest(artifact.get("digest"), "artifact.digest")
    if artifact != {"name": artifact_name, "digest": artifact_digest}:
        raise HandoffError("artifact identity does not match")
    archive = handoff.get("archive")
    if not isinstance(archive, dict) or set(archive) != {"path", "digest"}:
        raise HandoffError("archive identity is malformed")
    archive_path = _safe_relative(str(archive["path"])).as_posix()
    if digest_file(root / archive_path) != _require_digest(archive.get("digest"), "archive.digest"):
        raise HandoffError("archive digest does not match")
    assets = handoff.get("assets")
    if not isinstance(assets, list) or any(not isinstance(item, dict) for item in assets):
        raise HandoffError("assets must be an array of file records")
    asset_paths = [str(item.get("path")) for item in assets]
    if archive_path not in asset_paths:
        raise HandoffError("archive must be included in the asset set")
    _validate_files(root, asset_paths, assets, handoff.get("asset_set_digest"))
    actual_paths = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != candidate_manifest
    )
    if actual_paths != asset_paths:
        raise HandoffError("package contains missing or unexpected files")


def _validate_manifest_digest(path: Path, expected: str) -> None:
    _require_digest(expected, "manifest_digest")
    if digest_file(path) != expected:
        raise HandoffError("candidate manifest digest does not match")


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HandoffError(f"cannot read handoff: {path}") from error
    if not isinstance(value, dict):
        raise HandoffError("handoff must be a JSON object")
    return value


def main() -> int:
    """Run release handoff creation and verification commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    candidate = commands.add_parser("candidate-create")
    candidate.add_argument("--root", type=Path, default=Path())
    candidate.add_argument("--stage", type=Path, required=True)
    candidate.add_argument("--source-commit", required=True)
    candidate.add_argument("--upstream-digest", required=True)
    candidate.add_argument("--version", required=True)
    candidate.add_argument("--pipeline-fingerprint", required=True)
    candidate.add_argument("--path", action="append", required=True)
    verify = commands.add_parser("candidate-verify")
    verify.add_argument("--root", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--source-commit")
    verify.add_argument("--upstream-digest")
    verify.add_argument("--version")
    verify.add_argument("--pipeline-fingerprint")
    verify.add_argument("--manifest-digest")
    apply = commands.add_parser("candidate-apply")
    apply.add_argument("--stage", type=Path, required=True)
    apply.add_argument("--destination", type=Path, required=True)
    archive = commands.add_parser("archive")
    archive.add_argument("--root", type=Path, required=True)
    archive.add_argument("--output", type=Path, required=True)
    archive.add_argument("--timestamp", type=int, required=True)
    package = commands.add_parser("package-create")
    package.add_argument("--root", type=Path, required=True)
    package.add_argument("--candidate-manifest", type=Path, required=True)
    package.add_argument("--release-commit", required=True)
    package.add_argument("--artifact-name", required=True)
    package.add_argument("--artifact-digest", required=True)
    package.add_argument("--archive", required=True)
    package.add_argument("--asset", action="append", required=True)
    package.add_argument("--output", type=Path, required=True)
    package_verify = commands.add_parser("package-verify")
    package_verify.add_argument("--root", type=Path, required=True)
    package_verify.add_argument("--handoff", type=Path, required=True)
    package_verify.add_argument("--candidate-manifest", type=Path, required=True)
    package_verify.add_argument("--release-commit", required=True)
    package_verify.add_argument("--artifact-name", required=True)
    package_verify.add_argument("--artifact-digest", required=True)
    args = parser.parse_args()

    try:
        if args.command == "candidate-create":
            manifest = build_candidate_manifest(
                args.root,
                args.path,
                args.source_commit,
                args.upstream_digest,
                args.version,
                args.pipeline_fingerprint,
            )
            path = stage_candidate(args.root, args.stage, manifest)
            print(digest_file(path))
            return 0
        if args.command == "candidate-verify":
            if args.manifest_digest is not None:
                _validate_manifest_digest(args.manifest, args.manifest_digest)
            manifest = _load(args.manifest)
            validate_candidate_manifest(
                args.root,
                manifest,
                source_commit=args.source_commit,
                upstream_digest=args.upstream_digest,
                version=args.version,
                pipeline_fingerprint=args.pipeline_fingerprint,
            )
            return 0
        if args.command == "candidate-apply":
            manifest = _load(args.stage / ".handoff" / "candidate-manifest.json")
            apply_candidate(args.stage, args.destination, manifest)
            return 0
        if args.command == "archive":
            print(deterministic_archive(args.root, args.output, args.timestamp))
            return 0
        if args.command == "package-create":
            handoff = build_package_handoff(
                args.root,
                args.candidate_manifest,
                args.release_commit,
                args.artifact_name,
                args.artifact_digest,
                args.archive,
                args.asset,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(canonical_json(handoff))
            print(digest_file(args.output))
            return 0
        if args.command == "package-verify":
            validate_package_handoff(
                args.root,
                _load(args.handoff),
                args.candidate_manifest,
                args.release_commit,
                args.artifact_name,
                args.artifact_digest,
            )
            return 0
    except HandoffError as error:
        parser.error(str(error))
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
