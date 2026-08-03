# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Error catalog builder for API discovery.

Catalogs error responses discovered during API exploration,
building a comprehensive database of error patterns and resolutions.

Usage:
    builder = ErrorCatalogBuilder()
    builder.record_error(endpoint, status_code, response_body)
    catalog = builder.build_catalog()
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ErrorCatalogConfig:
    """Configuration for error catalog building."""

    enabled: bool = True
    max_errors_per_endpoint: int = 10
    max_message_length: int = 200
    track_frequency: bool = True
    categorize_errors: bool = True


@dataclass
class ErrorEntry:
    """A single error entry in the catalog."""

    status_code: int
    error_type: str
    message: str
    message_pattern: str
    endpoint: str
    method: str
    frequency: int = 1
    first_seen: str | None = None
    last_seen: str | None = None
    resolution: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result: dict[str, Any] = {
            "status_code": self.status_code,
            "error_type": self.error_type,
            "message_pattern": self.message_pattern,
            "frequency": self.frequency,
        }
        if self.resolution:
            result["resolution"] = self.resolution
        return result

    def to_extension(self) -> dict[str, Any]:
        """Convert to OpenAPI extension format."""
        result: dict[str, Any] = {
            "status_code": self.status_code,
            "error_type": self.error_type,
            "message_pattern": self.message_pattern,
            "frequency": self.frequency,
        }
        if self.resolution:
            result["resolution"] = self.resolution
        return result


@dataclass
class EndpointErrors:
    """Collection of errors for a specific endpoint."""

    endpoint: str
    method: str
    errors: list[ErrorEntry] = field(default_factory=list)
    total_errors: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "endpoint": self.endpoint,
            "method": self.method,
            "total_errors": self.total_errors,
            "errors": [e.to_dict() for e in self.errors],
        }


@dataclass
class ErrorCatalog:
    """Complete error catalog from discovery."""

    errors_by_endpoint: dict[str, EndpointErrors] = field(default_factory=dict)
    errors_by_status: dict[int, list[ErrorEntry]] = field(
        default_factory=lambda: defaultdict(list),
    )
    total_errors: int = 0
    unique_patterns: int = 0
    discovered_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_errors": self.total_errors,
            "unique_patterns": self.unique_patterns,
            "discovered_at": self.discovered_at,
            "by_endpoint": {
                path: errors.to_dict() for path, errors in self.errors_by_endpoint.items()
            },
            "by_status_code": {
                str(code): [e.to_dict() for e in entries]
                for code, entries in self.errors_by_status.items()
            },
        }

    def to_extension(self) -> list[dict[str, Any]]:
        """Convert to OpenAPI extension format (x-f5xc-discovered-error-catalog)."""
        # Flatten all unique error patterns
        seen: set[tuple[int, str]] = set()
        result: list[dict[str, Any]] = []

        for entries in self.errors_by_status.values():
            for entry in entries:
                key = (entry.status_code, entry.message_pattern)
                if key not in seen:
                    seen.add(key)
                    result.append(entry.to_extension())

        return result


@dataclass
class BuilderStats:
    """Statistics for error catalog builder."""

    errors_recorded: int = 0
    errors_deduplicated: int = 0
    patterns_extracted: int = 0
    resolutions_generated: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "errors_recorded": self.errors_recorded,
            "errors_deduplicated": self.errors_deduplicated,
            "patterns_extracted": self.patterns_extracted,
            "resolutions_generated": self.resolutions_generated,
        }


# Error type classification patterns
ERROR_TYPE_PATTERNS = {
    "validation": [
        r"invalid",
        r"malformed",
        r"required",
        r"missing",
        r"format",
        r"syntax",
    ],
    "authentication": [
        r"unauthorized",
        r"unauthenticated",
        r"token",
        r"credential",
        r"login",
    ],
    "authorization": [r"forbidden", r"permission", r"access denied", r"not allowed"],
    "not_found": [r"not found", r"does not exist", r"unknown", r"no such"],
    "conflict": [r"already exists", r"conflict", r"duplicate", r"in use"],
    "rate_limit": [r"rate limit", r"too many", r"throttl", r"quota"],
    "server_error": [r"internal", r"server error", r"unexpected", r"failed"],
    "timeout": [r"timeout", r"timed out", r"deadline"],
    "resource": [r"resource", r"capacity", r"limit exceeded", r"exhausted"],
}

