"""Step 10B Phase 2: ShadowToolExecutor is the headline SAFETY guard for the
shadow-compare harness — it lets the non-authoritative deep-agent runtime run
alongside the authoritative legacy runtime WITHOUT ever executing a real write.

READ-capability tool calls pass through to the real executor; WRITE-capability
(and UNKNOWN-capability, fail-closed) tool calls are HARD-SUPPRESSED and never
reach real dispatch.
"""

from unittest.mock import AsyncMock

from src.orchestrator.shadow_tool_executor import ShadowToolExecutor


class SpyToolExecutor:
    """Stand-in for src.orchestrator.tool_executor.ToolExecutor — records every
    call it receives so tests can assert a write NEVER reaches it."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute_tool(self, tool_name, tool_input, user_id, workspace_id=""):
        self.calls.append((tool_name, tool_input))
        return {"status": "ok"}


async def _resolve(name: str) -> str | None:
    return {"gmail_send": "email.send", "gmail_search": "email.search"}.get(name)


async def test_write_is_suppressed_and_never_reaches_real_dispatch():
    real = SpyToolExecutor()
    shadow = ShadowToolExecutor(real, resolve_capability=_resolve)

    out = await shadow.execute_tool("gmail_send", {"to": "x"}, user_id="u", workspace_id="w")

    assert out.get("shadow_suppressed") is True
    assert real.calls == []  # real dispatch NEVER invoked for a write


async def test_read_passes_through_to_real_executor():
    real = SpyToolExecutor()
    shadow = ShadowToolExecutor(real, resolve_capability=_resolve)

    await shadow.execute_tool("gmail_search", {"q": "x"}, user_id="u", workspace_id="w")

    assert real.calls == [("gmail_search", {"q": "x"})]


async def test_unknown_capability_is_suppressed_fail_closed():
    real = SpyToolExecutor()
    shadow = ShadowToolExecutor(real, resolve_capability=AsyncMock(return_value=None))

    out = await shadow.execute_tool("mystery_tool", {}, user_id="u", workspace_id="w")

    assert out.get("shadow_suppressed") is True
    assert real.calls == []


async def test_suppressed_result_has_no_error_key_and_no_failing_status():
    real = SpyToolExecutor()
    shadow = ShadowToolExecutor(real, resolve_capability=_resolve)

    out = await shadow.execute_tool("gmail_send", {"to": "x"}, user_id="u", workspace_id="w")

    assert "error" not in out
    assert out.get("status") not in ("error", "failed")


async def test_suppressed_result_carries_a_human_readable_note():
    real = SpyToolExecutor()
    shadow = ShadowToolExecutor(real, resolve_capability=_resolve)

    out = await shadow.execute_tool("gmail_send", {"to": "x"}, user_id="u", workspace_id="w")

    note = out.get("note")
    assert isinstance(note, str) and note.strip() != ""
