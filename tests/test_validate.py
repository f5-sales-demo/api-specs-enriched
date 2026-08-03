# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for scripts/validate.py - Automated validation against live F5 XC endpoints.

Tests cover:
- Configuration loading and merging
- Authentication header generation
- Base URL resolution
- Endpoint extraction from OpenAPI specs
- Endpoint filtering logic
- Pattern compilation and caching
"""

import copy
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.validate import (
    DEFAULT_CONFIG,
    _compile_patterns,
    _deep_merge,
    extract_endpoints,
    get_auth_headers,
    get_base_url,
    load_config,
    should_skip_endpoint,
)


class TestConfigurationLoading:
    """Test configuration loading and merging."""

    def test_explicit_missing_config_fails_closed(self):
        """An explicit missing config must not silently select another contract."""
        with pytest.raises(FileNotFoundError, match="validation configuration not found"):
            load_config(Path("/nonexistent/config.yaml"))

    def test_load_config_uses_packaged_config_when_path_is_none(self):
        """The installed default must include the packaged reporting contract."""
        result = load_config(None)
        assert result["reporting"]["format"] == "both"
        assert result["api"] == DEFAULT_CONFIG["api"]

    def test_load_config_merges_with_defaults(self, tmp_path):
        """Should deep merge loaded config with defaults."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
api:
    timeout: 60
scope:
    namespace: production
""")

        result = load_config(config_file)

        # Check merged values
        assert result["api"]["timeout"] == 60
        assert result["api"]["base_url"] == DEFAULT_CONFIG["api"]["base_url"]
        assert result["scope"]["namespace"] == "production"
        assert result["scope"]["validate_methods"] == DEFAULT_CONFIG["scope"]["validate_methods"]

    def test_default_config_has_no_unimplemented_execution_controls(self):
        """Configuration must describe behavior the validator actually implements."""
        assert set(DEFAULT_CONFIG["api"]) == {"base_url", "timeout"}
        assert set(DEFAULT_CONFIG["scope"]) == {
            "validate_methods",
            "skip_methods",
            "namespace",
        }
        assert set(DEFAULT_CONFIG["concurrency"]) == {"workers"}
        assert set(DEFAULT_CONFIG["authentication"]) == {"env_vars"}
        assert "cache" not in DEFAULT_CONFIG

    def test_repository_config_has_no_unimplemented_execution_controls(self):
        """The deployed YAML must not advertise retry, sampling, pooling, or caching."""
        import yaml

        config_path = Path(__file__).parents[1] / "config" / "validation.yaml"
        config = yaml.safe_load(config_path.read_text())

        assert set(config["api"]) == {"base_url", "timeout"}
        assert set(config["scope"]) == {
            "validate_methods",
            "skip_methods",
            "namespace",
        }
        assert set(config["concurrency"]) == {"workers"}
        assert set(config["authentication"]) == {"env_vars"}
        assert "cache" not in config

    def test_unknown_nested_authentication_control_fails_closed(self, tmp_path):
        """Nested environment mappings cannot hide unsupported runtime controls."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("authentication:\n  env_vars:\n    legacy_token: TOKEN\n")

        with pytest.raises(ValueError, match=r"authentication\.env_vars"):
            load_config(config_file)

    def test_deep_merge_preserves_base_values(self):
        """Should preserve base dict values not in override."""
        base = {"a": 1, "b": {"c": 2, "d": 3}}
        override = {"b": {"c": 20}}

        result = _deep_merge(base, override)

        assert result["a"] == 1
        assert result["b"]["c"] == 20
        assert result["b"]["d"] == 3

    def test_deep_merge_handles_nested_dicts(self):
        """Should recursively merge nested dictionaries."""
        base = {"level1": {"level2": {"level3": "base"}}}
        override = {"level1": {"level2": {"level3": "override", "new": "value"}}}

        result = _deep_merge(base, override)

        assert result["level1"]["level2"]["level3"] == "override"
        assert result["level1"]["level2"]["new"] == "value"

    def test_deep_merge_overwrites_non_dict_values(self):
        """Should overwrite non-dict values completely."""
        base = {"key": "value", "list": [1, 2, 3]}
        override = {"key": "new_value", "list": [4, 5]}

        result = _deep_merge(base, override)

        assert result["key"] == "new_value"
        assert result["list"] == [4, 5]


