"""Bridge the chat path's Anthropic multi-block system prompt to the flat system_prompt
string build_deep_agent / create_deep_agent expects (Step 6A)."""

from typing import Any


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
