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


def test_build_system_message_preserves_two_block_cache_layout():
    from langchain_core.messages import SystemMessage

    from src.deep_runtime.prompt_bridge import build_system_message

    blocks = [
        {"type": "text", "text": "SOUL+ROLE", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "CONTEXT"},
    ]
    sm = build_system_message(blocks)
    assert isinstance(sm, SystemMessage)
    assert sm.content == blocks
    assert sm.content[0].get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in sm.content[1]


def test_build_system_message_wraps_plain_string():
    from langchain_core.messages import SystemMessage

    from src.deep_runtime.prompt_bridge import build_system_message

    assert build_system_message("s").content == "s"
    assert isinstance(build_system_message("s"), SystemMessage)


def test_build_system_message_drops_empty_and_non_text():
    from src.deep_runtime.prompt_bridge import build_system_message

    blocks = [{"type": "text", "text": "A"}, {"type": "image"}, {"type": "text", "text": ""}]
    assert build_system_message(blocks).content == [{"type": "text", "text": "A"}]
