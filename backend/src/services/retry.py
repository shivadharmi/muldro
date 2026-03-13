"""Retry utilities — exponential backoff with jitter for external calls.

Provides a decorator for retrying async functions that call external services
(Claude API, Voyage API, Slack webhooks, etc.) with configurable backoff.
"""

import asyncio
import functools
import logging
import random
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Default retry config
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0  # seconds
DEFAULT_MAX_DELAY = 30.0  # seconds
DEFAULT_JITTER = 0.5  # jitter factor (0-1)


def retry_async(
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter: float = DEFAULT_JITTER,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    on_retry: Callable | None = None,
):
    """Decorator for retrying async functions with exponential backoff + jitter.

    Args:
        max_retries: Maximum number of retry attempts (0 = no retries).
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay cap in seconds.
        jitter: Jitter factor (0.0 = no jitter, 1.0 = full jitter).
        retryable_exceptions: Tuple of exception types that trigger retries.
        on_retry: Optional callback(attempt, exception, delay) called before each retry.
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exception = exc
                    if attempt >= max_retries:
                        break

                    delay = min(base_delay * (2**attempt), max_delay)
                    if jitter > 0:
                        delay = delay * (1 - jitter + random.uniform(0, jitter * 2))

                    logger.warning(
                        "Retry %d/%d for %s after %.2fs: %s",
                        attempt + 1,
                        max_retries,
                        func.__qualname__,
                        delay,
                        str(exc)[:200],
                    )

                    if on_retry:
                        on_retry(attempt + 1, exc, delay)

                    await asyncio.sleep(delay)

            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator
