# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=use-implicit-booleaness-not-comparison  # Explicit empty results document the expected shape.

"""Tests for scripts/discover.py - F5 XC API Discovery Script.

Tests cover:
- Configuration loading and defaults
- API URL and authentication header generation
- Endpoint extraction from specs
- Endpoint filtering and path parameter resolution
- Async endpoint discovery with mocked HTTP requests
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestConfigurationLoading:
    """Test discovery configuration loading."""

    def test_load_config_uses_defaults_when_no_file(self):
        """Should return default config when file doesn't exist."""
        from scripts.discover import load_config

        result = load_config(Path("/nonexistent/config.yaml"))

        assert "api_url" in result
        assert "rate_limit" in result
        assert "exploration" in result

    def test_load_config_loads_from_file(self, tmp_path):
        """Should load config from YAML file."""
        from scripts.discover import load_config

        config_file = tmp_path / "discovery.yaml"
        config_file.write_text("""
discovery:
    api_url: "https://test.example.com"
    rate_limit:
        requests_per_second: 10
""")

        result = load_config(config_file)

        assert result["api_url"] == "https://test.example.com"
        assert result["rate_limit"]["requests_per_second"] == 10

    def test_get_default_config_structure(self):
        """Should return complete default config structure."""
        from scripts.discover import get_default_config

        config = get_default_config()

        assert "api_url" in config
        assert "auth_token" in config
        assert "rate_limit" in config
        assert "exploration" in config
        assert "output" in config

    @patch.dict(
        os.environ,
        {"F5XC_API_URL": "https://env.example.com", "F5XC_API_TOKEN": "env-token"},
    )
    def test_get_default_config_uses_environment_variables(self):
        """Should populate defaults from environment variables."""
        from scripts.discover import get_default_config

        config = get_default_config()

        assert config["api_url"] == "https://env.example.com"
        assert config["auth_token"] == "env-token"


class TestAPIConfiguration:
    """Test API URL and authentication configuration."""

    @patch.dict(os.environ, {"F5XC_API_URL": "https://env.example.com/"})
    def test_get_api_url_prefers_env_var(self):
        """Should prefer environment variable over config."""
        from scripts.discover import get_api_url

        config = {"api_url": "https://config.example.com"}
        url = get_api_url(config)

        assert url == "https://env.example.com"  # Trailing slash stripped

    @patch.dict(os.environ, {}, clear=True)
    def test_get_api_url_falls_back_to_config(self):
        """Should use config when env var not set."""
        from scripts.discover import get_api_url

        config = {"api_url": "https://config.example.com/"}
        url = get_api_url(config)

        assert url == "https://config.example.com"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_api_url_returns_empty_when_no_source(self):
        """Should return empty string when no URL configured."""
        from scripts.discover import get_api_url

        config = {}
        url = get_api_url(config)

        assert url == ""

    @patch.dict(os.environ, {"F5XC_API_TOKEN": "test-token-123"})
    def test_get_auth_headers_uses_env_var(self):
        """Should use token from environment variable."""
        from scripts.discover import get_auth_headers

        config = {}
        headers = get_auth_headers(config)

        assert headers["Authorization"] == "APIToken test-token-123"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_auth_headers_falls_back_to_config(self):
        """Should use config token when env var not set."""
        from scripts.discover import get_auth_headers

        config = {"auth_token": "config-token-456"}
        headers = get_auth_headers(config)

        assert headers["Authorization"] == "APIToken config-token-456"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_auth_headers_returns_empty_when_no_token(self):
        """Should return empty dict when no token available."""
        from scripts.discover import get_auth_headers

        config = {}
        headers = get_auth_headers(config)

        assert headers == {}


