"""Reserved deepagents built-in tool names (Step 6A.5).

deepagents' create_deep_agent auto-installs a scaffolding toolset (todos, filesystem,
subagent) via required middleware Muldro cannot drop.  These are NOT Muldro registry
tools: they must run their own bodies and must NOT be capability-gated or routed
through ToolExecutor.execute_tool.  Both the capability_scope guard and the
muldro_tool_dispatcher (to be added in 6B) exempt these names, falling through to
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


# The subset of the built-ins backed by deepagents' VIRTUAL, per-thread filesystem. Muldro
# has no filesystem feature, so offering these is offering a place to "save" things that
# vanishes at end of thread: the model reports success over a write that never happened.
# Suppressed from every model request by ``make_no_virtual_filesystem_middleware``.
#
# NOT suppressed, and deliberately: ``task`` (how a lead reaches a delegate subagent, gated
# by name in governor_delegate_critique) and ``write_todos`` (an internal planning
# scratchpad with no data to lose).
#
# These names are still in DEEPAGENTS_BUILTIN_NAMES and still exempt from every gate — the
# middlewares are unchanged. Suppression removes them from what the model is OFFERED; it
# does not change how a call to one would be handled.
VIRTUAL_FILESYSTEM_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
    }
)