class TestAuthenticationHeaders:
    """Test authentication header generation."""

    @patch.dict(os.environ, {"F5XC_API_TOKEN": "test-token-12345"})
    def test_get_auth_headers_uses_default_env_var(self):
        """Should use F5XC_API_TOKEN by default."""
        config = DEFAULT_CONFIG

        headers = get_auth_headers(config)

        assert headers["Authorization"] == "APIToken test-token-12345"

    @patch.dict(os.environ, {"CUSTOM_TOKEN": "custom-token-67890"})
    def test_get_auth_headers_uses_custom_env_var(self):
        """Should use custom env var from config."""
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["authentication"]["env_vars"]["api_token"] = "CUSTOM_TOKEN"

        headers = get_auth_headers(config)

        assert headers["Authorization"] == "APIToken custom-token-67890"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_auth_headers_returns_empty_when_no_token(self):
        """Should return empty dict when no token in environment."""
        config = DEFAULT_CONFIG

        headers = get_auth_headers(config)

        assert headers == {}

    @patch.dict(os.environ, {"F5XC_API_TOKEN": ""})
    def test_get_auth_headers_returns_empty_for_empty_token(self):
        """Should return empty dict when token is empty string."""
        config = DEFAULT_CONFIG

        headers = get_auth_headers(config)

        assert headers == {}


class TestBaseURLResolution:
    """Test API base URL resolution."""

    @patch.dict(os.environ, {"F5XC_API_URL": "https://custom.api.com/"})
    def test_get_base_url_prefers_env_var(self):
        """Should prefer environment variable over config."""
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["api"]["base_url"] = "https://config.api.com"

        url = get_base_url(config)

        assert url == "https://custom.api.com"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_base_url_falls_back_to_config(self):
        """Should use config base_url when env var not set."""
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["api"]["base_url"] = "https://config.api.com/"

        url = get_base_url(config)

        assert url == "https://config.api.com"

    @patch.dict(os.environ, {}, clear=True)
    def test_get_base_url_uses_packaged_value_without_environment_override(self):
        """The canonical packaged URL is used without an environment override."""
        config = copy.deepcopy(DEFAULT_CONFIG)

        assert get_base_url(config) == DEFAULT_CONFIG["api"]["base_url"]

    @patch.dict(os.environ, {"F5XC_API_URL": "https://api.com/trailing/slash/"})
    def test_get_base_url_strips_trailing_slash(self):
        """Should strip trailing slashes from URL."""
        config = copy.deepcopy(DEFAULT_CONFIG)

        url = get_base_url(config)

        assert url == "https://api.com/trailing/slash"

    @patch.dict(os.environ, {"CUSTOM_URL_VAR": "https://custom.com"})
    def test_get_base_url_uses_custom_env_var_name(self):
        """Should respect custom environment variable names."""
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["authentication"]["env_vars"]["api_url"] = "CUSTOM_URL_VAR"

        url = get_base_url(config)

        assert url == "https://custom.com"


