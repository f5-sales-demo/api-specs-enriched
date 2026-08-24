# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=protected-access  # Tests intentionally verify deterministic internal helpers.

"""Unit tests for RateLimiter."""

import asyncio
import time

import pytest

from scripts.discovery.rate_limiter import RateLimitConfig, RateLimiter, RateLimiterStats


class TestRateLimitConfig:
    """Test RateLimitConfig dataclass."""

    def test_defaults(self):
        """Test default configuration values."""
        config = RateLimitConfig()
        assert config.requests_per_second == 5.0
        assert config.burst_limit == 10
        assert config.backoff_base == 1.0
        assert config.backoff_max == 60.0
        assert config.backoff_multiplier == 2.0
        assert config.retry_attempts == 3

    def test_custom_values(self):
        """Test custom configuration values."""
        config = RateLimitConfig(
            requests_per_second=10.0,
            burst_limit=20,
            backoff_base=0.5,
            backoff_max=30.0,
            backoff_multiplier=1.5,
            retry_attempts=5,
        )
        assert config.requests_per_second == 10.0
        assert config.burst_limit == 20
        assert config.backoff_base == 0.5
        assert config.backoff_max == 30.0
        assert config.backoff_multiplier == 1.5
        assert config.retry_attempts == 5


class TestRateLimiterStats:
    """Test RateLimiterStats dataclass."""

    def test_defaults(self):
        """Test default statistics values."""
        stats = RateLimiterStats()
        assert stats.requests_made == 0
        assert stats.requests_delayed == 0
        assert stats.total_wait_time == 0.0
        assert stats.rate_limit_hits == 0
        assert stats.retries == 0

    def test_custom_values(self):
        """Test statistics with custom values."""
        stats = RateLimiterStats(
            requests_made=100,
            requests_delayed=10,
            total_wait_time=5.5,
            rate_limit_hits=2,
            retries=3,
        )
        assert stats.requests_made == 100
        assert stats.requests_delayed == 10
        assert stats.total_wait_time == 5.5
        assert stats.rate_limit_hits == 2
        assert stats.retries == 3


class TestRateLimiterInitialization:
    """Test RateLimiter initialization."""

    def test_init_with_none(self):
        """Test initialization with None uses defaults."""
        limiter = RateLimiter(None)
        assert limiter.config.requests_per_second == 5.0
        assert limiter.config.burst_limit == 10

    def test_init_with_config(self):
        """Test initialization with RateLimitConfig."""
        config = RateLimitConfig(requests_per_second=20.0, burst_limit=5)
        limiter = RateLimiter(config)
        assert limiter.config.requests_per_second == 20.0
        assert limiter.config.burst_limit == 5

    def test_init_with_dict(self):
        """Test initialization with dictionary."""
        config_dict = {
            "requests_per_second": 15.0,
            "burst_limit": 8,
            "backoff_base": 2.0,
        }
        limiter = RateLimiter(config_dict)
        assert limiter.config.requests_per_second == 15.0
        assert limiter.config.burst_limit == 8
        assert limiter.config.backoff_base == 2.0

    def test_init_with_partial_dict(self):
        """Test initialization with partial dictionary uses defaults for missing."""
        config_dict = {"requests_per_second": 10.0}
        limiter = RateLimiter(config_dict)
        assert limiter.config.requests_per_second == 10.0
        assert limiter.config.burst_limit == 10  # Default
        assert limiter.config.backoff_base == 1.0  # Default

    def test_init_stats_zeroed(self):
        """Test that stats are initialized to zero."""
        limiter = RateLimiter()
        assert limiter.stats.requests_made == 0
        assert limiter.stats.requests_delayed == 0
        assert limiter.stats.total_wait_time == 0.0

    def test_init_tokens_equal_burst_limit(self):
        """Test that initial tokens equal burst limit."""
        config = RateLimitConfig(burst_limit=15)
        limiter = RateLimiter(config)
        assert limiter._tokens == 15.0

    def test_init_backoff_equals_base(self):
        """Test that initial backoff equals backoff_base."""
        config = RateLimitConfig(backoff_base=3.0)
        limiter = RateLimiter(config)
        assert limiter._current_backoff == 3.0