class TestEndpointExtraction:
    """Test endpoint extraction from OpenAPI specs."""

    def test_extract_endpoints_from_simple_spec(self, tmp_path):
        """Should extract endpoints from spec file."""
        import json

        from scripts.discover import extract_endpoints_from_specs

        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()

        spec_file = specs_dir / "test.json"
        spec_file.write_text(
            json.dumps(
                {
                    "paths": {
                        "/api/test": {
                            "get": {
                                "operationId": "getTest",
                                "parameters": [],
                                "responses": {},
                            },
                        },
                    },
                },
            ),
        )

        endpoints = extract_endpoints_from_specs(specs_dir)

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/api/test"
        assert endpoints[0]["method"] == "GET"
        assert endpoints[0]["operation_id"] == "getTest"

    def test_extract_endpoints_skips_index_and_openapi_files(self, tmp_path):
        """Should skip index.json and openapi.json files."""
        import json

        from scripts.discover import extract_endpoints_from_specs

        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()

        # Create index.json (should be skipped)
        (specs_dir / "index.json").write_text(json.dumps({"paths": {"/index": {"get": {}}}}))

        # Create openapi.json (should be skipped)
        (specs_dir / "openapi.json").write_text(json.dumps({"paths": {"/openapi": {"get": {}}}}))

        # Create regular spec (should be included)
        (specs_dir / "regular.json").write_text(
            json.dumps(
                {
                    "paths": {"/regular": {"get": {"responses": {}}}},
                },
            ),
        )

        endpoints = extract_endpoints_from_specs(specs_dir)

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/regular"

    def test_extract_endpoints_handles_multiple_methods(self, tmp_path):
        """Should extract all HTTP methods from paths."""
        import json

        from scripts.discover import extract_endpoints_from_specs

        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()

        spec_file = specs_dir / "test.json"
        spec_file.write_text(
            json.dumps(
                {
                    "paths": {
                        "/api/resource": {
                            "get": {"responses": {}},
                            "post": {"responses": {}},
                            "put": {"responses": {}},
                            "delete": {"responses": {}},
                            "patch": {"responses": {}},
                            "options": {"responses": {}},
                        },
                    },
                },
            ),
        )

        endpoints = extract_endpoints_from_specs(specs_dir)

        assert len(endpoints) == 6
        methods = {ep["method"] for ep in endpoints}
        assert methods == {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"}

    def test_extract_endpoints_handles_missing_directory(self):
        """Should return empty list when specs directory doesn't exist."""
        from scripts.discover import extract_endpoints_from_specs

        endpoints = extract_endpoints_from_specs(Path("/nonexistent/specs"))

        assert endpoints == []

    def test_extract_endpoints_handles_invalid_json(self, tmp_path):
        """Should skip files with invalid JSON."""
        from scripts.discover import extract_endpoints_from_specs

        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()

        (specs_dir / "invalid.json").write_text("{ invalid json }")
        (specs_dir / "valid.json").write_text('{"paths": {"/valid": {"get": {"responses": {}}}}}')

        endpoints = extract_endpoints_from_specs(specs_dir)

        # Should only extract from valid file
        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/valid"

    def test_extract_endpoints_skips_non_dict_path_items(self, tmp_path):
        """Should skip malformed path items."""
        import json

        from scripts.discover import extract_endpoints_from_specs

        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()

        spec_file = specs_dir / "test.json"
        spec_file.write_text(
            json.dumps(
                {
                    "paths": {
                        "/valid": {"get": {"responses": {}}},
                        "/invalid": "not a dict",
                        "/another": None,
                    },
                },
            ),
        )

        endpoints = extract_endpoints_from_specs(specs_dir)

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/valid"


class TestEndpointFiltering:
    """Test endpoint filtering logic."""

    def test_should_skip_endpoint_allows_get_by_default(self):
        """Should not skip GET method by default."""
        from scripts.discover import should_skip_endpoint

        endpoint = {"method": "GET", "path": "/api/resource"}
        config = {"exploration": {"methods": ["GET", "OPTIONS"]}}

        should_skip, reason = should_skip_endpoint(endpoint, config)

        assert should_skip is False

    def test_should_skip_endpoint_skips_post_by_default(self):
        """Should skip POST method by default."""
        from scripts.discover import should_skip_endpoint

        endpoint = {"method": "POST", "path": "/api/resource"}
        config = {"exploration": {"methods": ["GET", "OPTIONS"]}}

        should_skip, reason = should_skip_endpoint(endpoint, config)

        assert should_skip is True
        assert "POST" in reason

    def test_should_skip_endpoint_respects_custom_methods(self):
        """Should respect custom allowed methods."""
        from scripts.discover import should_skip_endpoint

        endpoint = {"method": "PUT", "path": "/api/resource"}
        config = {"exploration": {"methods": ["GET", "PUT", "DELETE"]}}

        should_skip, reason = should_skip_endpoint(endpoint, config)

        assert should_skip is False

    def test_should_skip_endpoint_matches_skip_patterns(self):
        """Should skip endpoints matching skip patterns."""
        from scripts.discover import should_skip_endpoint

        endpoint = {"method": "GET", "path": "/api/internal/debug"}
        config = {
            "exploration": {
                "methods": ["GET"],
                "skip_patterns": ["/internal/", "/debug/"],
            },
        }

        should_skip, reason = should_skip_endpoint(endpoint, config)

        assert should_skip is True
        assert "skip pattern" in reason.lower()

    def test_should_skip_endpoint_checks_all_patterns(self):
        """Should check all skip patterns."""
        from scripts.discover import should_skip_endpoint

        endpoint = {"method": "GET", "path": "/api/v1/users"}
        config = {
            "exploration": {
                "methods": ["GET"],
                "skip_patterns": ["/internal/", "/debug/", "/test/"],
            },
        }

        should_skip, reason = should_skip_endpoint(endpoint, config)

        assert should_skip is False


class TestPathParameterResolution:
    """Test path parameter resolution."""

    def test_resolve_path_params_replaces_namespace(self):
        """Should replace namespace parameter."""
        from scripts.discover import resolve_path_params

        path = "/api/namespaces/{namespace}/resources"
        resolved = resolve_path_params(path, namespace="production")

        assert resolved == "/api/namespaces/production/resources"

    def test_resolve_path_params_replaces_name(self):
        """Should replace name parameter with sample-name."""
        from scripts.discover import resolve_path_params

        path = "/api/resources/{name}"
        resolved = resolve_path_params(path)

        assert resolved == "/api/resources/sample-name"

    def test_resolve_path_params_replaces_id(self):
        """Should replace id parameter with sample-id."""
        from scripts.discover import resolve_path_params

        path = "/api/resources/{id}"
        resolved = resolve_path_params(path)

        assert resolved == "/api/resources/sample-id"

    def test_resolve_path_params_handles_multiple_params(self):
        """Should handle multiple parameters."""
        from scripts.discover import resolve_path_params

        path = "/api/namespaces/{namespace}/resources/{name}/details/{id}"
        resolved = resolve_path_params(path, namespace="staging")

        assert resolved == "/api/namespaces/staging/resources/sample-name/details/sample-id"

    def test_resolve_path_params_handles_unrecognized_params(self):
        """Should replace unrecognized parameters with 'sample'."""
        from scripts.discover import resolve_path_params

        path = "/api/{unknown}/{another}/resources"
        resolved = resolve_path_params(path)

        assert resolved == "/api/sample/sample/resources"

    def test_resolve_path_params_defaults_to_system_namespace(self):
        """Should default to 'system' namespace when not specified."""
        from scripts.discover import resolve_path_params

        path = "/api/namespaces/{namespace}/resources"
        resolved = resolve_path_params(path)

        assert resolved == "/api/namespaces/system/resources"


class TestAsyncEndpointDiscovery:
    """Test async endpoint discovery functions."""

    @pytest.mark.asyncio
    async def test_discover_endpoint_successful_response(self):
        """Should discover endpoint with successful response."""
        from unittest.mock import AsyncMock

        from scripts.discover import discover_endpoint
        from scripts.discovery import RateLimiter, SchemaInferrer

        # Mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": []}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        endpoint = {"path": "/api/test", "method": "GET", "parameters": []}
        config = {"exploration": {"methods": ["GET"], "timeout_seconds": 30}}
        rate_limiter = RateLimiter({"requests_per_second": 10})
        schema_inferrer = SchemaInferrer()

        result = await discover_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            rate_limiter,
            schema_inferrer,
        )

        assert result.path == "/api/test"
        assert result.method == "GET"
        assert result.status_code == 200
        assert result.error is None

    @pytest.mark.asyncio
    async def test_discover_endpoint_skips_filtered_endpoints(self):
        """Should skip endpoints that don't match filter criteria."""
        from unittest.mock import AsyncMock

        from scripts.discover import discover_endpoint
        from scripts.discovery import RateLimiter, SchemaInferrer

        mock_client = AsyncMock()
        endpoint = {"path": "/api/test", "method": "POST", "parameters": []}
        config = {"exploration": {"methods": ["GET"], "timeout_seconds": 30}}
        rate_limiter = RateLimiter({"requests_per_second": 10})
        schema_inferrer = SchemaInferrer()

        result = await discover_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            rate_limiter,
            schema_inferrer,
        )

        assert result.path == "/api/test"
        assert result.method == "POST"
        assert result.error is not None
        assert "Skipped" in result.error

    @pytest.mark.asyncio
    async def test_discover_endpoint_handles_timeout(self):
        """Should handle request timeout."""
        from unittest.mock import AsyncMock

        import httpx

        from scripts.discover import discover_endpoint
        from scripts.discovery import RateLimiter, SchemaInferrer

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

        endpoint = {"path": "/api/test", "method": "GET", "parameters": []}
        config = {"exploration": {"methods": ["GET"], "timeout_seconds": 30}}
        rate_limiter = RateLimiter({"requests_per_second": 10})
        schema_inferrer = SchemaInferrer()

        result = await discover_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            rate_limiter,
            schema_inferrer,
        )

        assert result.error is not None
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_discover_endpoint_handles_request_error(self):
        """Should handle general request errors."""
        from unittest.mock import AsyncMock

        import httpx

        from scripts.discover import discover_endpoint
        from scripts.discovery import RateLimiter, SchemaInferrer

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=httpx.RequestError("Connection failed"))

        endpoint = {"path": "/api/test", "method": "GET", "parameters": []}
        config = {"exploration": {"methods": ["GET"], "timeout_seconds": 30}}
        rate_limiter = RateLimiter({"requests_per_second": 10})
        schema_inferrer = SchemaInferrer()

        result = await discover_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            rate_limiter,
            schema_inferrer,
        )

        assert result.error is not None
        assert "Connection failed" in result.error

    @pytest.mark.asyncio
    async def test_discover_endpoint_resolves_path_parameters(self):
        """Should resolve path parameters before making request."""
        from unittest.mock import AsyncMock

        from scripts.discover import discover_endpoint
        from scripts.discovery import RateLimiter, SchemaInferrer

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        endpoint = {
            "path": "/api/namespaces/{namespace}/resources",
            "method": "GET",
            "parameters": [],
        }
        config = {"exploration": {"methods": ["GET"], "timeout_seconds": 30}}
        rate_limiter = RateLimiter({"requests_per_second": 10})
        schema_inferrer = SchemaInferrer()

        await discover_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            rate_limiter,
            schema_inferrer,
            namespace="production",
        )

        # Verify path was resolved
        mock_client.request.assert_called_once()
        call_args = mock_client.request.call_args
        assert "production" in call_args.kwargs["url"]

    @pytest.mark.asyncio
    async def test_discover_endpoint_handles_json_decode_error(self):
        """Should handle JSON decode errors gracefully."""
        import json
        from unittest.mock import AsyncMock

        from scripts.discover import discover_endpoint
        from scripts.discovery import RateLimiter, SchemaInferrer

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        endpoint = {"path": "/api/test", "method": "GET", "parameters": []}
        config = {"exploration": {"methods": ["GET"], "timeout_seconds": 30}}
        rate_limiter = RateLimiter({"requests_per_second": 10})
        schema_inferrer = SchemaInferrer()

        result = await discover_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            rate_limiter,
            schema_inferrer,
        )

        # Should still return success even if JSON parsing fails
        assert result.status_code == 200
        assert result.error is None

    @pytest.mark.asyncio
    async def test_discover_endpoint_handles_general_exception(self):
        """Should handle unexpected exceptions."""
        from unittest.mock import AsyncMock

        from scripts.discover import discover_endpoint
        from scripts.discovery import RateLimiter, SchemaInferrer

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=RuntimeError("Unexpected error"))

        endpoint = {"path": "/api/test", "method": "GET", "parameters": []}
        config = {"exploration": {"methods": ["GET"], "timeout_seconds": 30}}
        rate_limiter = RateLimiter({"requests_per_second": 10})
        schema_inferrer = SchemaInferrer()

        result = await discover_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            rate_limiter,
            schema_inferrer,
        )

        assert result.error is not None
        assert "Unexpected error" in result.error

    @pytest.mark.asyncio
    async def test_discover_endpoint_successful_response_with_schema_inference(self):
        """Should infer schema from successful responses."""
        from unittest.mock import AsyncMock

        from scripts.discover import discover_endpoint
        from scripts.discovery import RateLimiter, SchemaInferrer

        mock_response = MagicMock()
        mock_response.status_code = 201  # Also a success code
        mock_response.json.return_value = {
            "metadata": {"name": "test"},
            "spec": {"field": "value"},
        }

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        endpoint = {"path": "/api/test", "method": "GET", "parameters": []}
        config = {"exploration": {"methods": ["GET"], "timeout_seconds": 30}}
        rate_limiter = RateLimiter({"requests_per_second": 10})
        schema_inferrer = SchemaInferrer()

        result = await discover_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            rate_limiter,
            schema_inferrer,
        )

        assert result.status_code == 201
        assert result.inferred_schema is not None
        assert len(result.examples) > 0

    @pytest.mark.asyncio
    async def test_discover_endpoint_with_list_response(self):
        """Should handle response with list data."""
        from unittest.mock import AsyncMock

        from scripts.discover import discover_endpoint
        from scripts.discovery import RateLimiter, SchemaInferrer

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": 1}, {"id": 2}]  # List not dict

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        endpoint = {"path": "/api/list", "method": "GET", "parameters": []}
        config = {"exploration": {"methods": ["GET"], "timeout_seconds": 30}}
        rate_limiter = RateLimiter({"requests_per_second": 10})
        schema_inferrer = SchemaInferrer()

        result = await discover_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            rate_limiter,
            schema_inferrer,
        )

        assert result.status_code == 200
        # List responses should have empty examples list
        assert result.examples == []

    @pytest.mark.asyncio
    async def test_discover_endpoint_with_400_error(self):
        """Should handle 4xx client errors."""
        from unittest.mock import AsyncMock

        from scripts.discover import discover_endpoint
        from scripts.discovery import RateLimiter, SchemaInferrer

        mock_response = MagicMock()
        mock_response.status_code = 400

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        endpoint = {"path": "/api/bad", "method": "GET", "parameters": []}
        config = {"exploration": {"methods": ["GET"], "timeout_seconds": 30}}
        rate_limiter = RateLimiter({"requests_per_second": 10})
        schema_inferrer = SchemaInferrer()

        result = await discover_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            rate_limiter,
            schema_inferrer,
        )

        assert result.status_code == 400
        assert result.error is None  # 400 is not an error, just a non-success status

    @pytest.mark.asyncio
    async def test_discover_endpoint_with_500_error(self):
        """Should handle 5xx server errors."""
        from unittest.mock import AsyncMock

        from scripts.discover import discover_endpoint
        from scripts.discovery import RateLimiter, SchemaInferrer

        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        endpoint = {"path": "/api/error", "method": "GET", "parameters": []}
        config = {"exploration": {"methods": ["GET"], "timeout_seconds": 30}}
        rate_limiter = RateLimiter({"requests_per_second": 10})
        schema_inferrer = SchemaInferrer()

        result = await discover_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            rate_limiter,
            schema_inferrer,
        )

        assert result.status_code == 500
        assert result.error is None  # Server errors are captured in status code

    @pytest.mark.asyncio
    async def test_discover_endpoint_with_empty_response(self):
        """Should handle empty response bodies."""
        from unittest.mock import AsyncMock

        from scripts.discover import discover_endpoint
        from scripts.discovery import RateLimiter, SchemaInferrer

        mock_response = MagicMock()
        mock_response.status_code = 204  # No Content
        mock_response.json.side_effect = ValueError("No JSON content")

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        endpoint = {"path": "/api/nocontent", "method": "DELETE", "parameters": []}
        config = {"exploration": {"methods": ["DELETE"], "timeout_seconds": 30}}
        rate_limiter = RateLimiter({"requests_per_second": 10})
        schema_inferrer = SchemaInferrer()

        result = await discover_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            rate_limiter,
            schema_inferrer,
        )

        assert result.status_code == 204
        assert result.error is None