class TestEndpointExtraction:
    """Test endpoint extraction from OpenAPI specifications."""

    def test_extract_endpoints_from_simple_spec(self):
        """Should extract basic endpoint information."""
        spec = {
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "getUsers",
                        "summary": "Get all users",
                        "parameters": [],
                        "responses": {"200": {"description": "Success"}},
                    },
                },
            },
        }

        endpoints = extract_endpoints(spec)

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/users"
        assert endpoints[0]["method"] == "GET"
        assert endpoints[0]["operation_id"] == "getUsers"
        assert endpoints[0]["summary"] == "Get all users"

    def test_extract_endpoints_handles_multiple_methods(self):
        """Should extract all HTTP methods from a path."""
        spec = {
            "paths": {
                "/resource": {
                    "get": {"operationId": "getResource", "responses": {}},
                    "post": {"operationId": "createResource", "responses": {}},
                    "delete": {"operationId": "deleteResource", "responses": {}},
                },
            },
        }

        endpoints = extract_endpoints(spec)

        assert len(endpoints) == 3
        methods = {ep["method"] for ep in endpoints}
        assert methods == {"GET", "POST", "DELETE"}

    def test_extract_endpoints_handles_multiple_paths(self):
        """Should extract endpoints from multiple paths."""
        spec = {
            "paths": {
                "/users": {"get": {"responses": {}}},
                "/posts": {"get": {"responses": {}}},
                "/comments": {"get": {"responses": {}}},
            },
        }

        endpoints = extract_endpoints(spec)

        assert len(endpoints) == 3
        paths = {ep["path"] for ep in endpoints}
        assert paths == {"/users", "/posts", "/comments"}

    def test_extract_endpoints_is_sorted_by_path_and_method(self):
        """Extraction order must not depend on source-object insertion order."""
        spec = {
            "paths": {
                "/z": {"options": {"responses": {}}, "get": {"responses": {}}},
                "/a": {"post": {"responses": {}}, "get": {"responses": {}}},
            }
        }

        endpoints = extract_endpoints(spec)

        assert [(endpoint["path"], endpoint["method"]) for endpoint in endpoints] == [
            ("/a", "GET"),
            ("/a", "POST"),
            ("/z", "GET"),
            ("/z", "OPTIONS"),
        ]

    def test_extract_endpoints_includes_parameters(self):
        """Should include operation parameters."""
        spec = {
            "paths": {
                "/users/{id}": {
                    "get": {
                        "operationId": "getUserById",
                        "parameters": [
                            {"name": "id", "in": "path", "required": True},
                            {"name": "fields", "in": "query"},
                        ],
                        "responses": {},
                    },
                },
            },
        }

        endpoints = extract_endpoints(spec)

        assert len(endpoints[0]["parameters"]) == 2
        assert endpoints[0]["parameters"][0]["name"] == "id"
        assert endpoints[0]["parameters"][1]["name"] == "fields"

    def test_extract_endpoints_handles_empty_paths(self):
        """Should return empty list for spec with no paths."""
        spec = {"paths": {}}

        endpoints = extract_endpoints(spec)

        assert endpoints == []

    def test_extract_endpoints_skips_non_dict_path_items(self):
        """Should skip malformed path items."""
        spec = {
            "paths": {
                "/valid": {"get": {"responses": {}}},
                "/invalid": "not a dict",
                "/another": None,
            },
        }

        endpoints = extract_endpoints(spec)

        assert len(endpoints) == 1
        assert endpoints[0]["path"] == "/valid"

    def test_extract_endpoints_handles_missing_operation_fields(self):
        """Should use empty defaults for missing fields."""
        spec = {
            "paths": {
                "/minimal": {
                    "get": {},  # No operationId, summary, parameters, responses
                },
            },
        }

        endpoints = extract_endpoints(spec)

        assert endpoints[0]["operation_id"] == ""
        assert endpoints[0]["summary"] == ""
        assert endpoints[0]["parameters"] == []
        assert endpoints[0]["responses"] == {}


