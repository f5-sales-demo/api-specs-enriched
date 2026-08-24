"""Tests for consecutive stable upstream removal reporting."""

from __future__ import annotations

import json

import pytest

from scripts.upstream_contract_removals import (
    UpstreamRemovalError,
    build_report,
    find_removals,
    load_acknowledgements,
    select_previous_stable_release,
)


def test_selects_release_immediately_before_pinned_stable() -> None:
    releases = [
        {
            "tag_name": "v2026.08.20-1",
            "published_at": "2026-08-20T00:00:00Z",
            "draft": False,
            "prerelease": False,
        },
        {
            "tag_name": "v2026.08.19-1",
            "published_at": "2026-08-19T00:00:00Z",
            "draft": False,
            "prerelease": True,
        },
        {
            "tag_name": "v2026.08.18-1",
            "published_at": "2026-08-18T00:00:00Z",
            "draft": False,
            "prerelease": False,
        },
    ]
    assert select_previous_stable_release(releases, "v2026.08.20-1")["tag_name"] == "v2026.08.18-1"


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


def test_report_requires_dated_issue_linked_acknowledgement(tmp_path) -> None:
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