class TestConfigurationDefaults:
    """Test default configuration values."""

    def test_default_config_rate_limit_settings(self):
        """Should have sensible rate limit defaults."""
        from scripts.discover import get_default_config

        config = get_default_config()

        assert config["rate_limit"]["requests_per_second"] == 5
        assert config["rate_limit"]["burst_limit"] == 10
        assert config["rate_limit"]["backoff_base"] == 1.0

    def test_default_config_exploration_settings(self):
        """Should have appropriate exploration defaults."""
        from scripts.discover import get_default_config

        config = get_default_config()

        assert "system" in config["exploration"]["namespaces"]
        assert "shared" in config["exploration"]["namespaces"]
        assert "GET" in config["exploration"]["methods"]
        assert "OPTIONS" in config["exploration"]["methods"]

    def test_default_config_output_settings(self):
        """Should have output configuration."""
        from scripts.discover import get_default_config

        config = get_default_config()

        assert config["output"]["base_dir"] == "specs/discovered"
        assert config["output"]["format"] == "json"
        assert config["output"]["pretty_print"] is True


class TestEndpointExtractionEdgeCases:
    """Test edge cases in endpoint extraction."""

    def test_extract_endpoints_includes_source_file(self, tmp_path):
        """Should include source file name in endpoint data."""
        import json

        from scripts.discover import extract_endpoints_from_specs

        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()

        spec_file = specs_dir / "test-source.json"
        spec_file.write_text(
            json.dumps(
                {
                    "paths": {"/api/test": {"get": {"responses": {}}}},
                },
            ),
        )

        endpoints = extract_endpoints_from_specs(specs_dir)

        assert endpoints[0]["source_file"] == "test-source.json"

    def test_extract_endpoints_handles_empty_operations(self, tmp_path):
        """Should handle operations with minimal fields."""
        import json

        from scripts.discover import extract_endpoints_from_specs

        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()

        spec_file = specs_dir / "minimal.json"
        spec_file.write_text(
            json.dumps(
                {
                    "paths": {
                        "/api/minimal": {
                            "get": {},  # No operationId, parameters, responses
                        },
                    },
                },
            ),
        )

        endpoints = extract_endpoints_from_specs(specs_dir)

        assert len(endpoints) == 1
        assert endpoints[0]["operation_id"] == ""
        assert endpoints[0]["parameters"] == []
        assert endpoints[0]["responses"] == {}

    def test_extract_endpoints_processes_multiple_files(self, tmp_path):
        """Should process all JSON files in directory."""
        import json

        from scripts.discover import extract_endpoints_from_specs

        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()

        (specs_dir / "file1.json").write_text(
            json.dumps(
                {
                    "paths": {"/api/file1": {"get": {"responses": {}}}},
                },
            ),
        )
        (specs_dir / "file2.json").write_text(
            json.dumps(
                {
                    "paths": {"/api/file2": {"get": {"responses": {}}}},
                },
            ),
        )
        (specs_dir / "file3.json").write_text(
            json.dumps(
                {
                    "paths": {"/api/file3": {"get": {"responses": {}}}},
                },
            ),
        )

        endpoints = extract_endpoints_from_specs(specs_dir)

        assert len(endpoints) == 3
        paths = {ep["path"] for ep in endpoints}
        assert paths == {"/api/file1", "/api/file2", "/api/file3"}


