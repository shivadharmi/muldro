from src.llm.model_factory import build_langchain_model
from src.services.model_resolver import ResolvedModel


def test_anthropic_builds_chatanthropic():
    from langchain_anthropic import ChatAnthropic

    r = ResolvedModel(
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
    m = build_langchain_model(r)
    assert isinstance(m, ChatAnthropic)
    assert m.model == "claude-opus-4-8"
    assert m.max_tokens == 8192


def test_openai_builds_chatopenai():
    from langchain_openai import ChatOpenAI

    r = ResolvedModel(
        "openai", "gpt-5", "sk-o", None, {"max_tokens": 4096, "reasoning_effort": "high"}
    )
    m = build_langchain_model(r)
    assert isinstance(m, ChatOpenAI)
    assert m.model_name == "gpt-5"
