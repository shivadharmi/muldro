"""Tests for security middleware — rate limiting, request size limits."""

from src.middleware.security import RateLimiter


def test_rate_limiter_allows_within_limit():
    """Should allow requests within the rate limit."""
    limiter = RateLimiter(requests_per_minute=5)
    RateLimiter.reset()

    for _ in range(5):
        assert limiter.is_allowed("127.0.0.1") is True


def test_rate_limiter_blocks_over_limit():
    """Should block requests exceeding the rate limit."""
    limiter = RateLimiter(requests_per_minute=3)
    RateLimiter.reset()

    for _ in range(3):
        assert limiter.is_allowed("test_ip") is True

    assert limiter.is_allowed("test_ip") is False


def test_rate_limiter_separate_keys():
    """Should track rate limits per key independently."""
    limiter = RateLimiter(requests_per_minute=2)
    RateLimiter.reset()

    assert limiter.is_allowed("ip_a") is True
    assert limiter.is_allowed("ip_a") is True
    assert limiter.is_allowed("ip_a") is False

    # Different key should still be allowed
    assert limiter.is_allowed("ip_b") is True


def test_rate_limiter_reset():
    """Should clear all windows on reset."""
    limiter = RateLimiter(requests_per_minute=1)
    RateLimiter.reset()

    assert limiter.is_allowed("key") is True
    assert limiter.is_allowed("key") is False

    RateLimiter.reset()
    assert limiter.is_allowed("key") is True