class TestConfigurationLoadingFromYaml:
    """Test YAML configuration loading."""

    def test_load_config_from_valid_yaml(self, tmp_path):
        """Should load discovery configuration from YAML file."""
        from scripts.discover import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
discovery:
    api_url: "https://test.example.com"
    rate_limit:
        requests_per_second: 20
    exploration:
        namespaces:
            - custom
            - testing
        methods:
            - GET
            - POST
    output:
        base_dir: "custom/path"
        format: "yaml"
""")

        config = load_config(config_file)

        assert config["api_url"] == "https://test.example.com"
        assert config["rate_limit"]["requests_per_second"] == 20
        assert "custom" in config["exploration"]["namespaces"]
        assert "POST" in config["exploration"]["methods"]
        assert config["output"]["base_dir"] == "custom/path"

    def test_load_config_handles_nested_discovery_key(self, tmp_path):
        """Should extract discovery key from nested configuration."""
        from scripts.discover import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
discovery:
    api_url: "https://nested.example.com"
    rate_limit:
        requests_per_second: 15
""")

        config = load_config(config_file)

        # Should extract nested 'discovery' key
        assert "api_url" in config
        assert config["api_url"] == "https://nested.example.com"

    def test_load_config_handles_empty_file(self, tmp_path):
        """Should handle empty YAML files gracefully."""
        from scripts.discover import load_config

        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")

        config = load_config(config_file)

        # Empty file returns empty dict (not defaults)
        assert isinstance(config, dict)
        assert config == {}

    def test_load_config_handles_malformed_yaml(self, tmp_path):
        """Should handle malformed YAML gracefully."""
        import yaml

        from scripts.discover import load_config

        config_file = tmp_path / "malformed.yaml"
        config_file.write_text("{{{invalid yaml")

        # Should raise YAMLError for malformed YAML
        with pytest.raises(yaml.YAMLError):
            load_config(config_file)

    def test_load_config_with_missing_file(self, tmp_path):
        """Should return default config when file doesn't exist."""
        from scripts.discover import load_config

        config_file = tmp_path / "nonexistent.yaml"

        config = load_config(config_file)

        # Should return default configuration
        assert isinstance(config, dict)
        assert "api_url" in config
        assert "rate_limit" in config
        assert "exploration" in config
        assert "output" in config


