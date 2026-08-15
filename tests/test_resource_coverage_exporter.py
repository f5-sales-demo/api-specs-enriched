# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for the versioned resource coverage contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.utils.namespace_profiles_exporter import NamespaceProfilesExporter
from scripts.utils.resource_coverage_exporter import (
    ResourceCoverageError,
    ResourceCoverageExporter,
    discover_canonical_resources,
)


def operation(operation_id: str) -> dict[str, str]:
    return {"operationId": operation_id}


def spec(paths: dict[str, Any]) -> dict[str, Any]:
    return {"openapi": "3.0.3", "info": {"version": "1.2.3"}, "paths": paths}


def profile() -> dict[str, Any]:
    return {
        "constraint": {"allowed": ["custom"], "enforced": False},
        "recommendation": {"primary": "custom", "rationale": "test"},
        "classification": {"category": "application", "multi_tenant_pattern": "per-tenant"},
    }


def test_discovers_standard_qualified_and_views_create_identities() -> None:
    document = spec(
        {
            "/api/config/namespaces/{metadata.namespace}/widgets": {
                "post": operation("ves.io.schema.widget.API.Create")
            },
            "/api/data/namespaces/{metadata.namespace}/receivers": {
                "post": operation("ves.io.schema.shape.data_delivery.receiver.API.Create")
            },
            "/api/config/namespaces/{metadata.namespace}/applications": {
                "post": operation("ves.io.schema.views.application.API.Create")
            },
        }
    )

    candidates = discover_canonical_resources([document])

    assert list(candidates) == ["application", "receiver", "widget"]
    assert candidates["receiver"].operation_id == (
        "ves.io.schema.shape.data_delivery.receiver.API.Create"
    )
    assert candidates["application"].path.endswith("/applications")


def test_ignores_actions_aggregates_metadata_and_nested_create_routes() -> None:
    document = spec(
        {
            "/api/config/namespaces/{namespace}/widgets/{name}/activate": {
                "post": operation("ves.io.schema.widget.API.Create")
            },
            "/api/data/namespaces/{namespace}/widgets/aggregation": {
                "post": operation("ves.io.schema.widget.CustomAPI.Aggregate")
            },
            "/api/config/namespaces/{namespace}/widgets/{name}/rotate": {
                "post": operation("ves.io.schema.widget.CustomAPI.CreateToken")
            },
            "/metadata/resources": {"post": operation("ves.io.schema.metadata.API.Create")},
        }
    )

    assert discover_canonical_resources([document]) == {}


def test_build_assigns_one_disposition_to_every_profile(tmp_path: Path) -> None:
    manual_config = tmp_path / "resource_coverage.yaml"
    manual_config.write_text(
        """\
version: 1
manual:
  report:
    path: /api/data/namespaces/{namespace}/reports
"""
    )
    document = spec(
        {
            "/api/config/namespaces/{metadata.namespace}/widgets": {
                "post": operation("ves.io.schema.views.widget.API.Create")
            },
            "/api/data/namespaces/{namespace}/reports": {
                "get": operation("ves.io.schema.report.API.List")
            },
        }
    )
    profiles = {"resources": {key: profile() for key in ("widget", "report", "legacy")}}

    artifact = ResourceCoverageExporter(config_path=manual_config).build(
        [document], profiles, version="1.2.3"
    )

    assert artifact["version"] == "1.2.3"
    assert list(artifact["resources"]) == ["legacy", "report", "widget"]
    assert artifact["resources"]["widget"] == {
        "disposition": "generated",
        "path": "/api/config/namespaces/{metadata.namespace}/widgets",
        "operation_id": "ves.io.schema.views.widget.API.Create",
    }
    assert artifact["resources"]["report"] == {
        "disposition": "manual",
        "path": "/api/data/namespaces/{namespace}/reports",
    }
    assert artifact["resources"]["legacy"] == {
        "disposition": "excluded",
        "reason": "no_canonical_create",
    }
    assert artifact["coverage"] == {"excluded": 1, "generated": 1, "manual": 1, "total": 3}