# Resolution templates by error type
RESOLUTION_TEMPLATES = {
    "validation": "Verify request format matches API specification requirements",
    "authentication": "Check API token validity and ensure proper authentication headers",
    "authorization": "Verify user permissions and namespace access rights",
    "not_found": "Confirm resource exists and check for typos in resource name",
    "conflict": "Use unique name or delete existing resource before retry",
    "rate_limit": "Implement exponential backoff and reduce request frequency",
    "server_error": "Retry request; if persistent, contact support",
    "timeout": "Increase timeout or break into smaller operations",
    "resource": "Check resource quotas and clean up unused resources",
    "unknown": "Review error details and consult API documentation",
}


class ErrorCatalogBuilder:
    """Builds a catalog of API errors from discovery.

    Provides:
    - Error recording and deduplication
    - Pattern extraction from error messages
    - Error type classification
    - Resolution suggestion generation

    Attributes:
        config: Builder configuration
        stats: Building statistics
    """

    def __init__(self, config: ErrorCatalogConfig | dict | None = None) -> None:
        """Initialize error catalog builder.

        Args:
            config: Builder configuration
        """
        if config is None:
            self.config = ErrorCatalogConfig()
        elif isinstance(config, dict):
            self.config = ErrorCatalogConfig(
                enabled=config.get("enabled", True),
                max_errors_per_endpoint=config.get("max_errors_per_endpoint", 10),
                max_message_length=config.get("max_message_length", 200),
                track_frequency=config.get("track_frequency", True),
                categorize_errors=config.get("categorize_errors", True),
            )
        else:
            self.config = config

        self.stats = BuilderStats()
        self._errors: dict[str, dict[tuple[int, str], ErrorEntry]] = defaultdict(dict)
        self._error_counts: dict[str, int] = defaultdict(int)

    def record_error(
        self,
        endpoint: str,
        method: str,
        status_code: int,
        response_body: dict[str, Any] | str | None,
        headers: dict[str, str] | None = None,
    ) -> ErrorEntry | None:
        """Record an error from API discovery.

        Args:
            endpoint: API endpoint path
            method: HTTP method
            status_code: HTTP status code
            response_body: Error response body
            headers: Response headers

        Returns:
            ErrorEntry if recorded, None if disabled or limit reached
        """
        if not self.config.enabled:
            return None

        self.stats.errors_recorded += 1

        # Check per-endpoint limit
        endpoint_key = f"{method}:{endpoint}"
        if self._error_counts[endpoint_key] >= self.config.max_errors_per_endpoint:
            return None

        # Extract error message
        message = self._extract_message(response_body)

        # Generate pattern from message
        pattern = self._extract_pattern(message)
        self.stats.patterns_extracted += 1

        # Classify error type
        error_type = self._classify_error(status_code, message)

        # Generate resolution
        resolution = self._generate_resolution(error_type, status_code)
        if resolution:
            self.stats.resolutions_generated += 1

        # Create or update entry
        error_key = (status_code, pattern)
        now = datetime.now(UTC).isoformat()

        if error_key in self._errors[endpoint_key]:
            # Update existing entry
            entry = self._errors[endpoint_key][error_key]
            entry.frequency += 1
            entry.last_seen = now
            self.stats.errors_deduplicated += 1
        else:
            # Create new entry
            entry = ErrorEntry(
                status_code=status_code,
                error_type=error_type,
                message=message[: self.config.max_message_length],
                message_pattern=pattern,
                endpoint=endpoint,
                method=method,
                frequency=1,
                first_seen=now,
                last_seen=now,
                resolution=resolution,
            )
            self._errors[endpoint_key][error_key] = entry
            self._error_counts[endpoint_key] += 1

        return entry

    def _extract_message(self, response_body: dict[str, Any] | str | None) -> str:
        """Extract error message from response body.

        Args:
            response_body: Response body (dict or string)

        Returns:
            Extracted error message
        """
        if response_body is None:
            return ""

        if isinstance(response_body, str):
            return response_body[: self.config.max_message_length]

        # Try common error message fields
        for field_name in ["message", "error", "detail", "error_message", "msg"]:
            if field_name in response_body:
                value = response_body[field_name]
                if isinstance(value, str):
                    return value[: self.config.max_message_length]
                if isinstance(value, dict) and "message" in value:
                    return str(value["message"])[: self.config.max_message_length]

        # Try nested error object
        if "error" in response_body and isinstance(response_body["error"], dict):
            nested = response_body["error"]
            for field_name in ["message", "description", "detail"]:
                if field_name in nested:
                    return str(nested[field_name])[: self.config.max_message_length]

        return str(response_body)[: self.config.max_message_length]

    def _extract_pattern(self, message: str) -> str:
        """Extract a pattern from an error message.

        Removes variable parts like IDs, names, and values to create
        a generalizable pattern.

        Args:
            message: Original error message

        Returns:
            Generalized pattern string
        """
        if not message:
            return ""

        pattern = message

        # Replace UUIDs with placeholder
        pattern = re.sub(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "{id}",
            pattern,
            flags=re.IGNORECASE,
        )

        # Replace quoted strings with placeholder
        pattern = re.sub(r'"[^"]*"', '"{value}"', pattern)
        pattern = re.sub(r"'[^']*'", "'{value}'", pattern)

        # Replace numbers with placeholder
        pattern = re.sub(r"\b\d+\b", "{number}", pattern)

        # Replace IP addresses
        pattern = re.sub(r"\d+\.\d+\.\d+\.\d+", "{ip}", pattern)

        # Normalize whitespace
        pattern = " ".join(pattern.split())

        return pattern[: self.config.max_message_length]

    def _classify_error(self, status_code: int, message: str) -> str:
        """Classify error type from status code and message.

        Args:
            status_code: HTTP status code
            message: Error message

        Returns:
            Error type classification
        """
        message_lower = message.lower()

        # Check message patterns first
        for error_type, patterns in ERROR_TYPE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, message_lower):
                    return error_type

        # Fall back to status code classification using lookup table
        status_code_map = {
            400: "validation",
            401: "authentication",
            403: "authorization",
            404: "not_found",
            409: "conflict",
            429: "rate_limit",
        }

        if status_code in status_code_map:
            return status_code_map[status_code]
        if status_code >= 500:
            return "server_error"

        return "unknown"

    def _generate_resolution(self, error_type: str, status_code: int) -> str:
        """Generate resolution suggestion for error.

        Args:
            error_type: Classified error type
            status_code: HTTP status code

        Returns:
            Resolution suggestion
        """
        return RESOLUTION_TEMPLATES.get(error_type, RESOLUTION_TEMPLATES["unknown"])

    def build_catalog(self) -> ErrorCatalog:
        """Build the complete error catalog.

        Returns:
            ErrorCatalog with all recorded errors
        """
        catalog = ErrorCatalog(
            discovered_at=datetime.now(UTC).isoformat(),
        )

        for endpoint_key, errors in self._errors.items():
            method, endpoint = endpoint_key.split(":", 1)

            endpoint_errors = EndpointErrors(
                endpoint=endpoint,
                method=method,
                errors=list(errors.values()),
                total_errors=sum(e.frequency for e in errors.values()),
            )

            catalog.errors_by_endpoint[endpoint] = endpoint_errors

            for entry in errors.values():
                catalog.errors_by_status[entry.status_code].append(entry)
                catalog.total_errors += entry.frequency

        catalog.unique_patterns = sum(len(errors) for errors in self._errors.values())

        return catalog

    def get_errors_for_endpoint(
        self,
        endpoint: str,
        method: str = "GET",
    ) -> list[ErrorEntry]:
        """Get recorded errors for a specific endpoint.

        Args:
            endpoint: API endpoint path
            method: HTTP method

        Returns:
            List of error entries for the endpoint
        """
        endpoint_key = f"{method}:{endpoint}"
        return list(self._errors.get(endpoint_key, {}).values())

    def get_errors_by_status(self, status_code: int) -> list[ErrorEntry]:
        """Get all errors with a specific status code.

        Args:
            status_code: HTTP status code

        Returns:
            List of error entries with that status code
        """
        result: list[ErrorEntry] = []
        for errors in self._errors.values():
            for entry in errors.values():
                if entry.status_code == status_code:
                    result.append(entry)
        return result

    def clear(self) -> None:
        """Clear all recorded errors."""
        self._errors.clear()
        self._error_counts.clear()
        self.stats = BuilderStats()

    def get_stats(self) -> dict[str, Any]:
        """Get builder statistics.

        Returns:
            Dictionary with builder metrics
        """
        return self.stats.to_dict()


__all__ = [
    "BuilderStats",
    "EndpointErrors",
    "ErrorCatalog",
    "ErrorCatalogBuilder",
    "ErrorCatalogConfig",
    "ErrorEntry",
]