class TestEndpointFiltering:
    """Test endpoint filtering logic."""

    def test_should_skip_endpoint_skips_post_by_default(self):
        """Should skip POST method by default."""
        endpoint = {"method": "POST", "path": "/api/resource"}
        config = DEFAULT_CONFIG

        skip, reason = should_skip_endpoint(endpoint, config)

        assert skip is True
        assert "POST" in reason

    def test_should_skip_endpoint_allows_get_by_default(self):
        """Should not skip GET method by default."""
        endpoint = {"method": "GET", "path": "/api/resource"}
        config = DEFAULT_CONFIG

        skip, reason = should_skip_endpoint(endpoint, config)

        assert skip is False

    def test_should_skip_endpoint_respects_custom_validate_methods(self):
        """Should only validate methods in validate_methods list."""
        endpoint = {"method": "PUT", "path": "/api/resource"}
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["scope"]["validate_methods"] = ["PUT", "PATCH"]
        config["scope"]["skip_methods"] = []

        skip, reason = should_skip_endpoint(endpoint, config)

        assert skip is False

    def test_should_skip_endpoint_matches_skip_patterns(self):
        """Should skip endpoints matching skip patterns."""
        endpoint = {"method": "GET", "path": "/api/internal/debug"}
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["scope"]["validate_methods"] = ["GET"]
        config["scope"]["skip_methods"] = []
        config["filters"]["skip_patterns"] = ["/api/internal/*"]
        config["filters"]["include_patterns"] = []

        skip, reason = should_skip_endpoint(endpoint, config)

        assert skip is True
        assert "skip pattern" in reason.lower()

    def test_should_skip_endpoint_respects_include_patterns(self):
        """Should only validate endpoints matching include patterns."""
        endpoint = {"method": "GET", "path": "/api/public/users"}
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["scope"]["validate_methods"] = ["GET"]
        config["scope"]["skip_methods"] = []
        config["filters"]["skip_patterns"] = []
        config["filters"]["include_patterns"] = ["/api/public/*"]

        skip, reason = should_skip_endpoint(endpoint, config)

        assert skip is False


class TestPatternCompilation:
    """Test regex pattern compilation utility."""

    def test_compile_patterns_converts_glob_to_regex(self):
        """Should convert glob wildcards to regex."""
        patterns = ["/api/*/debug", "/internal/*"]

        compiled = _compile_patterns(patterns)

        assert len(compiled) == 2
        assert compiled[0].pattern == "/api/.*/debug"
        assert compiled[1].pattern == "/internal/.*"

    def test_compile_patterns_matches_correctly(self):
        """Compiled patterns should match expected strings."""
        patterns = ["/api/*/users"]
        compiled = _compile_patterns(patterns)

        assert compiled[0].match("/api/v1/users")
        assert compiled[0].match("/api/v2/users")
        assert not compiled[0].match("/api/users")

    def test_compile_patterns_handles_empty_list(self):
        """Should return empty list for empty input."""
        compiled = _compile_patterns([])

        assert compiled == []

    def test_compile_patterns_handles_multiple_wildcards(self):
        """Should handle multiple wildcards in single pattern."""
        patterns = ["/api/*/namespaces/*/resources"]
        compiled = _compile_patterns(patterns)

        assert compiled[0].match("/api/v1/namespaces/default/resources")
        assert compiled[0].match("/api/v2/namespaces/production/resources")


