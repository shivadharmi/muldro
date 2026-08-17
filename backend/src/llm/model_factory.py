"""Single leaf that builds a LangChain chat model from a ResolvedModel via init_chat_model."""

from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from src.services.model_resolver import ResolvedModel


def build_langchain_model(resolved: ResolvedModel) -> BaseChatModel:
    """Construct a provider-appropriate BaseChatModel from *resolved*."""
    kwargs = dict(resolved.kwargs)
    if resolved.api_key is not None:
        kwargs["api_key"] = resolved.api_key
    if resolved.base_url:
        kwargs["base_url"] = resolved.base_url
    return init_chat_model(resolved.model_id, model_provider=resolved.provider, **kwargs)
