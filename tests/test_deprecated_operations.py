"""Deprecated HTTP operation normalization contracts."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from scripts import enrich, pipeline
from scripts.utils.deprecated_operations import HTTP_METHODS, remove_deprecated_operations


def _operation(deprecated: object = None, *, include: bool = True) -> dict[str, Any]:
    operation: dict[str, Any] = {"responses": {"200": {"description": "ok"}}}
    if include:
        operation["deprecated"] = deprecated
    return operation


def test_removes_only_strictly_deprecated_http_operations_and_empty_paths() -> None:
    spec: dict[str, Any] = {
        "paths": {
            "/mixed": {
                "parameters": [{"name": "id", "in": "path"}],
                "get": _operation(True),
                "post": _operation(False),
                "x-note": "preserved",
            },
            "/missing": {"get": _operation(include=False)},
            "/truthy": {"put": _operation(1)},
            "/empty": {"parameters": [], "delete": _operation(True)},
        },
        "components": {"schemas": {"Referenced": {"type": "object"}}},
    }

    stats = remove_deprecated_operations(spec)

    assert stats.operations_removed == 2
    assert stats.paths_removed == 1
    assert set(spec["paths"]) == {"/mixed", "/missing", "/truthy"}
    assert "get" not in spec["paths"]["/mixed"]
    assert spec["paths"]["/mixed"]["post"]["deprecated"] is False
    assert spec["components"]["schemas"]["Referenced"] == {"type": "object"}


def test_filters_every_openapi_http_method_and_is_idempotent() -> None:
    spec = {
        "paths": {
            f"/{method}": {method: _operation(True), "parameters": []} for method in HTTP_METHODS
        }
    }

    first = remove_deprecated_operations(spec)
    second = remove_deprecated_operations(spec)

    assert first.operations_removed == len(HTTP_METHODS)
    assert first.paths_removed == len(HTTP_METHODS)
    assert spec["paths"] == {}
    assert second.operations_removed == 0
    assert second.paths_removed == 0


def test_pipeline_and_standalone_entrypoints_report_identical_counts(
    tmp_path: Path, monkeypatch
) -> None:
    document = {
        "openapi": "3.0.3",
        "info": {"title": "test", "version": "1"},
        "paths": {
            "/mixed": {"get": _operation(True), "post": _operation(False)},
            "/gone": {"trace": _operation(True)},
        },
    }
    pipeline_input = json.loads(json.dumps(document))
    _, pipeline_stats = pipeline.enrich_spec(pipeline_input, {})

    source = tmp_path / "source.json"
    output = tmp_path / "output.json"
    source.write_text(json.dumps(document))
    monkeypatch.setattr(enrich, "validate_spec", lambda _spec: (True, None))
    result = enrich.enrich_spec_file(source, output, enrich.DEFAULT_CONFIG)

    assert result.success, result.error
    expected = {"deprecated_operations_removed": 2, "deprecated_paths_removed": 1}
    assert {key: pipeline_stats[key] for key in expected} == expected
    assert {key: result.changes[key] for key in expected} == expected
    published = json.loads(output.read_text())
    assert set(published["paths"]) == {"/mixed"}
    assert set(published["paths"]["/mixed"]) == {"post"}
