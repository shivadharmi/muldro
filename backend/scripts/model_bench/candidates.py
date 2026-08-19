"""Candidate models, constructed directly rather than through `ModelResolver`.

Deliberate: `resolve_credential` returns ONE (api_key, base_url) pair per provider per
workspace, so real OpenAI and Ollama Cloud — which both speak the OpenAI protocol, at
different hosts — cannot both be configured as provider `openai` at the same time. Holding
the candidates here sidesteps that entirely and keeps the benchmark from mutating live
`model_bindings` / `provider_credentials` rows.

Consequence to be honest about: this does NOT exercise `ModelResolver`, `build_model_kwargs`
or the capability map. A candidate that WINS still needs a `ModelSpec` in
`src/config/model_catalog.py` before `PUT /v1/model-config` will accept it — that endpoint
400s on anything not in the catalog — and that wiring needs its own verification.

Ollama Cloud is reached through its OpenAI-compatible endpoint. Note `ChatOllama` has NO
`api_key` field and SILENTLY DROPS one passed to it, so the native `ollama` provider cannot
authenticate against the cloud today; see the session notes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

_OLLAMA_CLOUD_BASE = "https://ollama.com/v1"


@dataclass(frozen=True)
class Candidate:
    label: str
    build: Callable[[], object]
    supports_prompt_cache: bool = False
    note: str = ""


def _first_env(*names: str) -> str | None:
    """First of *names* that is set. Accepts both the `MULDRO_`-prefixed form the app
    reads and the bare form, so a key added for the benchmark also works for the app."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _openai(model_id: str, *, base_url: str | None = None, key_env: str = "OPENAI_API_KEY"):
    def _build():
        from langchain_openai import ChatOpenAI

        key = _first_env(f"MULDRO_{key_env}", key_env)
        if not key:
            raise RuntimeError(f"neither MULDRO_{key_env} nor {key_env} is set in backend/.env")
        kwargs = {"model": model_id, "api_key": key}
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)

    return _build


def _anthropic(model_id: str):
    def _build():
        from langchain_anthropic import ChatAnthropic

        key = _first_env("MULDRO_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("no Anthropic key in MULDRO_ANTHROPIC_API_KEY/ANTHROPIC_API_KEY")
        return ChatAnthropic(model=model_id, api_key=key, max_tokens=4096)

    return _build


def registry() -> list[Candidate]:
    """Every candidate. Ones whose key is missing fail loudly at build, not silently."""
    return [
        # The incumbent, as the reference line every other candidate is read against.
        Candidate("anthropic/haiku-4.5", _anthropic("claude-haiku-4-5-20251001"), True),
        Candidate("anthropic/sonnet-4.6", _anthropic("claude-sonnet-4-6"), True),
        # The pragmatic hosted baseline for fast + balanced.
        Candidate("openai/gpt-5-mini", _openai("gpt-5-mini")),
        Candidate("openai/gpt-5", _openai("gpt-5")),
        # Ollama Cloud, via its OpenAI-compatible endpoint.
        Candidate(
            "ollama-cloud/gpt-oss:120b",
            _openai("gpt-oss:120b", base_url=_OLLAMA_CLOUD_BASE, key_env="OLLAMA_API_KEY"),
            note="open-weight, hosted",
        ),
        Candidate(
            "ollama-cloud/gpt-oss:20b",
            _openai("gpt-oss:20b", base_url=_OLLAMA_CLOUD_BASE, key_env="OLLAMA_API_KEY"),
            note="open-weight, hosted",
        ),
        Candidate(
            "ollama-cloud/qwen3-coder:480b",
            _openai("qwen3-coder:480b", base_url=_OLLAMA_CLOUD_BASE, key_env="OLLAMA_API_KEY"),
            note="open-weight, hosted",
        ),
    ]
