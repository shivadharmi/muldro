"""Trace API routes — search and view orchestrator traces."""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from src.services.trace_store import TraceStore

router = APIRouter()
logger = logging.getLogger(__name__)


class TraceResponse(BaseModel):
    trace_id: str
    trigger: str
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    spans: list[dict] = []


class TraceListResponse(BaseModel):
    traces: list[dict]
    count: int


class AgentPerformanceResponse(BaseModel):
    agents: dict[str, dict]
    time_range_hours: int


def _get_trace_store() -> TraceStore:
    from src.config.settings import get_settings

    settings = get_settings()
    return TraceStore(elasticsearch_url=settings.elasticsearch_url)


@router.get("/v1/traces", response_model=TraceListResponse)
async def list_traces(
    trigger: str | None = None,
    agent_name: str | None = None,
    time_range_hours: int = 24,
    limit: int = 50,
):
    """Search traces with optional filters."""
    store = _get_trace_store()
    traces = await store.search_traces(
        trigger=trigger,
        agent_name=agent_name,
        time_range_hours=time_range_hours,
        limit=limit,
    )
    return TraceListResponse(traces=traces, count=len(traces))


@router.get("/v1/traces/performance", response_model=AgentPerformanceResponse)
async def agent_performance(time_range_hours: int = 24):
    """Get aggregated per-agent performance metrics."""
    store = _get_trace_store()
    agents = await store.get_agent_performance(time_range_hours=time_range_hours)
    return AgentPerformanceResponse(agents=agents, time_range_hours=time_range_hours)


@router.get("/v1/traces/{trace_id}", response_model=TraceResponse)
async def get_trace(trace_id: str):
    """Get a single trace by ID."""
    store = _get_trace_store()
    trace = await store.get_trace(trace_id)
    if not trace:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Trace not found")
    return TraceResponse(**trace)