class TestTokenBucketMechanism:
    """Test token bucket algorithm."""

    @pytest.mark.asyncio
    async def test_acquire_decrements_tokens(self):
        """Test that acquire decrements token count."""
        limiter = RateLimiter(RateLimitConfig(burst_limit=10))
        initial_tokens = limiter._tokens

        await limiter.acquire()
        assert limiter._tokens == initial_tokens - 1

        limiter.release()

    @pytest.mark.asyncio
    async def test_acquire_increments_requests_made(self):
        """Test that acquire increments requests_made stat."""
        limiter = RateLimiter()
        assert limiter.stats.requests_made == 0

        await limiter.acquire()
        assert limiter.stats.requests_made == 1

        limiter.release()

    @pytest.mark.asyncio
    async def test_multiple_acquires(self):
        """Test multiple sequential acquires."""
        limiter = RateLimiter(RateLimitConfig(burst_limit=5))

        for _ in range(3):
            await limiter.acquire()
            limiter.release()

        assert limiter.stats.requests_made == 3

    @pytest.mark.asyncio
    async def test_tokens_refill_over_time(self):
        """Test that tokens refill based on elapsed time."""
        config = RateLimitConfig(requests_per_second=10.0, burst_limit=5)
        limiter = RateLimiter(config)

        # Consume all tokens
        limiter._tokens = 0
        limiter._last_update = time.monotonic() - 0.5

        # Refill should add 5 tokens (10 rps * 0.5s)
        limiter._refill_tokens()

        assert limiter._tokens >= 4.9

    def test_tokens_capped_at_burst_limit(self):
        """Test that tokens don't exceed burst limit."""
        config = RateLimitConfig(requests_per_second=100.0, burst_limit=10)
        limiter = RateLimiter(config)

        # Set last update far in the past to get many new tokens
        limiter._last_update = time.monotonic() - 100

        limiter._refill_tokens()

        assert limiter._tokens == 10.0


class TestExponentialBackoff:
    """Test exponential backoff functionality."""

    @pytest.mark.asyncio
    async def test_backoff_increases_on_rate_limit(self):
        """Test that backoff increases after rate limit hit."""
        config = RateLimitConfig(backoff_base=1.0, backoff_multiplier=2.0)
        limiter = RateLimiter(config)

        initial_backoff = limiter._current_backoff
        await limiter.handle_rate_limit_response()

        assert limiter._current_backoff == initial_backoff * 2.0

    @pytest.mark.asyncio
    async def test_backoff_capped_at_max(self):
        """Test that backoff is capped at backoff_max."""
        config = RateLimitConfig(
            backoff_base=30.0,
            backoff_max=60.0,
            backoff_multiplier=3.0,
        )
        limiter = RateLimiter(config)

        # First hit: 30 * 3 = 90, but capped at 60
        await limiter.handle_rate_limit_response()

        assert limiter._current_backoff == 60.0

    @pytest.mark.asyncio
    async def test_retry_after_honored(self):
        """Test that retry_after value is used when provided."""
        limiter = RateLimiter()
        start = time.monotonic()

        # Use very short retry_after for test
        await limiter.handle_rate_limit_response(retry_after=0.01)

        elapsed = time.monotonic() - start
        assert elapsed >= 0.01

    @pytest.mark.asyncio
    async def test_max_retries_returns_false(self):
        """Test that handle_rate_limit_response returns False after max retries."""
        config = RateLimitConfig(retry_attempts=2, backoff_base=0.001)
        limiter = RateLimiter(config)

        # First retry
        result1 = await limiter.handle_rate_limit_response()
        assert result1 is True

        # Second retry
        result2 = await limiter.handle_rate_limit_response()
        assert result2 is True

        # Third attempt - should fail
        result3 = await limiter.handle_rate_limit_response()
        assert result3 is False

    def test_reset_backoff(self):
        """Test reset_backoff restores base value."""
        config = RateLimitConfig(backoff_base=1.0)
        limiter = RateLimiter(config)

        limiter._current_backoff = 30.0
        limiter.reset_backoff()

        assert limiter._current_backoff == 1.0

    def test_reset_retries(self):
        """Test reset_retries zeroes retry counter."""
        limiter = RateLimiter()
        limiter.stats.retries = 5

        limiter.reset_retries()

        assert limiter.stats.retries == 0


class TestContextManager:
    """Test async context manager functionality."""

    @pytest.mark.asyncio
    async def test_context_manager_acquires_and_releases(self):
        """Test that context manager acquires and releases properly."""
        limiter = RateLimiter(RateLimitConfig(burst_limit=5))

        async with limiter:
            assert limiter.stats.requests_made == 1

        # After context exit, should be able to acquire again
        async with limiter:
            assert limiter.stats.requests_made == 2

    @pytest.mark.asyncio
    async def test_context_manager_resets_on_success(self):
        """Test that context manager resets backoff on successful exit."""
        config = RateLimitConfig(backoff_base=1.0)
        limiter = RateLimiter(config)

        # Artificially increase backoff
        limiter._current_backoff = 10.0
        limiter.stats.retries = 3

        async with limiter:
            pass

        assert limiter._current_backoff == 1.0
        assert limiter.stats.retries == 0

    @pytest.mark.asyncio
    async def test_context_manager_on_exception(self):
        """Test context manager behavior on exception."""
        limiter = RateLimiter()
        limiter._current_backoff = 10.0

        with pytest.raises(ValueError, match="Test error"):
            async with limiter:
                raise ValueError("Test error")

        # Backoff should not reset on exception
        assert limiter._current_backoff == 10.0


