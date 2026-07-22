"""Step 10D A-5 (Task C): ``CapabilityResolver.capabilities_for_step``.

Returns the SET of capability strings whose tools ``resolve_for_step`` would offer for a
plan step — the primary ``step_capability`` plus its read-only family capabilities (same
``family.`` prefix, no approval required). Single-sourced with ``resolve_for_step``'s
filter, but returning capabilities (used to derive a deep lead's capability_scope) instead
of tool dicts.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.capability_resolver import CapabilityResolver


def _tool(name: str, capability: str | None, requires_approval: bool):
    """Minimal ToolDefinition stand-in — only the attrs the method reads."""
    return SimpleNamespace(name=name, capability=capability, requires_approval=requires_approval)


def _resolver_with(tools) -> CapabilityResolver:
    r = CapabilityResolver(db=None, workspace_id="ws")
    r._list_enabled_tools = AsyncMock(return_value=tools)
    return r


async def test_write_step_returns_primary_plus_family_reads():
    """A write step yields {write_cap} ∪ the read-only family capabilities, and NEVER
    another (non-primary) write in the same family or a write in an unrelated family."""
    resolver = _resolver_with(
        [
            _tool("send_email", "email.send", True),  # primary write
            _tool("search_email", "email.search", False),  # family read
            _tool("list_email", "email.list", False),  # family read
            _tool("draft_email", "email.draft", True),  # OTHER family write — excluded
            _tool("create_event", "calendar.create", True),  # unrelated write — excluded
        ]
    )

    caps = await resolver.capabilities_for_step("email.send")

    assert caps == {"email.send", "email.search", "email.list"}
    assert "email.draft" not in caps  # non-primary family write excluded (teeth)
    assert "calendar.create" not in caps  # unrelated write excluded (teeth)


async def test_read_step_pulls_in_only_family_reads_never_a_write():
    """A read step's scope is the primary read plus other family reads — no family write
    is ever pulled in (proves the scope stays read-only for a read step)."""
    resolver = _resolver_with(
        [
            _tool("search_email", "email.search", False),  # primary read
            _tool("list_email", "email.list", False),  # family read
            _tool("send_email", "email.send", True),  # family write — excluded
        ]
    )

    caps = await resolver.capabilities_for_step("email.search")

    assert caps == {"email.search", "email.list"}
    assert "email.send" not in caps  # a read step never grants a write (teeth)


async def test_lone_capability_with_no_family_returns_itself():
    """A capability with no dotted family (e.g. ``reason``) resolves to just itself, even
    when unrelated tools exist."""
    resolver = _resolver_with(
        [
            _tool("search_email", "email.search", False),
            _tool("send_email", "email.send", True),
        ]
    )

    caps = await resolver.capabilities_for_step("reason")

    assert caps == {"reason"}


async def test_none_capabilities_are_skipped_in_family_scan():
    """A tool with ``capability is None`` (unmapped/auto-registered) never contributes to
    the scope — the None guard mirrors ``resolve_for_step``."""
    resolver = _resolver_with(
        [
            _tool("mystery_tool", None, False),  # unmapped — must be ignored
            _tool("search_email", "email.search", False),  # family read
        ]
    )

    caps = await resolver.capabilities_for_step("email.search")

    assert caps == {"email.search"}
