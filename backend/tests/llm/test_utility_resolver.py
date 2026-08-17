"""Utility completions resolve via ModelResolver — legacy tier mapping is preserved.

The utility seam maps its legacy tier names (``resolved`` -> ``balanced``,
``haiku`` -> ``fast``) onto the ModelResolver tiers, resolves inside a short-lived
DB session, and builds the concrete model via ``build_langchain_model``. These
tests mock all three seams so no real DB or provider call happens.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock

from src.llm import utility
from src.services import model_resolver
from src.services.model_resolver import ResolvedModel


@asynccontextmanager
async def _fake_session():
    yield MagicMock()


class _FakeModel:
    async def ainvoke(self, messages):
        resp = MagicMock()
        resp.content = "ok"
        resp.usage_metadata = {}
        return resp


async def test_resolved_tier_maps_to_balanced(monkeypatch):
    seen = {}

    async def fake_resolve(self, **kw):
        seen.update(kw)
        return ResolvedModel("anthropic", "claude-sonnet-4-6", "sk-x", None, {"max_tokens": 512})

    monkeypatch.setattr(model_resolver.ModelResolver, "resolve", fake_resolve)
    monkeypatch.setattr(utility, "get_session_factory", lambda: lambda: _fake_session())
    monkeypatch.setattr(utility, "build_langchain_model", lambda resolved: _FakeModel())

    text, usage = await utility.complete_text_with_usage(
        system="s", user="u", tier="resolved", max_tokens=512
    )
    assert text == "ok"
    assert seen["tier"] == "balanced"


async def test_haiku_tier_maps_to_fast(monkeypatch):
    seen = {}

    async def fake_resolve(self, **kw):
        seen.update(kw)
        return ResolvedModel(
            "anthropic", "claude-haiku-4-5-20251001", "sk-x", None, {"max_tokens": 256}
        )

    monkeypatch.setattr(model_resolver.ModelResolver, "resolve", fake_resolve)
    monkeypatch.setattr(utility, "get_session_factory", lambda: lambda: _fake_session())
    monkeypatch.setattr(utility, "build_langchain_model", lambda resolved: _FakeModel())

    text, usage = await utility.complete_text_with_usage(
        system=None, user="u", tier="haiku", max_tokens=256
    )
    assert seen["tier"] == "fast"
