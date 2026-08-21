"""Model-config response schemas — the neutral contract both the API layer
(``src/api/routes_model_config.py``) and the service layer
(``src/services/model_config_service.py``) import downward from.

Keeping these here avoids a services→api up-import: the service builds the
response models without importing the routes module.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BindingScope = Literal["tier", "agent"]
Effort = Literal["none", "low", "medium", "high"]


class ModelBindingDTO(BaseModel):
    """One model binding, carrying the same (scope_type, scope_key) pair the DB stores.

    Replaces TierBinding, which flattened both into a single ``tier`` field and left
    ``scope_type`` to be recovered from whichever response array the binding arrived
    in — a lossy projection of its own storage model.
    """

    model_config = ConfigDict(extra="ignore", protected_namespaces=())
    scope_type: BindingScope
    scope_key: str  # a tier name ("balanced") or an agent name ("planner")
    provider: str
    model_id: str
    # "none" is legal and meaningful: models with thinking_style="none" take no effort.
    effort: Effort = "none"
    # >=1: max_tokens=0 yields a legacy thinking budget of -1 and breaks every call.
    max_tokens: int = Field(4096, ge=1)
    temperature: float | None = None


class ProviderStatus(BaseModel):
    """A provider's credential state, and WHERE that credential comes from.

    ``configured`` is true for three different sources, only one of which the
    workspace can delete. ``source`` names which, so a client can offer Remove
    for a workspace-owned row and not for one inherited from the deployment
    default row or a process env var — DELETE only ever removes the workspace
    row, so offering it elsewhere is a control that silently does nothing.
    """

    model_config = ConfigDict(extra="ignore")
    provider: str
    configured: bool
    status: str
    # "workspace" — this workspace's own ProviderCredential row (deletable)
    # "default"   — the NULL-workspace deployment-default row (not deletable here)
    # "env"       — the per-provider env fallback key (not deletable at all)
    # "none"      — no credential resolves; `configured` is False
    source: Literal["workspace", "default", "env", "none"] = "none"


class ModelConfigResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tiers: list[ModelBindingDTO]
    agent_overrides: list[ModelBindingDTO]
    providers: list[ProviderStatus]