class TestPathParameterResolution:
    """Test path parameter resolution."""

    def test_resolve_path_parameters_uses_configured_namespace(self):
        """The stable namespace is the only placeholder the validator resolves."""
        from scripts.validate import resolve_path_parameters

        result = resolve_path_parameters(
            "/api/namespaces/{namespace}/resources",
            "shared",
        )

        assert result.resolved_path == "/api/namespaces/shared/resources"
        assert result.unresolved_reason is None

    def test_resolve_path_parameters_rejects_identity_parameter(self):
        """Identity-bearing placeholders must never receive fabricated values."""
        from scripts.validate import resolve_path_parameters

        result = resolve_path_parameters("/api/resources/{id}", "shared")

        assert result.resolved_path is None
        assert result.unresolved_reason == "unsupported path parameter(s): id"

    @pytest.mark.parametrize("namespace", [None, "", "unsafe/scope", "{namespace}"])
    def test_resolve_path_parameters_requires_safe_configured_namespace(self, namespace):
        """Namespace resolution cannot fall back or inject a path fragment."""
        from scripts.validate import resolve_path_parameters

        result = resolve_path_parameters("/api/namespaces/{namespace}/resources", namespace)

        assert result.resolved_path is None
        assert result.unresolved_reason in {
            "namespace path parameter has no configured scope.namespace",
            "configured scope.namespace is not a safe path segment",
        }

    def test_resolve_path_parameters_with_unknown_params(self):
        """Every unsupported placeholder is reported deterministically."""
        from scripts.validate import resolve_path_parameters

        result = resolve_path_parameters("/api/{unknown}/{another}", "shared")

        assert result.resolved_path is None
        assert result.unresolved_reason == "unsupported path parameter(s): another, unknown"

    def test_resolve_path_parameters_leaves_literal_path_unchanged(self):
        """Literal API paths need no configured identifier."""
        from scripts.validate import resolve_path_parameters

        result = resolve_path_parameters("/api/resources", None)

        assert result.resolved_path == "/api/resources"
        assert result.unresolved_reason is None

    def test_resolve_path_parameters_handles_unresolved_braces(self):
        """Malformed braces are unresolved rather than sent to the tenant."""
        from scripts.validate import resolve_path_parameters

        result = resolve_path_parameters("/api/{namespace/resources", "shared")

        assert result.resolved_path is None
        assert result.unresolved_reason == "malformed path parameter template"

    @pytest.mark.parametrize(
        "path",
        [
            "https://example.invalid/api",
            "//example.invalid/api",
            "/api/resources?all=true",
            "/api/resources#fragment",
            "api/resources",
            "//[",
        ],
    )
    def test_resolve_path_parameters_rejects_non_relative_api_paths(self, path):
        """A specification path cannot redirect live validation off the configured tenant."""
        from scripts.validate import resolve_path_parameters

        result = resolve_path_parameters(path, "shared")

        assert result.resolved_path is None
        assert result.unresolved_reason == "path is not a safe API-relative path"