def test_build_is_deterministic(tmp_path: Path) -> None:
    config = tmp_path / "resource_coverage.yaml"
    config.write_text("version: 1\nmanual: {}\n")
    document = spec(
        {
            "/api/config/namespaces/{metadata.namespace}/zebras": {
                "post": operation("ves.io.schema.zebra.API.Create")
            },
            "/api/config/namespaces/{metadata.namespace}/apples": {
                "post": operation("ves.io.schema.apple.API.Create")
            },
        }
    )
    profiles = {"resources": {"zebra": profile(), "apple": profile()}}
    exporter = ResourceCoverageExporter(config_path=config)

    first = exporter.build([document], profiles, version="1.2.3")
    second = exporter.build([document], profiles, version="1.2.3")

    assert json.dumps(first, sort_keys=False) == json.dumps(second, sort_keys=False)
    assert list(first["resources"]) == ["apple", "zebra"]


def test_rejects_candidate_without_explicit_profile(tmp_path: Path) -> None:
    config = tmp_path / "resource_coverage.yaml"
    config.write_text("version: 1\nmanual: {}\n")
    document = spec(
        {
            "/api/config/namespaces/{metadata.namespace}/widgets": {
                "post": operation("ves.io.schema.widget.API.Create")
            }
        }
    )

    with pytest.raises(ResourceCoverageError, match=r"lack explicit namespace profiles.*widget"):
        ResourceCoverageExporter(config_path=config).build(
            [document], {"resources": {}}, version="1.2.3"
        )


@pytest.mark.parametrize(
    ("config_text", "message"),
    [
        ("version: 2\nmanual: {}\n", "unsupported contract config version"),
        ("version: 1\nmanual: []\n", "manual must be an object"),
        (
            "version: 1\nmanual: {}\nexclusions:\n  legacy: unsupported_guess\n",
            "invalid exclusion reason",
        ),
    ],
)
def test_rejects_malformed_contract_config(tmp_path: Path, config_text: str, message: str) -> None:
    config = tmp_path / "resource_coverage.yaml"
    config.write_text(config_text)

    with pytest.raises(ResourceCoverageError, match=message):
        ResourceCoverageExporter(config_path=config)


def test_rejects_stale_manual_path(tmp_path: Path) -> None:
    config = tmp_path / "resource_coverage.yaml"
    config.write_text(
        """\
version: 1
manual:
  report:
    path: /api/data/namespaces/{namespace}/old_reports
"""
    )
    document = spec(
        {
            "/api/data/namespaces/{namespace}/reports": {
                "get": operation("ves.io.schema.report.API.List")
            }
        }
    )
    profiles = {"resources": {"report": profile()}}

    with pytest.raises(ResourceCoverageError, match=r"manual path.*does not exist"):
        ResourceCoverageExporter(config_path=config).build([document], profiles, version="1.2.3")


def test_real_corpus_has_complete_profile_and_coverage_contract() -> None:
    specs_dir = Path("docs/specifications/api")
    domain_paths = sorted(
        path
        for path in specs_dir.glob("*.json")
        if path.name
        not in {
            "index.json",
            "minimal-export-defaults.json",
            "namespace_profiles.json",
            "resource_coverage.json",
            "validation.json",
        }
    )
    if not domain_paths:
        pytest.skip("specs not available")

    documents = [json.loads(path.read_text()) for path in domain_paths]
    profiles = NamespaceProfilesExporter().build(version="test-corpus")
    artifact = ResourceCoverageExporter().build(documents, profiles, version="test-corpus")

    assert set(artifact["resources"]) == set(profiles["resources"])
    assert artifact["coverage"]["generated"] > 0
    assert artifact["coverage"]["manual"] > 0
    assert artifact["coverage"]["excluded"] > 0
