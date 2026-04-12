"""System health dashboard endpoint.

Provides comprehensive system status: MCP server states, budget,
queues, agent stats, and observation health.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from src.api.deps import get_current_user_id, get_current_workspace_id
from src.config.settings import Settings, get_settings
from src.models.database import get_db

router = APIRouter()
logger = logging.getLogger(__name__)


class HealthDashboardResponse(BaseModel):
    status: str = "ok"
    budget: dict
    queues: dict
    observations: dict
    agents: dict
    traces: dict = {}
    runs: dict = {}
    components: dict = {}
    mcp: dict = {}
    # NOTE: graph_sync health (GraphSyncService.get_sync_stats()) is available
    # per-request via the service but not yet wired here — requires a module-level
    # stats accumulator or ServiceContainer singleton to expose without a DB session.


@router.get("/v1/system/dashboard", response_model=HealthDashboardResponse)
async def system_dashboard(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
):
    """Comprehensive system health dashboard."""

    budget_info = await _get_budget_info(workspace_id)
    queue_info = await _get_queue_info(workspace_id)
    observation_info = await _get_observation_info(user_id, workspace_id)
    agent_info = await _get_agent_info(workspace_id)
    trace_info = await _get_trace_metrics(workspace_id)
    run_info = await _get_run_metrics(workspace_id)

    components = {}
    try:
        from run import get_component_health

        components = get_component_health()
    except ImportError:
        pass

    mcp_health: dict = {}
    try:
        from src.connectors.mcp_bridge import get_bridge_health

        mcp_health = get_bridge_health()
    except Exception:
        pass

    return HealthDashboardResponse(
        budget=budget_info,
        queues=queue_info,
        observations=observation_info,
        agents=agent_info,
        traces=trace_info,
        runs=run_info,
        components=components,
        mcp=mcp_health,
    )


async def _get_budget_info(workspace_id: str) -> dict:
    """Get daily budget status from token_usage."""
    try:
        from src.models.database import get_session_factory
        from src.models.token_usage import TokenUsage

        async with get_session_factory()() as db:
            now_utc = datetime.now(timezone.utc)
            start_of_day = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

            result = await db.execute(
                select(
                    func.sum(TokenUsage.cost_usd),
                    func.count(TokenUsage.usage_id),
                ).where(
                    TokenUsage.created_at >= start_of_day,
                    TokenUsage.workspace_id == workspace_id,
                )
            )
            daily_spend, total_calls = result.one()
            daily_spend = daily_spend or 0.0
            total_calls = total_calls or 0

            from src.config.settings import get_settings

            settings = get_settings()
            limit = settings.daily_token_budget_usd

            pct = (daily_spend / limit * 100) if limit > 0 else 0
            mode = "normal"
            if pct >= 95:
                mode = "paused"
            elif pct >= 80:
                mode = "degraded"

            return {
                "daily_spend_usd": round(float(daily_spend), 4),
                "daily_limit_usd": limit,
                "percent_used": round(pct, 1),
                "budget_mode": mode,
                "total_calls_today": total_calls,
            }
    except Exception as e:
        logger.error("Failed to get budget info: %s", e)
        return {
            "daily_spend_usd": 0,
            "daily_limit_usd": 5.0,
            "percent_used": 0,
            "budget_mode": "unknown",
        }


async def _get_queue_info(workspace_id: str) -> dict:
    """Get queue depths for DLQ and approvals."""
    try:
        from src.models.approvals import Approval
        from src.models.database import get_session_factory
        from src.models.dead_letter import DeadLetterEntry
        from src.models.plans import Plan

        async with get_session_factory()() as db:
            dlq_result = await db.execute(
                select(func.count())
                .select_from(DeadLetterEntry)
                .where(
                    DeadLetterEntry.status == "pending",
                    DeadLetterEntry.workspace_id == workspace_id,
                )
            )
            dlq_pending = dlq_result.scalar() or 0

            approvals_result = await db.execute(
                select(func.count())
                .select_from(Approval)
                .where(Approval.status == "pending", Approval.workspace_id == workspace_id)
            )
            approvals_pending = approvals_result.scalar() or 0

            plans_result = await db.execute(
                select(func.count())
                .select_from(Plan)
                .where(
                    Plan.status.in_(["created", "executing"]),
                    Plan.workspace_id == workspace_id,
                )
            )
            plans_in_flight = plans_result.scalar() or 0

            return {
                "dlq_pending": dlq_pending,
                "approvals_pending": approvals_pending,
                "plans_in_flight": plans_in_flight,
            }
    except Exception as e:
        logger.error("Failed to get queue info: %s", e)
        return {"dlq_pending": 0, "approvals_pending": 0, "plans_in_flight": 0}


async def _get_observation_info(user_id: str, workspace_id: str) -> dict:
    """Get observation health status per source."""
    try:
        from src.models.database import get_session_factory
        from src.models.perception_state import PerceptionState

        async with get_session_factory()() as db:
            result = await db.execute(
                select(PerceptionState).where(
                    PerceptionState.user_id == user_id,
                    PerceptionState.workspace_id == workspace_id,
                )
            )
            states = result.scalars().all()

            return {
                ps.source: {
                    "last_run_at": (ps.last_run_at.isoformat() if ps.last_run_at else None),
                    "circuit_state": ps.circuit_state,
                    "event_count": ps.last_event_count,
                    "consecutive_failures": ps.consecutive_failures,
                }
                for ps in states
            }
    except Exception as e:
        logger.error("Failed to get observation info: %s", e)
        return {}


async def _get_agent_info(workspace_id: str) -> dict:
    """Get per-agent token usage stats for today."""
    try:
        from src.models.database import get_session_factory
        from src.models.token_usage import TokenUsage

        async with get_session_factory()() as db:
            now_utc = datetime.now(timezone.utc)
            start_of_day = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            result = await db.execute(
                select(
                    TokenUsage.agent_name,
                    func.count().label("calls_today"),
                    func.sum(TokenUsage.input_tokens).label("total_input"),
                    func.sum(TokenUsage.output_tokens).label("total_output"),
                    func.sum(TokenUsage.cost_usd).label("total_cost"),
                )
                .where(
                    TokenUsage.created_at >= start_of_day,
                    TokenUsage.workspace_id == workspace_id,
                )
                .group_by(TokenUsage.agent_name)
            )
            rows = result.all()

            return {
                row.agent_name: {
                    "calls_today": row.calls_today,
                    "total_input_tokens": row.total_input or 0,
                    "total_output_tokens": row.total_output or 0,
                    "total_cost_usd": round(float(row.total_cost or 0), 4),
                }
                for row in rows
            }
    except Exception as e:
        logger.error("Failed to get agent info: %s", e)
        return {}


async def _get_trace_metrics(workspace_id: str) -> dict:
    """Get aggregate trace metrics for last 24h."""
    try:
        from src.models.database import get_session_factory
        from src.services.trace_store import TraceStore

        store = TraceStore(db_factory=get_session_factory())
        return await store.get_aggregate_metrics(
            time_range_hours=24,
            workspace_id=workspace_id,
        )
    except Exception as e:
        logger.error("Failed to get trace metrics: %s", e)
        return {}


async def _get_run_metrics(workspace_id: str) -> dict:
    """Get aggregate task run metrics for today."""
    try:
        from src.models.database import get_session_factory
        from src.models.task_graph import TaskRun

        async with get_session_factory()() as db:
            now_utc = datetime.now(timezone.utc)
            start_of_day = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

            result = await db.execute(
                select(
                    TaskRun.status,
                    func.count().label("count"),
                )
                .where(
                    TaskRun.created_at >= start_of_day,
                    TaskRun.workspace_id == workspace_id,
                )
                .group_by(TaskRun.status)
            )
            rows = result.all()

            status_counts = {row.status: row.count for row in rows}
            total = sum(status_counts.values())
            completed = status_counts.get("completed", 0)
            failed = status_counts.get("failed", 0)

            return {
                "total_runs_today": total,
                "by_status": status_counts,
                "success_rate": round(completed / total, 3) if total else 0.0,
                "failure_rate": round(failed / total, 3) if total else 0.0,
            }
    except Exception as e:
        logger.error("Failed to get run metrics: %s", e)
        return {}


async def _build_store_health(
    settings: Settings,
    graph_engine,
    vector_store,
    redis,
    db: AsyncSession,
) -> dict:
    """Build health status for all data stores."""
    # Neo4j
    if graph_engine:
        neo4j_health = await graph_engine.health()
        neo4j_health["sync_stats"] = graph_engine.get_metrics()
    elif settings.neo4j_url:
        neo4j_health = {
            "status": "unreachable",
            "configured": True,
            "error": "GraphEngine failed to initialize at startup",
        }
    else:
        neo4j_health = {"status": "disabled", "configured": False}

    # Qdrant
    if vector_store:
        qdrant_health = await vector_store.health()
        qdrant_health["metrics"] = vector_store.get_metrics()
    elif settings.qdrant_url:
        qdrant_health = {
            "status": "unreachable",
            "configured": True,
            "error": "VectorStore failed to initialize at startup",
        }
    else:
        qdrant_health = {"status": "disabled", "configured": False}

    # Postgres
    postgres_health: dict = {"status": "healthy"}
    try:
        from src.models.dead_letter import DeadLetterEntry

        result = await db.execute(
            select(func.count()).where(DeadLetterEntry.status.in_(["pending", "retrying"]))
        )
        postgres_health["pending_dlq"] = result.scalar() or 0
    except Exception:
        postgres_health = {"status": "unreachable"}

    # Redis
    if redis:
        try:
            await redis.ping()
            redis_health: dict = {"status": "healthy"}
        except Exception:
            redis_health = {"status": "unreachable"}
    elif settings.redis_url:
        redis_health = {"status": "unreachable", "error": "Redis failed to initialize"}
    else:
        redis_health = {"status": "disabled"}

    # Collect degraded configured services
    degraded = []
    if neo4j_health.get("configured") and neo4j_health["status"] != "healthy":
        degraded.append("neo4j")
    if qdrant_health.get("configured") and qdrant_health["status"] != "healthy":
        degraded.append("qdrant")

    return {
        "neo4j": neo4j_health,
        "qdrant": qdrant_health,
        "postgres": postgres_health,
        "redis": redis_health,
        "degraded_services": degraded,
    }


@router.get("/v1/health/stores")
async def health_stores(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Data store health with sync metrics and degradation status."""
    graph_engine = getattr(request.app.state, "graph_engine", None)
    vector_store = getattr(request.app.state, "vector_store", None)
    redis = getattr(request.app.state, "redis", None)

    return await _build_store_health(
        settings=settings,
        graph_engine=graph_engine,
        vector_store=vector_store,
        redis=redis,
        db=db,
    )