class TestAsyncEndpointValidation:
    """Test async endpoint validation functions."""

    @pytest.mark.asyncio
    async def test_validate_endpoint_successful_response(self):
        """Should validate endpoint with successful response."""
        import asyncio
        from unittest.mock import AsyncMock

        from scripts.validate import validate_endpoint

        # Mock client and response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"metadata": {}, "spec": {}}'
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"metadata": {}, "spec": {}}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        endpoint = {
            "path": "/api/test",
            "method": "GET",
            "parameters": [],
            "responses": {
                "200": {
                    "description": "success",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["metadata", "spec"],
                            }
                        }
                    },
                }
            },
        }

        config = DEFAULT_CONFIG
        semaphore = asyncio.Semaphore(10)

        result = await validate_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            semaphore,
            resolved_path=endpoint["path"],
        )

        assert result.status == "available"
        assert result.status_code == 200
        assert result.schema_match is True

    @pytest.mark.asyncio
    async def test_validate_endpoint_authentication_error(self):
        """Should treat an authentication failure as unavailable."""
        import asyncio
        from unittest.mock import AsyncMock

        from scripts.validate import validate_endpoint

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.content = b""

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        endpoint = {
            "path": "/api/test",
            "method": "GET",
            "parameters": [],
            "responses": {"401": {"description": "unauthorized"}},
        }
        config = DEFAULT_CONFIG
        semaphore = asyncio.Semaphore(10)

        result = await validate_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            semaphore,
            resolved_path=endpoint["path"],
        )

        assert result.status == "unavailable"
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_validate_endpoint_timeout(self):
        """Should handle request timeout."""
        import asyncio
        from unittest.mock import AsyncMock

        import httpx

        from scripts.validate import validate_endpoint

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))

        endpoint = {"path": "/api/test", "method": "GET", "parameters": []}
        config = DEFAULT_CONFIG
        semaphore = asyncio.Semaphore(10)

        result = await validate_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            semaphore,
            resolved_path=endpoint["path"],
        )

        assert result.status == "error"
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_validate_endpoint_request_error(self):
        """Should handle general request errors."""
        import asyncio
        from unittest.mock import AsyncMock

        import httpx

        from scripts.validate import validate_endpoint

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=httpx.RequestError("Connection failed"))

        endpoint = {"path": "/api/test", "method": "GET", "parameters": []}
        config = DEFAULT_CONFIG
        semaphore = asyncio.Semaphore(10)

        result = await validate_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            semaphore,
            resolved_path=endpoint["path"],
        )

        assert result.status == "error"
        assert result.error == "Request failed (RequestError)"

    @pytest.mark.asyncio
    async def test_validate_endpoint_invalid_json_response(self):
        """Should detect invalid JSON in response."""
        import asyncio
        import json
        from unittest.mock import AsyncMock

        from scripts.validate import validate_endpoint

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"not-json"
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        endpoint = {
            "path": "/api/test",
            "method": "GET",
            "parameters": [],
            "responses": {
                "200": {
                    "description": "success",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
            },
        }
        config = DEFAULT_CONFIG
        semaphore = asyncio.Semaphore(10)

        result = await validate_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            semaphore,
            resolved_path=endpoint["path"],
        )

        assert result.schema_match is False
        assert "not valid JSON" in result.discrepancies[0]

    @pytest.mark.asyncio
    async def test_validate_endpoint_missing_expected_structure(self):
        """Should detect a body that violates the operation's response schema."""
        import asyncio
        from unittest.mock import AsyncMock

        from scripts.validate import validate_endpoint

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"unexpected": "structure"}'
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"unexpected": "structure"}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        endpoint = {
            "path": "/api/test",
            "method": "GET",
            "parameters": [],
            "responses": {
                "200": {
                    "description": "success",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["metadata"],
                            }
                        }
                    },
                }
            },
        }
        config = DEFAULT_CONFIG
        semaphore = asyncio.Semaphore(10)

        result = await validate_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            semaphore,
            resolved_path=endpoint["path"],
        )

        assert len(result.discrepancies) > 0
        assert "Response schema mismatch" in result.discrepancies[0]

    @pytest.mark.asyncio
    async def test_validate_endpoint_with_path_parameters(self):
        """Should request the path pre-resolved by deterministic selection."""
        import asyncio
        from unittest.mock import AsyncMock

        from scripts.validate import validate_endpoint

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": []}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        endpoint = {
            "path": "/api/namespaces/{namespace}/resources",
            "method": "GET",
            "parameters": [{"name": "namespace", "in": "path"}],
        }
        config = DEFAULT_CONFIG
        semaphore = asyncio.Semaphore(10)

        await validate_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            semaphore,
            resolved_path="/api/namespaces/shared/resources",
        )

        # Verify path parameters were resolved
        mock_client.request.assert_called_once()
        call_args = mock_client.request.call_args
        assert call_args.kwargs["url"] == (
            "https://api.example.com/api/namespaces/shared/resources"
        )

    @pytest.mark.asyncio
    async def test_validate_endpoint_value_error(self):
        """Should handle ValueError from configuration issues."""
        import asyncio
        from unittest.mock import AsyncMock

        from scripts.validate import validate_endpoint

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=ValueError("Invalid timeout"))

        endpoint = {"path": "/api/test", "method": "GET", "parameters": []}
        config = DEFAULT_CONFIG
        semaphore = asyncio.Semaphore(10)

        result = await validate_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            semaphore,
            resolved_path=endpoint["path"],
        )

        assert result.status == "error"
        assert result.error == "Configuration error (ValueError)"

    @pytest.mark.asyncio
    async def test_validate_endpoint_type_error(self):
        """Should handle TypeError from type mismatches."""
        import asyncio
        from unittest.mock import AsyncMock

        from scripts.validate import validate_endpoint

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=TypeError("Type mismatch"))

        endpoint = {"path": "/api/test", "method": "GET", "parameters": []}
        config = DEFAULT_CONFIG
        semaphore = asyncio.Semaphore(10)

        result = await validate_endpoint(
            mock_client,
            "https://api.example.com",
            endpoint,
            config,
            semaphore,
            resolved_path=endpoint["path"],
        )

        assert result.status == "error"
        assert result.error == "Type error in endpoint validation (TypeError)"


