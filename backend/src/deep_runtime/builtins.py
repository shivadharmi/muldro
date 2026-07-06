"""Reserved deepagents built-in tool names (Step 6A.5).

deepagents' create_deep_agent auto-installs a scaffolding toolset (todos, filesystem,
subagent) via required middleware Jarvis cannot drop.  These are NOT Jarvis registry
tools: they must run their own bodies and must NOT be capability-gated or routed
through ToolExecutor.execute_tool.  Both the capability_scope guard and the
jarvis_tool_dispatcher (to be added in 6B) exempt these names, falling through to
the real deepagents handler.

Version-pinned to deepagents 0.6.11; ``test_builtins_match_a_compiled_agent`` (in
``tests/deep_runtime/test_builtins.py``) asserts this set equals a freshly-compiled
agent's built-in tool names, so a deepagents upgrade that changes the built-ins fails
loudly instead of silently mis-gating tool calls.
"""

from __future__ import annotations

DEEPAGENTS_BUILTIN_NAMES: frozenset[str] = frozenset(
    {
        "write_todos",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
        "task",
    }
)
