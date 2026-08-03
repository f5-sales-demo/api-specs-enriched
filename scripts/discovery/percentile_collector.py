# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Response time percentile collector for API discovery.

Collects multiple response time samples and calculates statistical
percentiles (p50, p95, p99) for API endpoints.

Usage:
    collector = PercentileCollector(sample_count=5)
    stats = await collector.collect_samples(endpoint, http_client)
    percentiles = stats.to_extension()
"""

import asyncio
import statistics
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class PercentileConfig:
    """Configuration for percentile collection."""

    sample_count: int = 5
    percentiles: list[int] = field(default_factory=lambda: [50, 95, 99])
    timeout_seconds: float = 30.0
    delay_between_samples: float = 0.5
    discard_outliers: bool = True
    outlier_threshold: float = 3.0  # Standard deviations


@dataclass
class ResponseTimeStats:
    """Response time statistics from sample collection."""

    samples: list[float] = field(default_factory=list)
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    mean_ms: float = 0.0
    stdev_ms: float = 0.0
    sample_count: int = 0
    last_measured: str | None = None
    endpoint: str = ""
    method: str = "GET"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result: dict[str, Any] = {
            "samples": self.samples,
            "p50": round(self.p50, 2),
            "p95": round(self.p95, 2),
            "p99": round(self.p99, 2),
            "min_ms": round(self.min_ms, 2),
            "max_ms": round(self.max_ms, 2),
            "mean_ms": round(self.mean_ms, 2),
            "stdev_ms": round(self.stdev_ms, 2),
            "sample_count": self.sample_count,
            "endpoint": self.endpoint,
            "method": self.method,
        }
        if self.last_measured:
            result["last_measured"] = self.last_measured
        return result

    def to_extension(self) -> dict[str, Any]:
        """Convert to OpenAPI extension format (x-f5xc-discovered-response-time)."""
        result: dict[str, Any] = {
            "p50": round(self.p50, 2),
            "p95": round(self.p95, 2),
            "p99": round(self.p99, 2),
            "sample_count": self.sample_count,
        }
        if self.last_measured:
            result["last_measured"] = self.last_measured
        return result


@dataclass
class CollectorStats:
    """Statistics for percentile collector."""

    endpoints_measured: int = 0
    total_samples: int = 0
    failed_samples: int = 0
    outliers_discarded: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "endpoints_measured": self.endpoints_measured,
            "total_samples": self.total_samples,
            "failed_samples": self.failed_samples,
            "outliers_discarded": self.outliers_discarded,
        }


class PercentileCollector:
    """Collects response time samples and calculates percentiles.

    Provides:
    - Multiple sample collection per endpoint
    - Percentile calculation (p50, p95, p99)
    - Outlier detection and removal
    - Statistical summary (mean, stdev, min, max)

    Attributes:
        config: Configuration for sample collection
        stats: Collection statistics
    """

    def __init__(self, config: PercentileConfig | dict | None = None) -> None:
        """Initialize percentile collector.

        Args:
            config: Collection configuration (PercentileConfig, dict, or None for defaults)
        """
        if config is None:
            self.config = PercentileConfig()
        elif isinstance(config, dict):
            self.config = PercentileConfig(
                sample_count=config.get("sample_count", 5),
                percentiles=config.get("percentiles", [50, 95, 99]),
                timeout_seconds=config.get("timeout_seconds", 30.0),
                delay_between_samples=config.get("delay_between_samples", 0.5),
                discard_outliers=config.get("discard_outliers", True),
                outlier_threshold=config.get("outlier_threshold", 3.0),
            )
        else:
            self.config = config

        self.stats = CollectorStats()

    async def collect_samples(
        self,
        endpoint: str,
        request_func: Callable[[], Coroutine[Any, Any, tuple[int, float]]],
        method: str = "GET",
    ) -> ResponseTimeStats:
        """Collect response time samples for an endpoint.

        Args:
            endpoint: API endpoint path
            request_func: Async function that makes the request and returns (status_code, response_time_ms)
            method: HTTP method used

        Returns:
            ResponseTimeStats with calculated percentiles
        """
        samples: list[float] = []
        failed = 0

        for i in range(self.config.sample_count):
            try:
                _, response_time = await asyncio.wait_for(
                    request_func(),
                    timeout=self.config.timeout_seconds,
                )
                samples.append(response_time)
                self.stats.total_samples += 1
            except TimeoutError:
                failed += 1
                self.stats.failed_samples += 1
            except Exception:
                failed += 1
                self.stats.failed_samples += 1

            # Delay between samples (except after last)
            if i < self.config.sample_count - 1:
                await asyncio.sleep(self.config.delay_between_samples)

        self.stats.endpoints_measured += 1

        if not samples:
            return ResponseTimeStats(
                endpoint=endpoint,
                method=method,
                last_measured=datetime.now(UTC).isoformat(),
            )

        # Discard outliers if enabled
        if self.config.discard_outliers and len(samples) > 2:
            samples, discarded = self._remove_outliers(samples)
            self.stats.outliers_discarded += discarded

        return self._calculate_stats(samples, endpoint, method)

    def _remove_outliers(self, samples: list[float]) -> tuple[list[float], int]:
        """Remove statistical outliers from samples.

        Args:
            samples: List of response times in ms

        Returns:
            Tuple of (filtered samples, count of discarded)
        """
        if len(samples) < 3:
            return samples, 0

        mean = statistics.mean(samples)
        stdev = statistics.stdev(samples)

        if stdev == 0:
            return samples, 0

        threshold = self.config.outlier_threshold * stdev
        filtered = [s for s in samples if abs(s - mean) <= threshold]

        return filtered, len(samples) - len(filtered)

    def _calculate_stats(
        self,
        samples: list[float],
        endpoint: str,
        method: str,
    ) -> ResponseTimeStats:
        """Calculate statistics from samples.

        Args:
            samples: List of response times in ms
            endpoint: API endpoint path
            method: HTTP method

        Returns:
            ResponseTimeStats with all calculated metrics
        """
        sorted_samples = sorted(samples)
        n = len(sorted_samples)

        # Calculate percentiles
        p50 = self._percentile(sorted_samples, 50)
        p95 = self._percentile(sorted_samples, 95)
        p99 = self._percentile(sorted_samples, 99)

        # Calculate other stats
        mean_ms = statistics.mean(samples)
        stdev_ms = statistics.stdev(samples) if n > 1 else 0.0

        return ResponseTimeStats(
            samples=[round(s, 2) for s in samples],
            p50=p50,
            p95=p95,
            p99=p99,
            min_ms=min(samples),
            max_ms=max(samples),
            mean_ms=mean_ms,
            stdev_ms=stdev_ms,
            sample_count=n,
            last_measured=datetime.now(UTC).isoformat(),
            endpoint=endpoint,
            method=method,
        )

    def _percentile(self, sorted_data: list[float], percentile: int) -> float:
        """Calculate a specific percentile from sorted data.

        Uses linear interpolation method.

        Args:
            sorted_data: Sorted list of values
            percentile: Percentile to calculate (0-100)

        Returns:
            Percentile value
        """
        if not sorted_data:
            return 0.0

        n = len(sorted_data)
        if n == 1:
            return sorted_data[0]

        # Calculate position using linear interpolation
        pos = (percentile / 100) * (n - 1)
        lower = int(pos)
        upper = lower + 1

        if upper >= n:
            return sorted_data[-1]

        # Interpolate between values
        weight = pos - lower
        return sorted_data[lower] * (1 - weight) + sorted_data[upper] * weight

    def get_stats(self) -> dict[str, Any]:
        """Get collector statistics.

        Returns:
            Dictionary with collection metrics
        """
        return self.stats.to_dict()


def calculate_percentiles_from_samples(
    samples: list[float],
    percentiles: list[int] | None = None,
) -> dict[str, float]:
    """Calculate percentiles from a list of samples.

    Convenience function for calculating percentiles without
    collecting new samples.

    Args:
        samples: List of response times in ms
        percentiles: Percentiles to calculate (default: [50, 95, 99])

    Returns:
        Dictionary mapping percentile name to value
    """
    if percentiles is None:
        percentiles = [50, 95, 99]

    if not samples:
        return {f"p{p}": 0.0 for p in percentiles}

    sorted_samples = sorted(samples)
    n = len(sorted_samples)

    result: dict[str, float] = {}
    for p in percentiles:
        if n == 1:
            result[f"p{p}"] = sorted_samples[0]
        else:
            pos = (p / 100) * (n - 1)
            lower = int(pos)
            upper = min(lower + 1, n - 1)
            weight = pos - lower
            value = sorted_samples[lower] * (1 - weight) + sorted_samples[upper] * weight
            result[f"p{p}"] = round(value, 2)

    return result


__all__ = [
    "CollectorStats",
    "PercentileCollector",
    "PercentileConfig",
    "ResponseTimeStats",
    "calculate_percentiles_from_samples",
]
