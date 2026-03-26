"""Trust enforcer — per-tier enforcement and rate limiting.

Enforces restrictions based on server trust tiers:
  T0 (internal): no restrictions
  T1 (official): standard rate limits
  T2 (org-approved): approval required for writes, moderate rate limits
  T3 (user-added): strict rate limits, all writes require approval, sandboxed
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Rate limits per trust tier (calls per minute)
TIER_RATE_LIMITS: dict[str, int] = {
    "T0": 0,  # unlimited
    "T1": 120,
    "T2": 60,
    "T3": 20,
}

# Max concurrent calls per tier
TIER_MAX_CONCURRENT: dict[str, int] = {
    "T0": 0,  # unlimited
    "T1": 20,
    "T2": 10,
    "T3": 3,
}


@dataclass(frozen=True)
class EnforcementResult:
    allowed: bool
    reason: str | None = None
    requires_approval: bool = False
    rate_limit_remaining: int | None = None


@dataclass
class _RateBucket:
    """Sliding window rate limiter for a single server."""

    calls: list[float] = field(default_factory=list)
    active_count: int = 0

    def record_call(self) -> None:
        self.calls.append(time.monotonic())
        self.active_count += 1

    def complete_call(self) -> None:
        self.active_count = max(0, self.active_count - 1)

    def count_in_window(self, window_seconds: float = 60.0) -> int:
        cutoff = time.monotonic() - window_seconds
        self.calls = [t for t in self.calls if t > cutoff]
        return len(self.calls)


class TrustEnforcer:
    """Enforces trust-tier-based restrictions on MCP tool calls."""

    def __init__(self) -> None:
        self._buckets: dict[str, _RateBucket] = {}

    def _get_bucket(self, server_name: str) -> _RateBucket:
        if server_name not in self._buckets:
            self._buckets[server_name] = _RateBucket()
        return self._buckets[server_name]

    def check(
        self,
        server_name: str,
        trust_tier: str,
        tool_name: str,
        is_write: bool = False,
    ) -> EnforcementResult:
        """Check if a tool call is allowed under the server's trust tier."""
        # T0: no restrictions
        if trust_tier == "T0":
            return EnforcementResult(allowed=True)

        rate_limit = TIER_RATE_LIMITS.get(trust_tier, 20)
        max_concurrent = TIER_MAX_CONCURRENT.get(trust_tier, 3)

        bucket = self._get_bucket(server_name)

        # Check rate limit
        current_count = bucket.count_in_window()
        if rate_limit > 0 and current_count >= rate_limit:
            return EnforcementResult(
                allowed=False,
                reason=(
                    f"Rate limit exceeded for {trust_tier} server '{server_name}': "
                    f"{current_count}/{rate_limit} calls/min"
                ),
                rate_limit_remaining=0,
            )

        # Check concurrent limit
        if max_concurrent > 0 and bucket.active_count >= max_concurrent:
            return EnforcementResult(
                allowed=False,
                reason=(
                    f"Concurrent limit exceeded for {trust_tier} server '{server_name}': "
                    f"{bucket.active_count}/{max_concurrent} active"
                ),
            )

        # T3: all writes require approval
        if trust_tier == "T3" and is_write:
            return EnforcementResult(
                allowed=True,
                requires_approval=True,
                rate_limit_remaining=max(0, rate_limit - current_count - 1),
            )

        # T2: writes require approval
        if trust_tier == "T2" and is_write:
            return EnforcementResult(
                allowed=True,
                requires_approval=True,
                rate_limit_remaining=max(0, rate_limit - current_count - 1),
            )

        return EnforcementResult(
            allowed=True,
            rate_limit_remaining=max(0, rate_limit - current_count - 1) if rate_limit > 0 else None,
        )

    def record_call(self, server_name: str) -> None:
        """Record a call starting for rate limiting."""
        self._get_bucket(server_name).record_call()

    def complete_call(self, server_name: str) -> None:
        """Record a call completing."""
        self._get_bucket(server_name).complete_call()

    def get_usage(self, server_name: str) -> dict:
        """Get current usage stats for a server."""
        bucket = self._get_bucket(server_name)
        return {
            "calls_last_minute": bucket.count_in_window(),
            "active_calls": bucket.active_count,
        }
