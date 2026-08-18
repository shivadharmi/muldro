"""Build inert schema-shell LangChain tools for the deep runtime (Step 6A.5).

Muldro tools execute centrally via the muldro_tool_dispatcher middleware, not per-tool. But
create_deep_agent still needs BaseTool objects so the model SEES each tool's name + args
schema. This builds one StructuredTool per Muldro tool def whose coroutine is a TRIPWIRE
that raises if ever executed — the dispatcher short-circuits every Muldro tool call, so the
shell body must never run. Extra keys on the def (e.g. cache_control) are ignored; only
name/description/input_schema are used.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool


def build_tool_shells(tool_defs: list[dict]) -> list[StructuredTool]:
    """One inert StructuredTool shell per Muldro tool def."""
    return [_shell(d) for d in tool_defs]


def _shell(d: dict) -> StructuredTool:
    name = d["name"]

    async def _tripwire(**_kwargs: Any) -> Any:
        raise AssertionError(
            f"deep-runtime tool shell '{name}' executed — the muldro_tool_dispatcher "
            "middleware must intercept every Muldro tool call before the shell body runs."
        )

    return StructuredTool(
        name=name,
        description=d.get("description") or name,
        args_schema=d.get("input_schema") or {"type": "object", "properties": {}},
        coroutine=_tripwire,
    )