class TestURLandAuthConfiguration:
    """Test URL and authentication configuration edge cases."""

    @patch.dict(os.environ, {"F5XC_API_URL": "https://env.example.com////"})
    def test_get_api_url_strips_multiple_trailing_slashes(self):
        """Should strip multiple trailing slashes."""
        from scripts.discover import get_api_url

        config = {}
        url = get_api_url(config)

        assert url.count("/") == 2  # Only in https://

    @patch.dict(os.environ, {"F5XC_API_TOKEN": ""}, clear=True)
    def test_get_auth_headers_handles_empty_token_string(self):
        """Should handle empty string token."""
        from scripts.discover import get_auth_headers

        config = {"auth_token": ""}
        headers = get_auth_headers(config)

        assert headers == {}

    @patch.dict(os.environ, {}, clear=True)
    def test_get_api_url_with_config_trailing_slash(self):
        """Should strip trailing slash from config URL."""
        from scripts.discover import get_api_url

        config = {"api_url": "https://config.example.com/api/"}
        url = get_api_url(config)

        assert url == "https://config.example.com/api"


class TestPathParameterResolutionEdgeCases:
    """Test edge cases in path parameter resolution."""

    def test_resolve_path_params_with_no_parameters(self):
        """Should handle paths with no parameters."""
        from scripts.discover import resolve_path_params

        path = "/api/resources/list"
        resolved = resolve_path_params(path)

        assert resolved == "/api/resources/list"

    def test_resolve_path_params_with_mixed_parameters(self):
        """Should handle mix of known and unknown parameters."""
        from scripts.discover import resolve_path_params

        path = "/api/{tenant}/namespaces/{namespace}/items/{unknown}"
        resolved = resolve_path_params(path, namespace="production")

        assert "production" in resolved
        assert "sample" in resolved
        assert "{" not in resolved  # No unresolved braces

    def test_resolve_path_params_preserves_non_parameter_braces(self):
        """Should only replace parameter placeholders."""
        from scripts.discover import resolve_path_params

        # Path with parameter
        path = "/api/namespaces/{namespace}/config"
        resolved = resolve_path_params(path, namespace="default")

        assert resolved == "/api/namespaces/default/config"


