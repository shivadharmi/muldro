"""Model-tier registry — maps logical tiers (opus/sonnet/haiku) to concrete model IDs.

Lives in the config layer (no upward dependencies) so both the orchestrator and
the assessor services can import these downward instead of services reaching up
into ``orchestrator.jarvis``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Model IDs for each tier (direct Anthropic API — the only backend, Step 11).
MODEL_TIERS: dict[str, str] = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


def get_haiku_model() -> str:
    """Resolve the Haiku model ID.

    Single shared implementation for the assessor services (previously duplicated
    byte-for-byte in risk_assessor and relevance_assessor).
    """
    return MODEL_TIERS["haiku"]
