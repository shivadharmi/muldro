"""Seed deployment-default (NULL-workspace) model bindings reproducing today's Claude setup."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.model_binding import ModelBinding
from src.models.provider_credential import ProviderCredential

# reasoning/balanced/fast -> today's exact Claude ids + effort matching current thinking budgets
_DEFAULT_TIER_BINDINGS = [
    ("reasoning", "anthropic", "claude-opus-4-8", "high", 8192),
    ("balanced", "anthropic", "claude-sonnet-4-6", "medium", 4096),
    ("fast", "anthropic", "claude-haiku-4-5-20251001", "low", 4096),
]


async def has_encrypted_provider_credential(db: AsyncSession) -> bool:
    """True iff any provider_credentials row has a non-null api_key_encrypted (i.e. a
    ciphertext that requires the master key to decrypt)."""
    stmt = (
        select(ProviderCredential).where(ProviderCredential.api_key_encrypted.isnot(None)).limit(1)
    )
    return (await db.execute(stmt)).scalars().first() is not None


class ModelConfigRegistry:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def seed_defaults(self) -> int:
        existing = {
            (r.scope_type, r.scope_key)
            for r in (
                await self._db.execute(
                    select(ModelBinding).where(ModelBinding.workspace_id.is_(None))
                )
            )
            .scalars()
            .all()
        }
        added = 0
        for scope_key, provider, model_id, effort, max_tokens in _DEFAULT_TIER_BINDINGS:
            if ("tier", scope_key) in existing:
                continue
            self._db.add(
                ModelBinding(
                    workspace_id=None,
                    scope_type="tier",
                    scope_key=scope_key,
                    provider=provider,
                    model_id=model_id,
                    effort=effort,
                    max_tokens=max_tokens,
                )
            )
            added += 1
        if added:
            await self._db.flush()
        return added
