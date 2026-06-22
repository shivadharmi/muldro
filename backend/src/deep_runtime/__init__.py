"""Deep Agents runtime (LangChain/LangGraph migration — Phase 1 foundation).

Pure-addition package: the model factory + agent-builder scaffold for running
Jarvis sub-agents on ``deepagents``/LangGraph. No Jarvis policy middleware yet —
that lands in the next phase. Nothing under ``src/`` imports this package yet;
it is wired in incrementally behind a runtime flag in later phases.
"""

from src.deep_runtime.agent_builder import build_deep_agent
from src.deep_runtime.model_factory import MODEL_TIER_IDS, build_chat_model

__all__ = ["build_chat_model", "build_deep_agent", "MODEL_TIER_IDS"]
