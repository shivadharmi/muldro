"""Tests for retry utilities — exponential backoff with jitter."""

import pytest

from src.services.retry import retry_async


@pytest.mark.asyncio
async def test_retry_succeeds_first_attempt():
    """Should return immediately on success."""
    call_count = 0

    @retry_async(max_retries=3, base_delay=0.01)
    async def succeed():
        nonlocal call_count
        call_count += 1
        return "ok"

    result = await succeed()

    assert result == "ok"
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_succeeds_after_failures():
    """Should retry and succeed after transient failures."""
    call_count = 0

    @retry_async(max_retries=3, base_delay=0.01, retryable_exceptions=(ValueError,))
    async def fail_then_succeed():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("transient error")
        return "ok"

    result = await fail_then_succeed()

    assert result == "ok"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_exhausted():
    """Should raise after exhausting all retries."""
    call_count = 0

    @retry_async(max_retries=2, base_delay=0.01, retryable_exceptions=(ValueError,))
    async def always_fail():
        nonlocal call_count
        call_count += 1
        raise ValueError("persistent error")

    with pytest.raises(ValueError, match="persistent error"):
        await always_fail()

    assert call_count == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_retry_non_retryable_exception():
    """Should not retry on non-retryable exceptions."""
    call_count = 0

    @retry_async(max_retries=3, base_delay=0.01, retryable_exceptions=(ValueError,))
    async def wrong_error():
        nonlocal call_count
        call_count += 1
        raise TypeError("non-retryable")

    with pytest.raises(TypeError, match="non-retryable"):
        await wrong_error()

    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_callback():
    """Should call on_retry callback before each retry."""
    retries = []

    def on_retry(attempt, exc, delay):
        retries.append({"attempt": attempt, "error": str(exc)})

    @retry_async(
        max_retries=2,
        base_delay=0.01,
        retryable_exceptions=(ValueError,),
        on_retry=on_retry,
    )
    async def fail_twice():
        if len(retries) < 2:
            raise ValueError("fail")
        return "ok"

    result = await fail_twice()

    assert result == "ok"
    assert len(retries) == 2
    assert retries[0]["attempt"] == 1


@pytest.mark.asyncio
async def test_retry_zero_retries():
    """Should not retry when max_retries=0."""
    call_count = 0

    @retry_async(max_retries=0, retryable_exceptions=(ValueError,))
    async def no_retries():
        nonlocal call_count
        call_count += 1
        raise ValueError("fail")

    with pytest.raises(ValueError):
        await no_retries()

    assert call_count == 1
