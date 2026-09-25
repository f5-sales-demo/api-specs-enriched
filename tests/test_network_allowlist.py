"""Fixture coverage for the live F5 network allowlist OpenAPI extension."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from scripts import network_allowlist as allowlist


@pytest.fixture
def manifest() -> dict:
    return {
        "manifest_type": "firewall_proxy_allowlist",
        "schema_version": "1.0.0",
        "manifest_version": "1.0.0",
        "generated_at": "2025-09-10T00:00:00Z",
        "reference": "https://docs.cloud.f5.com/docs-v2/platform/reference/network-cloud-ref",
        "services": {
            "regional_edges": {"regions": {"americas": {"ipv4_cidrs": ["1.2.3.4/32", "5.6.7.8"]}}},
            "bot_defense": {"domains": [".volterra.io", "api.volterra.io"]},
        },
        "customer_edge": {
            "defaults": {"dns_servers": ["8.8.8.8"]},
            "site_types": {
                "secure_mesh_v2": {"egress_domain_rules": {"domains": [".ves.volterra.io"]}}
            },
        },
    }


@pytest.fixture
def spec_path(tmp_path: Path) -> Path:
    path = tmp_path / "openapi.json"
    path.write_text(
        json.dumps({"openapi": "3.0.0", "info": {"title": "F5", "version": "1"}}) + "\n"
    )
    return path


def fake_fetch(monkeypatch: pytest.MonkeyPatch, payload: bytes, returncode: int = 0) -> None:
    monkeypatch.setattr(
        allowlist.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], returncode, payload, b"HTTP failure"
        ),
    )


def test_unchanged_and_changed_feed_preserve_manifest_and_list_order(
    monkeypatch: pytest.MonkeyPatch, manifest: dict, spec_path: Path
) -> None:
    fake_fetch(monkeypatch, json.dumps(manifest).encode())
    assert allowlist.update(spec_path) is True
    first = spec_path.read_bytes()
    extension = json.loads(first)["info"][allowlist.EXTENSION]
    assert extension["source_url"] == allowlist.SOURCE_URL
    assert extension["manifest"] == manifest
    assert extension["manifest"]["services"]["regional_edges"]["regions"]["americas"][
        "ipv4_cidrs"
    ] == ["1.2.3.4/32", "5.6.7.8"]
    assert extension["sha256"] == allowlist.digest(manifest)
    assert allowlist.update(spec_path) is False
    assert spec_path.read_bytes() == first
    assert allowlist.check(spec_path) is False

    changed = copy.deepcopy(manifest)
    changed["services"]["bot_defense"]["domains"].append("new.volterra.io")
    fake_fetch(monkeypatch, json.dumps(changed).encode())
    assert allowlist.check(spec_path) is True
    assert allowlist.update(spec_path) is True
    assert json.loads(spec_path.read_text())["info"][allowlist.EXTENSION]["manifest"] == changed


def test_cached_openapi_is_refreshed_after_restore(
    monkeypatch: pytest.MonkeyPatch, manifest: dict, spec_path: Path
) -> None:
    stale = copy.deepcopy(manifest)
    stale["services"]["bot_defense"]["domains"] = ["old.volterra.io"]
    spec = json.loads(spec_path.read_text())
    spec["info"][allowlist.EXTENSION] = allowlist.extension(stale)
    spec_path.write_text(json.dumps(spec))
    fake_fetch(monkeypatch, json.dumps(manifest).encode())
    assert allowlist.update(spec_path) is True
    assert json.loads(spec_path.read_text())["info"][allowlist.EXTENSION]["manifest"] == manifest


@pytest.mark.parametrize(
    "payload",
    [b"{bad", b"[]", b'{"manifest_type":"other","services":{},"customer_edge":{}}'],
)
def test_malformed_or_unsupported_feed_fails_without_mutation(
    monkeypatch: pytest.MonkeyPatch, spec_path: Path, payload: bytes
) -> None:
    before = spec_path.read_bytes()
    fake_fetch(monkeypatch, payload)
    with pytest.raises(allowlist.AllowlistError):
        allowlist.update(spec_path)
    assert spec_path.read_bytes() == before


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("services", "regional_edges", "regions", "americas", "ipv4_cidrs"), ["999.2.3.4/24"]),
        (("services", "bot_defense", "domains"), ["bad domain"]),
        (("services", "bot_defense", "domains"), ["..volterra.io"]),
        (("customer_edge", "defaults", "dns_servers"), ["2001:db8::1"]),
    ],
)
def test_invalid_address_or_domain_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    manifest: dict,
    spec_path: Path,
    path: tuple[str, ...],
    value: list[str],
) -> None:
    node = manifest
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    before = spec_path.read_bytes()
    fake_fetch(monkeypatch, json.dumps(manifest).encode())
    with pytest.raises(allowlist.AllowlistError):
        allowlist.update(spec_path)
    assert spec_path.read_bytes() == before


def test_http_failure_and_oversize_fail_without_cached_fallback(
    monkeypatch: pytest.MonkeyPatch, spec_path: Path
) -> None:
    before = spec_path.read_bytes()
    fake_fetch(monkeypatch, b"", returncode=22)
    with pytest.raises(allowlist.AllowlistError):
        allowlist.update(spec_path)
    assert spec_path.read_bytes() == before
    fake_fetch(monkeypatch, b"x" * (allowlist.MAX_BYTES + 1))
    with pytest.raises(allowlist.AllowlistError):
        allowlist.update(spec_path)
    assert spec_path.read_bytes() == before


def test_write_failure_keeps_original(
    monkeypatch: pytest.MonkeyPatch, manifest: dict, spec_path: Path
) -> None:
    fake_fetch(monkeypatch, json.dumps(manifest).encode())
    before = spec_path.read_bytes()

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(allowlist, "write_json_file", fail_write)
    with pytest.raises(OSError, match="disk full"):
        allowlist.update(spec_path)
    assert spec_path.read_bytes() == before
    assert not list(spec_path.parent.glob(".network-allowlist-*"))


def test_duplicate_json_keys_are_rejected(monkeypatch: pytest.MonkeyPatch, spec_path: Path) -> None:
    fake_fetch(monkeypatch, b'{"manifest_type":"firewall_proxy_allowlist","manifest_type":"other"}')
    with pytest.raises(allowlist.AllowlistError, match="duplicate"):
        allowlist.update(spec_path)


def test_release_and_clean_rebuild_workflow_wiring() -> None:
    root = Path(__file__).parent.parent
    release = (root / ".github/workflows/sync-and-enrich.yml").read_text()
    tests = (root / ".github/workflows/tests.yml").read_text()
    makefile = (root / "Makefile").read_text()

    check_job, build_job = release.split("\n  sync-and-enrich:\n", maxsplit=1)
    assert "python -m scripts.network_allowlist --check" in check_job
    assert "steps.network-allowlist.outputs.updated == 'true'" in check_job
    assert check_job.index("Check for GitHub Release updates") < check_job.index(
        "Check live F5 network allowlist"
    )
    assert build_job.index("Restore enriched output cache") < build_job.index(
        "Fetch and merge F5 network allowlist"
    )
    assert build_job.index("Run enrichment pipeline") < build_job.index(
        "Fetch and merge F5 network allowlist"
    )
    assert (
        build_job.index("Fetch and merge F5 network allowlist")
        < build_job.index("Compile API catalog")
        < build_job.index("Validate specifications")
    )
    assert "run: make pipeline-core PYTHON=python" in tests
    assert 'pop("x-f5xc-network-allowlist", None)' in tests
    assert "pipeline: pipeline-core\n\t$(PYTHON) -m scripts.network_allowlist" in makefile
    assert 'cp docs/specifications/api/openapi.json "$RELEASE_DIR/"' in release
    assert "docs/specifications/api/openapi.json" in release.split("ASSET_PATHS=(", maxsplit=1)[1]
    assert "build-publication-receipt.sh" in release


def test_unknown_service_shape_is_rejected(
    monkeypatch: pytest.MonkeyPatch, manifest: dict, spec_path: Path
) -> None:
    manifest["services"]["unknown_service"] = {"domains": ["example.com"]}
    fake_fetch(monkeypatch, json.dumps(manifest).encode())
    with pytest.raises(allowlist.AllowlistError, match="unsupported"):
        allowlist.update(spec_path)
