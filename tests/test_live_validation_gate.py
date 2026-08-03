"""Fail-closed contracts for production live API validation."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from scripts.utils.validation_reporter import SpecValidationResult, ValidationStats
from scripts.validate import (
    DEFAULT_CONFIG,
    LiveValidationConfigurationError,
    _validation_spec_files,
    require_live_credentials,
    validate_all_specs,
    validate_endpoint,
    validation_failures,
)


def _write_openapi(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": path.stem, "version": "1.0.0"},
                "paths": {
                    "/api/items": {
                        "get": {
                            "operationId": f"ves.io.schema.fixture.{path.stem}.API.List",
                            "tags": [path.stem],
                            "responses": {"200": {"description": "Success"}},
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_validation_selects_declared_domains_without_selector_or_aggregate(tmp_path: Path) -> None:
    _write_openapi(tmp_path / "domain.json")
    _write_openapi(tmp_path / "openapi.json")
    (tmp_path / "validation.json").write_text("{}", encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "timestamp": "2026-08-02T00:00:00Z",
                "specifications": [{"file": "domain.json"}],
            }
        ),
        encoding="utf-8",
    )

    assert _validation_spec_files(tmp_path) == [tmp_path / "domain.json"]


def _referenced_response_document() -> tuple[dict, dict]:
    document = {
        "openapi": "3.0.3",
        "components": {
            "responses": {
                "ItemList": {
                    "description": "success",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ItemList"}}
                    },
                }
            },
            "schemas": {
                "ItemList": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Item"},
                        }
                    },
                },
                "Item": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                },
            },
        },
    }
    endpoint = {
        "path": "/items",
        "method": "GET",
        "parameters": [],
        "responses": {"200": {"$ref": "#/components/responses/ItemList"}},
    }
    return document, endpoint


async def _validate(
    response: httpx.Response,
    document: dict,
    endpoint: dict,
    config: dict | None = None,
):
    client = AsyncMock()
    client.request = AsyncMock(return_value=response)
    return await validate_endpoint(
        client,
        "https://example.invalid",
        endpoint,
        config or DEFAULT_CONFIG,
        asyncio.Semaphore(1),
        resolved_path=endpoint["path"],
        spec_document=document,
    )


@pytest.mark.asyncio
async def test_live_response_body_matches_nested_local_schema_references() -> None:
    document, endpoint = _referenced_response_document()

    result = await _validate(
        httpx.Response(200, json={"items": [{"name": "valid"}]}),
        document,
        endpoint,
    )

    assert result.status == "available"
    assert result.schema_match is True
    assert result.discrepancies == []


@pytest.mark.asyncio
async def test_live_response_schema_mismatch_records_no_response_value() -> None:
    document, endpoint = _referenced_response_document()

    result = await _validate(
        httpx.Response(200, json={"items": [{"name": 42}]}),
        document,
        endpoint,
    )

    assert result.status == "available"
    assert result.schema_match is False
    assert result.discrepancies == ["Response schema mismatch at $.items[0].name (type)"]
    assert "42" not in str(result.discrepancies)


@pytest.mark.asyncio
async def test_undeclared_content_type_is_a_schema_mismatch() -> None:
    document, endpoint = _referenced_response_document()

    result = await _validate(
        httpx.Response(200, text="plain text", headers={"content-type": "text/plain"}),
        document,
        endpoint,
    )

    assert result.schema_match is False
    assert result.discrepancies == ["Response Content-Type is not declared by the operation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_authentication_failures_are_unavailable(status_code: int) -> None:
    endpoint = {
        "path": "/items",
        "method": "GET",
        "parameters": [],
        "responses": {str(status_code): {"description": "authentication failure"}},
    }

    result = await _validate(httpx.Response(status_code), {}, endpoint)

    assert result.status == "unavailable"


@pytest.mark.asyncio
async def test_authentication_failures_cannot_be_configured_as_success() -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["response"] = {"success_codes": [200, 401, 403]}
    endpoint = {
        "path": "/items",
        "method": "GET",
        "parameters": [],
        "responses": {"401": {"description": "authentication failure"}},
    }

    result = await _validate(httpx.Response(401), {}, endpoint, config)

    assert result.status == "unavailable"


@pytest.mark.asyncio
async def test_response_schema_validation_cannot_be_disabled() -> None:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["response"] = {"validate_schema": False}
    endpoint = {
        "path": "/items",
        "method": "GET",
        "parameters": [],
        "responses": {
            "200": {
                "description": "success",
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "required": ["required_field"]}
                    }
                },
            }
        },
    }

    result = await _validate(httpx.Response(200, json={}), {}, endpoint, config)

    assert result.schema_match is False
    assert result.discrepancies == ["Response schema mismatch at $ (required)"]


@pytest.mark.asyncio
async def test_standard_response_formats_are_validated() -> None:
    endpoint = {
        "path": "/items",
        "method": "GET",
        "parameters": [],
        "responses": {
            "200": {
                "description": "success",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["created_at"],
                            "properties": {"created_at": {"type": "string", "format": "date-time"}},
                        }
                    }
                },
            }
        },
    }

    result = await _validate(
        httpx.Response(200, json={"created_at": "not-a-date"}),
        {},
        endpoint,
    )

    assert result.schema_match is False
    assert result.discrepancies == ["Response schema mismatch at $.created_at (format)"]


@pytest.mark.asyncio
async def test_production_client_does_not_follow_redirects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    metadata = json.dumps({"openapi": "3.0.3", "paths": {}}).encode()
    (tmp_path / "metadata.json").write_bytes(metadata)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "release_receipt": {
                    "version": "2026.08.02-1",
                    "tag_name": "v2026.08.02-1",
                    "published_at": "2026-08-02T00:00:00Z",
                    "asset_name": "api-specs-v2026.08.02-1.zip",
                    "asset_size": 1,
                    "asset_digest": f"sha256:{'a' * 64}",
                },
                "files": [
                    {
                        "name": "metadata.json",
                        "sha256": f"sha256:{hashlib.sha256(metadata).hexdigest()}",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("F5XC_API_URL", "https://example.invalid")
    monkeypatch.setenv("F5XC_API_TOKEN", "not-a-real-token")
    monkeypatch.setattr("scripts.validate.httpx.AsyncClient", FakeAsyncClient)

    await validate_all_specs(tmp_path, DEFAULT_CONFIG)

    assert captured["follow_redirects"] is False


def test_live_credentials_require_both_url_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("F5XC_API_URL", raising=False)
    monkeypatch.delenv("F5XC_API_TOKEN", raising=False)

    with pytest.raises(LiveValidationConfigurationError) as error:
        require_live_credentials(DEFAULT_CONFIG)

    assert "F5XC_API_URL" in str(error.value)
    assert "F5XC_API_TOKEN" in str(error.value)


def test_live_credentials_reject_plaintext_token_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("F5XC_API_URL", "http://example.invalid")
    monkeypatch.setenv("F5XC_API_TOKEN", "not-a-real-token")

    with pytest.raises(LiveValidationConfigurationError, match="HTTPS"):
        require_live_credentials(DEFAULT_CONFIG)


@pytest.mark.parametrize(
    "api_url",
    [
        "https://:443",
        "https://user:secret@example.invalid",
        "https://example.invalid?query=1",
        "https://example.invalid#fragment",
        "https://example.invalid:0",
        "https://example.invalid:99999",
        "https://example.invalid:notaport",
        "https://[invalid",
    ],
)
def test_live_credentials_reject_malformed_or_credential_bearing_urls(
    monkeypatch: pytest.MonkeyPatch, api_url: str
) -> None:
    monkeypatch.setenv("F5XC_API_URL", api_url)
    monkeypatch.setenv("F5XC_API_TOKEN", "not-a-real-token")

    with pytest.raises(LiveValidationConfigurationError, match="credential-free HTTPS URL"):
        require_live_credentials(DEFAULT_CONFIG)


@pytest.mark.asyncio
async def test_unresolved_local_response_schema_is_a_validation_error() -> None:
    document, endpoint = _referenced_response_document()
    document["components"]["responses"]["ItemList"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/Missing"
    }

    result = await _validate(httpx.Response(200, json={"items": []}), document, endpoint)

    assert result.status == "error"
    assert result.error is not None
    assert "response schema evaluation failed" in result.error


def test_gate_reports_every_threshold_and_validation_error() -> None:
    stats = ValidationStats(
        endpoints_validated=10,
        endpoints_available=7,
        schema_matches=4,
        spec_results=[SpecValidationResult(filename="fixture.json", errors=["validator failed"])],
        discrepancies=[{"issue": 1}, {"issue": 2}],
    )
    config = {
        "thresholds": {
            "min_availability": 80,
            "min_schema_match": 70,
            "max_discrepancies": 1,
        }
    }

    failures = validation_failures(stats, config)

    assert len(failures) == 4
    assert any("validation error" in failure for failure in failures)
    assert any("availability" in failure for failure in failures)
    assert any("schema match" in failure for failure in failures)
    assert any("discrepancies" in failure for failure in failures)


def test_gate_rejects_zero_validated_endpoints() -> None:
    failures = validation_failures(ValidationStats(), DEFAULT_CONFIG)

    assert "zero endpoints were validated" in failures


def test_gate_rejects_resolution_and_execution_accounting_mismatches() -> None:
    stats = ValidationStats(
        endpoints_eligible=3,
        endpoints_safely_resolved=2,
        endpoints_unresolved=0,
        endpoints_executed=1,
        endpoints_validated=1,
        endpoints_available=1,
        schema_matches=1,
    )

    failures = validation_failures(stats, DEFAULT_CONFIG)

    assert failures == [
        "endpoint resolution invariant failed: eligible=3, safely_resolved=2, unresolved=0",
        "endpoint execution invariant failed: executed=1, safely_resolved=2",
    ]


def test_production_entrypoints_make_real_authenticated_requests() -> None:
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/sync-and-enrich.yml").read_text()
    makefile = (root / "Makefile").read_text()

    assert "python -m scripts.validate --dry-run" not in workflow
    assert "python -m scripts.validate 2>&1" in workflow
    assert "status=${PIPESTATUS[0]}" in workflow
    assert "$(PYTHON) -m scripts.validate --dry-run" not in makefile
    assert "F5XC_API_TOKEN is required" in makefile
    assert "F5XC_API_URL is required" in makefile
