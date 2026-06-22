"""Model-tier registry — maps logical tiers (opus/sonnet/haiku) to concrete model IDs.

Lives in the config layer (no upward dependencies) so both the orchestrator and
the assessor services can import these downward instead of services reaching up
into ``orchestrator.jarvis``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Model IDs for each tier (direct Anthropic API).
MODEL_TIERS: dict[str, str] = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}

# Bedrock inference profile IDs (us.* cross-region, us-east-1 / us-west-2).
BEDROCK_MODEL_TIERS: dict[str, str] = {
    "opus": "us.anthropic.claude-opus-4-8",
    "sonnet": "us.anthropic.claude-sonnet-4-6",
    "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
}

# Last-resort Haiku ID if settings can't be read.
_HAIKU_MODEL_FALLBACK = MODEL_TIERS["haiku"]


def get_haiku_model() -> str:
    """Resolve the Haiku model ID honoring the Bedrock toggle.

    Single shared implementation for the assessor services (previously duplicated
    byte-for-byte in risk_assessor and relevance_assessor).
    """
    try:
        from src.config.settings import get_settings

        settings = get_settings()
        tiers = BEDROCK_MODEL_TIERS if settings.use_bedrock else MODEL_TIERS
        return tiers["haiku"]
    except Exception:
        logger.debug("get_haiku_model: settings unavailable, using fallback", exc_info=True)
        return _HAIKU_MODEL_FALLBACK
