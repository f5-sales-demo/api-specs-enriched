#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Verify every required published file against one same-run Pages artifact."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import stat
import sys
import tarfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import IO, TYPE_CHECKING, Any, Protocol
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import requests

from scripts.release.pages_content import ALLOWED_SOURCE_ENTRIES

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

ARTIFACT_NAME = "github-pages"
PUBLICATION_MANIFEST_PATH = "api/publication-manifest.json"
REQUIRED_SUPPORT_PATHS = frozenset({"api/revision.json", PUBLICATION_MANIFEST_PATH})
COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_ZIP_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARTIFACT_TAR_BYTES = 8 * 1024 * 1024 * 1024
MAX_RENDERED_FILE_BYTES = 128 * 1024 * 1024
MAX_RENDERED_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
MAX_TAR_MEMBERS = 200_000
MAX_VERIFIED_FILES = 20_000
MAX_TAR_EXTENSION_BYTES = 1024 * 1024
MAX_TAR_EXTENSION_TOTAL_BYTES = 64 * 1024 * 1024
MAX_TAR_PATH_LENGTH = 4096
SHA256_DIGEST = re.compile(r"^sha256:(?P<value>[0-9a-f]{64})$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
LOCALE_ROUTE_ROOT = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$")


class PagesVerificationError(RuntimeError):
    """The deployed Pages site cannot be proven equal to its build artifact."""


@dataclass(frozen=True)
class ArtifactDownload:
    """Bound download endpoint and digest for one same-run artifact."""

    url: str
    sha256: str


@dataclass(frozen=True)
class SpooledArtifactFile:
    """One required artifact file retained on bounded temporary storage."""

    path: Path
    size: int
    sha256: str


class ByteFetcher(Protocol):
    """Read one bounded HTTP response as bytes."""

    def __call__(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        max_bytes: int,
        label: str,
    ) -> bytes:
        """Fetch *url* or raise a verification error."""


class RequestsFetcher:
    """Bounded requests-based HTTP reader used by the workflow CLI."""

    def __init__(self) -> None:
        """Create one connection-reusing HTTP session."""
        self._session = requests.Session()

    def __call__(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        max_bytes: int,
        label: str,
    ) -> bytes:
        """Fetch one response without exposing its URL or credentials in errors."""
        try:
            with self._session.get(
                url,
                headers=dict(headers),
                allow_redirects=True,
                stream=True,
                timeout=(15, 120),
            ) as response:
                if label.startswith("remote "):
                    _validate_remote_response(url, response)
                return _bounded_response_bytes(response, max_bytes, label)
        except PagesVerificationError:
            raise
        except requests.RequestException as exc:
            raise PagesVerificationError(f"HTTP request failed while fetching {label}") from exc

    def download(
        self,
        url: str,
        destination: Path,
        *,
        headers: Mapping[str, str],
        max_bytes: int,
        label: str,
    ) -> str:
        """Stream one bounded response to an exclusive temporary file."""
        try:
            with self._session.get(
                url,
                headers=dict(headers),
                allow_redirects=True,
                stream=True,
                timeout=(15, 120),
            ) as response:
                return _stream_response_to_path(response, destination, max_bytes, label)
        except PagesVerificationError:
            raise
        except (OSError, requests.RequestException) as exc:
            raise PagesVerificationError(
                f"artifact download failed while fetching {label}"
            ) from exc


def _bounded_response_bytes(
    response: requests.Response,
    max_bytes: int,
    label: str,
) -> bytes:
    if response.status_code != 200:
        raise PagesVerificationError(f"HTTP {response.status_code} while fetching {label}")
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise PagesVerificationError(f"invalid Content-Length while fetching {label}") from exc
        if declared_size < 0 or declared_size > max_bytes:
            raise PagesVerificationError(f"oversized response while fetching {label}")

    chunks: list[bytes] = []
    received = 0
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if not chunk:
            continue
        received += len(chunk)
        if received > max_bytes:
            raise PagesVerificationError(f"oversized response while fetching {label}")
        chunks.append(chunk)
    return b"".join(chunks)


def _stream_response_to_path(
    response: requests.Response,
    destination: Path,
    max_bytes: int,
    label: str,
) -> str:
    _validate_response(response, max_bytes, label)
    received = 0
    digest = hashlib.sha256()
    with destination.open("xb") as stream:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            received += len(chunk)
            if received > max_bytes:
                raise PagesVerificationError(f"oversized response while fetching {label}")
            stream.write(chunk)
            digest.update(chunk)
    return digest.hexdigest()


def _validate_response(response: requests.Response, max_bytes: int, label: str) -> None:
    if response.status_code != 200:
        raise PagesVerificationError(f"HTTP {response.status_code} while fetching {label}")
    content_length = response.headers.get("Content-Length")
    if content_length is None:
        return
    try:
        declared_size = int(content_length)
    except ValueError as exc:
        raise PagesVerificationError(f"invalid Content-Length while fetching {label}") from exc
    if declared_size < 0 or declared_size > max_bytes:
        raise PagesVerificationError(f"oversized response while fetching {label}")


def _json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PagesVerificationError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise PagesVerificationError(f"{label} must contain a JSON object")
    return document


def _github_headers(token: str) -> dict[str, str]:
    if not token:
        raise PagesVerificationError("GitHub token is required")
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "api-specs-enriched-pages-verifier",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _remote_headers() -> dict[str, str]:
    return {"Accept-Encoding": "identity"}


def download_same_run_artifact(
    repository: str,
    run_id: int,
    token: str,
    fetch: ByteFetcher,
) -> bytes:
    """Download the unique, unexpired ``github-pages`` artifact from one run."""
    artifact = same_run_artifact_url(repository, run_id, token, fetch)
    payload = fetch(
        artifact.url,
        headers=_github_headers(token),
        max_bytes=MAX_ARTIFACT_ZIP_BYTES,
        label="same-run Pages artifact",
    )
    _verify_artifact_digest(_sha256(payload), artifact.sha256)
    return payload


def _verify_artifact_digest(actual: str, expected: str) -> None:
    if actual != expected:
        raise PagesVerificationError(
            "same-run Pages artifact digest does not match metadata: "
            f"metadata={expected}, downloaded={actual}"
        )


def same_run_artifact_url(
    repository: str,
    run_id: int,
    token: str,
    fetch: ByteFetcher,
) -> ArtifactDownload:
    """Return the bound download details for the unique artifact from one run."""
    if not REPOSITORY.fullmatch(repository):
        raise PagesVerificationError("repository must be an owner/name pair")
    if run_id <= 0:
        raise PagesVerificationError("run id must be positive")

    api_root = f"https://api.github.com/repos/{repository}"
    metadata_url = f"{api_root}/actions/runs/{run_id}/artifacts?" + urlencode(
        {"name": ARTIFACT_NAME, "per_page": 100}
    )
    metadata = _json_object(
        fetch(
            metadata_url,
            headers=_github_headers(token),
            max_bytes=MAX_METADATA_BYTES,
            label="same-run artifact metadata",
        ),
        "same-run artifact metadata",
    )
    total_count = metadata.get("total_count")
    artifacts = metadata.get("artifacts")
    if type(total_count) is not int or not isinstance(artifacts, list):
        raise PagesVerificationError("same-run artifact metadata is malformed")
    if total_count != 1 or len(artifacts) != 1:
        raise PagesVerificationError(
            f"expected exactly one same-run {ARTIFACT_NAME} artifact, found {total_count}"
        )
    artifact = artifacts[0]
    if not isinstance(artifact, dict) or artifact.get("name") != ARTIFACT_NAME:
        raise PagesVerificationError("same-run artifact identity is malformed")
    if artifact.get("expired") is not False:
        raise PagesVerificationError("same-run Pages artifact is expired")
    artifact_id = artifact.get("id")
    workflow_run = artifact.get("workflow_run")
    digest = artifact.get("digest")
    digest_match = SHA256_DIGEST.fullmatch(digest) if isinstance(digest, str) else None
    if (
        type(artifact_id) is not int
        or artifact_id <= 0
        or not isinstance(workflow_run, dict)
        or workflow_run.get("id") != run_id
    ):
        raise PagesVerificationError("same-run artifact binding is malformed")
    if digest_match is None:
        raise PagesVerificationError("same-run artifact SHA-256 digest is malformed")

    return ArtifactDownload(
        url=f"{api_root}/actions/artifacts/{artifact_id}/zip",
        sha256=digest_match.group("value"),
    )


def _safe_zip_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or PurePosixPath(name).is_absolute()
        or PurePosixPath(name).name != name
        or name in {".", ".."}
    ):
        return False
    unix_mode = info.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    return not info.is_dir() and file_type in {0, stat.S_IFREG}