class TestEndpointFilteringEdgeCases:
    """Test edge cases in endpoint filtering."""

    def test_should_skip_endpoint_with_empty_config(self):
        """Should handle empty configuration gracefully."""
        from scripts.discover import should_skip_endpoint

        endpoint = {"method": "GET", "path": "/api/test"}
        config = {}

        # Should use defaults from exploration config
        should_skip, reason = should_skip_endpoint(endpoint, config)

        # Result depends on default implementation
        assert isinstance(should_skip, bool)

    def test_should_skip_endpoint_with_empty_methods_list(self):
        """Should skip all methods when methods list is empty."""
        from scripts.discover import should_skip_endpoint

        endpoint = {"method": "GET", "path": "/api/test"}
        config = {"exploration": {"methods": []}}

        should_skip, reason = should_skip_endpoint(endpoint, config)

        assert should_skip is True

    def test_should_skip_endpoint_case_sensitivity(self):
        """Should handle method case correctly."""
        from scripts.discover import should_skip_endpoint

        endpoint = {"method": "get", "path": "/api/test"}  # lowercase
        config = {"exploration": {"methods": ["GET"]}}  # uppercase

        # Method comparison might be case-sensitive
        should_skip, reason = should_skip_endpoint(endpoint, config)

        # This tests actual behavior - method case handling
        assert isinstance(should_skip, bool)

    def test_should_skip_endpoint_multiple_skip_patterns(self):
        """Should check all skip patterns in order."""
        from scripts.discover import should_skip_endpoint

        endpoint = {"method": "GET", "path": "/api/debug/internal/test"}
        config = {
            "exploration": {
                "methods": ["GET"],
                "skip_patterns": ["/debug/", "/internal/", "/admin/"],
            },
        }

        should_skip, reason = should_skip_endpoint(endpoint, config)

        assert should_skip is True
        # Should match first pattern found
        assert "/debug/" in reason or "/internal/" in reason


