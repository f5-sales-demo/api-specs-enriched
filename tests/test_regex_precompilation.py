# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unit tests for regex pattern precompilation (Issue #391).

Tests verify that regex patterns are precompiled at module/class level
for performance optimization and that behavior remains unchanged.
"""

import copy
import re

import pytest


class TestGrammarPatterns:
    """Test precompiled patterns in grammar.py."""

    def test_patterns_are_precompiled(self):
        """Verify grammar patterns are module-level constants."""
        # Import after module is loaded to check constants exist
        from scripts.utils import grammar

        assert hasattr(grammar, "_WHITESPACE_PATTERN")
        assert hasattr(grammar, "_EXCESSIVE_NEWLINES_PATTERN")
        assert hasattr(grammar, "_DOUBLE_SPACES_PATTERN")
        assert hasattr(grammar, "_SENTENCE_SPLITTER_PATTERN")

        assert isinstance(grammar._WHITESPACE_PATTERN, re.Pattern)
        assert isinstance(grammar._EXCESSIVE_NEWLINES_PATTERN, re.Pattern)
        assert isinstance(grammar._DOUBLE_SPACES_PATTERN, re.Pattern)
        assert isinstance(grammar._SENTENCE_SPLITTER_PATTERN, re.Pattern)

    def test_pattern_behavior_unchanged(self):
        """Verify grammar patterns still work correctly."""
        from scripts.utils.grammar import GrammarImprover

        improver = GrammarImprover()

        # Test whitespace normalization
        text_with_tabs = "Hello\t\tWorld"
        result = improver._normalize_whitespace(text_with_tabs)
        assert "\t" not in result
        assert "Hello World" in result

        # Test double space removal
        text_with_doubles = "Hello  World"
        result = improver._fix_double_spaces(text_with_doubles)
        assert "  " not in result
        assert result == "Hello World"

        # Test sentence capitalization
        text_lowercase = "hello world. this is a test."
        result = improver._capitalize_sentences(text_lowercase)
        assert result[0].isupper()
        assert "Hello world" in result


class TestDiscoverPattern:
    """Test precompiled pattern in discover.py."""

    def test_pattern_is_precompiled(self):
        """Verify discover pattern is precompiled."""
        from scripts import discover

        assert hasattr(discover, "_PATH_PARAM_PATTERN")
        assert isinstance(discover._PATH_PARAM_PATTERN, re.Pattern)

    def test_pattern_behavior_unchanged(self):
        """Verify discover pattern still works correctly."""
        from scripts.discover import resolve_path_params

        # Test namespace replacement
        result = resolve_path_params("/api/{namespace}/items", namespace="production")
        assert "{namespace}" not in result
        assert "production" in result

        # Test remaining parameter replacement
        result = resolve_path_params("/api/{custom_param}/items")
        assert "{custom_param}" not in result
        assert "sample" in result


class TestValidatePatterns:
    """Test precompiled patterns and caching in validate.py."""

    def test_patterns_are_precompiled(self):
        """Verify validate patterns are precompiled."""
        from scripts import validate

        assert hasattr(validate, "_PATH_PARAM_PATTERN")
        assert isinstance(validate._PATH_PARAM_PATTERN, re.Pattern)

    def test_compile_patterns_helper(self):
        """Verify _compile_patterns helper function works."""
        from scripts.validate import _compile_patterns

        patterns = ["api/internal/*", "api/test/*"]
        compiled = _compile_patterns(patterns)

        assert len(compiled) == 2
        assert all(isinstance(p, re.Pattern) for p in compiled)

        # Test pattern matching
        assert compiled[0].match("api/internal/debug")
        assert compiled[1].match("api/test/endpoint")
        assert not compiled[0].match("api/public/endpoint")

    def test_should_skip_endpoint_caching(self):
        """Verify endpoint skipping uses cached patterns."""
        from scripts import validate
        from scripts.validate import DEFAULT_CONFIG, should_skip_endpoint

        config = copy.deepcopy(DEFAULT_CONFIG)
        config["scope"]["validate_methods"] = ["GET"]
        config["scope"]["skip_methods"] = []
        config["filters"]["skip_patterns"] = ["*/internal/*"]
        config["filters"]["include_patterns"] = []

        endpoint1 = {"method": "GET", "path": "/api/internal/debug"}
        endpoint2 = {"method": "GET", "path": "/api/public/users"}

        # First call should create cache
        should_skip1, reason1 = should_skip_endpoint(endpoint1, config)
        assert should_skip1
        assert "skip pattern" in reason1

        # Second call should use cached patterns
        should_skip2, reason2 = should_skip_endpoint(endpoint2, config)
        assert not should_skip2

        # Verify module-level caches exist (implementation uses module-level caching)
        assert hasattr(validate, "_skip_patterns_cache")
        assert hasattr(validate, "_include_patterns_cache")

    def test_resolve_path_parameters_with_precompiled(self):
        """Verify path parameter resolution uses precompiled pattern."""
        from scripts.validate import resolve_path_parameters

        # The configured namespace is the only identity safe to substitute.
        result = resolve_path_parameters(
            "/api/{namespace}/items",
            "system",
        )
        assert result.resolved_path == "/api/system/items"
        assert result.unresolved_reason is None

        # Unknown resource identities must remain unrequested, not use samples.
        result = resolve_path_parameters("/api/{name}/items", "system")
        assert result.resolved_path is None
        assert result.unresolved_reason == "unsupported path parameter(s): name"


class TestDiscoveryEnricherPatterns:
    """Test precompiled class patterns in discovery_enricher.py."""

    def test_patterns_are_class_level(self):
        """Verify discovery enricher patterns are class-level constants."""
        from scripts.utils.discovery_enricher import DiscoveryEnricher

        assert hasattr(DiscoveryEnricher, "_UUID_PATTERN")
        assert hasattr(DiscoveryEnricher, "_DATETIME_PATTERN")
        assert hasattr(DiscoveryEnricher, "_EMAIL_PATTERN")
        assert hasattr(DiscoveryEnricher, "_URI_PATTERN")
        assert hasattr(DiscoveryEnricher, "_PATH_NORMALIZATION_PATTERN")

        assert isinstance(DiscoveryEnricher._UUID_PATTERN, re.Pattern)
        assert isinstance(DiscoveryEnricher._DATETIME_PATTERN, re.Pattern)
        assert isinstance(DiscoveryEnricher._EMAIL_PATTERN, re.Pattern)
        assert isinstance(DiscoveryEnricher._URI_PATTERN, re.Pattern)
        assert isinstance(DiscoveryEnricher._PATH_NORMALIZATION_PATTERN, re.Pattern)

    def test_uuid_detection(self):
        """Verify UUID pattern detection still works."""
        from scripts.utils.discovery_enricher import DiscoveryEnricher

        enricher = DiscoveryEnricher(config={})

        # Valid UUIDs
        assert enricher._looks_like_uuid("550e8400-e29b-41d4-a716-446655440000")
        assert enricher._looks_like_uuid("f81d4fae-7dec-11d0-a765-00a0c91e6bf6")

        # Invalid UUIDs
        assert not enricher._looks_like_uuid("not-a-uuid")
        assert not enricher._looks_like_uuid("12345")

    def test_datetime_detection(self):
        """Verify datetime pattern detection still works."""
        from scripts.utils.discovery_enricher import DiscoveryEnricher

        enricher = DiscoveryEnricher(config={})

        # Valid datetimes
        assert enricher._looks_like_datetime("2024-01-15T10:30:45")
        assert enricher._looks_like_datetime("2024-12-31T23:59:59Z")

        # Invalid datetimes
        assert not enricher._looks_like_datetime("not-a-date")
        assert not enricher._looks_like_datetime("2024-01-15")

    def test_email_detection(self):
        """Verify email pattern detection still works."""
        from scripts.utils.discovery_enricher import DiscoveryEnricher

        enricher = DiscoveryEnricher(config={})

        # Valid emails
        assert enricher._looks_like_email("user@example.com")
        assert enricher._looks_like_email("test.user+tag@example.co.uk")

        # Invalid emails
        assert not enricher._looks_like_email("not-an-email")
        assert not enricher._looks_like_email("@example.com")

    def test_uri_detection(self):
        """Verify URI pattern detection still works."""
        from scripts.utils.discovery_enricher import DiscoveryEnricher

        enricher = DiscoveryEnricher(config={})

        # Valid URIs
        assert enricher._looks_like_uri("https://example.com")
        assert enricher._looks_like_uri("http://api.example.com/path")

        # Invalid URIs
        assert not enricher._looks_like_uri("not-a-uri")
        assert not enricher._looks_like_uri("ftp://example.com")

    def test_path_normalization(self):
        """Verify path normalization pattern still works."""
        from scripts.utils.discovery_enricher import DiscoveryEnricher

        enricher = DiscoveryEnricher(config={})

        # Test path normalization
        path = "/api/{namespace}/{name}/items"
        normalized = enricher._PATH_NORMALIZATION_PATTERN.sub("{}", path)
        assert normalized == "/api/{}/{}/items"
        assert "{namespace}" not in normalized
        assert "{name}" not in normalized


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
