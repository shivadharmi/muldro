"""ModelConfigService — read/write the workspace's effective model configuration.

Effective config per tier = the workspace row (scope_type="tier") if present,
else the deployment-default (NULL-workspace) row. Agent overrides = the
workspace's scope_type="agent" rows. Provider statuses come from
ProviderCredential rows (workspace row, else NULL default).

The handler owns the transaction: put_config() stages changes but never commits.
"""

from __future__ import annotations

import logging
from typing import get_args

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.model_catalog import MODEL_CATALOG
from src.config.provider_catalog import public_field_keys
from src.config.settings import get_settings
from src.contracts.model_config import Effort, ModelBindingDTO, ModelConfigResponse, ProviderStatus
from src.models.model_binding import ModelBinding
from src.models.provider_credential import ProviderCredential
from src.services.model_resolver import _ENV_KEY_ATTR

logger = logging.getLogger(__name__)

TIER_ORDER = ("reasoning", "balanced", "fast")


def _split_extra_config(provider: str, extra: dict | None) -> tuple[dict[str, str], list[str]]:
    """Split a stored extra_config into returnable values and secret key NAMES.

    Fails closed: a key is public only if it is a DECLARED non-secret field for this
    provider. An undeclared key is therefore treated as a secret and never echoed,
    so adding a field to a provider's schema is what makes it visible — not storing it.
    """
    if not extra:
        return {}, []
    allowed = public_field_keys(provider)
    public: dict[str, str] = {}
    for k, v in extra.items():
        if k not in allowed:
            continue  # not a declared field -> stays hidden, unchanged
        if v is None:
            # A stored JSON null means "no value". str(None) == "None" would
            # pre-fill the credential form's text box with the literal word
            # "None", which then round-trips back as a real value on Save --
            # so the key is omitted entirely rather than stringified.
            continue
        if not isinstance(v, str | int | float | bool):
            logger.warning(
                "dropping non-scalar extra_config value for %s.%s: %r", provider, k, type(v)
            )
            continue
        public[k] = str(v)
    hidden = sorted(k for k in extra if k not in allowed)
    return public, hidden


class ModelConfigService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def put_config(
        self,
        workspace_id: str,
        tiers: list[ModelBindingDTO],
        agent_overrides: list[ModelBindingDTO] | None,
    ) -> None:
        """UPSERT workspace tier bindings; REPLACE workspace agent overrides.

        Tiers merge (an omitted tier falls through to the deployment default). Agent
        overrides are three-valued: ``None`` means absent — leave existing workspace
        overrides untouched; any list (including ``[]``) is the complete replacement
        set, so a workspace override omitted from it is deleted and the agent reverts
        to its tier default. Does NOT commit (handler owns it).
        """
        for b in tiers:
            await self._upsert_binding(workspace_id, "tier", b.scope_key, b)
        if agent_overrides is None:
            # Absent, not empty: leave existing overrides untouched. An explicit []
            # still means "clear them all", which is what REPLACE semantics require.
            return
        for b in agent_overrides:
            await self._upsert_binding(workspace_id, "agent", b.scope_key, b)
        await self._prune_agent_overrides(workspace_id, keep={b.scope_key for b in agent_overrides})

    async def _prune_agent_overrides(self, workspace_id: str, keep: set[str]) -> None:
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

        tiers = [self._to_binding_dto(tier_by_key[t]) for t in TIER_ORDER if t in tier_by_key]
        # Only the workspace's own agent rows are surfaced as overrides.
        agent_overrides = [
            self._to_binding_dto(r) for r in agent_by_key.values() if r.workspace_id is not None
        ]

        providers = await self._provider_statuses(workspace_id)

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

    _VALID_EFFORTS = frozenset(get_args(Effort))

    @classmethod
    def _to_binding_dto(cls, r: ModelBinding) -> ModelBindingDTO:
        # Coerce an out-of-range stored effort rather than 500ing the whole endpoint:
        # `effort` was an unvalidated str until this change, so a legacy row may hold
        # anything. The PUT /v1/model-config write path is now Literal-validated, but
        # seed_defaults() writes ModelBinding rows straight from tuples (bypassing
        # ModelBindingDTO/Pydantic entirely) and the DB column has no CHECK constraint,
        # so other write paths (seeding, migrations, direct SQL) can still put anything
        # here. This stays as a safety net.
        effort = r.effort if r.effort in cls._VALID_EFFORTS else "none"
        if effort != r.effort:
            logger.warning(
                "coercing unknown effort %r on binding %s/%s to 'none'",
                r.effort,
                r.scope_type,
                r.scope_key,
            )
        return ModelBindingDTO(
            scope_type=r.scope_type,
            scope_key=r.scope_key,
            provider=r.provider,
            model_id=r.model_id,
            effort=effort,
            max_tokens=r.max_tokens,
            temperature=r.temperature,
        )

    async def provider_statuses(self, workspace_id: str) -> list[ProviderStatus]:
        """Public: the per-provider credential state for *workspace_id*.

        Exposed so the credentials routes can report the state that actually remains
        after a write or delete, instead of asserting one."""
        return await self._provider_statuses(workspace_id)

    async def _provider_statuses(self, workspace_id) -> list[ProviderStatus]:
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

        # Catalogued providers first, in catalog order; then any provider that has a
        # credential row but is no longer catalogued, so an orphaned row stays visible
        # and therefore removable.
        strays = sorted(p for p in cred_by_provider if p not in MODEL_CATALOG)
        statuses = []
        for provider in [*MODEL_CATALOG, *strays]:
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
            base_url = cred.base_url if cred is not None else None
            public, secret_keys = _split_extra_config(
                provider, cred.extra_config if cred is not None else None
            )
            statuses.append(
                ProviderStatus(
                    provider=provider,
                    configured=configured,
                    status=status,
                    source=source,
                    base_url=base_url,
                    extra_config_public=public,
                    extra_config_secret_keys=secret_keys,
                    catalogued=provider in MODEL_CATALOG,
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