class TestSpecValidation:
    """Test spec file validation."""

    @pytest.mark.asyncio
    async def test_validate_spec_processes_endpoints(self, tmp_path):
        """Should process all endpoints in a spec."""
        import asyncio
        import json
        from unittest.mock import AsyncMock

        from scripts.validate import validate_spec

        # Create test spec file
        spec_file = tmp_path / "test.json"
        spec_file.write_text(
            json.dumps(
                {
                    "paths": {
                        "/test1": {"get": {"operationId": "test1", "responses": {}}},
                        "/test2": {"get": {"operationId": "test2", "responses": {}}},
                    },
                },
            ),
        )

        # Mock successful responses
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": []}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        config = DEFAULT_CONFIG
        semaphore = asyncio.Semaphore(10)

        result = await validate_spec(spec_file, config, mock_client, semaphore)

        assert result.endpoints_total == 2
        assert result.filename == "test.json"

    @pytest.mark.asyncio
    async def test_validate_spec_handles_file_not_found(self):
        """Should handle missing spec file."""
        import asyncio
        from pathlib import Path
        from unittest.mock import AsyncMock

        from scripts.validate import validate_spec

        mock_client = AsyncMock()
        config = DEFAULT_CONFIG
        semaphore = asyncio.Semaphore(10)

        result = await validate_spec(Path("/nonexistent.json"), config, mock_client, semaphore)

        assert len(result.errors) > 0
        assert "Cannot read spec file" in result.errors[0]

    @pytest.mark.asyncio
    async def test_validate_spec_handles_invalid_json(self, tmp_path):
        """Should handle invalid JSON in spec file."""
        import asyncio
        from unittest.mock import AsyncMock

        from scripts.validate import validate_spec

        spec_file = tmp_path / "invalid.json"
        spec_file.write_text("{ invalid json }")

        mock_client = AsyncMock()
        config = DEFAULT_CONFIG
        semaphore = asyncio.Semaphore(10)

        result = await validate_spec(spec_file, config, mock_client, semaphore)

        assert len(result.errors) > 0
        assert "invalid JSON" in result.errors[0]

    @pytest.mark.asyncio
    async def test_validate_spec_executes_every_safe_endpoint(self, tmp_path):
        """Endpoint count cannot truncate or sample the deterministic probe set."""
        import asyncio
        import json
        from unittest.mock import AsyncMock

        from scripts.validate import validate_spec

        paths = {
            f"/resources/{i:03d}": {
                "get": {
                    "operationId": f"getResource{i}",
                    "responses": {"204": {"description": "success"}},
                }
            }
            for i in range(100)
        }
        spec_file = tmp_path / "large.json"
        spec_file.write_text(json.dumps({"paths": paths}))

        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_response.content = b""
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        config = DEFAULT_CONFIG
        semaphore = asyncio.Semaphore(10)

        result = await validate_spec(spec_file, config, mock_client, semaphore)

        assert result.endpoints_total == 100
        assert result.endpoints_eligible == 100
        assert result.endpoints_safely_resolved == 100
        assert result.endpoints_unresolved == 0
        assert result.endpoints_executed == 100
        assert result.endpoints_validated == 100
        assert mock_client.request.await_count == 100

    @pytest.mark.asyncio
    async def test_validate_spec_reports_unresolved_without_blocking_safe_execution(self, tmp_path):
        """Unresolved identities stay visible while every safe operation is executed."""
        import asyncio
        import json
        from unittest.mock import AsyncMock

        from scripts.utils.validation_reporter import ValidationStats
        from scripts.validate import validate_spec, validation_failures

        spec_file = tmp_path / "mixed.json"
        spec_file.write_text(
            json.dumps(
                {
                    "paths": {
                        "/api/resources": {
                            "get": {"responses": {"204": {"description": "success"}}}
                        },
                        "/api/namespaces/{namespace}/resources": {
                            "options": {"responses": {"204": {"description": "success"}}}
                        },
                        "/api/resources/{name}": {
                            "get": {"responses": {"204": {"description": "success"}}}
                        },
                        "/api/internal/status": {
                            "get": {"responses": {"204": {"description": "success"}}}
                        },
                        "/api/write": {"post": {"responses": {"204": {"description": "success"}}}},
                    }
                }
            )
        )
        mock_response = MagicMock(status_code=204, content=b"", headers={})
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        result = await validate_spec(
            spec_file,
            DEFAULT_CONFIG,
            mock_client,
            asyncio.Semaphore(10),
        )

        assert result.endpoints_total == 5
        assert result.endpoints_eligible == 3
        assert result.endpoints_safely_resolved == 2
        assert result.endpoints_unresolved == 1
        assert result.endpoints_executed == 2
        assert result.endpoints_skipped == 2
        assert result.errors == []
        assert result.unresolved_endpoints == [
            {
                "method": "GET",
                "path": "/api/resources/{name}",
                "reason": "unsupported path parameter(s): name",
            }
        ]
        unresolved = [item for item in result.endpoint_results if item.status == "unresolved"]
        assert [(item.method, item.path, item.error) for item in unresolved] == [
            ("GET", "/api/resources/{name}", "unsupported path parameter(s): name")
        ]
        assert [call.kwargs["url"] for call in mock_client.request.await_args_list] == [
            "https://console.ves.volterra.io/api/namespaces/shared/resources",
            "https://console.ves.volterra.io/api/resources",
        ]

        stats = ValidationStats(
            specs_processed=1,
            total_endpoints=result.endpoints_total,
            endpoints_eligible=result.endpoints_eligible,
            endpoints_safely_resolved=result.endpoints_safely_resolved,
            endpoints_unresolved=result.endpoints_unresolved,
            endpoints_executed=result.endpoints_executed,
            endpoints_validated=result.endpoints_validated,
            endpoints_available=result.endpoints_available,
            schema_matches=result.schema_matches,
            spec_results=[result],
        )
        assert validation_failures(stats, DEFAULT_CONFIG) == []


