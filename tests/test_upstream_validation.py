"""Tests for Phase 1 upstream validation assertions."""
# pylint: disable=use-implicit-booleaness-not-comparison  # Explicit empty results document the expected shape.

from __future__ import annotations

import pytest

from scripts.pipeline import validate_upstream_spec


@pytest.fixture
def valid_upstream_spec():
    """A spec that passes all upstream validation checks."""
    return {
        "openapi": "3.0.0",
        "info": {
            "title": "Test API",
            "version": "2026.05.20",
            "contact": {"name": "F5, Inc.", "url": "https://www.f5.com"},
        },
        "servers": [{"url": "https://{tenant}.console.ves.volterra.io"}],
        "components": {
            "securitySchemes": {
                "apiKeyAuth": {"type": "apiKey", "in": "header", "name": "Authorization"}
            }
        },
        "security": [{"apiKeyAuth": []}],
        "paths": {
            "/api/test": {
                "get": {
                    "operationId": "GetTest",
                    "tags": ["test"],
                    "description": "Get test resource",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


def test_valid_spec_passes(valid_upstream_spec):
    warnings = validate_upstream_spec(valid_upstream_spec)
    assert warnings == []


def test_missing_contact_warns():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "2026.05.20"},
        "servers": [{"url": "https://example.com"}],
        "components": {"securitySchemes": {"apiKeyAuth": {"type": "apiKey"}}},
        "security": [{"apiKeyAuth": []}],
        "paths": {},
    }
    warnings = validate_upstream_spec(spec)
    assert any("info.contact" in w for w in warnings)


def test_missing_servers_warns():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "2026.05.20", "contact": {"name": "F5"}},
        "components": {"securitySchemes": {"apiKeyAuth": {"type": "apiKey"}}},
        "security": [{"apiKeyAuth": []}],
        "paths": {},
    }
    warnings = validate_upstream_spec(spec)
    assert any("servers" in w for w in warnings)


def test_missing_security_schemes_warns():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "2026.05.20", "contact": {"name": "F5"}},
        "servers": [{"url": "https://example.com"}],
        "paths": {},
    }
    warnings = validate_upstream_spec(spec)
    assert any("securitySchemes" in w for w in warnings)


def test_operation_without_tags_warns():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "2026.05.20", "contact": {"name": "F5"}},
        "servers": [{"url": "https://example.com"}],
        "components": {"securitySchemes": {"apiKeyAuth": {"type": "apiKey"}}},
        "security": [{"apiKeyAuth": []}],
        "paths": {
            "/api/test": {
                "get": {
                    "operationId": "GetTest",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    warnings = validate_upstream_spec(spec)
    assert any("tags" in w for w in warnings)


def test_duplicate_operation_ids_warns():
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "2026.05.20", "contact": {"name": "F5"}},
        "servers": [{"url": "https://example.com"}],
        "components": {"securitySchemes": {"apiKeyAuth": {"type": "apiKey"}}},
        "security": [{"apiKeyAuth": []}],
        "paths": {
            "/api/a": {
                "get": {
                    "operationId": "Duplicate",
                    "tags": ["a"],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api/b": {
                "get": {
                    "operationId": "Duplicate",
                    "tags": ["b"],
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }
    warnings = validate_upstream_spec(spec)
    assert any("operationId" in w for w in warnings)


def test_script_tags_in_description_warns():
    spec = {
        "openapi": "3.0.0",
        "info": {
            "title": "Test",
            "version": "2026.05.20",
            "contact": {"name": "F5"},
            "description": "Has <script>alert(1)</script> in it",
        },
        "servers": [{"url": "https://example.com"}],
        "components": {"securitySchemes": {"apiKeyAuth": {"type": "apiKey"}}},
        "security": [{"apiKeyAuth": []}],
        "paths": {},
    }
    warnings = validate_upstream_spec(spec)
    assert any("script" in w.lower() for w in warnings)
