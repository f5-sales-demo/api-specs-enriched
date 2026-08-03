"""Tests for durable downstream delivery receipts."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from scripts.release import dispatch_downstreams as delivery, reconcile_publication as publication
from scripts.release.source_provenance import source_provenance_marker


def test_delivery_id_is_stable_and_target_specific() -> None:
    target = _target("provider")
    first = delivery.delivery_id("f5/specs", target, "2.1.208", "v2.1.208", "a" * 40)
    second = delivery.delivery_id("f5/specs", target, "2.1.208", "v2.1.208", "a" * 40)
    changed = delivery.delivery_id(
        "f5/specs",
        {**target, "repo": "cli"},
        "2.1.208",
        "v2.1.208",
        "a" * 40,
    )

    assert first == second
    assert len(first) == 64
    assert first != changed


@pytest.mark.parametrize(
    ("indentation", "expected"),
    [
        ("tabs", "e007ef699c4cdb8f32bae030246c03433fe3c773f50f7c7bd35e37d16ec438c4"),
        ("spaces", "20285cd9f807a20d4bc22238bbe44dfeb737928ff5f6cba7157e5f5a3ba93caa"),
    ],
)
def test_source_pin_hash_matches_receiver_canonical_json(indentation: str, expected: str) -> None:
    receipt = delivery.PublicationReceipt(
        version="2.1.208",
        commit="a" * 40,
        assets={"a.json": "1" * 64},
    )
    entry = {
        "release_tag": "v2.1.208",
        "target_commit": "a" * 40,
        "version": "2.1.208",
    }

    assert delivery._source_pin_sha256(receipt, entry, indentation) == expected


def _target(repo: str, *, kind: str = "receipt_map") -> dict[str, Any]:
    if kind == "current_pin":
        detail = {
            "detail_kind": kind,
            "detail_path": "tools/spec-release.json",
            "detail_ref": "main",
            "publication_authority": {"kind": "receiver_commit_source_pin"},
        }
    elif kind == "publication_map":
        detail = {
            "detail_collection": "publications",
            "detail_kind": kind,
            "detail_path": "tools/spec-publications.json",
            "detail_publication_fields": [
                "bundle_sha256",
                "marketplace_sha256",
                "marketplace_version",
                "open_vsx_sha256",
                "open_vsx_version",
                "publication_epoch",
                "publication_tag",
                "vsix_name",
                "vsix_sha256",
            ],
            "detail_ref": "main",
            "source_asset_name": "f5xc-api-specs-{release_tag}.zip",
            "publication_authority": {
                "kind": "github_release_marketplaces",
                "marketplace_asset_url": "https://marketplace.example/{version}/vspackage",
                "open_vsx_download_origin": "https://open-vsx.example/",
                "open_vsx_metadata_url": "https://open-vsx.example/api/{version}",
                "repository": f"f5/{repo}",
            },
        }
    else:
        detail = {
            "detail_asset_names": ["provider-{version}.zip"],
            "detail_collection": "receipts",
            "detail_kind": kind,
            "detail_path": "tools/provider-publication-receipts.json",
            "detail_publication_fields": [
                "assets",
                "commit",
                "spec_release_sha256",
                "tag",
                "version",
            ],
            "detail_ref": "main",
            "detail_spec_pin_indent": "spaces",
            "publication_authority": {
                "kind": "github_release",
                "repository": f"f5/{repo}",
                "source_pin_path": "tools/spec-release.json",
            },
        }
    return {
        "owner": "f5",
        "repo": repo,
        "event_type": "updated",
        "receipt_path": "tools/spec-deliveries.json",
        "receipt_ref": "main",
        **detail,
    }


def _payload(target: dict[str, Any], commit: str = "a" * 40) -> dict[str, str]:
    version = "2.1.208"
    tag = f"v{version}"
    source = "f5/specs"
    return {
        "delivery_id": delivery.delivery_id(source, target, version, tag, commit),
        "release_tag": tag,
        "release_url": "https://example.invalid/release",
        "target_commit": commit,
        "trigger_source": source,
        "version": version,
    }


def _source_receipt(
    commit: str = "a" * 40, version: str = "2.1.208"
) -> delivery.PublicationReceipt:
    return delivery.PublicationReceipt(
        version=version,
        commit=commit,
        assets={
            name: format(index, "064x")
            for index, name in enumerate(sorted(publication.expected_asset_names(version)), start=1)
        },
    )


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


@pytest.fixture(autouse=True)
def _committed_source_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(delivery, "source_provenance_at", lambda _commit: _source_provenance())


def _release(commit: str, *extra_lines: str, version: str = "2.1.208") -> dict[str, Any]:
    receipt = _source_receipt(commit, version)
    return {
        "assets": [
            {"digest": f"sha256:{digest}", "name": name} for name, digest in receipt.assets.items()
        ],
        "body": "\n".join(
            [
                source_provenance_marker(_source_provenance()),
                publication.publication_receipt_marker(receipt),
                *extra_lines,
            ]
        ),
        "draft": False,
        "html_url": "https://example/release",
        "immutable": True,
        "prerelease": False,
        "tag_name": f"v{version}",
    }


def _common(payload: dict[str, str]) -> dict[str, Any]:
    return {
        "deliveries": {
            payload["delivery_id"]: {
                "release_tag": payload["release_tag"],
                "target_commit": payload["target_commit"],
                "version": payload["version"],
            }
        },
        "version": 1,
    }


def _provider_evidence(
    target: dict[str, Any],
    payload: dict[str, str],
    receipt: delivery.PublicationReceipt,
) -> dict[str, Any]:
    provider_version = "9.8.7"
    entry = _common(payload)["deliveries"][payload["delivery_id"]]
    return {
        "assets": {
            name.format(version=provider_version): "1" * 64 for name in target["detail_asset_names"]
        },
        "commit": "b" * 40,
        "spec_release_sha256": delivery._source_pin_sha256(
            receipt, entry, target["detail_spec_pin_indent"]
        ),
        "tag": f"v{provider_version}",
        "version": provider_version,
    }


def _receipt_detail(
    target: dict[str, Any],
    payload: dict[str, str],
    receipt: delivery.PublicationReceipt,
) -> dict[str, Any]:
    entry = _common(payload)["deliveries"][payload["delivery_id"]]
    return {
        "receipts": {
            payload["delivery_id"]: {
                "delivery": entry,
                "publication": _provider_evidence(target, payload, receipt),
            }
        },
        "version": 1,
    }


def _xcsh_detail(
    target: dict[str, Any],
    payload: dict[str, str],
    receipt: delivery.PublicationReceipt,
) -> dict[str, Any]:
    entry = _common(payload)["deliveries"][payload["delivery_id"]]
    published_commit = "b" * 40
    evidence = {
        "assets": dict.fromkeys(target["detail_asset_names"], "1" * 64),
        "commit": published_commit,
        "generated": dict.fromkeys(target["detail_generated_names"], "2" * 64),
        "npm": {
            name: {"git_head": published_commit, "integrity": "sha512-QUJD"}
            for name in target["detail_npm_packages"]
        },
        "provider": {"commit": "c" * 40, "tag": "v9.8.7"},
        "spec_release_sha256": delivery._source_pin_sha256(receipt, entry, "tabs"),
        "tag": "v3.2.1",
        "version": "3.2.1",
    }
    return {
        "receipts": {
            payload["delivery_id"]: {
                "delivery": entry,
                "publication": evidence,
            }
        },
        "version": 1,
    }


def _mock_receiver_documents(monkeypatch, *responses: Any) -> None:
    monkeypatch.setenv("DOWNSTREAM_DISPATCH_TOKEN", "test-token")

    remaining = iter(responses)

    def run(*args, **_kwargs):
        command = args[0]
        if "/git/ref/heads/" in command[-1]:
            return subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps({"object": {"sha": "c" * 40, "type": "commit"}}),
                stderr="",
            )
        response = next(remaining)
        if isinstance(response, subprocess.CompletedProcess):
            return response
        body = response if isinstance(response, str) else json.dumps(response)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=body, stderr="")

    monkeypatch.setattr(
        delivery.subprocess,
        "run",
        run,
    )


def test_load_targets_rejects_duplicate_enabled_delivery(tmp_path: Path) -> None:
    config = tmp_path / "downstream.yaml"
    config.write_text(
        """downstream_repositories:
  - &target
    owner: f5
    repo: cli
    event_type: updated
    receipt_path: tools/spec-deliveries.json
    receipt_ref: main
    detail_path: tools/provider-publication-receipts.json
    detail_ref: main
    detail_kind: receipt_map
    detail_collection: receipts
    detail_publication_fields: [assets, commit, spec_release_sha256, tag, version]
    detail_asset_names: ['provider-{version}.zip']
    detail_spec_pin_indent: spaces
    publication_authority:
      kind: github_release
      repository: f5/cli
      source_pin_path: tools/spec-release.json
    enabled: true
  - *target
