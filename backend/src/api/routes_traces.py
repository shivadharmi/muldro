"""Trace API routes — search and view orchestrator traces."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.deps import get_current_user_id, get_current_workspace_id
from src.orchestrator.contracts import SpanRecord
from src.services.trace_store import TraceStore

router = APIRouter()
logger = logging.getLogger(__name__)


class TraceSummary(BaseModel):
    """Lightweight trace representation for list endpoints."""

    trace_id: str
    user_id: str = ""
    trigger: str = ""
    status: str = "running"
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_thinking_tokens: int = 0
    total_cost_usd: float = 0.0
    span_count: int = 0
    error_count: int = 0
    agents_invoked: list[str] = []
    tools_called: list[str] = []
    memory_writes: int = 0


class TraceDetailResponse(TraceSummary):
    """Full trace with span details."""

    context_summary: str | None = None
    final_result: str | None = None
    approval_ids: list[str] | None = None
    spans: list[SpanRecord] = []
    metadata_json: dict | None = None


class TraceListResponse(BaseModel):
    traces: list[TraceSummary]
    count: int


class AgentPerformanceEntry(BaseModel):
    call_count: int = 0
    total_duration_ms: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_thinking_tokens: int = 0
    total_cost_usd: float = 0.0
    error_count: int = 0
    avg_duration_ms: int = 0


class AgentPerformanceResponse(BaseModel):
    agents: dict[str, AgentPerformanceEntry]
    time_range_hours: int


class AggregateMetricsResponse(BaseModel):
    total_traces: int = 0
    completed: int = 0
    failed: int = 0
    success_rate: float = 0.0
    failure_rate: float = 0.0
    avg_duration_ms: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_thinking_tokens: int = 0
    total_cost_usd: float = 0.0
    total_errors: int = 0
    total_memory_writes: int = 0
    time_range_hours: int = 24


def _get_trace_store() -> TraceStore:
    from src.config.settings import get_settings

    settings = get_settings()
    try:
        from src.models.database import get_session_factory

        db_factory = get_session_factory()
    except Exception:
        db_factory = None
    return TraceStore(elasticsearch_url=settings.elasticsearch_url, db_factory=db_factory)


@router.get("/v1/traces", response_model=TraceListResponse)
async def list_traces(
    trigger: str | None = None,
    agent_name: str | None = None,
    time_range_hours: int = 24,
    limit: int = 50,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
):
    """Search traces with optional filters."""
    store = _get_trace_store()
    traces = await store.search_traces(
        trigger=trigger,
        agent_name=agent_name,
        time_range_hours=time_range_hours,
        limit=limit,
        user_id=user_id,
        workspace_id=workspace_id,
    )
    summaries = [TraceSummary(**t) for t in traces]
    return TraceListResponse(traces=summaries, count=len(summaries))


@router.get("/v1/traces/performance", response_model=AgentPerformanceResponse)
async def agent_performance(
    time_range_hours: int = 24,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
):
    """Get aggregated per-agent performance metrics."""
    store = _get_trace_store()
    agents_raw = await store.get_agent_performance(
        time_range_hours=time_range_hours,
        user_id=user_id,
        workspace_id=workspace_id,
    )
    agents = {name: AgentPerformanceEntry(**data) for name, data in agents_raw.items()}
    return AgentPerformanceResponse(agents=agents, time_range_hours=time_range_hours)


@router.get("/v1/traces/metrics", response_model=AggregateMetricsResponse)
async def aggregate_metrics(
    time_range_hours: int = 24,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
):
    """Get aggregate observability metrics from traces."""
    store = _get_trace_store()
    metrics = await store.get_aggregate_metrics(
        time_range_hours=time_range_hours,
        user_id=user_id,
        workspace_id=workspace_id,
    )
    return AggregateMetricsResponse(**metrics)


class DecisionLogEntry(BaseModel):
    log_id: str
    trace_id: str
    span_id: str | None = None
    agent_name: str
    tool_name: str | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    decision: str | None = None
    tokens_used: int = 0
    latency_ms: int = 0
    error: str | None = None
    created_at: str | None = None


@router.get("/v1/traces/{trace_id}/decisions", response_model=list[DecisionLogEntry])
async def get_trace_decisions(
    trace_id: str,
    agent_name: str | None = None,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
):
    """Get agent decision log entries for a trace."""
    from sqlalchemy import select

    from src.models.agent_decision_log import AgentDecisionLog
    from src.models.database import get_session_factory

    async with get_session_factory()() as db:
        stmt = select(AgentDecisionLog).where(
            AgentDecisionLog.trace_id == trace_id,
            AgentDecisionLog.workspace_id == workspace_id,
        )
        if agent_name:
            stmt = stmt.where(AgentDecisionLog.agent_name == agent_name)
        stmt = stmt.order_by(AgentDecisionLog.created_at)

        result = await db.execute(stmt)
        entries = result.scalars().all()

    return [
        DecisionLogEntry(
            log_id=e.log_id,
            trace_id=e.trace_id,
            span_id=e.span_id,
            agent_name=e.agent_name,
            tool_name=e.tool_name,
            input_summary=e.input_summary,
            output_summary=e.output_summary,
            decision=e.decision,
            tokens_used=e.tokens_used,
            latency_ms=e.latency_ms,
            error=e.error,
            created_at=e.created_at.isoformat() if e.created_at else None,
        )
        for e in entries
    ]


@router.get("/v1/traces/{trace_id}", response_model=TraceDetailResponse)
async def get_trace(
    trace_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
):
    """Get a single trace by ID with full span details."""
    store = _get_trace_store()
    trace = await store.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    # Parse spans into SpanRecord models
    raw_spans = trace.pop("spans", [])
    span_records = []
    for s in raw_spans:
        try:
            span_records.append(SpanRecord(**s))
        except Exception:
            logger.debug("Failed to parse span record: %s", s, exc_info=True)

    return TraceDetailResponse(**trace, spans=span_records)
