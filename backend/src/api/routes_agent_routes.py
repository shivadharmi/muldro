"""Agent route management endpoints — CRUD for dynamic routing rules."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_session
from src.services.route_resolver import RouteResolver

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────


class RouteResponse(BaseModel):
    route_id: str
    name: str
    description: str | None = None
    decision_type: str
    agent_pipeline: list[dict]
    conditions: dict | None = None
    priority: int
    enabled: bool
    keywords: list[str] | None = None
    weight: float
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RouteCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    decision_type: str = Field(..., min_length=1, max_length=64)
    agent_pipeline: list[dict] = Field(default_factory=list)
    description: str | None = None
    conditions: dict | None = None
    priority: int = Field(default=100, ge=1, le=1000)
    keywords: list[str] | None = None
    weight: float = Field(default=1.0, ge=0.0, le=10.0)


class RouteUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    decision_type: str | None = Field(default=None, min_length=1, max_length=64)
    agent_pipeline: list[dict] | None = None
    conditions: dict | None = None
    priority: int | None = Field(default=None, ge=1, le=1000)
    enabled: bool | None = None
    keywords: list[str] | None = None
    weight: float | None = Field(default=None, ge=0.0, le=10.0)


class ResolveRequest(BaseModel):
    decision: dict


class ResolveResponse(BaseModel):
    pipeline: list[dict]


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/v1/routes", response_model=list[RouteResponse])
async def list_routes(
    include_disabled: bool = False,
    _user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """List all agent routes."""
    resolver = RouteResolver(db)
    routes = await resolver.list_routes(include_disabled=include_disabled)
    return [_route_response(r) for r in routes]


@router.get("/v1/routes/{route_id}", response_model=RouteResponse)
async def get_route(
    route_id: str,
    _user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Get a single route by ID."""
    resolver = RouteResolver(db)
    route = await resolver.get_route(route_id)
    if not route:
        raise HTTPException(status_code=404, detail=f"Route {route_id} not found")
    return _route_response(route)


@router.post("/v1/routes", response_model=RouteResponse, status_code=201)
async def create_route(
    req: RouteCreateRequest,
    _user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Create a new agent route."""
    resolver = RouteResolver(db)
    route = await resolver.create_route(
        name=req.name,
        decision_type=req.decision_type,
        agent_pipeline=req.agent_pipeline,
        description=req.description,
        conditions=req.conditions,
        priority=req.priority,
        keywords=req.keywords,
        weight=req.weight,
    )
    await db.commit()
    return _route_response(route)


@router.patch("/v1/routes/{route_id}", response_model=RouteResponse)
async def update_route(
    route_id: str,
    req: RouteUpdateRequest,
    _user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Update a route's configuration."""
    resolver = RouteResolver(db)
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    route = await resolver.update_route(route_id, updates)
    if not route:
        raise HTTPException(status_code=404, detail=f"Route {route_id} not found")
    await db.commit()
    return _route_response(route)


@router.delete("/v1/routes/{route_id}", status_code=204)
async def delete_route(
    route_id: str,
    _user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Delete an agent route."""
    resolver = RouteResolver(db)
    deleted = await resolver.delete_route(route_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Route {route_id} not found")
    await db.commit()


@router.post("/v1/routes/resolve", response_model=ResolveResponse)
async def resolve_route(
    req: ResolveRequest,
    _user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Test route resolution — given a decision dict, return the resolved pipeline."""
    resolver = RouteResolver(db)
    pipeline = await resolver.resolve(req.decision)
    return ResolveResponse(pipeline=pipeline)


def _route_response(route) -> RouteResponse:
    return RouteResponse(
        route_id=route.route_id,
        name=route.name,
        description=route.description,
        decision_type=route.decision_type,
        agent_pipeline=route.agent_pipeline or [],
        conditions=route.conditions,
        priority=route.priority,
        enabled=route.enabled,
        keywords=route.keywords,
        weight=route.weight,
        created_at=route.created_at,
        updated_at=route.updated_at,
    )