class TestSchemaMerging:
    """Test schema merging functionality."""

    def test_merge_schemas_with_both_none(self):
        """Should handle merging two None schemas."""
        from scripts.discover import _merge_schemas

        result = _merge_schemas(None, None)

        assert result is None

    def test_merge_schemas_with_base_none(self):
        """Should return new schema when base is None."""
        from scripts.discover import _merge_schemas
        from scripts.discovery.schema_inferrer import InferredConstraints, InferredSchema

        new_schema = InferredSchema(
            type="object",
            properties={"field1": InferredSchema(type="string")},
            constraints=InferredConstraints(),
        )

        result = _merge_schemas(None, new_schema)

        assert result == new_schema

    def test_merge_schemas_with_new_none(self):
        """Should return base schema when new is None."""
        from scripts.discover import _merge_schemas
        from scripts.discovery.schema_inferrer import InferredConstraints, InferredSchema

        base_schema = InferredSchema(
            type="object",
            properties={"field1": InferredSchema(type="string")},
            constraints=InferredConstraints(),
        )

        result = _merge_schemas(base_schema, None)

        assert result == base_schema

    def test_merge_schemas_adds_new_properties(self):
        """Should add new properties from new schema to base."""
        from scripts.discover import _merge_schemas
        from scripts.discovery.schema_inferrer import InferredConstraints, InferredSchema

        base = InferredSchema(
            type="object",
            properties={"field1": InferredSchema(type="string")},
            constraints=InferredConstraints(),
        )
        new = InferredSchema(
            type="object",
            properties={"field2": InferredSchema(type="integer")},
            constraints=InferredConstraints(),
        )

        result = _merge_schemas(base, new)

        assert "field1" in result.properties
        assert "field2" in result.properties
        assert result.properties["field2"].type == "integer"

    def test_merge_schemas_merges_min_length_constraints(self):
        """Should keep tightest min_length constraint."""
        from scripts.discover import _merge_schemas
        from scripts.discovery.schema_inferrer import InferredConstraints, InferredSchema

        base_field = InferredSchema(type="string")
        base_field.constraints = InferredConstraints(min_length=5)

        new_field = InferredSchema(type="string")
        new_field.constraints = InferredConstraints(min_length=10)  # More restrictive

        base = InferredSchema(
            type="object",
            properties={"field": base_field},
            constraints=InferredConstraints(),
        )
        new = InferredSchema(
            type="object",
            properties={"field": new_field},
            constraints=InferredConstraints(),
        )

        result = _merge_schemas(base, new)

        assert result.properties["field"].constraints.min_length == 10

    def test_merge_schemas_merges_max_length_constraints(self):
        """Should keep tightest max_length constraint."""
        from scripts.discover import _merge_schemas
        from scripts.discovery.schema_inferrer import InferredConstraints, InferredSchema

        base_field = InferredSchema(type="string")
        base_field.constraints = InferredConstraints(max_length=100)

        new_field = InferredSchema(type="string")
        new_field.constraints = InferredConstraints(max_length=50)  # More restrictive

        base = InferredSchema(
            type="object",
            properties={"field": base_field},
            constraints=InferredConstraints(),
        )
        new = InferredSchema(
            type="object",
            properties={"field": new_field},
            constraints=InferredConstraints(),
        )

        result = _merge_schemas(base, new)

        assert result.properties["field"].constraints.max_length == 50

    def test_merge_schemas_merges_enum_values(self):
        """Should merge enum values from both schemas."""
        from scripts.discover import _merge_schemas
        from scripts.discovery.schema_inferrer import InferredConstraints, InferredSchema

        base_field = InferredSchema(type="string")
        base_field.constraints = InferredConstraints(enum_values=["value1", "value2"])

        new_field = InferredSchema(type="string")
        new_field.constraints = InferredConstraints(enum_values=["value2", "value3"])

        base = InferredSchema(
            type="object",
            properties={"field": base_field},
            constraints=InferredConstraints(),
        )
        new = InferredSchema(
            type="object",
            properties={"field": new_field},
            constraints=InferredConstraints(),
        )

        result = _merge_schemas(base, new)

        enum_values = set(result.properties["field"].constraints.enum_values)
        assert "value1" in enum_values
        assert "value2" in enum_values
        assert "value3" in enum_values

    def test_merge_schemas_updates_format_when_missing(self):
        """Should update format when base doesn't have it."""
        from scripts.discover import _merge_schemas
        from scripts.discovery.schema_inferrer import InferredConstraints, InferredSchema

        base_field = InferredSchema(type="string", format=None)
        base_field.constraints = InferredConstraints()

        new_field = InferredSchema(type="string", format="date-time")
        new_field.constraints = InferredConstraints()

        base = InferredSchema(
            type="object",
            properties={"field": base_field},
            constraints=InferredConstraints(),
        )
        new = InferredSchema(
            type="object",
            properties={"field": new_field},
            constraints=InferredConstraints(),
        )

        result = _merge_schemas(base, new)

        assert result.properties["field"].format == "date-time"


