"""Deep-runtime Librarian extraction middleware (Step 7B1).

``@after_model`` post-processing that relocates the chat ``InteractionLearner`` extraction
into the deep turn. WIRED-BUT-DORMANT: ``active=False`` on the live direct-chat path so it
never double-fires with ``InteractionLearner`` (chat_processor's background spawn). Fires the
injected ``learn`` ONCE per turn (terminal round only — ``@after_model`` fires per model
round; spike 0.2). Live activation (flip ``active`` + skip ``InteractionLearner`` on
``runtime=deep``) is a Step-10 gate. Best-effort: extraction failure never breaks the turn.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, after_model

logger = logging.getLogger(__name__)

LearnFn = Callable[[str, str], Awaitable[None]]


def make_librarian_extract_middleware(
    *, workspace_id: str, user_id: str, learn: LearnFn, active: bool = False
) -> AgentMiddleware:
    """Build the Librarian extraction middleware for one turn.

    ``workspace_id`` / ``user_id`` are captured in the closure (never LLM-supplied) so a
    caller can key logs/attribution; the actual extraction scope is bound inside ``learn``.

    Args:
        workspace_id: Tenant scope (closure-bound, never LLM-supplied).
        user_id: The interacting user (closure-bound, never LLM-supplied).
        learn: Async ``(user_message, agent_response) -> None`` — adapts the existing,
            tested ``InteractionLearner.learn(...)`` (intent gate, empty-response gate,
            Redis cooldown, fresh DB session, memory + world-model extraction). Never
            re-implemented here.
        active: When ``False`` (the live-path default) the hook is inert so it never
            double-fires with the still-live ``InteractionLearner``. A Step-10 gate flips
            this to ``True`` once the ``InteractionLearner`` spawn is skipped on the deep
            path.

    Returns:
        An ``AgentMiddleware`` exposing an async ``aafter_model`` hook.
    """

    @after_model(name="JarvisLibrarianExtract")
    async def _extract(state: dict[str, Any], runtime: Any) -> None:
        if not active:
            return None
        try:
            messages = state.get("messages") or []
            # Terminal round only: @after_model fires per model round (spike 0.2). If the
            # last AI message still has tool_calls, this is an intermediate round — wait for
            # the terminal round so extraction fires exactly once per turn.
            last_ai = next(
                (m for m in reversed(messages) if getattr(m, "type", None) == "ai"), None
            )
            if last_ai is None or getattr(last_ai, "tool_calls", None):
                return None
            user_message = next(
                (str(m.content) for m in messages if getattr(m, "type", None) == "human"), ""
            )
            agent_response = str(last_ai.content)
            if not user_message and not agent_response:
                return None
            await learn(user_message, agent_response)
        except Exception:  # noqa: BLE001 — best-effort: extraction never breaks the turn.
            logger.debug("[deep_runtime] librarian extraction failed", exc_info=True)
        return None

    return _extract
