"""Resolve (role/tier + workspace) -> a concrete ResolvedModel, merging DB
bindings + provider credentials + the code capability map. workspace_id is
optional: None resolves against the deployment-default (NULL-workspace) rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import secret_crypto
from src.config.capability_map import build_model_kwargs
from src.config.model_catalog import get_model_spec
from src.config.settings import get_settings
from src.models.model_binding import ModelBinding
from src.models.provider_credential import ProviderCredential

# Env-var fallback per provider (used when no DB credential row exists).
_ENV_KEY_ATTR = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "google_genai": "google_api_key",
}


class ModelConfigError(RuntimeError):
    """Raised when a model cannot be resolved (unknown model / missing credential)."""


@dataclass(frozen=True)
class ResolvedModel:
    provider: str
    model_id: str
    api_key: str | None
    base_url: str | None
    kwargs: dict[str, Any]


class ModelResolver:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def resolve(
        self,
        *,
        tier: str | None = None,
        agent: str | None = None,
        agent_tier: str | None = None,
        workspace_id: str | None = None,
        thinking_enabled: bool = True,
    ) -> ResolvedModel:
        binding = await self._pick_binding(
            tier=tier, agent=agent, agent_tier=agent_tier, workspace_id=workspace_id
        )
        if binding is None:
            raise ModelConfigError(f"no model binding for tier={tier} agent={agent}")

        spec = get_model_spec(binding.provider, binding.model_id)
        if spec is None:
            raise ModelConfigError(f"unknown model {binding.provider}/{binding.model_id}")

        api_key, base_url = await self._resolve_credential(binding.provider, workspace_id)
        if api_key is None and binding.provider != "ollama":  # ollama needs no key
            raise ModelConfigError(f"provider {binding.provider} is not configured")

        kwargs = build_model_kwargs(
            spec,
            effort=binding.effort,
            max_tokens=binding.max_tokens,
            temperature=binding.temperature,
            thinking_enabled=thinking_enabled,
        )
        if binding.params:
            kwargs.update(binding.params)
        return ResolvedModel(binding.provider, binding.model_id, api_key, base_url, kwargs)

    async def supports_prompt_cache(
        self,
        *,
        tier: str | None = None,
        agent: str | None = None,
        agent_tier: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        """Whether the model backing this (agent/tier) supports prompt caching.

        Binding + catalog only — NO credential decryption. Returns True when the
        binding or spec cannot be determined (Anthropic-safe default).
        """
        try:
            binding = await self._pick_binding(
                tier=tier, agent=agent, agent_tier=agent_tier, workspace_id=workspace_id
            )
            if binding is None:
                return True
            spec = get_model_spec(binding.provider, binding.model_id)
            return spec.supports_prompt_cache if spec else True
        except Exception:
            return True

    async def resolved_model_id(
        self,
        *,
        tier: str | None = None,
        agent: str | None = None,
        agent_tier: str | None = None,
        workspace_id: str | None = None,
    ) -> str | None:
        """The model id the binding for this (agent/tier) resolves to — binding + precedence
        only, no credential work. None when no binding is found (caller falls back)."""
        try:
            binding = await self._pick_binding(
                tier=tier, agent=agent, agent_tier=agent_tier, workspace_id=workspace_id
            )
            return binding.model_id if binding is not None else None
        except Exception:
            return None

    async def _pick_binding(self, *, tier, agent, agent_tier, workspace_id) -> ModelBinding | None:
        # Precedence: agent-override row -> tier row. Each lookup prefers the
        # workspace row, else the deployment-default (NULL) row.
        if agent is not None:
            b = await self._binding_row("agent", agent, workspace_id)
            if b:
                return b
            tier = agent_tier  # fall back to the agent's tier
        if tier is not None:
            return await self._binding_row("tier", tier, workspace_id)
        return None

    async def _binding_row(self, scope_type, scope_key, workspace_id) -> ModelBinding | None:
        stmt = (
            select(ModelBinding)
            .where(
                ModelBinding.scope_type == scope_type,
                ModelBinding.scope_key == scope_key,
                ModelBinding.enabled.is_(True),
                or_(ModelBinding.workspace_id == workspace_id, ModelBinding.workspace_id.is_(None)),
            )
            # workspace row (NULLs last) beats the deployment default
            .order_by(ModelBinding.workspace_id.is_(None))
        )
        return (await self._db.execute(stmt)).scalars().first()

    async def _resolve_credential(self, provider, workspace_id) -> tuple[str | None, str | None]:
        stmt = (
            select(ProviderCredential)
            .where(
                ProviderCredential.provider == provider,
                ProviderCredential.enabled.is_(True),
                or_(
                    ProviderCredential.workspace_id == workspace_id,
                    ProviderCredential.workspace_id.is_(None),
                ),
            )
            .order_by(ProviderCredential.workspace_id.is_(None))
        )
        row = (await self._db.execute(stmt)).scalars().first()
        if row and row.api_key_encrypted:
            return secret_crypto.decrypt_secret(row.api_key_encrypted), row.base_url
        # env fallback
        attr = _ENV_KEY_ATTR.get(provider)
        env_key = getattr(get_settings(), attr, "") if attr else ""
        return (env_key or None), (row.base_url if row else None)