class TestReportGeneration:
    """Test validation report generation."""

    def test_generate_report_creates_json_file(self, tmp_path):
        """Should generate JSON report file."""
        from scripts.validate import ValidationStats, generate_report

        stats = ValidationStats()
        stats.specs_processed = 5
        stats.total_endpoints = 100
        stats.endpoints_validated = 90
        stats.endpoints_available = 85
        stats.schema_matches = 80

        output_path = tmp_path / "report.json"
        generate_report(stats, output_path)

        assert output_path.exists()

        # Verify report structure
        import json

        with output_path.open() as f:
            report = json.load(f)

        assert "timestamp" not in report
        assert report["summary"]["specs_processed"] == 5
        assert report["summary"]["total_endpoints"] == 100

    def test_generate_report_calculates_percentages(self, tmp_path):
        """Should calculate availability and schema match percentages."""
        from scripts.validate import ValidationStats, generate_report

        stats = ValidationStats()
        stats.endpoints_validated = 100
        stats.endpoints_available = 80
        stats.schema_matches = 70

        output_path = tmp_path / "report.json"
        generate_report(stats, output_path)

        import json

        with output_path.open() as f:
            report = json.load(f)

        assert report["summary"]["availability_percentage"] == 80.0
        assert report["summary"]["schema_match_percentage"] == 87.5  # 70/80 * 100

    def test_generate_report_handles_zero_division(self, tmp_path):
        """Should handle zero division gracefully."""
        from scripts.validate import ValidationStats, generate_report

        stats = ValidationStats()
        stats.endpoints_validated = 0
        stats.endpoints_available = 0

        output_path = tmp_path / "report.json"
        generate_report(stats, output_path)

        import json

        with output_path.open() as f:
            report = json.load(f)

        assert report["summary"]["availability_percentage"] == 0
        assert report["summary"]["schema_match_percentage"] == 0
