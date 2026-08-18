"""Unit tests for src.llm.utility.complete_text — the resolver seam is mocked.

``complete_text_with_usage`` now resolves a model via ``ModelResolver`` inside a
short-lived DB session and builds it with ``build_langchain_model``. These tests inject
a mock model by patching all three seam names in ``src.llm.utility``'s namespace, so the
message-building + text/usage-extraction logic under test stays exactly as before.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.llm.utility import complete_text
from src.services.model_resolver import ResolvedModel


def _mock_model(return_text: str):
    model = AsyncMock()
    model.ainvoke = AsyncMock(return_value=AIMessage(content=return_text))
    return model


@asynccontextmanager
async def _fake_session():
    yield object()


async def _fake_resolve(self, **kwargs):
    # model_id here is irrelevant: usage.model comes from the mock model's `.model`
    # attr via getattr(model, "model", tier), not from the resolved model.
    return ResolvedModel("anthropic", "claude-haiku-4-5-20251001", "sk", None, {"max_tokens": 64})


@contextmanager
def _seam(model):
    """Patch the resolver seam so `complete_text*` uses the given mock model."""
    with (
        patch("src.llm.utility.build_langchain_model", return_value=model),
        patch("src.llm.utility.get_session_factory", lambda: lambda: _fake_session()),
        patch("src.llm.utility.ModelResolver.resolve", _fake_resolve),
    ):
        yield


async def test_complete_text_returns_model_content():
    model = _mock_model('{"ok": true}')
    with _seam(model):
        out = await complete_text(system="sys", user="hello", tier="haiku", max_tokens=256)
    assert out == '{"ok": true}'
    msgs = model.ainvoke.call_args.args[0]
    assert isinstance(msgs[0], SystemMessage) and msgs[0].content == "sys"
    assert isinstance(msgs[1], HumanMessage) and msgs[1].content == "hello"


async def test_complete_text_omits_system_when_none():
    model = _mock_model("summary text")
    with _seam(model):
        out = await complete_text(system=None, user="u", tier="haiku", max_tokens=300)
    msgs = model.ainvoke.call_args.args[0]
    assert isinstance(msgs[0], HumanMessage)  # no SystemMessage
    assert out == "summary text"


async def test_complete_text_accepts_block_list_system():
    # context_assembler + intent_classifier pass a [{"type":"text",...}] block-list system.
    model = _mock_model("ok")
    with _seam(model):
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
    with _seam(model):
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
    with _seam(model):
        out = await complete_text(system=None, user="u", tier="haiku", max_tokens=16)
    assert out == "ab"  # only text blocks joined; non-text/non-dict ignored


async def test_complete_text_empty_content_returns_empty():
    model = _mock_model("")
    with _seam(model):
        out = await complete_text(system="s", user="u", tier="haiku", max_tokens=16)
    assert out == ""


async def test_complete_text_with_usage_surfaces_token_counts():
    from src.llm.utility import complete_text_with_usage

    model = AsyncMock()
    model.model = "claude-haiku-4-5-20251001"
    model.ainvoke = AsyncMock(
        return_value=AIMessage(
            content="hi",
            usage_metadata={
                "input_tokens": 321,
                "output_tokens": 12,
                "total_tokens": 333,
                "input_token_details": {"cache_read": 100, "cache_creation": 5},
            },
        )
    )
    with _seam(model):
        text, usage = await complete_text_with_usage(
            system="s", user="u", tier="haiku", max_tokens=64
        )
    assert text == "hi"
    assert usage.model == "claude-haiku-4-5-20251001"
    assert usage.input_tokens == 321
    assert usage.output_tokens == 12
    assert usage.cache_read_input_tokens == 100
    assert usage.cache_creation_input_tokens == 5


async def test_complete_text_with_usage_zeros_when_no_metadata():
    from src.llm.utility import complete_text_with_usage

    model = AsyncMock()
    model.model = "claude-haiku-4-5-20251001"
    model.ainvoke = AsyncMock(return_value=AIMessage(content="x"))  # no usage_metadata
    with _seam(model):
        text, usage = await complete_text_with_usage(
            system=None, user="u", tier="haiku", max_tokens=8
        )
    assert text == "x"
    assert usage.input_tokens == 0 and usage.output_tokens == 0


@contextmanager
def _capturing_seam(model, seen: dict):
    """Like ``_seam`` but records the kwargs ``ModelResolver.resolve`` was called with."""

    async def _capture(self, **kwargs):
        seen.update(kwargs)
        return await _fake_resolve(self, **kwargs)

    with (
        patch("src.llm.utility.build_langchain_model", return_value=model),
        patch("src.llm.utility.get_session_factory", lambda: lambda: _fake_session()),
        patch("src.llm.utility.ModelResolver.resolve", _capture),
    ):
        yield


async def test_complete_text_threads_workspace_id_to_the_resolver():
    """A workspace model override must apply to utility completions too.

    ``complete_text`` is the entry point for every shared-machinery side-call
    (risk_assessor, intent_classifier, presenter, relevance_assessor, event_processor,
    context_assembler, step_runner, verifier, contradictions, governor critique). If it
    cannot carry ``workspace_id``, all of them resolve against the deployment default
    and the workspace's configured model is silently ignored on the entire utility path.
    """
    seen: dict = {}
    with _capturing_seam(_mock_model("ok"), seen):
        await complete_text(
            system="s", user="u", tier="haiku", max_tokens=16, workspace_id="ws_target"
        )
    assert seen.get("workspace_id") == "ws_target"


async def test_complete_text_workspace_id_defaults_to_none():
    """Callers with no workspace in scope keep resolving against the deployment default."""
    seen: dict = {}
    with _capturing_seam(_mock_model("ok"), seen):
        await complete_text(system="s", user="u", tier="haiku", max_tokens=16)
    assert seen.get("workspace_id") is None
