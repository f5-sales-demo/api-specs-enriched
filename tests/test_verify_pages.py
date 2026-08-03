from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pytest
import requests

from scripts.release import verify_pages

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

TARGET = "a" * 40
REPOSITORY = "example/docs"
RUN_ID = 42
BASE_URL = "https://example.invalid/site"


class FakeFetcher:
    def __init__(self, handler: Callable[[str, str], bytes]) -> None:
        self.handler = handler
        self.calls: list[tuple[str, str]] = []

    def __call__(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        max_bytes: int,
        label: str,
    ) -> bytes:
        self.calls.append((url, label))
        payload = self.handler(url, label)
        if len(payload) > max_bytes:
            raise verify_pages.PagesVerificationError(f"oversized test response for {label}")
        if label.startswith("remote "):
            assert headers == {"Accept-Encoding": "identity"}
        elif "same-run" in label:
            assert headers["Authorization"] == "Bearer token"
        else:
            assert headers == {}
        return payload


def _tar(files: list[tuple[str, bytes]], *, link: str | None = None) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, payload in files:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        if link is not None:
            info = tarfile.TarInfo(link)
            info.type = tarfile.SYMTYPE
            info.linkname = "target"
            archive.addfile(info)
    return output.getvalue()


def _artifact_zip(tar_payloads: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w") as archive:
        for name, payload in tar_payloads:
            archive.writestr(name, payload)
    return output.getvalue()


def _metadata(
    *,
    count: int = 1,
    bound_run: int = RUN_ID,
    digest: str = "0" * 64,
) -> bytes:
    artifacts = [
        {
            "id": index + 100,
            "name": "github-pages",
            "expired": False,
            "digest": f"sha256:{digest}",
            "workflow_run": {"id": bound_run},
        }
        for index in range(count)
    ]
    return json.dumps({"total_count": count, "artifacts": artifacts}).encode()


def _docs_root(tmp_path: Path) -> Path:
    root = tmp_path / "docs" / "en"
    (root / "Guide").mkdir(parents=True)
    (root / "index.mdx").write_text("# Home\n", encoding="utf-8")
    (root / "Guide" / "Start.md").write_text("# Start\n", encoding="utf-8")
    return root


def _publication_roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    docs_root = _docs_root(tmp_path)
    api_reference_root = tmp_path / "docs" / "api-reference"
    api_reference_root.mkdir()
    (api_reference_root / "index.mdx").write_text("# API Reference\n", encoding="utf-8")
    (api_reference_root / "Widget-API.mdx").write_text("# Widget API\n", encoding="utf-8")

    specs_root = tmp_path / "docs" / "specifications" / "api"
    specs_root.mkdir(parents=True)
    (specs_root / "index.json").write_text('{"version":"1.2.3"}\n', encoding="utf-8")
    (specs_root / "openapi.json").write_text('{"openapi":"3.0.3"}\n', encoding="utf-8")
    (specs_root / "widget.json").write_text('{"openapi":"3.0.3"}\n', encoding="utf-8")

    openapi_config = tmp_path / "docs" / "openapi-specs-config.json"
    openapi_config.write_text(
        json.dumps(
            [
                {
                    "base": "api-reference/widget",
                    "schema": "public/specifications/api/widget.json",
                    "sidebar": {"collapsed": True, "label": "Widget"},
                }
            ]
        ),
        encoding="utf-8",
    )
    return docs_root, api_reference_root, specs_root, openapi_config


def _site_files(
    docs_root: Path,
    api_reference_root: Path,
    specs_root: Path,
    openapi_config: Path,
) -> dict[str, bytes]:
    revision = json.dumps(
        {"commit": TARGET, "content_ref": TARGET},
        sort_keys=True,
    ).encode()
    files = {"api/revision.json": revision}
    rendered_routes = {
        **verify_pages.documentation_routes(docs_root),
        **verify_pages.generated_api_reference_routes(api_reference_root),
        **verify_pages.configured_openapi_routes(openapi_config, specs_root),
    }
    files.update({route: f"<h1>{route}</h1>\n".encode() for route in rendered_routes})
    files.update(
        {
            artifact_path: source.read_bytes()
            for artifact_path, source in verify_pages.published_specification_paths(
                specs_root
            ).items()
        }
    )
    _refresh_manifest(files)
    return files


def _refresh_manifest(files: dict[str, bytes]) -> None:
    manifest_files = {
        path: hashlib.sha256(payload).hexdigest()
        for path, payload in sorted(files.items())
        if path != verify_pages.PUBLICATION_MANIFEST_PATH
    }
    files[verify_pages.PUBLICATION_MANIFEST_PATH] = (
        json.dumps(
            {"version": 1, "files": manifest_files},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )


def _spooled_files(
    root: Path, files: dict[str, bytes]
) -> dict[str, verify_pages.SpooledArtifactFile]:
    root.mkdir()
    spooled: dict[str, verify_pages.SpooledArtifactFile] = {}
    for index, (path, payload) in enumerate(sorted(files.items())):
        destination = root / f"{index:06d}.bin"
        destination.write_bytes(payload)
        spooled[path] = verify_pages.SpooledArtifactFile(
            destination,
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        )
    return spooled


def _end_to_end_fetcher(site_files: dict[str, bytes], *, stale_first: bool = False) -> FakeFetcher:
    tar_payload = _tar([(f"./{name}", value) for name, value in site_files.items()])
    artifact_zip = _artifact_zip([("artifact.tar", tar_payload)])
    revision_calls = 0

    def handler(url: str, label: str) -> bytes:
        nonlocal revision_calls
        if label == "same-run artifact metadata":
            return _metadata(digest=hashlib.sha256(artifact_zip).hexdigest())
        if label == "same-run Pages artifact":
            return artifact_zip
        path = urlsplit(url).path.removeprefix("/site/")
        if path == "api/revision.json" and stale_first and revision_calls == 0:
            revision_calls += 1
            return json.dumps({"commit": "b" * 40, "content_ref": "b" * 40}).encode()
        revision_calls += path == "api/revision.json"
        return site_files[path]

    return FakeFetcher(handler)


def test_documentation_routes_are_lowercase_and_index_aware(tmp_path: Path) -> None:
    docs_root = _docs_root(tmp_path)

    routes = verify_pages.documentation_routes(docs_root)

    assert list(routes) == ["en/guide/start/index.html", "en/index.html"]


def test_documentation_route_collision_fails_closed(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs" / "en"
    (docs_root / "thing").mkdir(parents=True)
    (docs_root / "thing.md").write_text("one", encoding="utf-8")
    (docs_root / "thing" / "index.mdx").write_text("two", encoding="utf-8")

    with pytest.raises(verify_pages.PagesVerificationError, match="route collision"):
        verify_pages.documentation_routes(docs_root)


def test_documentation_symlink_fails_closed(tmp_path: Path) -> None:
    docs_root = _docs_root(tmp_path)
    (docs_root / "linked.mdx").symlink_to(docs_root / "index.mdx")

    with pytest.raises(verify_pages.PagesVerificationError, match="symlink"):
        verify_pages.documentation_routes(docs_root)


def test_publication_contract_requires_all_english_generated_and_spec_paths(
    tmp_path: Path,
) -> None:
    docs_root, api_reference_root, specs_root, openapi_config = _publication_roots(tmp_path)

    required = verify_pages._required_artifact_paths(
        docs_root,
        api_reference_root,
        specs_root,
        openapi_config,
    )

    assert required == {
        "api/publication-manifest.json",
        "api/revision.json",
        "api-reference/index.html",
        "api-reference/widget-api/index.html",
        "api-reference/widget/index.html",
        "en/guide/start/index.html",
        "en/index.html",
        "specifications/api/index.json",
        "specifications/api/openapi.json",
        "specifications/api/widget.json",
    }


@pytest.mark.parametrize(
    ("base", "schema", "message"),
    [
        ("../outside", "public/specifications/api/widget.json", "base"),
        ("api-reference/widget", "public/specifications/api/missing.json", "schema"),
        ("api-reference/widget", "../widget.json", "schema"),
    ],
)
def test_openapi_config_rejects_unsafe_or_unpublished_contracts(
    tmp_path: Path,
    base: str,
    schema: str,
    message: str,
) -> None:
    _, _, specs_root, openapi_config = _publication_roots(tmp_path)
    openapi_config.write_text(
        json.dumps(
            [
                {
                    "base": base,
                    "schema": schema,
                    "sidebar": {"collapsed": True, "label": "Widget"},
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(verify_pages.PagesVerificationError, match=message):
        verify_pages.configured_openapi_routes(openapi_config, specs_root)


@pytest.mark.parametrize(
    "members",
    [
        [],
        [("first.tar", b"first"), ("second.tar", b"second")],
        [("nested/artifact.tar", b"nested")],
        [("artifact.txt", b"not a tar")],
    ],
)
def test_artifact_zip_requires_exactly_one_safe_tar(members: list[tuple[str, bytes]]) -> None:
    with pytest.raises(verify_pages.PagesVerificationError):
        verify_pages.artifact_tar_bytes(_artifact_zip(members))


@pytest.mark.parametrize(
    "payload",
    [
        _tar([("../escape", b"unsafe")]),
        _tar([("./safe", b"first"), ("safe", b"duplicate")]),
        _tar([], link="./linked"),
    ],
)
def test_pages_tar_rejects_unsafe_duplicate_and_link_members(payload: bytes) -> None:
    with pytest.raises(verify_pages.PagesVerificationError):
        verify_pages.parse_pages_tar(payload)


def test_pages_tar_reads_regular_files_without_extraction() -> None:
    payload = _tar([("./en/index.html", b"rendered")])

    assert verify_pages.parse_pages_tar(payload) == {"en/index.html": b"rendered"}


def test_streamed_artifact_parser_retains_every_artifact_file(tmp_path: Path) -> None:
    artifact = tmp_path / "pages.zip"
    artifact.write_bytes(
        _artifact_zip(
            [
                (
                    "artifact.tar",
                    _tar(
                        [
                            ("./en/index.html", b"required"),
                            ("./assets/large.bin", b"not retained"),
                        ]
                    ),
                )
            ]
        )
    )

    files = verify_pages.required_pages_from_artifact(
        artifact,
        tmp_path / "spool",
    )

    retained = files["en/index.html"]
    assert retained.path.read_bytes() == b"required"
    assert retained.size == len(b"required")
    assert retained.sha256 == hashlib.sha256(b"required").hexdigest()
    assert files["assets/large.bin"].path.read_bytes() == b"not retained"


def test_streamed_artifact_parser_rejects_file_parent_of_directory(tmp_path: Path) -> None:
    tar_output = io.BytesIO()
    with tarfile.open(fileobj=tar_output, mode="w") as archive:
        parent = tarfile.TarInfo("./collision")
        parent.size = 4
        archive.addfile(parent, io.BytesIO(b"file"))
        child = tarfile.TarInfo("./collision/child")
        child.type = tarfile.DIRTYPE
        archive.addfile(child)
    artifact = tmp_path / "pages.zip"
    artifact.write_bytes(_artifact_zip([("artifact.tar", tar_output.getvalue())]))

    with pytest.raises(verify_pages.PagesVerificationError, match="path collision"):
        verify_pages.required_pages_from_artifact(
            artifact,
            tmp_path / "spool",
        )


def test_streamed_artifact_parser_rejects_nonzero_trailing_bytes(tmp_path: Path) -> None:
    tar_payload = bytearray(_tar([("./en/index.html", b"rendered")]))
    tar_payload[-1] = 1
    artifact = tmp_path / "pages.zip"
    artifact.write_bytes(_artifact_zip([("artifact.tar", bytes(tar_payload))]))

    with pytest.raises(verify_pages.PagesVerificationError, match="non-padding trailing bytes"):
        verify_pages.required_pages_from_artifact(
            artifact,
            tmp_path / "spool",
        )


def test_streamed_artifact_parser_requires_two_zero_end_blocks(tmp_path: Path) -> None:
    tar_payload = _tar([("./en/index.html", b"rendered")])
    one_zero_block_only = tar_payload[: 2 * tarfile.BLOCKSIZE + tarfile.BLOCKSIZE]
    artifact = tmp_path / "pages.zip"
    artifact.write_bytes(_artifact_zip([("artifact.tar", one_zero_block_only)]))

    with pytest.raises(verify_pages.PagesVerificationError, match="invalid end padding"):
        verify_pages.required_pages_from_artifact(
            artifact,
            tmp_path / "spool",
        )


@pytest.mark.parametrize("archive_format", [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT])
def test_tar_extension_headers_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
    archive_format: int,
) -> None:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=archive_format) as archive:
        info = tarfile.TarInfo("nested/" + "x" * 180)
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    monkeypatch.setattr(verify_pages, "MAX_TAR_EXTENSION_BYTES", 32)

    with pytest.raises(verify_pages.PagesVerificationError, match="extension header"):
        verify_pages.parse_pages_tar(output.getvalue())


def test_tar_extension_headers_count_toward_member_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("nested/" + "x" * 180)
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    monkeypatch.setattr(verify_pages, "MAX_TAR_MEMBERS", 1)

    with pytest.raises(verify_pages.PagesVerificationError, match="too many members"):
        verify_pages.parse_pages_tar(output.getvalue())


def test_tar_extension_headers_have_an_aggregate_memory_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("nested/" + "x" * 180)
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    monkeypatch.setattr(verify_pages, "MAX_TAR_EXTENSION_TOTAL_BYTES", 511)

    with pytest.raises(verify_pages.PagesVerificationError, match="extension headers"):
        verify_pages.parse_pages_tar(output.getvalue())


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (_metadata(count=0), "exactly one"),
        (_metadata(count=2), "exactly one"),
        (_metadata(bound_run=99), "binding"),
    ],
)
def test_same_run_artifact_selection_fails_closed(metadata: bytes, message: str) -> None:
    fetcher = FakeFetcher(lambda _url, _label: metadata)

    with pytest.raises(verify_pages.PagesVerificationError, match=message):
        verify_pages.download_same_run_artifact(REPOSITORY, RUN_ID, "token", fetcher)


def test_same_run_artifact_selection_requires_sha256_digest() -> None:
    metadata = json.loads(_metadata())
    metadata["artifacts"][0]["digest"] = "sha256:not-a-digest"
    fetcher = FakeFetcher(lambda _url, _label: json.dumps(metadata).encode())

    with pytest.raises(verify_pages.PagesVerificationError, match="digest is malformed"):
        verify_pages.same_run_artifact_url(REPOSITORY, RUN_ID, "token", fetcher)


def test_same_run_artifact_download_must_match_metadata_digest() -> None:
    payload = b"downloaded artifact"
    metadata = _metadata(digest=hashlib.sha256(b"different artifact").hexdigest())

    def handler(_url: str, label: str) -> bytes:
        return metadata if label == "same-run artifact metadata" else payload

    with pytest.raises(verify_pages.PagesVerificationError, match="digest does not match"):
        verify_pages.download_same_run_artifact(
            REPOSITORY,
            RUN_ID,
            "token",
            FakeFetcher(handler),
        )


@pytest.mark.parametrize(
    ("history_url", "final_url"),
    [
        (None, "https://other.invalid/site/en/index.html"),
        (None, "http://example.invalid/site/en/index.html"),
        (
            "https://other.invalid/intermediate",
            "https://example.invalid/site/en/index.html",
        ),
    ],
)
def test_remote_fetch_rejects_redirects_outside_configured_https_origin(
    history_url: str | None,
    final_url: str,
) -> None:
    response = requests.Response()
    response.status_code = 200
    response.url = final_url
    response._content = b"rendered"
    response._content_consumed = True
    response.headers["Content-Length"] = str(len(response.content))
    if history_url is not None:
        history = requests.Response()
        history.status_code = 302
        history.url = history_url
        response.history = [history]

    class FakeSession:
        def get(self, *_args: object, **_kwargs: object) -> requests.Response:
            return response

    fetcher = verify_pages.RequestsFetcher()
    fetcher._session = FakeSession()  # type: ignore[assignment]

    with pytest.raises(verify_pages.PagesVerificationError, match="redirect left"):
        fetcher(
            "https://example.invalid/site/en/index.html",
            headers={"Accept-Encoding": "identity"},
            max_bytes=1024,
            label="remote en/index.html",
        )


def test_remote_fetch_rejects_non_identity_response_encoding() -> None:
    response = requests.Response()
    response.status_code = 200
    response.url = "https://example.invalid/site/en/index.html"
    response._content = b"rendered"
    response._content_consumed = True
    response.headers["Content-Encoding"] = "gzip"

    class FakeSession:
        def get(self, *_args: object, **_kwargs: object) -> requests.Response:
            return response

    fetcher = verify_pages.RequestsFetcher()
    fetcher._session = FakeSession()  # type: ignore[assignment]

    with pytest.raises(verify_pages.PagesVerificationError, match="identity content encoding"):
        fetcher(
            "https://example.invalid/site/en/index.html",
            headers={"Accept-Encoding": "identity"},
            max_bytes=1024,
            label="remote en/index.html",
        )


@pytest.mark.parametrize(
    ("base_url", "attempts", "interval_seconds", "message"),
    [
        ("http://example.invalid", 1, 0, "base URL"),
        ("https://example.invalid:bad/site", 1, 0, "authority or port"),
        ("https://:443/site", 1, 0, "HTTPS origin"),
        ("https://[::1/site", 1, 0, "invalid authority"),
        ("https://example.invalid:0/site", 1, 0, "HTTPS origin"),
        ("https://example.invalid:99999/site", 1, 0, "authority or port"),
        (BASE_URL, 0, 0, "attempts"),
        (BASE_URL, 1, -1, "interval"),
        (BASE_URL, 1, float("nan"), "interval"),
        (BASE_URL, 1, float("inf"), "interval"),
    ],
)
def test_verification_settings_fail_before_artifact_download(
    tmp_path: Path,
    base_url: str,
    attempts: int,
    interval_seconds: float,
    message: str,
) -> None:
    fetcher = FakeFetcher(lambda _url, _label: pytest.fail("unexpected network request"))

    with pytest.raises(verify_pages.PagesVerificationError, match=message):
        verify_pages.verify_pages(
            repository=REPOSITORY,
            run_id=RUN_ID,
            token="token",
            target_revision=TARGET,
            base_url=base_url,
            docs_root=_docs_root(tmp_path),
            attempts=attempts,
            interval_seconds=interval_seconds,
            fetch=fetcher,
        )

    assert fetcher.calls == []


def test_required_artifact_files_reject_missing_rendered_route(tmp_path: Path) -> None:
    docs_root, api_reference_root, specs_root, openapi_config = _publication_roots(tmp_path)
    files = _site_files(docs_root, api_reference_root, specs_root, openapi_config)
    del files["en/guide/start/index.html"]
    _refresh_manifest(files)

    with pytest.raises(verify_pages.PagesVerificationError, match="missing required files"):
        verify_pages._required_artifact_files(
            _spooled_files(tmp_path / "spool", files),
            docs_root,
            api_reference_root,
            specs_root,
            openapi_config,
            TARGET,
        )


def test_required_artifact_files_reject_unlisted_extra_file(tmp_path: Path) -> None:
    docs_root, api_reference_root, specs_root, openapi_config = _publication_roots(tmp_path)
    files = _site_files(docs_root, api_reference_root, specs_root, openapi_config)
    files["fr/index.html"] = b"translated route"

    with pytest.raises(verify_pages.PagesVerificationError, match=r"unlisted=.*fr/index"):
        verify_pages._required_artifact_files(
            _spooled_files(tmp_path / "spool", files),
            docs_root,
            api_reference_root,
            specs_root,
            openapi_config,
            TARGET,
        )


def test_required_artifact_files_reject_manifest_listed_translated_route(
    tmp_path: Path,
) -> None:
    docs_root, api_reference_root, specs_root, openapi_config = _publication_roots(tmp_path)
    translated_root = docs_root.parent / "fr"
    translated_root.mkdir()
    (translated_root / "index.mdx").write_text("# French\n", encoding="utf-8")
    files = _site_files(docs_root, api_reference_root, specs_root, openapi_config)
    files["fr/index.html"] = b"translated route"
    _refresh_manifest(files)

    with pytest.raises(verify_pages.PagesVerificationError, match="non-allowlisted source routes"):
        verify_pages._required_artifact_files(
            _spooled_files(tmp_path / "spool", files),
            docs_root,
            api_reference_root,
            specs_root,
            openapi_config,
            TARGET,
        )


def test_required_artifact_files_reject_unknown_non_english_locale_route(
    tmp_path: Path,
) -> None:
    docs_root, api_reference_root, specs_root, openapi_config = _publication_roots(tmp_path)
    files = _site_files(docs_root, api_reference_root, specs_root, openapi_config)
    files["zz/index.html"] = b"unexpected locale route"
    _refresh_manifest(files)

    with pytest.raises(verify_pages.PagesVerificationError, match="non-allowlisted source routes"):
        verify_pages._required_artifact_files(
            _spooled_files(tmp_path / "spool", files),
            docs_root,
            api_reference_root,
            specs_root,
            openapi_config,
            TARGET,
        )


def test_required_artifact_files_reject_manifest_digest_drift(tmp_path: Path) -> None:
    docs_root, api_reference_root, specs_root, openapi_config = _publication_roots(tmp_path)
    files = _site_files(docs_root, api_reference_root, specs_root, openapi_config)
    manifest = json.loads(files[verify_pages.PUBLICATION_MANIFEST_PATH])
    manifest["files"]["en/index.html"] = "0" * 64
    files[verify_pages.PUBLICATION_MANIFEST_PATH] = json.dumps(manifest).encode()

    with pytest.raises(verify_pages.PagesVerificationError, match="digest does not match"):
        verify_pages._required_artifact_files(
            _spooled_files(tmp_path / "spool", files),
            docs_root,
            api_reference_root,
            specs_root,
            openapi_config,
            TARGET,
        )


def test_required_artifact_files_reject_manifest_entry_without_artifact_file(
    tmp_path: Path,
) -> None:
    docs_root, api_reference_root, specs_root, openapi_config = _publication_roots(tmp_path)
    files = _site_files(docs_root, api_reference_root, specs_root, openapi_config)
    del files["en/index.html"]

    with pytest.raises(verify_pages.PagesVerificationError, match=r"missing=.*en/index"):
        verify_pages._required_artifact_files(
            _spooled_files(tmp_path / "spool", files),
            docs_root,
            api_reference_root,
            specs_root,
            openapi_config,
            TARGET,
        )


def test_required_artifact_files_bounds_exhaustive_remote_request_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docs_root, api_reference_root, specs_root, openapi_config = _publication_roots(tmp_path)
    files = _site_files(docs_root, api_reference_root, specs_root, openapi_config)
    monkeypatch.setattr(verify_pages, "MAX_VERIFIED_FILES", 1)

    with pytest.raises(verify_pages.PagesVerificationError, match="file-count limit"):
        verify_pages._required_artifact_files(
            _spooled_files(tmp_path / "spool", files),
            docs_root,
            api_reference_root,
            specs_root,
            openapi_config,
            TARGET,
        )


def test_verify_pages_waits_for_revision_then_compares_every_required_byte(
    tmp_path: Path,
) -> None:
    docs_root, api_reference_root, specs_root, openapi_config = _publication_roots(tmp_path)
    fetcher = _end_to_end_fetcher(
        _site_files(docs_root, api_reference_root, specs_root, openapi_config),
        stale_first=True,
    )
    sleeps: list[float] = []

    verified = verify_pages.verify_pages(
        repository=REPOSITORY,
        run_id=RUN_ID,
        token="token",
        target_revision=TARGET,
        base_url=BASE_URL,
        docs_root=docs_root,
        api_reference_root=api_reference_root,
        specs_root=specs_root,
        openapi_config=openapi_config,
        attempts=2,
        interval_seconds=0.25,
        fetch=fetcher,
        sleep=sleeps.append,
    )

    assert verified == 10
    assert sleeps == [0.25]
    remote_labels = [label for _, label in fetcher.calls if label.startswith("remote ")]
    assert remote_labels == [
        "remote api/revision.json",
        "remote api/revision.json",
        "remote api-reference/index.html",
        "remote api-reference/widget-api/index.html",
        "remote api-reference/widget/index.html",
        "remote api/publication-manifest.json",
        "remote en/guide/start/index.html",
        "remote en/index.html",
        "remote specifications/api/index.json",
        "remote specifications/api/openapi.json",
        "remote specifications/api/widget.json",
    ]


def test_verify_pages_rejects_remote_byte_drift(tmp_path: Path) -> None:
    docs_root, api_reference_root, specs_root, openapi_config = _publication_roots(tmp_path)
    artifact_files = _site_files(docs_root, api_reference_root, specs_root, openapi_config)
    remote_files = dict(artifact_files)
    remote_files["en/index.html"] = b"different"
    fetcher = _end_to_end_fetcher(artifact_files)
    original_handler = fetcher.handler

    def drifted(url: str, label: str) -> bytes:
        if label == "remote en/index.html":
            return remote_files["en/index.html"]
        return original_handler(url, label)

    fetcher.handler = drifted

    with pytest.raises(verify_pages.PagesVerificationError, match=r"byte drift at en/index\.html"):
        verify_pages.verify_pages(
            repository=REPOSITORY,
            run_id=RUN_ID,
            token="token",
            target_revision=TARGET,
            base_url=BASE_URL,
            docs_root=docs_root,
            api_reference_root=api_reference_root,
            specs_root=specs_root,
            openapi_config=openapi_config,
            attempts=1,
            interval_seconds=0,
            fetch=fetcher,
            sleep=lambda _seconds: None,
        )


def test_verify_pages_rejects_artifact_spec_bytes_not_in_release_commit(
    tmp_path: Path,
) -> None:
    docs_root, api_reference_root, specs_root, openapi_config = _publication_roots(tmp_path)
    artifact_files = _site_files(docs_root, api_reference_root, specs_root, openapi_config)
    artifact_files["specifications/api/widget.json"] = b'{"different":true}\n'
    _refresh_manifest(artifact_files)
    fetcher = _end_to_end_fetcher(artifact_files)

    with pytest.raises(
        verify_pages.PagesVerificationError,
        match="artifact specification differs from release commit",
    ):
        verify_pages.verify_pages(
            repository=REPOSITORY,
            run_id=RUN_ID,
            token="token",
            target_revision=TARGET,
            base_url=BASE_URL,
            docs_root=docs_root,
            api_reference_root=api_reference_root,
            specs_root=specs_root,
            openapi_config=openapi_config,
            attempts=1,
            interval_seconds=0,
            fetch=fetcher,
            sleep=lambda _seconds: None,
        )


def test_verify_pages_propagates_remote_http_failure(tmp_path: Path) -> None:
    docs_root, api_reference_root, specs_root, openapi_config = _publication_roots(tmp_path)
    fetcher = _end_to_end_fetcher(
        _site_files(docs_root, api_reference_root, specs_root, openapi_config)
    )
    original_handler = fetcher.handler

    def failing(url: str, label: str) -> bytes:
        if label == "remote en/index.html":
            raise verify_pages.PagesVerificationError("HTTP 503 while fetching remote page")
        return original_handler(url, label)

    fetcher.handler = failing

    with pytest.raises(verify_pages.PagesVerificationError, match="HTTP 503"):
        verify_pages.verify_pages(
            repository=REPOSITORY,
            run_id=RUN_ID,
            token="token",
            target_revision=TARGET,
            base_url=BASE_URL,
            docs_root=docs_root,
            api_reference_root=api_reference_root,
            specs_root=specs_root,
            openapi_config=openapi_config,
            attempts=1,
            interval_seconds=0,
            fetch=fetcher,
            sleep=lambda _seconds: None,
        )
