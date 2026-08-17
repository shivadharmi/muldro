"""Model-config response schemas — the neutral contract both the API layer
(``src/api/routes_model_config.py``) and the service layer
(``src/services/model_config_service.py``) import downward from.

Keeping these here avoids a services→api up-import: the service builds the
response models without importing the routes module.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TierBinding(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())
    # For an agent override this field carries the AGENT NAME (round-tripped as the
    # scope_key of a scope_type="agent" ModelBinding); for a tier it is the tier name.
    tier: str
    provider: str
    model_id: str
    effort: str = "none"
    max_tokens: int = 4096
    temperature: float | None = None


class ProviderStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")
    provider: str
    configured: bool
    status: str


class ModelConfigResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tiers: list[TierBinding]
    agent_overrides: list[TierBinding]
    providers: list[ProviderStatus]
