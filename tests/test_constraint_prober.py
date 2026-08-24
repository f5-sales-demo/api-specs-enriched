"""Tests for ConstraintProber orchestrator."""
# pylint: disable=protected-access  # Tests intentionally verify deterministic internal helpers.

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.discovery.constraint_prober import (
    ConstraintProber,
    _load_base_payload,
    _load_published_spec,
    _result_to_dict,
)
from scripts.discovery.probes import FieldProbeResult, ResourceAuditResult

MINIMAL_SPEC = {
    "components": {
        "schemas": {
            "healthcheckCreateSpecType": {
                "type": "object",
                "properties": {
                    "timeout": {"type": "integer"},
                    "interval": {"type": "integer"},
                    "http_health_check": {
                        "$ref": "#/components/schemas/healthcheckHttpHealthCheck"
                    },
                    "tcp_health_check": {"$ref": "#/components/schemas/healthcheckTcpHealthCheck"},
                },
                "x-ves-oneof-field-health_check": [
                    "http_health_check",
                    "tcp_health_check",
                ],
            },
        },
    },
}

BASE_PAYLOAD = {
    "metadata": {"name": "test-hc", "namespace": "default"},
    "spec": {
        "http_health_check": {"path": "/health"},
        "interval": 15,
        "timeout": 3,
        "healthy_threshold": 3,
        "unhealthy_threshold": 1,
    },
}


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_returns_result(self):
        prober = ConstraintProber(
            api_url="https://example.com",
            api_token="fake-token",
            dry_run=True,
        )
        result = await prober.probe_resource("healthcheck", MINIMAL_SPEC, BASE_PAYLOAD)
        assert result.resource_type == "healthcheck"
        assert result.cleanup_status == "skipped"
        assert result.probes_executed > 0

    @pytest.mark.asyncio
    async def test_dry_run_makes_no_api_calls(self):
        prober = ConstraintProber(
            api_url="https://example.com",
            api_token="fake-token",
            dry_run=True,
        )

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await prober.probe_resource("healthcheck", MINIMAL_SPEC, BASE_PAYLOAD)

        assert mock_client.post.call_count == 0

    @pytest.mark.asyncio
    async def test_dry_run_generates_field_results_for_numeric_props(self):
        prober = ConstraintProber(
            api_url="https://example.com",
            api_token="fake-token",
            dry_run=True,
        )
        result = await prober.probe_resource("healthcheck", MINIMAL_SPEC, BASE_PAYLOAD)
        field_paths = [f.field_path for f in result.fields]
        assert "spec.timeout" in field_paths
        assert "spec.interval" in field_paths

    @pytest.mark.asyncio
    async def test_dry_run_generates_oneof_field(self):
        prober = ConstraintProber(
            api_url="https://example.com",
            api_token="fake-token",
            dry_run=True,
        )
        result = await prober.probe_resource("healthcheck", MINIMAL_SPEC, BASE_PAYLOAD)
        field_paths = [f.field_path for f in result.fields]
        assert "spec.health_check" in field_paths

    @pytest.mark.asyncio
    async def test_dry_run_timestamp_is_iso8601(self):
        prober = ConstraintProber(
            api_url="https://example.com",
            api_token="fake-token",
            dry_run=True,
        )
        result = await prober.probe_resource("healthcheck", MINIMAL_SPEC, BASE_PAYLOAD)
        from datetime import datetime

        datetime.fromisoformat(result.timestamp)


class TestLiveProbeResponse:
    @pytest.mark.asyncio
    async def test_parses_constraint_from_400_response(self):
        prober = ConstraintProber(
            api_url="https://example.com",
            api_token="test-token",
            dry_run=False,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.is_success = False
        mock_resp.json.return_value = {"message": "value must be >= 1 and <= 600"}
        mock_resp.text = '{"message": "value must be >= 1 and <= 600"}'

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        from scripts.discovery.probes import ProbeRequest

        probe = ProbeRequest(
            field_path="spec.timeout",
            method="POST",
            payload=BASE_PAYLOAD,
            description="test",
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            response = await prober._execute_probe(mock_client, probe, "healthcheck")

        assert response.status_code == 400
        assert response.accepted is False
        assert response.parsed_constraint == {"minimum": 1, "maximum": 600}


class TestConfigLoading:
    def test_loads_healthcheck_base_payload(self):
        payload = _load_base_payload("healthcheck")
        assert "metadata" in payload
        assert "spec" in payload

    def test_loads_published_spec(self):
        spec = _load_published_spec("healthcheck")
        assert "components" in spec
        assert "schemas" in spec["components"]


class TestResultToDict:
    def test_converts_dataclass_to_dict(self):
        result = ResourceAuditResult(
            resource_type="healthcheck",
            timestamp="2026-01-01T00:00:00Z",
            namespace="default",
        )
        d = _result_to_dict(result)
        assert isinstance(d, dict)
        assert d["resource_type"] == "healthcheck"

    def test_converts_nested_dataclass(self):
        field_result = FieldProbeResult(
            field_path="spec.timeout",
            field_type="number",
            probe_strategy="numeric_boundary",
            expected={},
            actual={"minimum": 1},
            confidence=0.95,
            gap_type=None,
        )
        result = ResourceAuditResult(
            resource_type="healthcheck",
            timestamp="2026-01-01T00:00:00Z",
            namespace="default",
            fields=[field_result],
        )
        d = _result_to_dict(result)
        assert d["fields"][0]["field_path"] == "spec.timeout"
        assert d["fields"][0]["actual"] == {"minimum": 1}