"""
    )

    with pytest.raises(ValueError, match="duplicate downstream"):
        delivery.load_targets(config)


def test_load_targets_rejects_duplicate_mapping_key(tmp_path: Path) -> None:
    config = tmp_path / "downstream.yaml"
    config.write_text(
        """downstream_repositories:
  - owner: f5
    repo: first
    repo: silently-replaced
    enabled: true
"""
    )

    with pytest.raises(ValueError, match="duplicate key 'repo'"):
        delivery.load_targets(config)


def test_load_targets_rejects_unknown_root_field(tmp_path: Path) -> None:
    config = tmp_path / "downstream.yaml"
    config.write_text(
        json.dumps(
            {
                "downstream_repositories": [],
                "downstream_repository": [],
            }
        )
    )

    with pytest.raises(ValueError, match="unknown root fields: downstream_repository"):
        delivery.load_targets(config)


@pytest.mark.parametrize("enabled", [True, False])
def test_load_targets_rejects_unknown_target_field(tmp_path: Path, enabled: bool) -> None:
    config = tmp_path / "downstream.yaml"
    config.write_text(
        json.dumps(
            {
                "downstream_repositories": [
                    {
                        **_target("cli"),
                        "enabled": enabled,
                        "receipt_ref": "main",
                        "reciept_ref": "main",
                    }
                ]
            }
        )
    )

    with pytest.raises(ValueError, match="unknown fields: reciept_ref"):
        delivery.load_targets(config)


def test_load_targets_rejects_zero_enabled_targets(tmp_path: Path) -> None:
    config = tmp_path / "downstream.yaml"
    config.write_text("downstream_repositories:\n  - {enabled: false}\n")

    with pytest.raises(ValueError, match="no enabled targets"):
        delivery.load_targets(config)


def test_repository_target_contracts_are_complete() -> None:
    targets = delivery.load_targets(Path("config/downstream_repos.yaml"))

    assert {target["detail_kind"] for target in targets} == {
        "current_pin",
        "publication_map",
        "receipt_map",
    }
    assert {target["publication_authority"]["kind"] for target in targets} == {
        "github_release",
        "github_release_marketplaces",
        "github_release_npm",
        "receiver_commit_source_pin",
    }
    assert len(targets) == 4


def test_load_targets_requires_explicit_publication_authority(tmp_path: Path) -> None:
    config = tmp_path / "downstream.yaml"
    config.write_text(
        """downstream_repositories:
  - owner: f5
    repo: cli
    event_type: updated
    receipt_path: tools/spec-deliveries.json
    receipt_ref: main
    detail_path: tools/provider-publication-receipts.json
    detail_ref: main
    detail_kind: receipt_map
    detail_collection: receipts
    detail_publication_fields: [assets, commit, spec_release_sha256, tag, version]
    detail_asset_names: ['provider-{version}.zip']
    detail_spec_pin_indent: spaces
    enabled: true
