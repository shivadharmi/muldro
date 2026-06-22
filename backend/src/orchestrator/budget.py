"""Token budget tracking and cost control for Jarvis sub-agents.

Tracks per-agent token usage, enforces daily limits, and triggers
graceful degradation when budget is approaching limits.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.token_usage import TokenUsage

logger = logging.getLogger(__name__)

# Pricing per million tokens (as of 2026-06)
# cache_write = 1.25x input, cache_read = 0.1x input, thinking = same as output
MODEL_PRICING = {
    # Latest
    "claude-opus-4-8": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    # Legacy direct API
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-20250514": {"input": 0.80, "output": 4.0},
    # Bedrock us.* inference profiles
    "us.anthropic.claude-opus-4-8": {"input": 15.0, "output": 75.0},
    "us.anthropic.claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": {"input": 0.80, "output": 4.0},
    # Legacy Bedrock
    "anthropic.claude-opus-4-20250514-v1:0": {"input": 15.0, "output": 75.0},
    "anthropic.claude-sonnet-4-20250514-v1:0": {"input": 3.0, "output": 15.0},
}
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10

# Per-perception-cycle token budget (input tokens)
PERCEPTION_CYCLE_BUDGET = 50_000


@dataclass
class BudgetStatus:
    daily_spend_usd: float
    daily_limit_usd: float
    budget_mode: str  # normal, degraded, paused
    remaining_usd: float
    percent_used: float


class BudgetTracker:
    """Tracks token usage and enforces daily budget limits.

    Uses Redis as a hot counter (INCRBYFLOAT) for multi-instance accuracy.
    DB remains the source of truth; Redis is a fast approximation.
    """

    _REDIS_KEY_PREFIX = "jarvis:budget"
    _REDIS_TTL = 86_400  # 24 hours

    def __init__(self, daily_limit_usd: float = 5.0, redis=None):
        self.daily_limit_usd = daily_limit_usd
        self._redis = redis
        self._today_spend: float = 0.0
        self._today_date: str = ""

    def calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        thinking_tokens: int = 0,
    ) -> float:
        billable_tokens = (
            input_tokens
            + output_tokens
            + cache_creation_input_tokens
            + cache_read_input_tokens
            + thinking_tokens
        )
        # A span with no billable tokens never made an API call, so cost is 0
        # regardless of model. Skip the lookup (and the warning) — a missing
        # model here is legitimate, not a propagation gap or a new model id.
        if billable_tokens <= 0:
            return 0.0

        pricing = MODEL_PRICING.get(model)
        if not pricing:
            logger.warning(
                "Unknown model %r not in MODEL_PRICING — billing at Sonnet rates; "
                "Opus/Bedrock would be under-billed. Add it to MODEL_PRICING.",
                model,
            )
            pricing = MODEL_PRICING["claude-sonnet-4-6"]
        per_m = 1_000_000
        input_cost = (input_tokens / per_m) * pricing["input"]
        output_cost = (output_tokens / per_m) * pricing["output"]
        cache_write_cost = (
            (cache_creation_input_tokens / per_m) * pricing["input"] * CACHE_WRITE_MULTIPLIER
        )
        cache_read_cost = (
            (cache_read_input_tokens / per_m) * pricing["input"] * CACHE_READ_MULTIPLIER
        )
        thinking_cost = (thinking_tokens / per_m) * pricing["output"]
        return input_cost + output_cost + cache_write_cost + cache_read_cost + thinking_cost

    async def record_usage(
        self,
        db: AsyncSession,
        *,
        agent_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        thinking_tokens: int = 0,
        trigger: str,
        conversation_id: str | None = None,
        trace_id: str | None = None,
        workspace_id: str = "",
    ) -> TokenUsage:
        if not workspace_id:
            raise ValueError("workspace_id is required for record_usage")
        cost = self.calculate_cost(
            model,
            input_tokens,
            output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            thinking_tokens=thinking_tokens,
        )

        usage = TokenUsage(
            usage_id=f"usage_{ULID()}",
            agent_name=agent_name,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            thinking_tokens=thinking_tokens,
            cost_usd=cost,
            trigger=trigger,
            conversation_id=conversation_id,
            trace_id=trace_id,
            workspace_id=workspace_id,
        )
        db.add(usage)
        await db.flush()

        # Update daily counter — Redis (multi-instance safe) or in-memory fallback
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._redis and workspace_id:
            try:
                rkey = f"{self._REDIS_KEY_PREFIX}:{workspace_id}:{today}"
                self._today_spend = float(await self._redis.incrbyfloat(rkey, cost))
                await self._redis.expire(rkey, self._REDIS_TTL)
            except Exception:
                logger.debug("Redis budget counter failed, using in-memory")
                self._today_spend += cost
        else:
            if self._today_date != today:
                self._today_date = today
                try:
                    self._today_spend = await self.get_daily_spend(db, workspace_id=workspace_id)
                except Exception:
                    self._today_spend = 0.0
            self._today_spend += cost

        logger.info(
            "token_usage_recorded",
            extra={
                "agent": agent_name,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
                "thinking_tokens": thinking_tokens,
                "cost_usd": round(cost, 6),
                "daily_spend": round(self._today_spend, 4),
                "trace_id": trace_id,
            },
        )
        return usage

    async def record_from_span(
        self,
        db: AsyncSession,
        *,
        span,
        agent_name: str,
        model: str,
        trigger: str,
        trace_id: str | None = None,
        conversation_id: str | None = None,
        workspace_id: str,
    ) -> TokenUsage:
        """Record budget usage from a completed trace span (single source of truth)."""
        return await self.record_usage(
            db,
            agent_name=agent_name,
            model=model,
            input_tokens=getattr(span, "input_tokens", 0) or 0,
            output_tokens=getattr(span, "output_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(span, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(span, "cache_read_input_tokens", 0) or 0,
            thinking_tokens=getattr(span, "thinking_tokens", 0) or 0,
            trigger=trigger,
            trace_id=trace_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )

    async def get_daily_spend(self, db: AsyncSession, *, workspace_id: str = "") -> float:
        # Fast path: read from Redis if available
        if self._redis and workspace_id:
            try:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                rkey = f"{self._REDIS_KEY_PREFIX}:{workspace_id}:{today}"
                val = await self._redis.get(rkey)
                if val is not None:
                    return float(val)
            except Exception:
                pass  # fall through to DB

        # DB: source of truth
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        query = select(func.coalesce(func.sum(TokenUsage.cost_usd), 0.0)).where(
            TokenUsage.created_at >= today_start
        )
        if workspace_id:
            query = query.where(TokenUsage.workspace_id == workspace_id)
        result = await db.execute(query)
        return float(result.scalar_one())

    async def get_budget_status(self, db: AsyncSession, *, workspace_id: str = "") -> BudgetStatus:
        daily_spend = await self.get_daily_spend(db, workspace_id=workspace_id)
        remaining = max(0.0, self.daily_limit_usd - daily_spend)
        percent_used = (daily_spend / self.daily_limit_usd * 100) if self.daily_limit_usd else 0

        if percent_used >= 95:
            mode = "paused"
        elif percent_used >= 80:
            mode = "degraded"
        else:
            mode = "normal"

        return BudgetStatus(
            daily_spend_usd=round(daily_spend, 4),
            daily_limit_usd=self.daily_limit_usd,
            budget_mode=mode,
            remaining_usd=round(remaining, 4),
            percent_used=round(percent_used, 1),
        )

    def should_allow_perception(self, budget_status: BudgetStatus) -> bool:
        return budget_status.budget_mode != "paused"

    def get_perception_interval_multiplier(self, budget_status: BudgetStatus) -> int:
        """Return multiplier for perception intervals when budget is tight."""
        if budget_status.budget_mode == "degraded":
            return 3  # 5min -> 15min
        return 1

    def check_cycle_budget(self, input_tokens_so_far: int) -> bool:
        """Check if we're within the per-cycle token budget."""
        return input_tokens_so_far < PERCEPTION_CYCLE_BUDGET
