# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Rate limit discoverer for API endpoints.

Safely probes API endpoints to characterize rate limiting behavior.
This is an opt-in feature that should be used carefully to avoid
disrupting production systems.

Usage:
    discoverer = RateLimitDiscoverer(config)
    limits = await discoverer.discover_limits(endpoint, http_client)
    extension = limits.to_extension()
"""

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class RateLimitDiscoveryConfig:
    """Configuration for rate limit discovery."""

    enabled: bool = False  # Opt-in only
    max_probe_rate: int = 20  # Max requests per probe session
    probe_delay_seconds: float = 0.1  # Delay between probe requests
    cooldown_seconds: float = 60.0  # Wait after hitting limit
    safe_mode: bool = True  # Stop at first 429, don't probe further


@dataclass
class RateLimitInfo:
    """Discovered rate limit information for an endpoint."""

    endpoint: str = ""
    method: str = "GET"
    requests_per_minute: int | None = None
    requests_per_second: float | None = None
    burst_limit: int | None = None
    retry_after_header: bool = False
    retry_after_value: float | None = None
    rate_limit_header: str | None = None
    discovered_at: str | None = None
    confidence: str = "unknown"  # low, medium, high

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result: dict[str, Any] = {
            "endpoint": self.endpoint,
            "method": self.method,
            "confidence": self.confidence,
        }
        if self.discovered_at:
            result["discovered_at"] = self.discovered_at
        if self.requests_per_minute is not None:
            result["requests_per_minute"] = self.requests_per_minute
        if self.requests_per_second is not None:
            result["requests_per_second"] = self.requests_per_second
        if self.burst_limit is not None:
            result["burst_limit"] = self.burst_limit
        if self.retry_after_header:
            result["retry_after_header"] = True
            if self.retry_after_value is not None:
                result["retry_after_value"] = self.retry_after_value
        if self.rate_limit_header:
            result["rate_limit_header"] = self.rate_limit_header
        return result

    def to_extension(self) -> dict[str, Any]:
        """Convert to OpenAPI extension format (x-f5xc-discovered-rate-limits)."""
        result: dict[str, Any] = {}
        if self.discovered_at:
            result["discovered_at"] = self.discovered_at
        if self.requests_per_minute is not None:
            result["requests_per_minute"] = self.requests_per_minute
        if self.requests_per_second is not None:
            result["requests_per_second"] = self.requests_per_second
        if self.burst_limit is not None:
            result["burst_limit"] = self.burst_limit
        if self.retry_after_header:
            result["retry_after_header"] = True
        return result


@dataclass
class DiscovererStats:
    """Statistics for rate limit discoverer."""

    endpoints_probed: int = 0
    requests_made: int = 0
    rate_limits_hit: int = 0
    limits_discovered: int = 0
    skipped_disabled: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "endpoints_probed": self.endpoints_probed,
            "requests_made": self.requests_made,
            "rate_limits_hit": self.rate_limits_hit,
            "limits_discovered": self.limits_discovered,
            "skipped_disabled": self.skipped_disabled,
        }


@dataclass
class ProbeResult:
    """Result of a single probe request."""

    status_code: int
    response_time_ms: float
    retry_after: float | None = None
    rate_limit_remaining: int | None = None
    rate_limit_limit: int | None = None
    rate_limit_reset: float | None = None
    headers: dict[str, str] = field(default_factory=dict)


class RateLimitDiscoverer:
    """Discovers rate limiting behavior for API endpoints.

    CAUTION: This feature should be used carefully as it intentionally
    makes many requests to probe rate limits. Only enable when:
    - You have permission to probe the API
    - The environment can handle increased load
    - You understand the potential impact

    Provides:
    - Safe probing with configurable limits
    - Rate limit header detection
    - Retry-After header parsing
    - Confidence-based limit estimation

    Attributes:
        config: Discovery configuration
        stats: Discovery statistics
    """

    def __init__(self, config: RateLimitDiscoveryConfig | dict | None = None) -> None:
        """Initialize rate limit discoverer.

        Args:
            config: Discovery configuration
        """
        if config is None:
            self.config = RateLimitDiscoveryConfig()
        elif isinstance(config, dict):
            self.config = RateLimitDiscoveryConfig(
                enabled=config.get("enabled", False),
                max_probe_rate=config.get("max_probe_rate", 20),
                probe_delay_seconds=config.get("probe_delay_seconds", 0.1),
                cooldown_seconds=config.get("cooldown_seconds", 60.0),
                safe_mode=config.get("safe_mode", True),
            )
        else:
            self.config = config

        self.stats = DiscovererStats()

    async def discover_limits(
        self,
        endpoint: str,
        request_func: Callable[[], Coroutine[Any, Any, ProbeResult]],
        method: str = "GET",
    ) -> RateLimitInfo:
        """Discover rate limits for an endpoint.

        Args:
            endpoint: API endpoint path
            request_func: Async function that makes a request and returns ProbeResult
            method: HTTP method

        Returns:
            RateLimitInfo with discovered limits
        """
        if not self.config.enabled:
            self.stats.skipped_disabled += 1
            return RateLimitInfo(
                endpoint=endpoint,
                method=method,
                discovered_at=datetime.now(UTC).isoformat(),
                confidence="unknown",
            )

        self.stats.endpoints_probed += 1

        # Start with a baseline probe
        results: list[ProbeResult] = []
        rate_limited = False

        for _ in range(self.config.max_probe_rate):
            try:
                result = await request_func()
                results.append(result)
                self.stats.requests_made += 1

                # Check for rate limit response
                if result.status_code == 429:
                    self.stats.rate_limits_hit += 1
                    rate_limited = True

                    if self.config.safe_mode:
                        break

            except Exception:
                break

            await asyncio.sleep(self.config.probe_delay_seconds)

        return self._analyze_results(results, endpoint, method, rate_limited)

    def _analyze_results(
        self,
        results: list[ProbeResult],
        endpoint: str,
        method: str,
        rate_limited: bool,
    ) -> RateLimitInfo:
        """Analyze probe results to determine rate limits.

        Args:
            results: List of probe results
            endpoint: API endpoint
            method: HTTP method
            rate_limited: Whether a 429 was encountered

        Returns:
            RateLimitInfo with analysis
        """
        info = RateLimitInfo(
            endpoint=endpoint,
            method=method,
            discovered_at=datetime.now(UTC).isoformat(),
        )

        if not results:
            info.confidence = "unknown"
            return info

        # Check for rate limit headers in any response
        for result in results:
            if result.retry_after is not None:
                info.retry_after_header = True
                info.retry_after_value = result.retry_after

            if result.rate_limit_limit is not None:
                info.requests_per_minute = result.rate_limit_limit
                info.rate_limit_header = "X-RateLimit-Limit"

            if result.rate_limit_remaining is not None:
                # Estimate burst limit from remaining
                if info.burst_limit is None:
                    info.burst_limit = result.rate_limit_remaining

        # Estimate rate from request count if we hit a limit
        if rate_limited:
            self.stats.limits_discovered += 1
            success_count = sum(1 for r in results if r.status_code != 429)

            # Estimate based on time window
            if success_count > 0:
                info.burst_limit = success_count
                info.confidence = "medium"
        elif len(results) == self.config.max_probe_rate:
            # Didn't hit limit - rate is higher than our probe
            info.confidence = "low"
        else:
            info.confidence = "unknown"

        # Calculate requests per second if we have timing data
        if len(results) >= 2:
            total_time = sum(r.response_time_ms for r in results) / 1000
            if total_time > 0:
                info.requests_per_second = round(len(results) / total_time, 2)

        return info

    async def discover_from_headers(
        self,
        endpoint: str,
        request_func: Callable[[], Coroutine[Any, Any, ProbeResult]],
        method: str = "GET",
    ) -> RateLimitInfo:
        """Discover rate limits by analyzing response headers only.

        This is a safer alternative that only makes a single request
        and examines headers for rate limit information.

        Args:
            endpoint: API endpoint path
            request_func: Async function that makes a request
            method: HTTP method

        Returns:
            RateLimitInfo from header analysis
        """
        self.stats.endpoints_probed += 1

        info = RateLimitInfo(
            endpoint=endpoint,
            method=method,
            discovered_at=datetime.now(UTC).isoformat(),
        )

        try:
            result = await request_func()
            self.stats.requests_made += 1

            # Parse common rate limit headers
            headers = {k.lower(): v for k, v in result.headers.items()}

            # X-RateLimit-Limit
            if "x-ratelimit-limit" in headers:
                try:
                    info.requests_per_minute = int(headers["x-ratelimit-limit"])
                    info.rate_limit_header = "X-RateLimit-Limit"
                    info.confidence = "high"
                except ValueError:
                    pass

            # X-RateLimit-Remaining - use limit as burst limit if available
            if "x-ratelimit-remaining" in headers and info.requests_per_minute:
                with contextlib.suppress(ValueError):
                    int(headers["x-ratelimit-remaining"])  # Validate it's an int
                    info.burst_limit = info.requests_per_minute

            # Retry-After
            if "retry-after" in headers:
                info.retry_after_header = True
                with contextlib.suppress(ValueError):
                    info.retry_after_value = float(headers["retry-after"])

            if result.status_code == 429:
                self.stats.rate_limits_hit += 1
                self.stats.limits_discovered += 1

        except Exception:
            info.confidence = "unknown"

        return info

    def is_enabled(self) -> bool:
        """Check if rate limit discovery is enabled.

        Returns:
            True if discovery is enabled
        """
        return self.config.enabled

    def get_stats(self) -> dict[str, Any]:
        """Get discovery statistics.

        Returns:
            Dictionary with discovery metrics
        """
        return self.stats.to_dict()


def parse_rate_limit_headers(headers: dict[str, str]) -> dict[str, Any]:
    """Parse rate limit information from HTTP headers.

    Convenience function for parsing rate limit headers from
    a response without full discovery.

    Args:
        headers: HTTP response headers

    Returns:
        Dictionary with parsed rate limit info
    """
    result: dict[str, Any] = {}
    headers_lower = {k.lower(): v for k, v in headers.items()}

    # Common rate limit headers
    if "x-ratelimit-limit" in headers_lower:
        with contextlib.suppress(ValueError):
            result["limit"] = int(headers_lower["x-ratelimit-limit"])

    if "x-ratelimit-remaining" in headers_lower:
        with contextlib.suppress(ValueError):
            result["remaining"] = int(headers_lower["x-ratelimit-remaining"])

    if "x-ratelimit-reset" in headers_lower:
        with contextlib.suppress(ValueError):
            result["reset"] = int(headers_lower["x-ratelimit-reset"])

    if "retry-after" in headers_lower:
        try:
            result["retry_after"] = float(headers_lower["retry-after"])
        except ValueError:
            result["retry_after"] = headers_lower["retry-after"]

    return result


__all__ = [
    "DiscovererStats",
    "ProbeResult",
    "RateLimitDiscoverer",
    "RateLimitDiscoveryConfig",
    "RateLimitInfo",
    "parse_rate_limit_headers",
]
