"""Tests for explicit-receipt upstream contract evidence."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from scripts.upstream_contract_removals import (
    UpstreamRemovalError,
    build_change_report,
    build_report,
    find_removals,
    load_acknowledgements,
    load_release_receipt,
    operation_inventory,
    render_change_markdown,
    validate_receipt_asset,
)


def _receipt(tag: str = "v2026.09.25-1", digest: str = "a" * 64) -> dict:
    return {
        "version": tag.removeprefix("v"),
        "tag_name": tag,
        "published_at": "2026-09-26T10:39:30Z",
        "asset_name": f"api-specs-{tag}.zip",
        "asset_size": 123,
        "asset_sha256": digest,
    }


def _release(receipt: dict) -> dict:
    return {
        "tag_name": receipt["tag_name"],
        "assets": [
            {
                "name": receipt["asset_name"],
                "size": receipt["asset_size"],
                "digest": f"sha256:{receipt['asset_sha256']}",
                "browser_download_url": "https://github.com/f5-sales-demo/api-specs/releases/download/file.zip",
            }
        ],
    }


def test_receipt_selects_explicit_consumed_release_across_skipped_versions(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(_receipt("v2026.09.25-1")))

    receipt = load_release_receipt(path)
    asset = validate_receipt_asset(receipt, _release(receipt))

    assert receipt["tag_name"] == "v2026.09.25-1"
    assert asset["name"] == "api-specs-v2026.09.25-1.zip"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda receipt: receipt.update(tag_name="latest"), "stable tag"),
        (lambda receipt: receipt.update(asset_name="other.zip"), "asset name"),
        (lambda receipt: receipt.update(asset_sha256="bad"), "SHA-256"),
    ],
)
def test_receipt_identity_mismatches_fail_closed(tmp_path: Path, mutation, message: str) -> None:
    receipt = _receipt()
    mutation(receipt)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt))
    with pytest.raises(UpstreamRemovalError, match=message):
        load_release_receipt(path)


@pytest.mark.parametrize("field", ["tag_name", "name", "digest", "size"])
def test_remote_release_mismatches_fail_closed(field: str) -> None:
    receipt = _receipt()
    release = _release(receipt)
    if field == "tag_name":
        release[field] = "v2026.09.24-1"
    elif field == "name":
        release["assets"][0][field] = "wrong.zip"
    elif field == "digest":
        release["assets"][0][field] = f"sha256:{'b' * 64}"
    else:
        release["assets"][0][field] = 999
    with pytest.raises(UpstreamRemovalError):
        validate_receipt_asset(receipt, release)


def test_enumerates_every_removal_category_and_additive_bump_is_empty() -> None:
    previous = {
        "components": {
            "schemas": {
                "Gone": {"type": "object"},
                "Keep": {
                    "type": "object",
                    "properties": {"gone": {"type": "string"}},
                    "required": ["gone"],
                    "enum": ["a", "b"],
                },
            }
        },
        "paths": {"/gone": {"get": {}}, "/keep": {"get": {}, "post": {}}},
    }
    current = {
        "components": {
            "schemas": {"Keep": {"type": "object", "properties": {}, "required": [], "enum": ["a"]}}
        },
        "paths": {"/keep": {"get": {}}},
    }
    assert {finding.category for finding in find_removals(previous, current)} == {
        "schema",
        "property",
        "path",
        "method",
        "enum-member",
        "required-entry",
    }
    additive = json.loads(json.dumps(previous))
    additive["components"]["schemas"]["Added"] = {"type": "string"}
    additive["paths"]["/added"] = {"get": {}}
    assert find_removals(previous, additive) == []


def test_report_requires_dated_issue_linked_acknowledgement(tmp_path: Path) -> None:
    removal = find_removals(
        {"components": {"schemas": {"Gone": {"type": "string"}}}, "paths": {}},
        {"components": {"schemas": {}}, "paths": {}},
    )[0]
    with pytest.raises(UpstreamRemovalError, match="lack acknowledgement"):
        build_report("v2026.08.18-1", "v2026.08.20-1", [removal], {})
    path = tmp_path / "acks.yaml"
    path.write_text(
        "acknowledgements:\n"
        f"  - fingerprint: {removal.fingerprint}\n"
        "    issue: '#1108'\n"
        "    acknowledged: '2026-08-24'\n"
    )
    acknowledgements = load_acknowledgements(path)
    report = build_report("v2026.08.18-1", "v2026.08.20-1", [removal], acknowledgements)
    assert report["removals"][0]["acknowledgement"]["issue"] == "#1108"


def test_change_report_is_sorted_and_uses_path_plus_method_identities() -> None:
    baseline = {
        "paths": {"/z": {"get": {}}, "/same": {"post": {}, "get": {}}},
        "components": {
            "schemas": {
                "Thing": {
                    "properties": {
                        "removed": {"type": "string"},
                        "modified": {"type": "string", "description": "before"},
                    }
                }
            }
        },
    }
    target = {
        "paths": {"/a": {"patch": {}}, "/same": {"get": {}, "put": {}}},
        "components": {
            "schemas": {
                "Thing": {
                    "properties": {
                        "added": {"type": "integer"},
                        "modified": {"type": "string", "description": "after"},
                    }
                }
            }
        },
    }

    report = build_change_report(_receipt(), _receipt("v2026.09.28-2", "b" * 64), baseline, target)

    assert operation_inventory(baseline) == [
        {"path": "/same", "method": "GET"},
        {"path": "/same", "method": "POST"},
        {"path": "/z", "method": "GET"},
    ]
    assert report["operations"]["additions"] == [
        {"path": "/a", "method": "PATCH"},
        {"path": "/same", "method": "PUT"},
    ]
    assert report["operations"]["removals"] == [
        {"path": "/same", "method": "POST"},
        {"path": "/z", "method": "GET"},
    ]
    assert report["schema_properties"]["added"][0]["value"] == {"type": "integer"}
    assert report["schema_properties"]["removed"][0]["value"] == {"type": "string"}
    assert report["schema_properties"]["modified"] == [
        {
            "schema": "Thing",
            "property": "modified",
            "before": {"type": "string", "description": "before"},
            "after": {"type": "string", "description": "after"},
        }
    ]
    markdown = render_change_markdown(report)
    assert "`POST /same`" in markdown
    assert "1 added, 1 removed, 1 modified" in markdown
    assert "full before/after values" in markdown
