"""Unit tests for src.llm.utility.complete_text — ChatAnthropic.ainvoke is mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.llm.utility import complete_text


def _mock_model(return_text: str):
    model = AsyncMock()
    model.ainvoke = AsyncMock(return_value=AIMessage(content=return_text))
    return model


async def test_complete_text_returns_model_content():
    model = _mock_model('{"ok": true}')
    with patch("src.llm.utility.build_utility_model", return_value=model):
        out = await complete_text(system="sys", user="hello", tier="haiku", max_tokens=256)
    assert out == '{"ok": true}'
    msgs = model.ainvoke.call_args.args[0]
    assert isinstance(msgs[0], SystemMessage) and msgs[0].content == "sys"
    assert isinstance(msgs[1], HumanMessage) and msgs[1].content == "hello"


async def test_complete_text_omits_system_when_none():
    model = _mock_model("summary text")
    with patch("src.llm.utility.build_utility_model", return_value=model):
        out = await complete_text(system=None, user="u", tier="haiku", max_tokens=300)
    msgs = model.ainvoke.call_args.args[0]
    assert isinstance(msgs[0], HumanMessage)  # no SystemMessage
    assert out == "summary text"


async def test_complete_text_accepts_block_list_system():
    # context_assembler + intent_classifier pass a [{"type":"text",...}] block-list system.
    model = _mock_model("ok")
    with patch("src.llm.utility.build_utility_model", return_value=model):
        await complete_text(
            system=[{"type": "text", "text": "sys prompt"}],
            user="u",
            tier="haiku",
            max_tokens=10,
        )
    msgs = model.ainvoke.call_args.args[0]
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[0].content == [{"type": "text", "text": "sys prompt"}]


async def test_complete_text_conversation_ends_with_user_message():
    # Adaptive-thinking models (every model Jarvis runs) reject a conversation that
    # ends with an assistant turn. complete_text must never append an assistant
    # (prefill) message — the last message is always the HumanMessage.
    model = _mock_model('{"passed": true}')
    with patch("src.llm.utility.build_utility_model", return_value=model):
        out = await complete_text(system="s", user="u", tier="resolved", max_tokens=256)
    msgs = model.ainvoke.call_args.args[0]
    assert isinstance(msgs[-1], HumanMessage)
    assert not any(isinstance(m, AIMessage) for m in msgs)
    assert out == '{"passed": true}'


async def test_complete_text_joins_block_list_content():
    model = AsyncMock()
    model.ainvoke = AsyncMock(
        return_value=AIMessage(
            content=[
                {"type": "text", "text": "a"},
                {"type": "other", "data": 1},
                {"type": "text", "text": "b"},
            ]
        )
    )
    with patch("src.llm.utility.build_utility_model", return_value=model):
        out = await complete_text(system=None, user="u", tier="haiku", max_tokens=16)
    assert out == "ab"  # only text blocks joined; non-text/non-dict ignored


async def test_complete_text_empty_content_returns_empty():
    model = _mock_model("")
    with patch("src.llm.utility.build_utility_model", return_value=model):
        out = await complete_text(system="s", user="u", tier="haiku", max_tokens=16)
    assert out == ""