"""
    )

    with pytest.raises(TypeError, match="no publication_authority"):
        delivery.load_targets(config)


def test_authoritative_release_rejects_receiver_attested_asset_digests(monkeypatch) -> None:
    evidence = {
        "assets": {"provider-9.8.7.zip": "1" * 64},
        "commit": "b" * 40,
        "spec_release_sha256": "2" * 64,
        "tag": "v9.8.7",
        "version": "9.8.7",
    }
    release = {
        "assets": [{"digest": f"sha256:{'f' * 64}", "name": "provider-9.8.7.zip"}],
        "draft": False,
        "immutable": True,
        "prerelease": False,
        "tag_name": "v9.8.7",
    }
    monkeypatch.setattr(delivery, "_github_json", lambda *_args: release)

    with pytest.raises(RuntimeError, match="assets differ from receiver evidence"):
        delivery._verify_authoritative_publication(_target("provider"), evidence, set())


def test_authoritative_release_is_queried_once_and_bound_to_tag_bytes(monkeypatch) -> None:
    target = _target("provider")
    pin = b'{"exact":"source pin"}\n'
    evidence = {
        "assets": {"provider-9.8.7.zip": "1" * 64},
        "commit": "b" * 40,
        "spec_release_sha256": hashlib.sha256(pin).hexdigest(),
        "tag": "v9.8.7",
        "version": "9.8.7",
    }
    calls: list[str] = []

    def github_json(endpoint: str, _label: str) -> dict[str, Any]:
        calls.append(endpoint)
        if "/releases/tags/" in endpoint:
            return {
                "assets": [{"digest": f"sha256:{'1' * 64}", "name": "provider-9.8.7.zip"}],
                "draft": False,
                "immutable": True,
                "prerelease": False,
                "tag_name": "v9.8.7",
            }
        return {"sha": "b" * 40}

    raw_calls: list[str] = []
    monkeypatch.setattr(delivery, "_github_json", github_json)
    monkeypatch.setattr(
        delivery,
        "_github_raw",
        lambda endpoint, _label: raw_calls.append(endpoint) or pin,
    )
    cache: set[str] = set()

    delivery._verify_authoritative_publication(target, evidence, cache)
    delivery._verify_authoritative_publication(target, evidence, cache)

    assert len(calls) == 2
    assert len(raw_calls) == 1


def test_npm_authority_hashes_exact_registry_tarball_bytes(monkeypatch) -> None:
    tarball = b"published npm tarball"
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode()
    authority = {
        "kind": "github_release_npm",
        "npm_registry": "https://registry.example",
    }
    evidence = {
        "commit": "b" * 40,
        "npm": {"@f5/example": {"git_head": "b" * 40, "integrity": integrity}},
        "version": "9.8.7",
    }
    urls: list[str] = []
    monkeypatch.setattr(
        delivery,
        "_http_json",
        lambda url, _label: {
            "dist": {
                "integrity": integrity,
                "tarball": "https://registry.example/@f5/example/-/example-9.8.7.tgz",
            },
            "gitHead": "b" * 40,
            "version": "9.8.7",
        },
    )
    monkeypatch.setattr(
        delivery,
        "_download_bytes",
        lambda url, _label: urls.append(url) or tarball,
    )

    delivery._verify_npm(authority, evidence)

    assert urls == ["https://registry.example/@f5/example/-/example-9.8.7.tgz"]


def test_marketplace_authority_hashes_both_public_vsix_artifacts(monkeypatch) -> None:
    vsix = b"published extension"
    digest = hashlib.sha256(vsix).hexdigest()
    authority = {
        "kind": "github_release_marketplaces",
        "marketplace_asset_url": "https://marketplace.example/{version}/vspackage",
        "open_vsx_download_origin": "https://open-vsx.example/",
        "open_vsx_metadata_url": "https://open-vsx.example/api/{version}",
        "repository": "f5/vscode",
    }
    evidence = {
        "marketplace_sha256": digest,
        "marketplace_version": "2.2608.1120000",
        "open_vsx_sha256": digest,
        "publication_tag": "v2.1.208-260801120000",
        "vsix_name": "xcsh.vsix",
        "vsix_sha256": digest,
    }
    downloads: list[str] = []
    monkeypatch.setattr(delivery, "_authoritative_release", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        delivery,
        "_http_json",
        lambda *_args: {"files": {"download": "https://open-vsx.example/xcsh.vsix"}},
    )
    monkeypatch.setattr(
        delivery,
        "_download_bytes",
        lambda url, _label: (
            downloads.append(url) or (gzip.compress(vsix) if "marketplace.example" in url else vsix)
        ),
    )

    delivery._verify_marketplaces(authority, evidence)

    assert downloads == [
        "https://open-vsx.example/xcsh.vsix",
        "https://marketplace.example/2.2608.1120000/vspackage",
    ]


def test_receiver_ledger_requires_valid_json(monkeypatch) -> None:
    target = _target("cli")
    payload = _payload(target)
    _mock_receiver_documents(monkeypatch, "not-json")

    with pytest.raises(RuntimeError, match="is not valid JSON"):
        delivery.receiver_has_delivery(target, payload, _source_receipt())


@pytest.mark.parametrize(
    "ledger",
    [
        [],
        {},
        {"version": 2, "deliveries": {}},
        {"version": 1, "deliveries": []},
    ],
)
def test_receiver_ledger_rejects_malformed_contract(monkeypatch, ledger) -> None:
    target = _target("cli")
    _mock_receiver_documents(monkeypatch, ledger)

    with pytest.raises(RuntimeError, match="is malformed"):
        delivery.receiver_has_delivery(target, _payload(target), _source_receipt())


def test_receiver_ledger_rejects_conflicting_delivery_identity(monkeypatch) -> None:
    target = _target("cli")
    payload = _payload(target)
    ledger = _common(payload)
    ledger["deliveries"][payload["delivery_id"]]["target_commit"] = "b" * 40
    _mock_receiver_documents(monkeypatch, ledger)

    with pytest.raises(RuntimeError, match="noncanonical delivery ID"):
        delivery.receiver_has_delivery(target, payload, _source_receipt())


def test_receiver_ledger_returns_false_only_for_absent_delivery(monkeypatch) -> None:
    target = _target("cli")
    _mock_receiver_documents(
        monkeypatch,
        {"version": 1, "deliveries": {}},
        {"version": 1, "receipts": {}},
    )

    assert not delivery.receiver_has_delivery(target, _payload(target), _source_receipt())


def test_receiver_evidence_reads_one_pinned_main_snapshot(monkeypatch) -> None:
    target = _target("cli")
    _mock_receiver_documents(
        monkeypatch,
        {"version": 1, "deliveries": {}},
        {"version": 1, "receipts": {}},
    )
    calls: list[str] = []
    mocked_run = delivery.subprocess.run

    def recording_run(*args, **kwargs):
        calls.append(args[0][-1])
        return mocked_run(*args, **kwargs)

    monkeypatch.setattr(delivery.subprocess, "run", recording_run)

    assert not delivery.receiver_has_delivery(target, _payload(target), _source_receipt())
    assert calls[0].endswith("/git/ref/heads/main")
    assert [endpoint.rsplit("?ref=", 1)[1] for endpoint in calls[1:]] == ["c" * 40] * 2


def test_unchanged_receiver_commit_reuses_fully_validated_snapshot(monkeypatch) -> None:
    target = _target("console", kind="current_pin")
    payload = _payload(target)
    receipt = _source_receipt()
    pin = {
        "assets": receipt.assets,
        "release_tag": payload["release_tag"],
        "target_commit": payload["target_commit"],
        "version": payload["version"],
    }
    _mock_receiver_documents(monkeypatch, _common(payload), pin)
    endpoints: list[str] = []
    mocked_run = delivery.subprocess.run

    def recording_run(*args, **kwargs):
        endpoints.append(args[0][-1])
        return mocked_run(*args, **kwargs)

    monkeypatch.setattr(delivery.subprocess, "run", recording_run)
    snapshot_cache: dict[tuple[str, str, str, str, str], bool] = {}

    assert delivery.receiver_has_delivery(target, payload, receipt, snapshot_cache=snapshot_cache)
    assert delivery.receiver_has_delivery(target, payload, receipt, snapshot_cache=snapshot_cache)

    assert sum("/git/ref/heads/" in endpoint for endpoint in endpoints) == 2
    assert sum("/contents/" in endpoint for endpoint in endpoints) == 2


def test_common_receipt_without_detailed_ledger_fails_closed(monkeypatch) -> None:
    target = _target("provider")
    payload = _payload(target)
    not_found = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="gh: Not Found (HTTP 404)"
    )
    _mock_receiver_documents(monkeypatch, _common(payload), not_found)

    with pytest.raises(RuntimeError, match=r"detailed ledger.*absent"):
        delivery.receiver_has_delivery(target, payload, _source_receipt())


def test_common_and_detailed_keysets_must_match(monkeypatch) -> None:
    target = _target("provider")
    payload = _payload(target)
    _mock_receiver_documents(
        monkeypatch,
        _common(payload),
        {"receipts": {}, "version": 1},
    )

    with pytest.raises(RuntimeError, match="different delivery keys"):
        delivery.receiver_has_delivery(target, payload, _source_receipt())


def test_detailed_receipt_must_cross_bind_common_delivery(monkeypatch) -> None:
    target = _target("provider")
    payload = _payload(target)
    detail = _receipt_detail(target, payload, _source_receipt())
    detail["receipts"][payload["delivery_id"]]["delivery"]["target_commit"] = "b" * 40
    _mock_receiver_documents(monkeypatch, _common(payload), detail)

    with pytest.raises(RuntimeError, match="conflicts with the common ledger"):
        delivery.receiver_has_delivery(target, payload, _source_receipt())


def test_receipt_map_requires_exact_nested_artifact_sets(monkeypatch) -> None:
    target = next(
        target
        for target in delivery.load_targets(Path("config/downstream_repos.yaml"))
        if target["repo"] == "xcsh"
    )
    payload = _payload(target)
    receipt = _source_receipt()
    entry = _common(payload)["deliveries"][payload["delivery_id"]]
    published_commit = "b" * 40
    evidence = {
        "assets": dict.fromkeys(target["detail_asset_names"], "1" * 64),
        "commit": published_commit,
        "generated": dict.fromkeys(target["detail_generated_names"], "2" * 64),
        "npm": {
            name: {"git_head": published_commit, "integrity": "sha512-QUJD"}
            for name in target["detail_npm_packages"]
        },
        "provider": {"commit": "c" * 40, "tag": "v9.8.7"},
        "spec_release_sha256": delivery._source_pin_sha256(receipt, entry, "tabs"),
        "tag": "v3.2.1",
        "version": "3.2.1",
    }

    for field, invalid in (
        ("assets", {"junk.bin": "1" * 64}),
        ("generated", {"junk.ts": "2" * 64}),
        (
            "npm",
            {"not-an-xcsh-package": {"git_head": published_commit, "integrity": "sha512-QUJD"}},
        ),
    ):
        malformed = deepcopy(evidence)
        malformed[field] = invalid
        detail = {
            "receipts": {payload["delivery_id"]: {"delivery": entry, "publication": malformed}},
            "version": 1,
        }
        _mock_receiver_documents(monkeypatch, _common(payload), detail)
        with pytest.raises(RuntimeError, match=rf"wrong .*{field.removesuffix('s')}.* set"):
            delivery.receiver_has_delivery(target, payload, receipt)


def test_receipt_map_binds_requested_source_pin_digest(monkeypatch) -> None:
    target = _target("provider")
    payload = _payload(target)
    receipt = _source_receipt()
    detail = _receipt_detail(target, payload, receipt)
    detail["receipts"][payload["delivery_id"]]["publication"]["spec_release_sha256"] = "f" * 64
    _mock_receiver_documents(monkeypatch, _common(payload), detail)

    with pytest.raises(RuntimeError, match="not bound to the source pin"):
        delivery.receiver_has_delivery(target, payload, receipt)


def test_receiver_attestation_cannot_complete_without_authoritative_publication(
    monkeypatch,
) -> None:
    target = _target("provider")
    payload = _payload(target)
    receipt = _source_receipt()
    detail = _receipt_detail(target, payload, receipt)
    _mock_receiver_documents(monkeypatch, _common(payload), detail)
    monkeypatch.setattr(
        delivery,
        "_verify_authoritative_publication",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("authoritative publication absent")),
    )

    with pytest.raises(RuntimeError, match="authoritative publication absent"):
        delivery.receiver_has_delivery(target, payload, receipt)


def test_receipt_map_binds_historical_source_pin_digest(monkeypatch) -> None:
    target = _target("provider")
    payload = _payload(target)
    current_receipt = _source_receipt()
    historical_version = "2.1.207"
    historical_commit = "b" * 40
    historical_tag = f"v{historical_version}"
    historical_id = delivery.delivery_id(
        payload["trigger_source"],
        target,
        historical_version,
        historical_tag,
        historical_commit,
    )
    historical_payload = {
        **payload,
        "delivery_id": historical_id,
        "release_tag": historical_tag,
        "target_commit": historical_commit,
        "version": historical_version,
    }
    historical_receipt = _source_receipt(historical_commit, historical_version)
    common = _common(payload)
    common["deliveries"][historical_id] = {
        "release_tag": historical_tag,
        "target_commit": historical_commit,
        "version": historical_version,
    }
    detail = _receipt_detail(target, payload, current_receipt)
    historical_entry = common["deliveries"][historical_id]
    historical_evidence = _provider_evidence(target, historical_payload, historical_receipt)
    historical_evidence["spec_release_sha256"] = "f" * 64
    detail["receipts"][historical_id] = {
        "delivery": historical_entry,
        "publication": historical_evidence,
    }
    monkeypatch.setattr(
        delivery,
        "github_release",
        lambda _repo, tag: (
            _release(historical_commit, version=historical_version)
            if tag == historical_tag
            else pytest.fail("unexpected release lookup")
        ),
    )
    _mock_receiver_documents(monkeypatch, common, detail)

    with pytest.raises(RuntimeError, match="not bound to the source pin"):
        delivery.receiver_has_delivery(target, payload, current_receipt)


def _vscode_detail(payload: dict[str, str], bundle_sha256: str) -> dict[str, Any]:
    evidence = {
        "bundle_sha256": bundle_sha256,
        "marketplace_sha256": "3" * 64,
        "marketplace_version": "2.2608.1120000",
        "open_vsx_sha256": "3" * 64,
        "open_vsx_version": "2.2608.1120000",
        "publication_epoch": "1785585600",
        "publication_tag": "v2.1.208-260801120000",
        "vsix_name": "xcsh.vsix",
        "vsix_sha256": "3" * 64,
    }
    return {"publications": {payload["delivery_id"]: evidence}, "version": 1}


def test_vscode_publication_must_bind_source_bundle_bytes(monkeypatch) -> None:
    target = _target("vscode", kind="publication_map")
    payload = _payload(target)
    receipt = _source_receipt()
    _mock_receiver_documents(monkeypatch, _common(payload), _vscode_detail(payload, "f" * 64))

    with pytest.raises(RuntimeError, match="source release bytes"):
        delivery.receiver_has_delivery(target, payload, receipt)


def test_vscode_publication_identity_is_derived_from_source_and_epoch(monkeypatch) -> None:
    target = _target("vscode", kind="publication_map")
    payload = _payload(target)
    receipt = _source_receipt()
    asset_name = target["source_asset_name"].format(release_tag=payload["release_tag"])
    detail = _vscode_detail(payload, receipt.assets[asset_name])
    detail["publications"][payload["delivery_id"]]["publication_tag"] = "v7.7.7-260801120000"
    _mock_receiver_documents(monkeypatch, _common(payload), detail)

    with pytest.raises(RuntimeError, match="disagrees with its source identity"):
        delivery.receiver_has_delivery(target, payload, receipt)


def test_console_nonempty_ledger_requires_current_pin(monkeypatch) -> None:
    target = _target("console", kind="current_pin")
    payload = _payload(target)
    not_found = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="gh: Not Found (HTTP 404)"
    )
    _mock_receiver_documents(monkeypatch, _common(payload), not_found)

    with pytest.raises(RuntimeError, match="current pin is absent"):
        delivery.receiver_has_delivery(target, payload, _source_receipt())


def test_console_current_pin_cross_binds_identity_and_source_bytes(monkeypatch) -> None:
    target = _target("console", kind="current_pin")
    payload = _payload(target)
    receipt = _source_receipt()
    pin = {
        "assets": receipt.assets,
        "release_tag": payload["release_tag"],
        "target_commit": payload["target_commit"],
        "version": payload["version"],
    }
    _mock_receiver_documents(monkeypatch, _common(payload), pin)

    assert delivery.receiver_has_delivery(target, payload, receipt)


def test_console_historical_retry_still_verifies_newest_pin_bytes(monkeypatch) -> None:
    target = _target("console", kind="current_pin")
    payload = _payload(target)
    current_version = "2.1.209"
    current_commit = "b" * 40
    current_tag = f"v{current_version}"
    current_id = delivery.delivery_id(
        payload["trigger_source"], target, current_version, current_tag, current_commit
    )
    common = _common(payload)
    common["deliveries"][current_id] = {
        "release_tag": current_tag,
        "target_commit": current_commit,
        "version": current_version,
    }
    pin = {
        "assets": dict.fromkeys(publication.expected_asset_names(current_version), "f" * 64),
        "release_tag": current_tag,
        "target_commit": current_commit,
        "version": current_version,
    }
    monkeypatch.setattr(
        delivery,
        "github_release",
        lambda *_args: _release(current_commit, version=current_version),
    )
    _mock_receiver_documents(monkeypatch, common, pin)

    with pytest.raises(RuntimeError, match="conflicts with the source release bytes"):
        delivery.receiver_has_delivery(target, payload, _source_receipt())


def test_retry_skips_receipted_target_and_resumes_next(monkeypatch) -> None:
    targets = [
        _target("first"),
        _target("second"),
    ]
    commit = "a" * 40
    first_id = delivery.delivery_id("f5/specs", targets[0], "2.1.208", "v2.1.208", commit)
    release = _release(commit, delivery.receipt_marker(first_id))
    posted: list[str] = []
    receipted: list[str] = []
    checks = {"first": 0, "second": 0}

    monkeypatch.setattr(delivery, "github_release", lambda _repo, _tag: release)
    monkeypatch.setattr(
        delivery,
        "post_dispatch",
        lambda target, _payload: posted.append(target["repo"]),
    )
    monkeypatch.setattr(
        delivery,
        "append_receipt",
        lambda _repo, _tag, marker, _source: receipted.append(marker),
    )

    def receiver_has_delivery(target, _payload, _source_receipt, **_kwargs):
        checks[target["repo"]] += 1
        return target["repo"] == "first" or checks[target["repo"]] > 1

    monkeypatch.setattr(delivery, "receiver_has_delivery", receiver_has_delivery)

    delivery.dispatch_all(
        "f5/specs",
        targets,
        "2.1.208",
        "v2.1.208",
        "https://example/release",
        commit,
        poll_attempts=1,
        poll_interval=0,
    )

    assert posted == ["second"]
    assert len(receipted) == 1


def test_verify_only_rechecks_every_receipted_target_without_mutation(monkeypatch) -> None:
    targets = [_target("first"), _target("second")]
    commit = "a" * 40
    markers = [
        delivery.receipt_marker(
            delivery.delivery_id("f5/specs", target, "2.1.208", "v2.1.208", commit)
        )
        for target in targets
    ]
    release = _release(commit, *markers)
    verified: list[str] = []
    monkeypatch.setattr(delivery, "github_release", lambda *_args: release)
    monkeypatch.setattr(
        delivery,
        "receiver_has_delivery",
        lambda target, *_args, **_kwargs: verified.append(target["repo"]) or True,
    )
    monkeypatch.setattr(
        delivery,
        "post_dispatch",
        lambda *_args: pytest.fail("verify-only mode must not dispatch"),
    )
    monkeypatch.setattr(
        delivery,
        "append_receipt",
        lambda *_args: pytest.fail("verify-only mode must not mutate receipts"),
    )

    delivery.dispatch_all(
        "f5/specs",
        targets,
        "2.1.208",
        "v2.1.208",
        "https://example/release",
        commit,
        verify_only=True,
    )

    assert verified == ["first", "second"]


def test_verify_only_fails_closed_when_receipted_authority_drifts(monkeypatch) -> None:
    target = _target("provider")
    commit = "a" * 40
    marker = delivery.receipt_marker(
        delivery.delivery_id("f5/specs", target, "2.1.208", "v2.1.208", commit)
    )
    monkeypatch.setattr(delivery, "github_release", lambda *_args: _release(commit, marker))
    monkeypatch.setattr(delivery, "receiver_has_delivery", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        delivery,
        "post_dispatch",
        lambda *_args: pytest.fail("authority drift must not trigger a duplicate dispatch"),
    )
    monkeypatch.setattr(
        delivery,
        "append_receipt",
        lambda *_args: pytest.fail("authority drift must not rewrite its receipt"),
    )

    with pytest.raises(RuntimeError, match="verify-only authority audit failed"):
        delivery.dispatch_all(
            "f5/specs",
            [target],
            "2.1.208",
            "v2.1.208",
            "https://example/release",
            commit,
            verify_only=True,
        )


def test_verify_only_rejects_unreceipted_target_without_dispatch(monkeypatch) -> None:
    target = _target("new-target")
    commit = "a" * 40
    monkeypatch.setattr(delivery, "github_release", lambda *_args: _release(commit))
    monkeypatch.setattr(
        delivery,
        "receiver_has_delivery",
        lambda *_args, **_kwargs: pytest.fail("unreceipted targets have no authority to audit"),
    )
    monkeypatch.setattr(
        delivery,
        "post_dispatch",
        lambda *_args: pytest.fail("verify-only mode must not dispatch"),
    )

    with pytest.raises(RuntimeError, match="has no durable source receipt"):
        delivery.dispatch_all(
            "f5/specs",
            [target],
            "2.1.208",
            "v2.1.208",
            "https://example/release",
            commit,
            verify_only=True,
        )


def test_verify_only_reaches_every_configured_authority_kind(monkeypatch) -> None:
    targets = delivery.load_targets(Path("config/downstream_repos.yaml"))
    commit = "a" * 40
    receipt = _source_receipt(commit)
    markers = [
        delivery.receipt_marker(
            delivery.delivery_id("f5/specs", target, "2.1.208", "v2.1.208", commit)
        )
        for target in targets
    ]
    receiver_documents: dict[tuple[str, str], Any] = {}
    for target in targets:
        payload = _payload(target, commit)
        receiver_documents[(target["repo"], target["receipt_path"])] = _common(payload)
        if target["detail_kind"] == "current_pin":
            detail = {
                "assets": receipt.assets,
                "release_tag": payload["release_tag"],
                "target_commit": payload["target_commit"],
                "version": payload["version"],
            }
        elif target["publication_authority"]["kind"] == "github_release_npm":
            detail = _xcsh_detail(target, payload, receipt)
        elif target["detail_kind"] == "publication_map":
            asset_name = target["source_asset_name"].format(release_tag=payload["release_tag"])
            detail = _vscode_detail(payload, receipt.assets[asset_name])
        else:
            detail = _receipt_detail(target, payload, receipt)
        receiver_documents[(target["repo"], target["detail_path"])] = detail

    reached: list[str] = []
    monkeypatch.setattr(delivery, "github_release", lambda *_args: _release(commit, *markers))
    monkeypatch.setattr(
        delivery,
        "_receiver_ref_commit",
        lambda _target: "d" * 40,
    )
    monkeypatch.setattr(
        delivery,
        "_receiver_document",
        lambda target, path, *_args: deepcopy(receiver_documents[(target["repo"], path)]),
    )
    monkeypatch.setattr(
        delivery,
        "_authoritative_release",
        lambda repository, *_args, **_kwargs: reached.append(f"github:{repository}"),
    )
    monkeypatch.setattr(
        delivery,
        "_verify_source_pin_and_generated",
        lambda authority, _evidence: reached.append(f"source:{authority['repository']}"),
    )
    monkeypatch.setattr(delivery, "_verify_npm", lambda *_args: reached.append("npm"))
    monkeypatch.setattr(
        delivery,
        "_verify_marketplaces",
        lambda *_args: reached.append("marketplaces"),
    )
    monkeypatch.setattr(delivery, "_github_json", lambda *_args: {"sha": "c" * 40})
    validate_current_pin = delivery._validate_current_pin

    def validate_pin(*args, **kwargs):
        reached.append("current-pin")
        return validate_current_pin(*args, **kwargs)

    monkeypatch.setattr(delivery, "_validate_current_pin", validate_pin)
    monkeypatch.setattr(
        delivery,
        "post_dispatch",
        lambda *_args: pytest.fail("verify-only mode must not dispatch"),
    )
    monkeypatch.setattr(
        delivery,
        "append_receipt",
        lambda *_args: pytest.fail("verify-only mode must not mutate receipts"),
    )

    delivery.dispatch_all(
        "f5/specs",
        targets,
        "2.1.208",
        "v2.1.208",
        "https://example/release",
        commit,
        verify_only=True,
    )

    assert reached == [
        "github:f5-sales-demo/xcsh",
        "source:f5-sales-demo/xcsh",
        "npm",
        "marketplaces",
        "github:f5-sales-demo/terraform-provider-xcsh",
        "source:f5-sales-demo/terraform-provider-xcsh",
        "current-pin",
    ]


def test_verify_only_cli_forwards_non_mutating_mode(monkeypatch) -> None:
    target = _target("provider")
    captured: list[dict[str, object]] = []
    monkeypatch.setenv("GH_TOKEN", "source-token")
    monkeypatch.setenv("DOWNSTREAM_DISPATCH_TOKEN", "downstream-token")
    monkeypatch.setattr(delivery, "repository_name", lambda: "f5/specs")
    monkeypatch.setattr(delivery, "load_targets", lambda _path: [target])
    monkeypatch.setattr(
        delivery,
        "dispatch_all",
        lambda *_args, **kwargs: captured.append(kwargs),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dispatch-downstreams",
            "--version",
            "2.1.208",
            "--tag",
            "v2.1.208",
            "--release-url",
            "https://example/release",
            "--target-commit",
            "a" * 40,
            "--verify-only",
        ],
    )

    assert delivery.main() == 0
    assert captured == [
        {
            "poll_attempts": 60,
            "poll_interval": 30.0,
            "verify_only": True,
        }
    ]


def test_dispatch_payload_contains_stable_receiver_idempotency_key(monkeypatch) -> None:
    target = _target("cli")
    commit = "b" * 40
    payloads: list[dict[str, str]] = []
    monkeypatch.setattr(delivery, "github_release", lambda _repo, _tag: _release(commit))
    monkeypatch.setattr(
        delivery,
        "post_dispatch",
        lambda _target, payload: payloads.append(payload),
    )
    monkeypatch.setattr(delivery, "append_receipt", lambda *_args: None)
    checks = iter((False, True))
    monkeypatch.setattr(
        delivery,
        "receiver_has_delivery",
        lambda *_args, **_kwargs: next(checks),
    )

    delivery.dispatch_all(
        "f5/specs",
        [target],
        "2.1.208",
        "v2.1.208",
        "https://example/release",
        commit,
        poll_attempts=1,
        poll_interval=0,
    )

    assert payloads[0]["delivery_id"] == delivery.delivery_id(
        "f5/specs", target, "2.1.208", "v2.1.208", commit
    )
    assert "run_id" not in payloads[0]


@pytest.mark.parametrize(
    ("attempts", "interval"),
    [(61, 0), (1, 31), (60, 30.01)],
)
def test_dispatch_rejects_unbounded_polling(attempts: int, interval: float) -> None:
    with pytest.raises(ValueError, match="polling configuration"):
        delivery.dispatch_all(
            "f5/specs",
            [_target("cli")],
            "2.1.208",
            "v2.1.208",
            "https://example/release",
            "b" * 40,
            poll_attempts=attempts,
            poll_interval=interval,
        )


@pytest.mark.parametrize(
    ("source_marker", "message"),
    [
        (None, "no source provenance"),
        (source_provenance_marker(_source_provenance(asset_size=1)), "asset_size"),
    ],
)
def test_dispatch_rejects_missing_or_mismatched_source_provenance(
    monkeypatch: pytest.MonkeyPatch,
    source_marker: str | None,
    message: str,
) -> None:
    target = _target("cli")
    release = _release("b" * 40)
    lines = [
        line
        for line in str(release["body"]).splitlines()
        if "api-specs-source-provenance:" not in line
    ]
    if source_marker is not None:
        lines.insert(0, source_marker)
    release["body"] = "\n".join(lines)
    monkeypatch.setattr(delivery, "github_release", lambda *_args: release)
    monkeypatch.setattr(
        delivery,
        "post_dispatch",
        lambda *_args: pytest.fail("unverified provenance must not dispatch"),
    )

    with pytest.raises(RuntimeError, match=message):
        delivery.dispatch_all(
            "f5/specs",
            [target],
            "2.1.208",
            "v2.1.208",
            "https://example/release",
            "b" * 40,
            poll_attempts=1,
            poll_interval=0,
        )


def test_append_receipt_preserves_source_provenance_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "b" * 40
    release = _release(commit)
    marker = delivery.receipt_marker("delivery-id")
    monkeypatch.setattr(delivery, "github_release", lambda *_args: deepcopy(release))

    def edit_release(args: list[str], *, use_downstream_token: bool):
        assert use_downstream_token is False
        notes = Path(args[args.index("--notes-file") + 1])
        release["body"] = notes.read_text()
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(delivery, "_network_run", edit_release)

    delivery.append_receipt(
        "f5/specs",
        "v2.1.208",
        marker,
        _source_provenance(),
    )

    assert marker in release["body"]
    assert str(release["body"]).count("api-specs-source-provenance:") == 1


def test_append_receipt_rechecks_source_provenance_before_body_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _release("b" * 40)
    release["body"] = "\n".join(
        line
        for line in str(release["body"]).splitlines()
        if "api-specs-source-provenance:" not in line
    )
    monkeypatch.setattr(delivery, "github_release", lambda *_args: release)
    monkeypatch.setattr(
        delivery,
        "_network_run",
        lambda *_args, **_kwargs: pytest.fail("missing provenance must block body mutation"),
    )

    with pytest.raises(RuntimeError, match="no source provenance"):
        delivery.append_receipt(
            "f5/specs",
            "v2.1.208",
            delivery.receipt_marker("delivery-id"),
            _source_provenance(),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("draft", True, "still a draft"),
        ("prerelease", True, "unexpectedly a prerelease"),
        ("immutable", False, "is not immutable"),
        ("tag_name", "v2.1.207", "identity differs"),
    ],
)
def test_dispatch_rejects_unverified_source_release_state(
    monkeypatch, field: str, value: object, message: str
) -> None:
    target = _target("cli")
    release = _release("b" * 40)
    release[field] = value
    monkeypatch.setattr(delivery, "github_release", lambda *_args: release)
    monkeypatch.setattr(
        delivery,
        "post_dispatch",
        lambda *_args: pytest.fail("unverified releases must not be dispatched"),
    )

    with pytest.raises(RuntimeError, match=message):
        delivery.dispatch_all(
            "f5/specs",
            [target],
            "2.1.208",
            "v2.1.208",
            "https://example/release",
            "b" * 40,
            poll_attempts=1,
            poll_interval=0,
        )


def test_dispatch_rejects_source_api_digest_disagreement(monkeypatch) -> None:
    target = _target("cli")
    release = _release("b" * 40)
    release["assets"][0]["digest"] = f"sha256:{'f' * 64}"
    monkeypatch.setattr(delivery, "github_release", lambda *_args: release)

    with pytest.raises(RuntimeError, match="asset digests differ"):
        delivery.dispatch_all(
            "f5/specs",
            [target],
            "2.1.208",
            "v2.1.208",
            "https://example/release",
            "b" * 40,
            poll_attempts=1,
            poll_interval=0,
        )


def test_dispatch_rejects_release_url_not_bound_to_tag(monkeypatch) -> None:
    target = _target("cli")
    monkeypatch.setattr(delivery, "github_release", lambda *_args: _release("b" * 40))

    with pytest.raises(RuntimeError, match="URL differs"):
        delivery.dispatch_all(
            "f5/specs",
            [target],
            "2.1.208",
            "v2.1.208",
            "https://attacker.invalid/not-the-release",
            "b" * 40,
            poll_attempts=1,
            poll_interval=0,
        )


def test_dispatch_acceptance_without_receiver_completion_is_not_receipted(monkeypatch) -> None:
    target = _target("cli")
    receipted: list[str] = []
    monkeypatch.setattr(delivery, "github_release", lambda *_args: _release("c" * 40))
    monkeypatch.setattr(delivery, "post_dispatch", lambda *_args: None)
    monkeypatch.setattr(delivery, "receiver_has_delivery", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        delivery,
        "append_receipt",
        lambda _repo, _tag, marker, _source: receipted.append(marker),
    )

    with pytest.raises(RuntimeError, match="not receipted after 2 checks"):
        delivery.dispatch_all(
            "f5/specs",
            [target],
            "2.1.208",
            "v2.1.208",
            "https://example/release",
            "c" * 40,
            poll_attempts=2,
            poll_interval=0,
        )

    assert receipted == []


def test_source_receipt_without_receiver_completion_fails_closed(monkeypatch) -> None:
    target = _target("cli")
    commit = "d" * 40
    identifier = delivery.delivery_id("f5/specs", target, "2.1.208", "v2.1.208", commit)
    posted: list[str] = []
    monkeypatch.setattr(
        delivery,
        "github_release",
        lambda *_args: _release(commit, delivery.receipt_marker(identifier)),
    )
    monkeypatch.setattr(delivery, "receiver_has_delivery", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        delivery,
        "post_dispatch",
        lambda target, _payload: posted.append(target["repo"]),
    )

    with pytest.raises(RuntimeError, match="source receipt exists without receiver"):
        delivery.dispatch_all(
            "f5/specs",
            [target],
            "2.1.208",
            "v2.1.208",
            "https://example/release",
            commit,
            poll_attempts=1,
            poll_interval=0,
        )

    assert posted == []


def test_receiver_completion_without_source_marker_is_reconciled(monkeypatch) -> None:
    target = _target("cli")
    posted: list[str] = []
    receipted: list[str] = []
    monkeypatch.setattr(delivery, "github_release", lambda *_args: _release("d" * 40))
    monkeypatch.setattr(delivery, "receiver_has_delivery", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        delivery, "post_dispatch", lambda target, _payload: posted.append(target["repo"])
    )
    monkeypatch.setattr(
        delivery,
        "append_receipt",
        lambda _repo, _tag, marker, _source: receipted.append(marker),
    )

    delivery.dispatch_all(
        "f5/specs",
        [target],
        "2.1.208",
        "v2.1.208",
        "https://example/release",
        "d" * 40,
        poll_attempts=1,
        poll_interval=0,
    )

    assert posted == []
    assert len(receipted) == 1