def artifact_tar_bytes(artifact_zip: bytes) -> bytes:
    """Return the only safe tar payload from a downloaded artifact ZIP."""
    try:
        with zipfile.ZipFile(io.BytesIO(artifact_zip)) as archive:
            payload, expected_size = _read_artifact_tar(archive)
    except PagesVerificationError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise PagesVerificationError("Pages artifact is not a readable ZIP") from exc
    if len(payload) != expected_size:
        raise PagesVerificationError("Pages artifact tar size does not match its ZIP metadata")
    return payload


def _read_artifact_tar(archive: zipfile.ZipFile) -> tuple[bytes, int]:
    member = _artifact_tar_member(archive)
    return archive.read(member), member.file_size


def _artifact_tar_member(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    members = archive.infolist()
    if len(members) != 1:
        raise PagesVerificationError(
            f"Pages artifact ZIP must contain exactly one tar, found {len(members)} members"
        )
    member = members[0]
    if not _safe_zip_member(member) or not member.filename.endswith(".tar"):
        raise PagesVerificationError("Pages artifact ZIP member is not one safe tar")
    if member.flag_bits & 0x1:
        raise PagesVerificationError("encrypted Pages artifact ZIP member is unsupported")
    if member.file_size < 0 or member.file_size > MAX_ARTIFACT_TAR_BYTES:
        raise PagesVerificationError("Pages artifact tar exceeds the size limit")
    if member.file_size and member.compress_size <= 0:
        raise PagesVerificationError("Pages artifact tar has an invalid compressed size")
    return member


def _canonical_tar_path(member: tarfile.TarInfo) -> str | None:
    name = member.name
    if (
        not name
        or len(name) > MAX_TAR_PATH_LENGTH
        or "\x00" in name
        or "\\" in name
        or name.startswith("/")
    ):
        raise PagesVerificationError("Pages tar contains an unsafe member path")
    if member.isdir() and name.endswith("/"):
        name = name[:-1]
    parts = name.split("/")
    if parts and parts[0] == ".":
        parts = parts[1:]
    if not parts:
        return None
    if any(part in {"", ".", ".."} for part in parts) or parts[0].endswith(":"):
        raise PagesVerificationError("Pages tar contains an unsafe member path")
    return PurePosixPath(*parts).as_posix()


class _BoundedTarInfo(tarfile.TarInfo):
    """Tar metadata parser that bounds hidden extension headers."""

    def _proc_member(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        if self.type == tarfile.GNUTYPE_SPARSE:
            raise PagesVerificationError("Pages tar contains unsupported sparse metadata")
        if self.type in {
            tarfile.GNUTYPE_LONGNAME,
            tarfile.GNUTYPE_LONGLINK,
            tarfile.XHDTYPE,
            tarfile.XGLTYPE,
            tarfile.SOLARIS_XHDTYPE,
        }:
            self._record_extension(archive)
        return super()._proc_member(archive)  # type: ignore[misc]

    def _record_extension(self, archive: tarfile.TarFile) -> None:
        if self.size < 0 or self.size > MAX_TAR_EXTENSION_BYTES:
            raise PagesVerificationError("Pages tar extension header exceeds the size limit")
        count = int(getattr(archive, "_pages_extension_members", 0)) + 1
        padded_size = ((self.size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE) * tarfile.BLOCKSIZE
        total_size = int(getattr(archive, "_pages_extension_bytes", 0)) + padded_size
        if count > MAX_TAR_MEMBERS:
            raise PagesVerificationError("Pages tar has too many members")
        if total_size > MAX_TAR_EXTENSION_TOTAL_BYTES:
            raise PagesVerificationError("Pages tar extension headers exceed the total size limit")
        archive.__dict__["_pages_extension_members"] = count
        archive.__dict__["_pages_extension_bytes"] = total_size

    def _proc_gnusparse_00(self, *_args: Any) -> None:
        raise PagesVerificationError("Pages tar contains unsupported sparse metadata")

    def _proc_gnusparse_01(self, *_args: Any) -> None:
        raise PagesVerificationError("Pages tar contains unsupported sparse metadata")

    def _proc_gnusparse_10(self, *_args: Any) -> None:
        raise PagesVerificationError("Pages tar contains unsupported sparse metadata")


def parse_pages_tar(payload: bytes) -> dict[str, bytes]:
    """Read regular files from one tar without extracting or following links."""
    try:
        with tarfile.open(
            fileobj=io.BytesIO(payload),
            mode="r:*",
            tarinfo=_BoundedTarInfo,
        ) as archive:
            files = _read_pages_tar(archive, wanted_paths=None, spool_dir=None)
    except PagesVerificationError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise PagesVerificationError("Pages artifact tar is unreadable") from exc

    if not all(isinstance(item, bytes) for item in files.values()):
        raise PagesVerificationError("Pages artifact in-memory parsing failed")
    return {path: item for path, item in files.items() if isinstance(item, bytes)}


def _read_pages_tar(
    archive: tarfile.TarFile,
    wanted_paths: set[str] | None,
    spool_dir: Path | None,
) -> dict[str, bytes | SpooledArtifactFile]:
    files: dict[str, bytes | SpooledArtifactFile] = {}
    member_kinds: dict[str, str] = {}
    total_size = 0
    for member_count, member in enumerate(archive, start=1):
        extension_members = int(getattr(archive, "_pages_extension_members", 0))
        extension_bytes = int(getattr(archive, "_pages_extension_bytes", 0))
        if member_count + extension_members > MAX_TAR_MEMBERS:
            raise PagesVerificationError("Pages tar has too many members")
        path = _canonical_tar_path(member)
        if path is None:
            if not member.isdir():
                raise PagesVerificationError("Pages tar has an invalid root member")
            continue
        if path in member_kinds:
            raise PagesVerificationError(f"Pages tar has duplicate member path {path!r}")
        if member.isdir():
            member_kinds[path] = "directory"
            continue
        if member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}:
            raise PagesVerificationError("Pages tar contains a link or special member")
        if member.size < 0 or member.size > MAX_RENDERED_FILE_BYTES:
            raise PagesVerificationError(f"Pages tar member {path!r} exceeds the size limit")
        total_size += member.size
        if total_size + extension_bytes > MAX_RENDERED_TOTAL_BYTES:
            raise PagesVerificationError("Pages tar exceeds the total size limit")
        member_kinds[path] = "file"
        if wanted_paths is not None and path not in wanted_paths:
            continue
        stream = archive.extractfile(member)
        if stream is None:
            raise PagesVerificationError(f"Pages tar member {path!r} is unreadable")
        if spool_dir is None:
            content = stream.read(member.size + 1)
            if len(content) != member.size:
                raise PagesVerificationError(
                    f"Pages tar member {path!r} size does not match its header"
                )
            files[path] = content
        else:
            files[path] = _spool_artifact_member(stream, member, spool_dir, len(files))
    for path in member_kinds:
        parents = (parent.as_posix() for parent in PurePosixPath(path).parents)
        if any(member_kinds.get(parent) == "file" for parent in parents if parent != "."):
            raise PagesVerificationError("Pages tar contains a file/directory path collision")
    return files


def _spool_artifact_member(
    stream: IO[bytes],
    member: tarfile.TarInfo,
    spool_dir: Path,
    sequence: int,
) -> SpooledArtifactFile:
    destination = spool_dir / f"{sequence:06d}.bin"
    digest = hashlib.sha256()
    received = 0
    with destination.open("xb") as output:
        while received < member.size:
            chunk = stream.read(min(1024 * 1024, member.size - received))
            if not chunk:
                break
            output.write(chunk)
            digest.update(chunk)
            received += len(chunk)
    if received != member.size:
        raise PagesVerificationError(
            f"Pages tar member {member.name!r} size does not match its header"
        )
    return SpooledArtifactFile(destination, received, digest.hexdigest())


def _validate_zero_tar_padding(tar_stream: IO[bytes], member_size: int) -> None:
    remaining = member_size - tar_stream.tell()
    if remaining < tarfile.BLOCKSIZE or remaining % tarfile.BLOCKSIZE:
        raise PagesVerificationError("Pages artifact tar has invalid end padding")
    consumed = 0
    while chunk := tar_stream.read(1024 * 1024):
        consumed += len(chunk)
        if chunk.strip(b"\0"):
            raise PagesVerificationError("Pages artifact tar contains non-padding trailing bytes")
    if consumed != remaining:
        raise PagesVerificationError("Pages artifact tar padding is truncated")


def required_pages_from_artifact(
    artifact_zip: Path,
    spool_dir: Path,
) -> dict[str, SpooledArtifactFile]:
    """Stream one artifact tar and retain every rendered file."""
    spool_dir.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(artifact_zip) as outer:
            member = _artifact_tar_member(outer)
            with outer.open(member) as tar_stream:
                with tarfile.open(
                    fileobj=tar_stream,
                    mode="r|*",
                    bufsize=tarfile.BLOCKSIZE,
                    tarinfo=_BoundedTarInfo,
                ) as archive:
                    scanned = _read_pages_tar(
                        archive,
                        wanted_paths=None,
                        spool_dir=spool_dir,
                    )
                _validate_zero_tar_padding(tar_stream, member.file_size)
    except PagesVerificationError:
        raise
    except (OSError, tarfile.TarError, zipfile.BadZipFile, RuntimeError) as exc:
        raise PagesVerificationError("Pages artifact archive is unreadable") from exc
    if not all(isinstance(item, SpooledArtifactFile) for item in scanned.values()):
        raise PagesVerificationError("Pages artifact spooling failed")
    return {path: item for path, item in scanned.items() if isinstance(item, SpooledArtifactFile)}


def _markdown_routes(
    source_root: Path,
    route_prefix: tuple[str, ...],
    label: str,
) -> dict[str, Path]:
    """Map one safe Markdown tree to its lowercase rendered artifact paths."""
    if not source_root.is_dir() or source_root.is_symlink():
        raise PagesVerificationError(f"{label} root is missing or unsafe")
    entries = sorted(
        source_root.rglob("*"),
        key=lambda path: path.relative_to(source_root).as_posix(),
    )
    if any(path.is_symlink() for path in entries):
        raise PagesVerificationError(f"{label} tree contains a symlink")

    routes: dict[str, Path] = {}
    for source in entries:
        if not source.is_file() or source.suffix not in {".md", ".mdx"}:
            continue
        relative = source.relative_to(source_root)
        stem = relative.stem
        parents = relative.parent.parts
        if stem.lower() == "index":
            route_parts = (*route_prefix, *parents, "index.html")
        else:
            route_parts = (*route_prefix, *parents, stem, "index.html")
        artifact_path = PurePosixPath(*(part.lower() for part in route_parts)).as_posix()
        previous = routes.get(artifact_path)
        if previous is not None:
            raise PagesVerificationError(f"{label} route collision at /{artifact_path}")
        routes[artifact_path] = source
    if not routes:
        raise PagesVerificationError(f"{label} tree has no Markdown routes")
    return dict(sorted(routes.items()))


def documentation_routes(docs_root: Path) -> dict[str, Path]:
    """Map each English Markdown source to its rendered ``/en`` artifact path."""
    return _markdown_routes(docs_root, ("en",), "English documentation")


def generated_api_reference_routes(api_reference_root: Path) -> dict[str, Path]:
    """Map every generated API-reference wrapper to its rendered root route."""
    return _markdown_routes(
        api_reference_root,
        ("api-reference",),
        "generated API reference",
    )


def published_specification_paths(specs_root: Path) -> dict[str, Path]:
    """Return every safe top-level JSON specification keyed by published path."""
    if not specs_root.is_dir() or specs_root.is_symlink():
        raise PagesVerificationError("published specification root is missing or unsafe")
    entries = sorted(specs_root.iterdir(), key=lambda path: path.name)
    if any(path.is_symlink() for path in entries):
        raise PagesVerificationError("published specification tree contains a symlink")
    specifications: dict[str, Path] = {}
    folded_names: set[str] = set()
    for path in entries:
        if path.suffix != ".json":
            continue
        if not path.is_file():
            raise PagesVerificationError("published specification JSON is not a regular file")
        folded = path.name.casefold()
        if folded in folded_names:
            raise PagesVerificationError("published specification paths collide by case")
        folded_names.add(folded)
        specifications[f"specifications/api/{path.name}"] = path
    if not specifications:
        raise PagesVerificationError("published specification tree has no JSON files")
    return specifications


def configured_openapi_routes(openapi_config: Path, specs_root: Path) -> dict[str, Path]:
    """Validate plugin configuration and return every configured base route."""
    if openapi_config.is_symlink() or not openapi_config.is_file():
        raise PagesVerificationError("OpenAPI configuration is missing or unsafe")
    try:
        document = json.loads(openapi_config.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PagesVerificationError("OpenAPI configuration is not valid UTF-8 JSON") from exc
    if not isinstance(document, list) or not document:
        raise PagesVerificationError("OpenAPI configuration must be a nonempty array")

    specifications = published_specification_paths(specs_root)
    routes: dict[str, Path] = {}
    configured_schemas: set[str] = set()
    for index, entry in enumerate(document):
        if not isinstance(entry, dict) or set(entry) != {"base", "schema", "sidebar"}:
            raise PagesVerificationError(f"OpenAPI configuration entry {index} has invalid fields")
        base = entry.get("base")
        schema = entry.get("schema")
        sidebar = entry.get("sidebar")
        if not isinstance(base, str) or not base:
            raise PagesVerificationError(f"OpenAPI configuration entry {index} has invalid base")
        base_path = PurePosixPath(base)
        if (
            base_path.is_absolute()
            or base_path.as_posix() != base
            or len(base_path.parts) < 2
            or base_path.parts[0] != "api-reference"
            or any(part in {"", ".", ".."} for part in base_path.parts)
            or base != base.lower()
        ):
            raise PagesVerificationError(f"OpenAPI configuration entry {index} has unsafe base")
        if (
            not isinstance(schema, str)
            or not schema.startswith("public/specifications/api/")
            or schema.removeprefix("public/") not in specifications
        ):
            raise PagesVerificationError(
                f"OpenAPI configuration entry {index} references an unpublished schema"
            )
        if schema in configured_schemas:
            raise PagesVerificationError("OpenAPI configuration contains a duplicate schema")
        configured_schemas.add(schema)
        if (
            not isinstance(sidebar, dict)
            or set(sidebar) != {"collapsed", "label"}
            or type(sidebar.get("collapsed")) is not bool
            or not isinstance(sidebar.get("label"), str)
            or not sidebar["label"]
        ):
            raise PagesVerificationError(
                f"OpenAPI configuration entry {index} has invalid sidebar metadata"
            )
        route = f"{base}/index.html"
        if route in routes:
            raise PagesVerificationError("OpenAPI configuration contains a duplicate base")
        routes[route] = specifications[schema.removeprefix("public/")]
    return dict(sorted(routes.items()))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise PagesVerificationError("artifact publication manifest has duplicate fields")
        document[key] = value
    return document


def _safe_manifest_path(path: str) -> bool:
    if not path or "\x00" in path or "\\" in path:
        return False
    pure = PurePosixPath(path)
    return (
        not pure.is_absolute()
        and pure.as_posix() == path
        and all(part not in {"", ".", ".."} for part in pure.parts)
        and not pure.parts[0].endswith(":")
    )


def _verify_publication_manifest(
    artifact_files: Mapping[str, SpooledArtifactFile],
) -> None:
    manifest = artifact_files.get(PUBLICATION_MANIFEST_PATH)
    if manifest is None:
        raise PagesVerificationError("Pages artifact is missing the publication manifest")
    if manifest.size > MAX_METADATA_BYTES:
        raise PagesVerificationError("artifact publication manifest exceeds the size limit")
    try:
        document = json.loads(
            manifest.path.read_bytes(),
            object_pairs_hook=_manifest_object,
        )
    except PagesVerificationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PagesVerificationError(
            "artifact publication manifest is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(document, dict) or set(document) != {"version", "files"}:
        raise PagesVerificationError("artifact publication manifest has invalid fields")
    if type(document["version"]) is not int or document["version"] != 1:
        raise PagesVerificationError("artifact publication manifest has an invalid version")
    files = document["files"]
    if not isinstance(files, dict) or not files:
        raise PagesVerificationError("artifact publication manifest has an invalid file map")
    for path, digest in files.items():
        if (
            not isinstance(path, str)
            or not _safe_manifest_path(path)
            or path == PUBLICATION_MANIFEST_PATH
            or not isinstance(digest, str)
            or SHA256_HEX.fullmatch(digest) is None
        ):
            raise PagesVerificationError("artifact publication manifest has an invalid file entry")

    artifact_paths = set(artifact_files) - {PUBLICATION_MANIFEST_PATH}
    manifest_paths = set(files)
    if manifest_paths != artifact_paths:
        unlisted = sorted(artifact_paths - manifest_paths)
        missing = sorted(manifest_paths - artifact_paths)
        raise PagesVerificationError(
            "artifact publication manifest file set does not match the artifact: "
            f"unlisted={unlisted}, missing={missing}"
        )
    for path, expected_digest in files.items():
        if artifact_files[path].sha256 != expected_digest:
            raise PagesVerificationError(
                f"artifact publication manifest digest does not match {path}"
            )


def _reject_non_allowlisted_source_routes(
    artifact_files: Mapping[str, SpooledArtifactFile],
    docs_root: Path,
) -> None:
    source_root = docs_root.parent
    if not source_root.is_dir() or source_root.is_symlink():
        raise PagesVerificationError("documentation source root is missing or unsafe")
    try:
        forbidden_roots = {
            entry.name.casefold()
            for entry in source_root.iterdir()
            if entry.name.casefold() not in ALLOWED_SOURCE_ENTRIES
        }
    except OSError as exc:
        raise PagesVerificationError("documentation source root is unreadable") from exc
    violations = sorted(
        path
        for path in artifact_files
        if (
            PurePosixPath(path).parts[0].casefold() in forbidden_roots
            or (
                PurePosixPath(path).parts[0].casefold() != "en"
                and LOCALE_ROUTE_ROOT.fullmatch(PurePosixPath(path).parts[0].casefold()) is not None
            )
        )
    )
    if violations:
        raise PagesVerificationError(
            f"Pages artifact contains non-allowlisted source routes: {violations}"
        )


def _required_artifact_files(
    artifact_files: Mapping[str, SpooledArtifactFile],
    docs_root: Path,
    api_reference_root: Path,
    specs_root: Path,
    openapi_config: Path,
    target_revision: str,
) -> dict[str, SpooledArtifactFile]:
    _verify_publication_manifest(artifact_files)
    if len(artifact_files) > MAX_VERIFIED_FILES:
        raise PagesVerificationError(
            "Pages artifact exceeds the exhaustive remote verification file-count limit"
        )
    _reject_non_allowlisted_source_routes(artifact_files, docs_root)
    required_paths = _required_artifact_paths(
        docs_root,
        api_reference_root,
        specs_root,
        openapi_config,
    )
    missing = sorted(required_paths - set(artifact_files))
    if missing:
        raise PagesVerificationError(f"Pages artifact is missing required files: {missing}")
    revision_file = artifact_files["api/revision.json"]
    if revision_file.size > MAX_METADATA_BYTES:
        raise PagesVerificationError("artifact api/revision.json exceeds the size limit")
    revision = _json_object(
        revision_file.path.read_bytes(),
        "artifact api/revision.json",
    )
    if revision.get("commit") != target_revision or revision.get("content_ref") != target_revision:
        raise PagesVerificationError("Pages artifact revision does not match the target revision")
    for artifact_path, source_path in published_specification_paths(specs_root).items():
        artifact = artifact_files[artifact_path]
        if artifact.size != source_path.stat().st_size or artifact.sha256 != _file_sha256(
            source_path
        ):
            raise PagesVerificationError(
                f"Pages artifact specification differs from release commit: {artifact_path}"
            )
    return {path: artifact_files[path] for path in sorted(artifact_files)}


def _required_artifact_paths(
    docs_root: Path,
    api_reference_root: Path,
    specs_root: Path,
    openapi_config: Path,
) -> set[str]:
    route_groups = (
        documentation_routes(docs_root),
        generated_api_reference_routes(api_reference_root),
        configured_openapi_routes(openapi_config, specs_root),
    )
    required = set(REQUIRED_SUPPORT_PATHS) | set(published_specification_paths(specs_root))
    for routes in route_groups:
        collisions = required & set(routes)
        if collisions:
            raise PagesVerificationError(
                f"publication contract contains route collisions: {sorted(collisions)}"
            )
        required.update(routes)
    return required


def _remote_url(base_url: str, artifact_path: str, query: Mapping[str, str]) -> str:
    try:
        parsed = urlsplit(base_url)
    except ValueError as exc:
        raise PagesVerificationError("Pages base URL has an invalid authority") from exc
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PagesVerificationError("Pages base URL must be one uncredentialed HTTPS origin/path")
    _https_origin(base_url)
    encoded_path = "/".join(quote(part, safe="-._~") for part in artifact_path.split("/"))
    base_path = parsed.path.rstrip("/")
    return urlunsplit(
        (parsed.scheme, parsed.netloc, f"{base_path}/{encoded_path}", urlencode(query), "")
    )


def _https_origin(url: str) -> tuple[str, str, int]:
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise PagesVerificationError("Pages URL has an invalid authority or port") from exc
    try:
        port = parsed.port
    except ValueError as exc:
        raise PagesVerificationError("Pages URL has an invalid authority or port") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme.lower() != "https"
        or hostname is None
        or port == 0
        or parsed.username is not None
        or parsed.password is not None
        or any(character.isspace() for character in hostname)
    ):
        raise PagesVerificationError("Pages URL has an invalid HTTPS origin")
    try:
        hostname = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise PagesVerificationError("Pages URL has an invalid HTTPS origin") from exc
    return ("https", hostname, port or 443)


def _validate_remote_response(request_url: str, response: requests.Response) -> None:
    expected_origin = _https_origin(request_url)
    responses = [*response.history, response]
    try:
        origins_match = all(_https_origin(item.url) == expected_origin for item in responses)
    except PagesVerificationError as exc:
        raise PagesVerificationError(
            "remote Pages redirect left the configured HTTPS origin"
        ) from exc
    if not origins_match:
        raise PagesVerificationError("remote Pages redirect left the configured HTTPS origin")
    content_encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
    if content_encoding not in {"", "identity"}:
        raise PagesVerificationError("remote Pages response ignored identity content encoding")


def _revision_matches(payload: bytes, target_revision: str) -> bool:
    revision = _json_object(payload, "remote api/revision.json")
    return (
        revision.get("commit") == target_revision and revision.get("content_ref") == target_revision
    )


def wait_for_revision(
    *,
    base_url: str,
    target_revision: str,
    attempts: int,
    interval_seconds: float,
    fetch: ByteFetcher,
    sleep: Callable[[float], None],
) -> bytes:
    """Wait until both remote revision identities equal the deployment target."""
    if attempts <= 0:
        raise PagesVerificationError("revision attempts must be positive")
    if not math.isfinite(interval_seconds) or interval_seconds < 0:
        raise PagesVerificationError("revision interval must be finite and non-negative")
    for attempt in range(1, attempts + 1):
        payload = fetch(
            _remote_url(
                base_url,
                "api/revision.json",
                {"attempt": str(attempt), "target": target_revision},
            ),
            headers=_remote_headers(),
            max_bytes=MAX_METADATA_BYTES,
            label="remote api/revision.json",
        )
        if _revision_matches(payload, target_revision):
            return payload
        if attempt < attempts:
            sleep(interval_seconds)
    raise PagesVerificationError("Pages revision did not converge to the target")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _matches_spooled(actual: bytes, expected: SpooledArtifactFile) -> bool:
    if len(actual) != expected.size:
        return False
    offset = 0
    with expected.path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            next_offset = offset + len(chunk)
            if chunk != actual[offset:next_offset]:
                return False
            offset = next_offset
    return offset == expected.size


def verify_pages(
    *,
    repository: str,
    run_id: int,
    token: str,
    target_revision: str,
    base_url: str,
    docs_root: Path = Path("docs/en"),
    api_reference_root: Path = Path("docs/api-reference"),
    specs_root: Path = Path("docs/specifications/api"),
    openapi_config: Path = Path("docs/openapi-specs-config.json"),
    attempts: int = 20,
    interval_seconds: float = 15,
    fetch: ByteFetcher | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Verify every required remote byte against one exact same-run artifact."""
    if not COMMIT.fullmatch(target_revision):
        raise PagesVerificationError("target revision must be a full hexadecimal commit id")
    if attempts <= 0:
        raise PagesVerificationError("revision attempts must be positive")
    if not math.isfinite(interval_seconds) or interval_seconds < 0:
        raise PagesVerificationError("revision interval must be finite and non-negative")
    _remote_url(base_url, "api/revision.json", {})
    _required_artifact_paths(
        docs_root,
        api_reference_root,
        specs_root,
        openapi_config,
    )
    with TemporaryDirectory(prefix="pages-verification-") as temporary:
        artifact_zip_path = Path(temporary) / "github-pages.zip"
        if fetch is None:
            http_reader = RequestsFetcher()
            reader: ByteFetcher = http_reader
            artifact = same_run_artifact_url(repository, run_id, token, reader)
            actual_digest = http_reader.download(
                artifact.url,
                artifact_zip_path,
                headers=_github_headers(token),
                max_bytes=MAX_ARTIFACT_ZIP_BYTES,
                label="same-run Pages artifact",
            )
            _verify_artifact_digest(actual_digest, artifact.sha256)
        else:
            reader = fetch
            artifact_zip = download_same_run_artifact(repository, run_id, token, reader)
            artifact_zip_path.write_bytes(artifact_zip)
        artifact_files = required_pages_from_artifact(
            artifact_zip_path,
            Path(temporary) / "required",
        )
        required = _required_artifact_files(
            artifact_files,
            docs_root,
            api_reference_root,
            specs_root,
            openapi_config,
            target_revision,
        )

        remote_revision = wait_for_revision(
            base_url=base_url,
            target_revision=target_revision,
            attempts=attempts,
            interval_seconds=interval_seconds,
            fetch=reader,
            sleep=sleep,
        )
        revision_file = required["api/revision.json"]
        if not _matches_spooled(remote_revision, revision_file):
            raise PagesVerificationError(
                "remote byte drift at api/revision.json: "
                f"artifact={revision_file.sha256}, remote={_sha256(remote_revision)}"
            )

        for artifact_path, expected in required.items():
            if artifact_path == "api/revision.json":
                continue
            actual = reader(
                _remote_url(
                    base_url,
                    artifact_path,
                    {"target": target_revision, "verify": "artifact"},
                ),
                headers=_remote_headers(),
                max_bytes=max(expected.size, 1) + 1,
                label=f"remote {artifact_path}",
            )
            if not _matches_spooled(actual, expected):
                raise PagesVerificationError(
                    f"remote byte drift at {artifact_path}: "
                    f"artifact={expected.sha256}, remote={_sha256(actual)}"
                )
        return len(required)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--target-revision", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--docs-root", type=Path, default=Path("docs/en"))
    parser.add_argument(
        "--api-reference-root",
        type=Path,
        default=Path("docs/api-reference"),
    )
    parser.add_argument("--specs-root", type=Path, default=Path("docs/specifications/api"))
    parser.add_argument(
        "--openapi-config",
        type=Path,
        default=Path("docs/openapi-specs-config.json"),
    )
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--interval-seconds", type=float, default=15)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the Pages artifact verifier."""
    args = parse_args(argv)
    try:
        verified = verify_pages(
            repository=args.repository,
            run_id=args.run_id,
            token=os.environ.get("GITHUB_TOKEN", ""),
            target_revision=args.target_revision,
            base_url=args.base_url,
            docs_root=args.docs_root,
            api_reference_root=args.api_reference_root,
            specs_root=args.specs_root,
            openapi_config=args.openapi_config,
            attempts=args.attempts,
            interval_seconds=args.interval_seconds,
        )
    except PagesVerificationError as exc:
        print(f"Pages verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified {verified} published files byte-for-byte against the same-run artifact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
