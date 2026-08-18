"""Build a LangChain chat model for a Jarvis SubAgent via the ModelResolver."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from src.llm.model_factory import build_langchain_model
from src.orchestrator.agents import SubAgent
from src.services.model_resolver import ModelResolver


async def build_chat_model(
    agent: SubAgent, *, workspace_id: str = "", db_factory=None
) -> BaseChatModel:
    """Resolve *agent* (by name, falling back to its tier) and build its chat model."""
    async with db_factory() as db:
        resolved = await ModelResolver(db).resolve(
            agent=agent.name,
            agent_tier=agent.model_tier,
            workspace_id=workspace_id or None,
            thinking_enabled=agent.thinking.enabled,
        )
    return build_langchain_model(resolved)
