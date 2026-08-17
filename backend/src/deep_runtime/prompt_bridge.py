"""Bridge the chat path's Anthropic multi-block system prompt to the flat system_prompt
string build_deep_agent / create_deep_agent expects (Step 6A).

``build_system_message`` (Step 6A.5) preserves the two-block cache layout so
langchain-anthropic's ``AnthropicPromptCachingMiddleware`` (injected by deepagents)
can honour the per-block ``cache_control`` markers.
"""

from typing import Any

from langchain_core.messages import SystemMessage


def flatten_system_blocks(system_blocks: Any) -> str:
    """Join the ``text`` of Anthropic system content blocks into one string.

    The chat path builds ``system_blocks`` as a list of ``{"type": "text", "text": ...}``
    dicts (some with ``cache_control`` ephemeral markers). Deep Agents takes a flat
    ``system_prompt`` str, and langchain-anthropic manages prompt caching itself, so the
    cache_control markers are dropped. A plain string is returned unchanged.
    """
    if isinstance(system_blocks, str):
        return system_blocks
    parts = [
        b["text"]
        for b in system_blocks
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
    ]
    return "\n\n".join(parts)


def build_system_message(system_blocks: Any) -> SystemMessage:
    """Build a SystemMessage preserving the chat path's two-block cache layout.

    Legacy emits [{soul+role, cache_control: ephemeral}, {context}]. Flattening merged
    the volatile context into the same cache prefix as the stable soul+role, thrashing
    turn-2 caching. A structured SystemMessage (cache_control on the soul+role block
    only) restores the breakpoint: langchain-anthropic honours block-level
    ``cache_control`` and ``create_deep_agent`` preserves the caller's blocks. A plain
    string is wrapped as-is.
    """
    if isinstance(system_blocks, str):
        return SystemMessage(content=system_blocks)
    blocks = [
        b
        for b in system_blocks
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
    ]
    return SystemMessage(content=blocks)


def strip_cache_control(system_prompt):
    """Return *system_prompt* with any Anthropic ``cache_control`` markers removed from
    its SystemMessage content blocks. Anthropic-only; strip it for providers that do
    not support prompt caching so the block is provider-neutral. A plain string / None
    / non-block content is returned unchanged."""
    if not isinstance(system_prompt, SystemMessage):
        return system_prompt
    content = system_prompt.content
    if not isinstance(content, list):
        return system_prompt
    cleaned = [
        {k: v for k, v in b.items() if k != "cache_control"} if isinstance(b, dict) else b
        for b in content
    ]
    return SystemMessage(content=cleaned)
