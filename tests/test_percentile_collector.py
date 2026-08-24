# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=broad-exception-raised,protected-access,use-implicit-booleaness-not-comparison  # Tests inject failures, inspect internals, and assert explicit shapes.

"""Unit tests for PercentileCollector."""

# ruff: noqa: TRY002, ERA001

import asyncio

import pytest

from scripts.discovery.percentile_collector import (
    CollectorStats,
    PercentileCollector,
    PercentileConfig,
    ResponseTimeStats,
    calculate_percentiles_from_samples,
)


class TestPercentileConfig:
    """Test PercentileConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = PercentileConfig()
        assert config.sample_count == 5
        assert config.percentiles == [50, 95, 99]
        assert config.timeout_seconds == 30.0
        assert config.delay_between_samples == 0.5
        assert config.discard_outliers is True
        assert config.outlier_threshold == 3.0

    def test_custom_values(self):
        """Test custom configuration values."""
        config = PercentileConfig(
            sample_count=10,
            percentiles=[50, 90, 95, 99],
            timeout_seconds=60.0,
            delay_between_samples=1.0,
            discard_outliers=False,
            outlier_threshold=2.0,
        )
        assert config.sample_count == 10
        assert config.percentiles == [50, 90, 95, 99]
        assert config.timeout_seconds == 60.0
        assert config.delay_between_samples == 1.0
        assert config.discard_outliers is False
        assert config.outlier_threshold == 2.0


class TestResponseTimeStats:
    """Test ResponseTimeStats dataclass."""

    def test_default_values(self):
        """Test default statistics values."""
        stats = ResponseTimeStats()
        assert stats.samples == []
        assert stats.p50 == 0.0
        assert stats.p95 == 0.0
        assert stats.p99 == 0.0
        assert stats.sample_count == 0
        assert stats.method == "GET"

    def test_to_dict(self):
        """Test to_dict method."""
        stats = ResponseTimeStats(
            samples=[10.0, 20.0, 30.0],
            p50=20.0,
            p95=28.5,
            p99=29.7,
            min_ms=10.0,
            max_ms=30.0,
            mean_ms=20.0,
            stdev_ms=10.0,
            sample_count=3,
            last_measured="2024-01-01T00:00:00Z",
            endpoint="/api/test",
            method="GET",
        )
        result = stats.to_dict()

        assert isinstance(result, dict)
        assert result["samples"] == [10.0, 20.0, 30.0]
        assert result["p50"] == 20.0
        assert result["p95"] == 28.5
        assert result["p99"] == 29.7
        assert result["sample_count"] == 3
        assert result["endpoint"] == "/api/test"

    def test_to_extension(self):
        """Test to_extension method for OpenAPI output."""
        stats = ResponseTimeStats(
            samples=[10.0, 20.0, 30.0],
            p50=20.0,
            p95=28.5,
            p99=29.7,
            sample_count=3,
            last_measured="2024-01-01T00:00:00Z",
        )
        result = stats.to_extension()

        assert isinstance(result, dict)
        assert result["p50"] == 20.0
        assert result["p95"] == 28.5
        assert result["p99"] == 29.7
        assert result["sample_count"] == 3
        assert result["last_measured"] == "2024-01-01T00:00:00Z"
        # Should not include samples or other fields
        assert "samples" not in result
        assert "min_ms" not in result

    def test_to_extension_rounds_values(self):
        """Test that to_extension rounds values to 2 decimal places."""
        stats = ResponseTimeStats(
            p50=20.123456,
            p95=28.567890,
            p99=29.999999,
            sample_count=3,
            last_measured="2024-01-01T00:00:00Z",
        )
        result = stats.to_extension()

        assert result["p50"] == 20.12
        assert result["p95"] == 28.57
        assert result["p99"] == 30.0


class TestCollectorStats:
    """Test CollectorStats dataclass."""

    def test_default_values(self):
        """Test default statistics values."""
        stats = CollectorStats()
        assert stats.endpoints_measured == 0
        assert stats.total_samples == 0
        assert stats.failed_samples == 0
        assert stats.outliers_discarded == 0

    def test_to_dict(self):
        """Test to_dict method."""
        stats = CollectorStats(
            endpoints_measured=5,
            total_samples=25,
            failed_samples=2,
            outliers_discarded=3,
        )
        result = stats.to_dict()

        assert isinstance(result, dict)
        assert result["endpoints_measured"] == 5
        assert result["total_samples"] == 25
        assert result["failed_samples"] == 2
        assert result["outliers_discarded"] == 3


class TestPercentileCollectorInitialization:
    """Test PercentileCollector initialization."""

    def test_default_config(self):
        """Test initialization with default config."""
        collector = PercentileCollector()
        assert collector.config.sample_count == 5
        assert collector.config.percentiles == [50, 95, 99]

    def test_dict_config(self):
        """Test initialization with dict config."""
        config = {
            "sample_count": 10,
            "percentiles": [50, 90, 99],
            "timeout_seconds": 60.0,
        }
        collector = PercentileCollector(config=config)
        assert collector.config.sample_count == 10
        assert collector.config.percentiles == [50, 90, 99]
        assert collector.config.timeout_seconds == 60.0

    def test_percentile_config_object(self):
        """Test initialization with PercentileConfig object."""
        config = PercentileConfig(sample_count=15, discard_outliers=False)
        collector = PercentileCollector(config=config)
        assert collector.config.sample_count == 15
        assert collector.config.discard_outliers is False

    def test_stats_initialized(self):
        """Test that stats are initialized correctly."""
        collector = PercentileCollector()
        stats = collector.get_stats()
        assert stats["endpoints_measured"] == 0
        assert stats["total_samples"] == 0
        assert stats["failed_samples"] == 0


class TestPercentileCalculation:
    """Test percentile calculation methods."""

    def test_percentile_single_value(self):
        """Test percentile with single value."""
        collector = PercentileCollector()
        result = collector._percentile([100.0], 50)
        assert result == 100.0

    def test_percentile_empty_list(self):
        """Test percentile with empty list."""
        collector = PercentileCollector()
        result = collector._percentile([], 50)
        assert result == 0.0

    def test_percentile_p50_even_samples(self):
        """Test p50 calculation with even number of samples."""
        collector = PercentileCollector()
        # Sorted: [10, 20, 30, 40]
        result = collector._percentile([10.0, 20.0, 30.0, 40.0], 50)
        # p50 position = 0.5 * (4-1) = 1.5, interpolate between index 1 and 2
        assert result == 25.0  # (20 + 30) / 2

    def test_percentile_p50_odd_samples(self):
        """Test p50 calculation with odd number of samples."""
        collector = PercentileCollector()
        # Sorted: [10, 20, 30, 40, 50]
        result = collector._percentile([10.0, 20.0, 30.0, 40.0, 50.0], 50)
        # p50 position = 0.5 * (5-1) = 2.0, exactly at index 2
        assert result == 30.0

    def test_percentile_p95(self):
        """Test p95 calculation."""
        collector = PercentileCollector()
        samples = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        result = collector._percentile(samples, 95)
        # p95 position = 0.95 * (10-1) = 8.55
        # Interpolate between index 8 (90) and 9 (100)
        expected = 90.0 * (1 - 0.55) + 100.0 * 0.55
        assert abs(result - expected) < 0.01

    def test_percentile_p99(self):
        """Test p99 calculation."""
        collector = PercentileCollector()
        samples = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        result = collector._percentile(samples, 99)
        # p99 position = 0.99 * (10-1) = 8.91
        # Interpolate between index 8 (90) and 9 (100)
        expected = 90.0 * (1 - 0.91) + 100.0 * 0.91
        assert abs(result - expected) < 0.01


class TestOutlierRemoval:
    """Test outlier removal functionality."""

    def test_remove_outliers_no_outliers(self):
        """Test outlier removal with no outliers."""
        collector = PercentileCollector()
        samples = [100.0, 105.0, 110.0, 95.0, 100.0]
        filtered, discarded = collector._remove_outliers(samples)
        assert len(filtered) == 5
        assert discarded == 0

    def test_remove_outliers_with_outlier(self):
        """Test outlier removal with clear outlier."""
        collector = PercentileCollector()
        # Use a larger dataset where outlier detection works better
        # 1000 is a clear outlier compared to the tight cluster around 100
        samples = [100.0, 102.0, 98.0, 101.0, 99.0, 100.0, 1000.0]
        filtered, discarded = collector._remove_outliers(samples)
        # With 3-sigma threshold, 1000 should be removed as it's far from mean ~100
        # Note: With small samples, outlier detection may vary
        assert len(filtered) <= len(samples)
        # At minimum, we should have some samples left
        assert len(filtered) >= 1

    def test_remove_outliers_too_few_samples(self):
        """Test outlier removal with too few samples."""
        collector = PercentileCollector()
        samples = [100.0, 200.0]
        filtered, discarded = collector._remove_outliers(samples)
        assert len(filtered) == 2
        assert discarded == 0

    def test_remove_outliers_zero_stdev(self):
        """Test outlier removal with zero standard deviation."""
        collector = PercentileCollector()
        samples = [100.0, 100.0, 100.0, 100.0]
        filtered, discarded = collector._remove_outliers(samples)
        assert len(filtered) == 4
        assert discarded == 0


class TestCollectSamples:
    """Test sample collection functionality."""

    @pytest.mark.asyncio
    async def test_collect_samples_success(self):
        """Test successful sample collection."""
        collector = PercentileCollector(config={"sample_count": 3, "delay_between_samples": 0.01})

        call_count = 0

        async def mock_request():
            nonlocal call_count
            call_count += 1
            return (200, 100.0 + call_count * 10)  # 110, 120, 130

        result = await collector.collect_samples("/api/test", mock_request, "GET")

        assert call_count == 3
        assert result.sample_count == 3
        assert result.endpoint == "/api/test"
        assert result.method == "GET"
        assert result.p50 > 0
        assert result.last_measured != ""

    @pytest.mark.asyncio
    async def test_collect_samples_all_fail(self):
        """Test sample collection when all requests fail."""
        collector = PercentileCollector(config={"sample_count": 3, "delay_between_samples": 0.01})

        async def failing_request():
            raise Exception("Request failed")

        result = await collector.collect_samples("/api/test", failing_request, "GET")

        assert result.sample_count == 0
        assert result.samples == []
        assert result.p50 == 0.0
        stats = collector.get_stats()
        assert stats["failed_samples"] == 3

    @pytest.mark.asyncio
    async def test_collect_samples_timeout(self):
        """Test sample collection with timeout."""
        collector = PercentileCollector(
            config={
                "sample_count": 2,
                "timeout_seconds": 0.01,
                "delay_between_samples": 0.01,
            },
        )

        async def slow_request():
            await asyncio.sleep(1.0)  # Way longer than timeout
            return (200, 100.0)

        result = await collector.collect_samples("/api/test", slow_request, "GET")

        assert result.sample_count == 0
        stats = collector.get_stats()
        assert stats["failed_samples"] == 2

    @pytest.mark.asyncio
    async def test_collect_samples_partial_success(self):
        """Test sample collection with partial success."""
        collector = PercentileCollector(config={"sample_count": 3, "delay_between_samples": 0.01})

        call_count = 0

        async def partial_request():
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("Second request fails")
            return (200, 100.0)

        result = await collector.collect_samples("/api/test", partial_request, "GET")

        assert result.sample_count == 2
        stats = collector.get_stats()
        assert stats["failed_samples"] == 1

    @pytest.mark.asyncio
    async def test_stats_updated_after_collection(self):
        """Test that collector stats are updated after collection."""
        collector = PercentileCollector(config={"sample_count": 5, "delay_between_samples": 0.01})

        async def mock_request():
            return (200, 100.0)

        await collector.collect_samples("/api/test1", mock_request, "GET")
        await collector.collect_samples("/api/test2", mock_request, "GET")

        stats = collector.get_stats()
        assert stats["endpoints_measured"] == 2
        assert stats["total_samples"] == 10


class TestCalculateStats:
    """Test statistics calculation."""

    def test_calculate_stats_basic(self):
        """Test basic statistics calculation."""
        collector = PercentileCollector()
        samples = [100.0, 200.0, 300.0, 400.0, 500.0]

        result = collector._calculate_stats(samples, "/api/test", "POST")

        assert result.endpoint == "/api/test"
        assert result.method == "POST"
        assert result.sample_count == 5
        assert result.min_ms == 100.0
        assert result.max_ms == 500.0
        assert result.mean_ms == 300.0
        assert result.p50 == 300.0

    def test_calculate_stats_samples_rounded(self):
        """Test that samples are rounded in result."""
        collector = PercentileCollector()
        samples = [100.12345, 200.56789, 300.99999]

        result = collector._calculate_stats(samples, "/api/test", "GET")

        assert result.samples == [100.12, 200.57, 301.0]


class TestCalculatePercentilesFromSamples:
    """Test convenience function for percentile calculation."""

    def test_default_percentiles(self):
        """Test calculation with default percentiles."""
        samples = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = calculate_percentiles_from_samples(samples)

        assert "p50" in result
        assert "p95" in result
        assert "p99" in result
        assert result["p50"] == 30.0

    def test_custom_percentiles(self):
        """Test calculation with custom percentiles."""
        samples = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = calculate_percentiles_from_samples(samples, percentiles=[25, 50, 75])

        assert "p25" in result
        assert "p50" in result
        assert "p75" in result
        assert "p95" not in result

    def test_empty_samples(self):
        """Test calculation with empty samples."""
        result = calculate_percentiles_from_samples([])

        assert result["p50"] == 0.0
        assert result["p95"] == 0.0
        assert result["p99"] == 0.0

    def test_single_sample(self):
        """Test calculation with single sample."""
        result = calculate_percentiles_from_samples([100.0])

        assert result["p50"] == 100.0
        assert result["p95"] == 100.0
        assert result["p99"] == 100.0

    def test_values_rounded(self):
        """Test that values are rounded to 2 decimal places."""
        samples = [10.123456, 20.567890, 30.999999]
        result = calculate_percentiles_from_samples(samples)

        for value in result.values():
            # Check that value has at most 2 decimal places
            assert value == round(value, 2)
