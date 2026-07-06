"""Jarvis policy middleware for the Deep Agents runtime.

Each module here re-homes one piece of policy that the legacy ``agent_loop`` welded
into the loop, onto a LangChain ``@wrap_tool_call`` / ``@after_model`` hook. Each is a
``make_*_middleware(...)`` factory that closure-binds the per-turn deps
(agent / workspace_id / db_factory / budget) and returns an ``AgentMiddleware``, passed
to ``build_deep_agent(..., extra_middleware=[...])``.

Only per-call policies live here. Whole-turn concerns (``turn_scope``) and once-per-turn
prompt assembly (``ContextPack``) are integration-layer (Phase 2), because the deep agent
is built per turn. See ``docs/deep-agents-migration-assessment.md`` Part F (Phase 1) + §B.8.
"""

from src.deep_runtime.middleware.budget import make_budget_middleware
from src.deep_runtime.middleware.capability_scope import make_capability_scope_middleware
from src.deep_runtime.middleware.jarvis_tool_dispatcher import make_jarvis_tool_dispatcher
from src.deep_runtime.middleware.unavailable_server import (
    make_unavailable_server_middleware,
)

__all__ = [
    "make_budget_middleware",
    "make_capability_scope_middleware",
    "make_jarvis_tool_dispatcher",
    "make_unavailable_server_middleware",
]
