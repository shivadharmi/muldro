"""Step 6A: flatten the chat path's Anthropic multi-block system prompt into the single
system_prompt string build_deep_agent expects, preserving text order and dropping
cache_control markers (langchain-anthropic manages caching itself)."""

from src.deep_runtime.prompt_bridge import flatten_system_blocks


def test_flatten_joins_text_blocks_in_order():
    blocks = [
        {"type": "text", "text": "SOUL"},
        {"type": "text", "text": "ROLE", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "CONTEXT"},
    ]
    assert flatten_system_blocks(blocks) == "SOUL\n\nROLE\n\nCONTEXT"


def test_flatten_accepts_plain_string():
    assert flatten_system_blocks("already a string") == "already a string"


def test_flatten_ignores_non_text_blocks_and_empty():
    blocks = [{"type": "text", "text": "A"}, {"type": "image"}, {"type": "text", "text": ""}]
    assert flatten_system_blocks(blocks) == "A"
