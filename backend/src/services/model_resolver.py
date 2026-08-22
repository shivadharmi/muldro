"""Resolve (role/tier + workspace) -> a concrete ResolvedModel, merging DB
bindings + provider credentials + the code capability map. workspace_id is
optional: None resolves against the deployment-default (NULL-workspace) rows.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

# Env-var fallback per provider (used when no DB credential row exists).
_ENV_KEY_ATTR = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "google_genai": "google_api_key",
}

# Providers that authenticate without an API key (local endpoints keyed by base_url).
# Encoded twice: here, and as auth_kind == "keyless_base_url" in provider_catalog.py.
# Docs standard is durable intent, not inventory, so we don't merge them into one
# import; tests/test_provider_catalog.py::test_keyless_providers_agree_with_the_resolver
# is what stops the two encodings from drifting apart.
KEYLESS_PROVIDERS = frozenset({"ollama"})


def credential_is_usable(api_key: str | None, provider: str) -> bool:
    """The runtime's own definition of "configured", stated once.

    ``_build_resolved`` raises unless there is an api_key or the provider is keyless,
    so anything that REPORTS configuration state has to agree with exactly this
    condition -- otherwise the API calls a provider ready while every run on it dies.
    """
    return api_key is not None or provider in KEYLESS_PROVIDERS


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

        try:
            return await self._build_resolved(binding, workspace_id, thinking_enabled)
        except ModelConfigError:
            # §10 override-degradation: an AGENT override that can't be resolved (missing
            # credential or otherwise) falls back to the tier binding rather than breaking the
            # turn. A genuinely broken tier config still raises (the retry surfaces its error).
            if binding.scope_type == "agent" and agent_tier is not None:
                tier_binding = await self._binding_row("tier", agent_tier, workspace_id)
                if tier_binding is not None and tier_binding.id != binding.id:
                    logger.warning(
                        "model override for agent=%s (%s/%s) unusable; falling back to tier '%s'",
                        agent,
                        binding.provider,
                        binding.model_id,
                        agent_tier,
                    )
                    return await self._build_resolved(tier_binding, workspace_id, thinking_enabled)
            raise

    async def _build_resolved(
        self, binding: ModelBinding, workspace_id: str | None, thinking_enabled: bool
    ) -> ResolvedModel:
        spec = get_model_spec(binding.provider, binding.model_id)
        if spec is None:
            raise ModelConfigError(f"unknown model {binding.provider}/{binding.model_id}")

        api_key, base_url = await self.resolve_credential(binding.provider, workspace_id)
        if not credential_is_usable(api_key, binding.provider):
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

        Reflects the model resolve() actually runs: it replays §10 override-degradation
        so an unusable Anthropic override that falls back to a non-Anthropic tier does not
        keep Anthropic cache_control on the wrong model (H1 regression under degradation).
        Returns True when the binding or spec cannot be determined (Anthropic-safe default).
        """
        try:
            binding = await self._effective_binding(
                tier=tier, agent=agent, agent_tier=agent_tier, workspace_id=workspace_id
            )
            if binding is None:
                return True
            spec = get_model_spec(binding.provider, binding.model_id)
            return spec.supports_prompt_cache if spec else True
        except Exception:
            return True

    async def resolved_model_ref(
        self,
        *,
        tier: str | None = None,
        agent: str | None = None,
        agent_tier: str | None = None,
        workspace_id: str | None = None,
    ) -> tuple[str, str] | None:
        """The ``(provider, model_id)`` the (agent/tier) actually resolves to.

        Replays §10 override-degradation so a degraded override reports the tier model
        that actually runs, not the unusable override. None when no binding is found
        (caller falls back to the tier default).

        Callers that key a provider-scoped registry off the running model — e.g. the
        deepagents harness-profile key ``f"{provider}:{model_id}"`` — need BOTH halves:
        a workspace can override an agent onto a non-Anthropic provider, and a key
        built from a hardcoded provider would silently miss."""
        try:
            binding = await self._effective_binding(
                tier=tier, agent=agent, agent_tier=agent_tier, workspace_id=workspace_id
            )
            return (binding.provider, binding.model_id) if binding is not None else None
        except Exception:
            return None

    async def resolved_model_id(
        self,
        *,
        tier: str | None = None,
        agent: str | None = None,
        agent_tier: str | None = None,
        workspace_id: str | None = None,
    ) -> str | None:
        """The model id the (agent/tier) actually resolves to, for budget attribution.

        The model-id half of :meth:`resolved_model_ref` — see there for the
        override-degradation semantics."""
        ref = await self.resolved_model_ref(
            tier=tier, agent=agent, agent_tier=agent_tier, workspace_id=workspace_id
        )
        return ref[1] if ref is not None else None

    async def _effective_binding(
        self, *, tier, agent, agent_tier, workspace_id
    ) -> ModelBinding | None:
        """The binding resolve() actually runs, replaying §10 override-degradation: an
        agent override whose provider credential is unusable falls back to the agent's
        tier binding. Mirrors resolve()'s fallback condition (missing credential for a
        non-keyless provider) so the identity/cache helpers report the running model.

        Costs one credential lookup only when an agent override is present — tier lookups
        stay credential-free. The trade of a once-per-build lookup for correct cache
        gating / cost attribution supersedes the earlier credential-free-helper note."""
        binding = await self._pick_binding(
            tier=tier, agent=agent, agent_tier=agent_tier, workspace_id=workspace_id
        )
        if binding is None:
            return None
        if binding.scope_type == "agent" and agent_tier is not None:
            api_key, _ = await self.resolve_credential(binding.provider, workspace_id)
            if api_key is None and binding.provider not in KEYLESS_PROVIDERS:
                tier_binding = await self._binding_row("tier", agent_tier, workspace_id)
                if tier_binding is not None and tier_binding.id != binding.id:
                    return tier_binding
        return binding

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

    async def resolve_credential(self, provider, workspace_id) -> tuple[str | None, str | None]:
        """Resolve a provider's (api_key, base_url) exactly as ``resolve`` does: the
        workspace credential row, else the deployment-default (NULL) row, else the
        per-provider env fallback key. Public so the /test endpoint can probe the
        same credential source GET /model-config reports as configured."""
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
            try:
                return secret_crypto.decrypt_secret(row.api_key_encrypted), row.base_url
            except Exception:
                # An undecryptable ciphertext is an UNUSABLE credential, not a crash --
                # the master key may have been rotated out from under it. Falling through
                # means GET /v1/model-config WARNS about the provider instead of 500ing on
                # the one page that could delete the bad row, and the runtime raises an
                # articulate ModelConfigError instead of a raw Fernet error.
                logger.warning(
                    "credential for provider %s could not be decrypted; treating it as "
                    "unconfigured (has the config encryption key been rotated?)",
                    provider,
                )
        # env fallback
        attr = _ENV_KEY_ATTR.get(provider)
        env_key = getattr(get_settings(), attr, "") if attr else ""
        return (env_key or None), (row.base_url if row else None)
