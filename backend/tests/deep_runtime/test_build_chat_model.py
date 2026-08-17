from contextlib import asynccontextmanager
from unittest.mock import MagicMock

from src.deep_runtime.model_factory import build_chat_model
from src.orchestrator.agents import AGENTS
from src.services import model_resolver
from src.services.model_resolver import ResolvedModel


@asynccontextmanager
async def _fake_db_factory_cm():
    yield MagicMock()


def _fake_db_factory():
    return _fake_db_factory_cm()


async def test_planner_builds_opus_adaptive(monkeypatch):
    async def fake_resolve(self, **kw):
        assert kw["agent"] == "planner"
        return ResolvedModel(
            "anthropic",
            "claude-opus-4-8",
            "sk-x",
            None,
            {
                "max_tokens": 8192,
                "thinking": {"type": "adaptive", "display": "summarized"},
                "effort": "high",
            },
        )

    monkeypatch.setattr(model_resolver.ModelResolver, "resolve", fake_resolve)
    m = await build_chat_model(AGENTS["planner"], workspace_id="ws_x", db_factory=_fake_db_factory)
    assert m.model == "claude-opus-4-8"
