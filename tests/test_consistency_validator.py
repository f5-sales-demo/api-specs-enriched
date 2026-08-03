"""Regression tests for OpenAPI consistency semantics."""

from __future__ import annotations

from scripts.utils.consistency_validator import ConsistencyValidator


def test_path_bindings_and_query_filters_are_independent_parameter_locations() -> None:
    spec = {
        "paths": {
            "/namespaces/{metadata.namespace}/objects/{metadata.name}": {
                "get": {
                    "operationId": "getObject",
                    "parameters": [
                        {"name": "metadata.namespace", "in": "path"},
                        {"name": "metadata.name", "in": "path"},
                    ],
                },
            },
            "/objects": {
                "get": {
                    "operationId": "listObjects",
                    "parameters": [
                        {"name": "namespace", "in": "query"},
                        {"name": "name", "in": "query"},
                    ],
                },
            },
        },
    }

    assert ConsistencyValidator().validate(spec) == []


def test_parameter_validation_still_reports_malformed_path_parameter_names() -> None:
    spec = {
        "paths": {
            "/namespaces/{namespace}": {
                "get": {
                    "operationId": "getNamespace",
                    "parameters": [{"name": "{namespace}", "in": "path"}],
                },
            },
        },
    }

    findings = ConsistencyValidator().validate(spec)

    assert len(findings) == 1
    assert findings[0]["message"] == "Path parameter '{namespace}' contains braces"
