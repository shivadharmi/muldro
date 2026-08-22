"""Build a LangChain chat model for a Muldro SubAgent via the ModelResolver."""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel

from src.llm.model_factory import build_langchain_model
from src.orchestrator.agents import SubAgent
from src.services.model_resolver import ModelConfigError, ModelResolver

logger = logging.getLogger(__name__)


async def build_chat_model(
    agent: SubAgent, *, workspace_id: str = "", db_factory=None
) -> BaseChatModel:
    """Resolve *agent* (by name, falling back to its tier) and build its chat model."""
    async with db_factory() as db:
        # The boundary where a config failure becomes articulate (B7): the resolver
        # knows the binding it tried, but only this frame knows which agent asked for
        # it -- ModelConfigError otherwise reached agent-build time as a bare
        # RuntimeError naming neither the tier nor the provider.
        try:
            resolved = await ModelResolver(db).resolve(
                agent=agent.name,
                agent_tier=agent.model_tier,
                workspace_id=workspace_id or None,
                thinking_enabled=agent.thinking.enabled,
            )
        except ModelConfigError as exc:
            if exc.scope_key is None:
                exc.scope_type, exc.scope_key = "agent", agent.name
            logger.error(
                "model config error building agent=%s scope=%s/%s provider=%s: %s (%s)",
                agent.name,
                exc.scope_type,
                exc.scope_key,
                exc.provider,
                exc,
                exc.remediation or "no remediation recorded",
            )
            raise
    return build_langchain_model(resolved)
