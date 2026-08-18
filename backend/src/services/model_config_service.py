"""ModelConfigService — read/write the workspace's effective model configuration.

Effective config per tier = the workspace row (scope_type="tier") if present,
else the deployment-default (NULL-workspace) row. Agent overrides = the
workspace's scope_type="agent" rows. Provider statuses come from
ProviderCredential rows (workspace row, else NULL default).

The handler owns the transaction: put_config() stages changes but never commits.
"""

from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.model_catalog import MODEL_CATALOG
from src.config.settings import get_settings
from src.contracts.model_config import ModelConfigResponse, ProviderStatus, TierBinding
from src.models.model_binding import ModelBinding
from src.models.provider_credential import ProviderCredential
from src.services.model_resolver import _ENV_KEY_ATTR

TIER_ORDER = ("reasoning", "balanced", "fast")


class ModelConfigService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def put_config(self, workspace_id, tiers, agent_overrides) -> None:
        """UPSERT workspace tier bindings; REPLACE workspace agent overrides.

        Tiers merge (an omitted tier falls through to the deployment default). Agent
        overrides use replace semantics: the submitted list is the complete set, so a
        workspace override omitted from it is deleted and the agent reverts to its tier
        default. Does NOT commit (handler owns it).
        """
        for b in tiers:
            await self._upsert_binding(workspace_id, "tier", b.tier, b)
        for b in agent_overrides:
            # For an agent override the reused TierBinding carries the agent name
            # in the ``tier`` field; it is written as scope_key of a scope_type="agent" row.
            await self._upsert_binding(workspace_id, "agent", b.tier, b)
        await self._prune_agent_overrides(workspace_id, keep={b.tier for b in agent_overrides})

    async def _prune_agent_overrides(self, workspace_id, keep: set[str]) -> None:
        """Delete this workspace's agent-override rows whose agent is not in ``keep``.

        Scoped to the workspace's own rows (never the NULL-default rows), so an
        omitted override reverts the agent to its tier binding via the resolver.
        """
        conditions = [
            ModelBinding.workspace_id == workspace_id,
            ModelBinding.scope_type == "agent",
        ]
        if keep:  # empty keep => delete every workspace agent override
            conditions.append(ModelBinding.scope_key.notin_(keep))
        await self._db.execute(delete(ModelBinding).where(*conditions))

    async def _upsert_binding(self, workspace_id, scope_type, scope_key, binding) -> None:
        stmt = select(ModelBinding).where(
            ModelBinding.workspace_id == workspace_id,
            ModelBinding.scope_type == scope_type,
            ModelBinding.scope_key == scope_key,
        )
        existing = (await self._db.execute(stmt)).scalars().first()
        if existing is not None:
            existing.provider = binding.provider
            existing.model_id = binding.model_id
            existing.effort = binding.effort
            existing.max_tokens = binding.max_tokens
            existing.temperature = binding.temperature
            existing.enabled = True
        else:
            self._db.add(
                ModelBinding(
                    workspace_id=workspace_id,
                    scope_type=scope_type,
                    scope_key=scope_key,
                    provider=binding.provider,
                    model_id=binding.model_id,
                    effort=binding.effort,
                    max_tokens=binding.max_tokens,
                    temperature=binding.temperature,
                    enabled=True,
                )
            )

    async def get_config_response(self, workspace_id):
        rows = await self._load_bindings(workspace_id)

        # Precedence: workspace row beats the NULL-default row for a (scope_type, scope_key).
        tier_by_key: dict[str, ModelBinding] = {}
        agent_by_key: dict[str, ModelBinding] = {}
        for r in rows:
            bucket = tier_by_key if r.scope_type == "tier" else agent_by_key
            current = bucket.get(r.scope_key)
            # Prefer the workspace row (workspace_id not None) over the default.
            if current is None or (current.workspace_id is None and r.workspace_id is not None):
                bucket[r.scope_key] = r

        tiers = [
            self._to_tier_binding(TierBinding, tier_by_key[t])
            for t in TIER_ORDER
            if t in tier_by_key
        ]
        # Only the workspace's own agent rows are surfaced as overrides.
        agent_overrides = [
            self._to_tier_binding(TierBinding, r)
            for r in agent_by_key.values()
            if r.workspace_id is not None
        ]

        providers = await self._provider_statuses(workspace_id, provider_status_cls=ProviderStatus)

        return ModelConfigResponse(
            tiers=tiers,
            agent_overrides=agent_overrides,
            providers=providers,
        )

    async def _load_bindings(self, workspace_id) -> list[ModelBinding]:
        stmt = select(ModelBinding).where(
            ModelBinding.enabled.is_(True),
            or_(
                ModelBinding.workspace_id == workspace_id,
                ModelBinding.workspace_id.is_(None),
            ),
        )
        return list((await self._db.execute(stmt)).scalars().all())

    @staticmethod
    def _to_tier_binding(tier_binding_cls, r: ModelBinding):
        return tier_binding_cls(
            tier=r.scope_key,
            provider=r.provider,
            model_id=r.model_id,
            effort=r.effort,
            max_tokens=r.max_tokens,
            temperature=r.temperature,
        )

    async def provider_statuses(self, workspace_id: str) -> list[ProviderStatus]:
        """Public: the per-provider credential state for *workspace_id*.

        Exposed so the credentials routes can report the state that actually remains
        after a write or delete, instead of asserting one."""
        return await self._provider_statuses(workspace_id, provider_status_cls=ProviderStatus)

    async def _provider_statuses(self, workspace_id, *, provider_status_cls) -> list:
        stmt = select(ProviderCredential).where(
            or_(
                ProviderCredential.workspace_id == workspace_id,
                ProviderCredential.workspace_id.is_(None),
            )
        )
        cred_rows = (await self._db.execute(stmt)).scalars().all()

        # Prefer the workspace credential over the NULL default per provider.
        cred_by_provider: dict[str, ProviderCredential] = {}
        for c in cred_rows:
            current = cred_by_provider.get(c.provider)
            if current is None or (current.workspace_id is None and c.workspace_id is not None):
                cred_by_provider[c.provider] = c

        statuses = []
        for provider in MODEL_CATALOG:
            cred = cred_by_provider.get(provider)
            if cred is not None:
                # A real credential row always wins (its own status). Only a row
                # OWNED by this workspace is deletable through the credentials API —
                # the NULL-workspace row is the deployment default and shared.
                configured, status = True, cred.status
                source = "workspace" if cred.workspace_id is not None else "default"
            elif self._env_key_set(provider):
                # No row, but the deployment's env fallback key is set: the same
                # key the resolver uses, so the provider is a working default.
                configured, status, source = True, "valid", "env"
            else:
                configured, status, source = False, "unconfigured", "none"
            statuses.append(
                provider_status_cls(
                    provider=provider,
                    configured=configured,
                    status=status,
                    source=source,
                )
            )
        return statuses

    @staticmethod
    def _env_key_set(provider: str) -> bool:
        """Whether the per-provider env fallback key (used by the resolver when no
        credential row exists) is non-empty. Providers with no env attr (e.g.
        ``ollama``, which uses base_url) are never env-configured."""
        attr = _ENV_KEY_ATTR.get(provider)
        return bool(getattr(get_settings(), attr, "")) if attr else False