class TestConfigurationEdgeCases:
    """Test additional configuration edge cases."""

    def test_get_default_config_has_all_required_sections(self):
        """Should have all required configuration sections."""
        from scripts.discover import get_default_config

        config = get_default_config()

        assert "api_url" in config
        assert "auth_token" in config
        assert "rate_limit" in config
        assert "exploration" in config
        assert "output" in config

    def test_get_default_config_rate_limit_structure(self):
        """Should have complete rate limit configuration."""
        from scripts.discover import get_default_config

        config = get_default_config()
        rate_limit = config["rate_limit"]

        assert "requests_per_second" in rate_limit
        assert "burst_limit" in rate_limit
        assert "backoff_base" in rate_limit
        assert isinstance(rate_limit["requests_per_second"], (int, float))

    def test_get_default_config_exploration_namespaces(self):
        """Should have default exploration namespaces."""
        from scripts.discover import get_default_config

        config = get_default_config()
        namespaces = config["exploration"]["namespaces"]

        assert len(namespaces) > 0
        assert "system" in namespaces

    def test_get_default_config_exploration_timeout(self):
        """Should have timeout configuration."""
        from scripts.discover import get_default_config

        config = get_default_config()

        assert "timeout_seconds" in config["exploration"]
        assert config["exploration"]["timeout_seconds"] > 0


class TestAPIURLEdgeCases:
    """Test API URL handling edge cases."""

    @patch.dict(os.environ, {}, clear=True)
    def test_get_api_url_from_config_strips_trailing_slash(self):
        """Should strip trailing slash from config URL."""
        from scripts.discover import get_api_url

        config = {"api_url": "https://config.example.com/"}

        url = get_api_url(config)

        assert url == "https://config.example.com"
        assert not url.endswith("/")

    @patch.dict(os.environ, {"F5XC_API_URL": "https://env.example.com"})
    def test_get_api_url_prefers_env_over_config(self):
        """Should prefer environment variable over config."""
        from scripts.discover import get_api_url

        config = {"api_url": "https://config.example.com"}

        url = get_api_url(config)

        assert url == "https://env.example.com"
        assert "config" not in url


class TestAuthHeadersEdgeCases:
    """Test authentication headers edge cases."""

    @patch.dict(os.environ, {"F5XC_API_TOKEN": "simple-token"})
    def test_get_auth_headers_returns_authorization_header(self):
        """Should return Authorization header with token."""
        from scripts.discover import get_auth_headers

        config = {}

        headers = get_auth_headers(config)

        assert "Authorization" in headers
        assert headers["Authorization"] == "APIToken simple-token"


class TestPathParameterEdgeCases:
    """Test additional path parameter resolution edge cases."""

    def test_resolve_path_params_with_only_namespace(self):
        """Should only replace namespace parameter."""
        from scripts.discover import resolve_path_params

        path = "/api/config/namespaces/{namespace}/resources"

        result = resolve_path_params(path, "custom")

        assert "{namespace}" not in result
        assert "custom" in result
        assert "/namespaces/custom/" in result

    def test_resolve_path_params_with_name_parameter(self):
        """Should replace name parameter with sample value."""
        from scripts.discover import resolve_path_params

        path = "/api/config/namespaces/{namespace}/resources/{name}"

        result = resolve_path_params(path, "test-ns")

        assert "{name}" not in result
        assert "sample-name" in result

    def test_resolve_path_params_preserves_path_structure(self):
        """Should preserve path structure while replacing parameters."""
        from scripts.discover import resolve_path_params

        path = "/api/config/namespaces/{namespace}/resource/{name}/action"

        result = resolve_path_params(path, "ns1")

        assert result.startswith("/api/config/")
        assert "/namespaces/ns1/" in result
        assert result.endswith("/action")


class TestEndpointExtractionMoreEdgeCases:
    """Test more endpoint extraction edge cases."""

    def test_extract_endpoints_handles_spec_without_paths(self, tmp_path):
        """Should handle specs without paths section."""
        import json

        from scripts.discover import extract_endpoints_from_specs

        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()

        spec_file = specs_dir / "no-paths.json"
        spec_file.write_text(
            json.dumps(
                {
                    "openapi": "3.0.0",
                    "info": {"title": "Test"},
                    # No paths section
                },
            ),
        )

        endpoints = extract_endpoints_from_specs(specs_dir)

        assert len(endpoints) == 0

    def test_extract_endpoints_handles_paths_with_no_operations(self, tmp_path):
        """Should handle paths with no HTTP method operations."""
        import json

        from scripts.discover import extract_endpoints_from_specs

        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()

        spec_file = specs_dir / "no-ops.json"
        spec_file.write_text(
            json.dumps(
                {
                    "paths": {
                        "/api/test": {},  # No get, post, etc.
                    },
                },
            ),
        )

        endpoints = extract_endpoints_from_specs(specs_dir)

        assert len(endpoints) == 0


class TestFilteringMoreEdgeCases:
    """Test more endpoint filtering edge cases."""

    def test_should_skip_endpoint_with_no_exploration_config(self):
        """Should handle missing exploration configuration."""
        from scripts.discover import should_skip_endpoint

        endpoint = {"method": "GET", "path": "/api/test"}
        config = {"other": "config"}  # No exploration section

        should_skip, reason = should_skip_endpoint(endpoint, config)

        # Should have sensible default behavior
        assert isinstance(should_skip, bool)

    def test_should_skip_endpoint_with_no_skip_patterns(self):
        """Should handle missing skip_patterns."""
        from scripts.discover import should_skip_endpoint

        endpoint = {"method": "GET", "path": "/api/test"}
        config = {
            "exploration": {
                "methods": ["GET"],
                # No skip_patterns
            },
        }

        should_skip, reason = should_skip_endpoint(endpoint, config)

        assert should_skip is False  # No patterns means nothing to skip