class TestStatistics:
    """Test statistics tracking."""

    @pytest.mark.asyncio
    async def test_rate_limit_hits_tracked(self):
        """Test that rate limit hits are tracked."""
        config = RateLimitConfig(backoff_base=0.001)
        limiter = RateLimiter(config)

        await limiter.handle_rate_limit_response()
        await limiter.handle_rate_limit_response()

        assert limiter.stats.rate_limit_hits == 2

    @pytest.mark.asyncio
    async def test_total_wait_time_tracked(self):
        """Test that total wait time is tracked."""
        config = RateLimitConfig(backoff_base=0.01)
        limiter = RateLimiter(config)

        await limiter.handle_rate_limit_response()

        assert limiter.stats.total_wait_time >= 0.01

    def test_get_stats_returns_dict(self):
        """Test get_stats returns dictionary."""
        limiter = RateLimiter()
        stats = limiter.get_stats()

        assert isinstance(stats, dict)
        assert "requests_made" in stats
        assert "requests_delayed" in stats
        assert "total_wait_time_seconds" in stats
        assert "rate_limit_hits" in stats
        assert "retries" in stats
        assert "avg_wait_per_request" in stats

    @pytest.mark.asyncio
    async def test_get_stats_accuracy(self):
        """Test that get_stats returns accurate values."""
        limiter = RateLimiter(RateLimitConfig(burst_limit=10))

        # Make some requests
        for _ in range(3):
            await limiter.acquire()
            limiter.release()

        stats = limiter.get_stats()
        assert stats["requests_made"] == 3

    def test_avg_wait_zero_when_no_requests(self):
        """Test avg_wait_per_request is 0 when no requests made."""
        limiter = RateLimiter()
        stats = limiter.get_stats()

        assert stats["avg_wait_per_request"] == 0


class TestConcurrentRateLimiting:
    """Test concurrent request limiting with semaphore."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent(self):
        """Test that semaphore limits concurrent requests."""
        config = RateLimitConfig(burst_limit=2, requests_per_second=100)
        limiter = RateLimiter(config)

        active = 0
        max_active = 0

        async def worker():
            nonlocal active, max_active
            await limiter.acquire()
            try:
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
            finally:
                active -= 1
                limiter.release()

        # Run 5 workers concurrently
        await asyncio.gather(*[worker() for _ in range(5)])

        # Max concurrent should be limited by burst_limit
        assert max_active <= 2

    @pytest.mark.asyncio
    async def test_release_allows_next_request(self):
        """Test that release allows next waiting request."""
        config = RateLimitConfig(burst_limit=1, requests_per_second=100)
        limiter = RateLimiter(config)

        acquired = [False, False]

        async def first_worker():
            await limiter.acquire()
            acquired[0] = True
            await asyncio.sleep(0.05)
            limiter.release()

        async def second_worker():
            await asyncio.sleep(0.01)  # Start slightly later
            await limiter.acquire()
            acquired[1] = True
            limiter.release()

        await asyncio.gather(first_worker(), second_worker())

        assert acquired[0] is True
        assert acquired[1] is True


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_requests_per_second(self):
        """Test with very low requests per second."""
        config = RateLimitConfig(requests_per_second=0.1)
        limiter = RateLimiter(config)
        assert limiter.config.requests_per_second == 0.1

    def test_burst_limit_of_one(self):
        """Test with burst limit of 1."""
        config = RateLimitConfig(burst_limit=1)
        limiter = RateLimiter(config)
        assert limiter._tokens == 1.0

    @pytest.mark.asyncio
    async def test_immediate_release(self):
        """Test acquiring and immediately releasing."""
        limiter = RateLimiter()

        await limiter.acquire()
        limiter.release()

        # Should work without issues
        assert limiter.stats.requests_made == 1

    def test_empty_dict_config(self):
        """Test initialization with empty dictionary uses all defaults."""
        limiter = RateLimiter({})
        assert limiter.config.requests_per_second == 5.0
        assert limiter.config.burst_limit == 10

    @pytest.mark.asyncio
    async def test_zero_retry_attempts(self):
        """Test with zero retry attempts."""
        config = RateLimitConfig(retry_attempts=0, backoff_base=0.001)
        limiter = RateLimiter(config)

        result = await limiter.handle_rate_limit_response()
        assert result is False
