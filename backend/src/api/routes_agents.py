"""Agent management endpoints — CRUD for dynamic agent configuration."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_session
from src.models.traces import ModelCall
from src.services.agent_registry import AgentRegistry

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────


class AgentResponse(BaseModel):
    agent_id: str
    name: str
    display_name: str
    description: str | None = None
    system_prompt: str
    model_tier: str
    capability_scope: list[str]
    max_tokens: int
    temperature: float
    enabled: bool
    calls_today: int = 0
    success_rate: float = 0.0
    avg_latency_ms: int = 0
    last_invoked_at: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str = Field(..., min_length=1, max_length=128)
    system_prompt: str = Field(..., min_length=1)
    description: str | None = None
    model_tier: str = Field(default="sonnet", pattern=r"^(opus|sonnet|haiku)$")
    capability_scope: list[str] = Field(default_factory=list)
    max_tokens: int = Field(default=4096, ge=256, le=32768)
    temperature: float = Field(default=0.3, ge=0.0, le=1.0)


class AgentUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    system_prompt: str | None = Field(default=None, min_length=1)
    model_tier: str | None = Field(default=None, pattern=r"^(opus|sonnet|haiku)$")
    capability_scope: list[str] | None = None
    max_tokens: int | None = Field(default=None, ge=256, le=32768)
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    enabled: bool | None = None


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/v1/agents", response_model=list[AgentResponse])
async def list_agents(
    include_disabled: bool = False,
    _user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """List all agent definitions with performance stats."""
    registry = AgentRegistry(db)
    agents = await registry.list_agents(include_disabled=include_disabled)

    # Build performance stats from model_calls
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    stats_result = await db.execute(
        select(
            ModelCall.agent_name,
            func.count().label("calls_today"),
            func.avg(ModelCall.duration_ms).label("avg_latency_ms"),
            func.max(ModelCall.created_at).label("last_invoked_at"),
            func.count().filter(ModelCall.error.is_(None)).label("success_count"),
        )
        .where(ModelCall.created_at >= today_start)
        .group_by(ModelCall.agent_name)
    )
    stats_map: dict[str, dict] = {}
    for row in stats_result.all():
        total = row.calls_today or 1
        stats_map[row.agent_name] = {
            "calls_today": row.calls_today or 0,
            "avg_latency_ms": int(row.avg_latency_ms or 0),
            "last_invoked_at": (row.last_invoked_at.isoformat() if row.last_invoked_at else None),
            "success_rate": round((row.success_count or 0) / total, 2),
        }

    return [_agent_response(a, stats_map.get(a.name, {})) for a in agents]


@router.get("/v1/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    _user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Get a single agent by ID."""
    registry = AgentRegistry(db)
    agent = await registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    return _agent_response(agent)


@router.post("/v1/agents", response_model=AgentResponse, status_code=201)
async def create_agent(
    req: AgentCreateRequest,
    _user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Create a new agent definition."""
    registry = AgentRegistry(db)

    existing = await registry.get_agent_by_name(req.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Agent '{req.name}' already exists")

    agent = await registry.create_agent(
        name=req.name,
        display_name=req.display_name,
        system_prompt=req.system_prompt,
        description=req.description,
        model_tier=req.model_tier,
        capability_scope=req.capability_scope,
        max_tokens=req.max_tokens,
        temperature=req.temperature,
    )
    await db.commit()
    return _agent_response(agent)


@router.patch("/v1/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    req: AgentUpdateRequest,
    _user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Update an agent's configuration."""
    registry = AgentRegistry(db)
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    agent = await registry.update_agent(agent_id, updates)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    await db.commit()
    return _agent_response(agent)


@router.post("/v1/agents/{agent_id}/enable", response_model=AgentResponse)
async def enable_agent(
    agent_id: str,
    _user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Enable a disabled agent."""
    registry = AgentRegistry(db)
    agent = await registry.toggle_agent(agent_id, enabled=True)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    await db.commit()
    return _agent_response(agent)


@router.post("/v1/agents/{agent_id}/disable", response_model=AgentResponse)
async def disable_agent(
    agent_id: str,
    _user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Disable an agent (it won't be loaded by the orchestrator)."""
    registry = AgentRegistry(db)
    agent = await registry.toggle_agent(agent_id, enabled=False)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    await db.commit()
    return _agent_response(agent)


def _agent_response(agent, stats: dict | None = None) -> AgentResponse:
    s = stats or {}
    return AgentResponse(
        agent_id=agent.agent_id,
        name=agent.name,
        display_name=agent.display_name,
        description=agent.description,
        system_prompt=agent.system_prompt,
        model_tier=agent.model_tier,
        capability_scope=agent.capability_scope or [],
        max_tokens=agent.max_tokens,
        temperature=agent.temperature,
        enabled=agent.enabled,
        calls_today=s.get("calls_today", 0),
        success_rate=s.get("success_rate", 0.0),
        avg_latency_ms=s.get("avg_latency_ms", 0),
        last_invoked_at=s.get("last_invoked_at"),
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )
