"""TraceStore — persists MuldroTrace objects for search and replay.

Uses PostgreSQL as primary store. Falls back to in-memory ring buffer
when no DB session factory is available. Optionally indexes to
Elasticsearch for full-text search.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ulid import ULID

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class TraceStore:
    """Persists and queries orchestrator traces."""

    def __init__(
        self,
        db_factory: Callable[[], AsyncSession] | None = None,
    ):
        self._db_factory = db_factory
        self._fallback: deque[dict] = deque(maxlen=500)

    async def store_trace(
        self,
        trace_dict: dict,
        user_id: str,
        workspace_id: str = "",
        run_id: str | None = None,
    ) -> str:
        """Persist a completed trace. Returns trace_id.

        ``run_id`` ties the trace to a specific TaskRun so the detail
        endpoint can resolve observability metrics either via
        ``task_runs.trace_id`` or the reverse ``traces.run_id`` index.
        """
        trace_id = trace_dict.get("trace_id", "")

        # Primary: persist to Postgres
        if self._db_factory:
            try:
                await self._store_to_db(trace_dict, user_id, workspace_id, run_id=run_id)
            except Exception:
                logger.warning("Failed to persist trace to DB", exc_info=True)
                self._fallback.append(trace_dict)

        # Fallback: in-memory if no DB
        if not self._db_factory:
            self._fallback.append(trace_dict)

        return trace_id

    async def _store_to_db(
        self,
        trace_dict: dict,
        user_id: str,
        workspace_id: str = "",
        run_id: str | None = None,
    ) -> None:
        """Write trace + model_calls to Postgres."""
        from src.models.traces import ModelCall, Trace
        from src.orchestrator.budget import BudgetTracker

        budget = BudgetTracker()
        spans = trace_dict.get("spans", [])
        agents = list({s.get("agent_name", "") for s in spans})
        tools = []
        for s in spans:
            tools.extend(s.get("tools_called", []))
        tools = list(set(tools))

        total_input = trace_dict.get("total_input_tokens", 0)
        total_output = trace_dict.get("total_output_tokens", 0)
        error_count = sum(1 for s in spans if s.get("error"))

        ended_at = None
        if trace_dict.get("ended_at"):
            ended_at = datetime.fromisoformat(trace_dict["ended_at"])

        # Calculate costs from spans
        total_cost = 0.0
        total_cache_creation = 0
        total_cache_read = 0
        total_thinking = 0
        model_calls = []
        for span in spans:
            span_cost = span.get("cost_usd", 0.0)
            if not span_cost:
                span_cost = budget.calculate_cost(
                    model=span.get("model", "unknown"),
                    input_tokens=span.get("input_tokens", 0),
                    output_tokens=span.get("output_tokens", 0),
                    cache_creation_input_tokens=span.get("cache_creation_input_tokens", 0),
                    cache_read_input_tokens=span.get("cache_read_input_tokens", 0),
                    thinking_tokens=span.get("thinking_tokens", 0),
                )
            total_cost += span_cost
            total_cache_creation += span.get("cache_creation_input_tokens", 0)
            total_cache_read += span.get("cache_read_input_tokens", 0)
            total_thinking += span.get("thinking_tokens", 0)

            mc = ModelCall(
                call_id=span.get("span_id", f"call_{ULID()}"),
                trace_id=trace_dict.get("trace_id", ""),
                workspace_id=workspace_id,
                agent_name=span.get("agent_name", "unknown"),
                model=span.get("model", "unknown"),
                input_tokens=span.get("input_tokens", 0),
                output_tokens=span.get("output_tokens", 0),
                cache_creation_input_tokens=span.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=span.get("cache_read_input_tokens", 0),
                thinking_tokens=span.get("thinking_tokens", 0),
                cost_usd=round(span_cost, 6),
                duration_ms=span.get("duration_ms", 0),
                tools_called=span.get("tools_called") or None,
                decision=span.get("decision"),
                error=span.get("error"),
            )
            model_calls.append(mc)

        trace = Trace(
            trace_id=trace_dict.get("trace_id", f"trace_{ULID()}"),
            user_id=user_id,
            workspace_id=workspace_id,
            run_id=run_id,
            trigger=trace_dict.get("trigger", "unknown"),
            status="completed" if ended_at else "running",
            started_at=datetime.fromisoformat(trace_dict["started_at"]),
            ended_at=ended_at,
            duration_ms=trace_dict.get("duration_ms", 0),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_cache_creation_tokens=total_cache_creation,
            total_cache_read_tokens=total_cache_read,
            total_thinking_tokens=total_thinking,
            total_cost_usd=round(total_cost, 6),
            span_count=len(spans),
            error_count=error_count,
            agents_invoked=agents or None,
            tools_called=tools or None,
            context_summary=trace_dict.get("context_summary"),
            final_result=trace_dict.get("final_result"),
            memory_writes=trace_dict.get("memory_writes", 0),
            approval_ids=trace_dict.get("approval_ids"),
            spans_json=spans or None,
            metadata_json=trace_dict.get("metadata"),
        )

        from sqlalchemy import delete

        async with self._db_factory() as db:
            # Upsert by trace_id: a trace may be stored more than once for the
            # same id — first as a partial checkpoint when a run pauses at an
            # approval gate, then again (complete) at terminal finalize. A plain
            # INSERT would violate the traces PK on the second write, so clear
            # any prior rows for this trace_id (+ its ModelCalls) and re-insert
            # the latest version. Resume segments use a fresh trace_id and are
            # unaffected.
            trace_id = trace.trace_id
            await db.execute(delete(ModelCall).where(ModelCall.trace_id == trace_id))
            await db.execute(delete(Trace).where(Trace.trace_id == trace_id))
            db.add(trace)
            for mc in model_calls:
                db.add(mc)
            await db.commit()

    async def get_trace(self, trace_id: str) -> dict | None:
        """Retrieve a single trace by ID."""
        if self._db_factory:
            try:
                return await self._get_trace_from_db(trace_id)
            except Exception:
                logger.debug("DB trace lookup failed for %s", trace_id, exc_info=True)

        for t in self._fallback:
            if t.get("trace_id") == trace_id:
                return t
        return None

    async def _get_trace_from_db(self, trace_id: str) -> dict | None:
        from sqlalchemy import select

        from src.models.traces import Trace

        async with self._db_factory() as db:
            result = await db.execute(select(Trace).where(Trace.trace_id == trace_id))
            trace = result.scalar_one_or_none()
            if not trace:
                return None
            return _trace_to_dict(trace)

    async def search_traces(
        self,
        user_id: str | None = None,
        trigger: str | None = None,
        agent_name: str | None = None,
        time_range_hours: int = 24,
        limit: int = 50,
        workspace_id: str | None = None,
    ) -> list[dict]:
        """Search traces with optional filters."""
        if self._db_factory:
            try:
                return await self._search_db(
                    user_id, trigger, agent_name, time_range_hours, limit, workspace_id
                )
            except Exception:
                logger.debug("DB trace search failed", exc_info=True)

        return self._search_fallback(user_id, trigger, agent_name, time_range_hours, limit)

    async def _search_db(
        self,
        user_id: str | None,
        trigger: str | None,
        agent_name: str | None,
        time_range_hours: int,
        limit: int,
        workspace_id: str | None = None,
    ) -> list[dict]:
        from datetime import timedelta

        from sqlalchemy import select

        from src.models.traces import Trace

        cutoff = datetime.now(timezone.utc) - timedelta(hours=time_range_hours)
        stmt = select(Trace).where(Trace.started_at >= cutoff)

        if user_id:
            stmt = stmt.where(Trace.user_id == user_id)
        if workspace_id:
            stmt = stmt.where(Trace.workspace_id == workspace_id)
        if trigger:
            stmt = stmt.where(Trace.trigger == trigger)
        if agent_name:
            stmt = stmt.where(Trace.agents_invoked.any(agent_name))

        stmt = stmt.order_by(Trace.started_at.desc()).limit(limit)

        async with self._db_factory() as db:
            result = await db.execute(stmt)
            traces = result.scalars().all()
            return [_trace_to_dict(t) for t in traces]

    def _search_fallback(
        self,
        user_id: str | None,
        trigger: str | None,
        agent_name: str | None,
        time_range_hours: int,
        limit: int,
    ) -> list[dict]:
        cutoff = datetime.now(timezone.utc).timestamp() - (time_range_hours * 3600)
        results = []
        for t in reversed(list(self._fallback)):
            started = t.get("started_at", "")
            if isinstance(started, str) and started:
                try:
                    ts = datetime.fromisoformat(started).timestamp()
                    if ts < cutoff:
                        continue
                except ValueError:
                    pass
            if trigger and t.get("trigger") != trigger:
                continue
            if agent_name:
                spans = t.get("spans", [])
                if not any(s.get("agent_name") == agent_name for s in spans):
                    continue
            results.append(t)
            if len(results) >= limit:
                break
        return results

    async def get_agent_performance(
        self,
        time_range_hours: int = 24,
        user_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, dict]:
        """Aggregate performance metrics per agent."""
        if self._db_factory:
            try:
                return await self._agent_performance_db(time_range_hours, user_id, workspace_id)
            except Exception:
                logger.debug("DB agent performance query failed", exc_info=True)

        return await self._agent_performance_fallback(time_range_hours)

    async def _agent_performance_db(
        self,
        time_range_hours: int,
        user_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, dict]:
        from datetime import timedelta

        from sqlalchemy import func, select

        from src.models.traces import ModelCall

        cutoff = datetime.now(timezone.utc) - timedelta(hours=time_range_hours)
        async with self._db_factory() as db:
            stmt = select(
                ModelCall.agent_name,
                func.count().label("call_count"),
                func.sum(ModelCall.duration_ms).label("total_duration_ms"),
                func.sum(ModelCall.input_tokens).label("total_input_tokens"),
                func.sum(ModelCall.output_tokens).label("total_output_tokens"),
                func.sum(ModelCall.cache_creation_input_tokens).label(
                    "total_cache_creation_tokens"
                ),
                func.sum(ModelCall.cache_read_input_tokens).label("total_cache_read_tokens"),
                func.sum(ModelCall.thinking_tokens).label("total_thinking_tokens"),
                func.sum(ModelCall.cost_usd).label("total_cost_usd"),
                func.count(ModelCall.error).label("error_count"),
            ).where(ModelCall.created_at >= cutoff)

            if workspace_id:
                stmt = stmt.where(ModelCall.workspace_id == workspace_id)

            stmt = stmt.group_by(ModelCall.agent_name)
            result = await db.execute(stmt)
            agents = {}
            for row in result.all():
                count = row.call_count or 0
                total_ms = row.total_duration_ms or 0
                agents[row.agent_name] = {
                    "call_count": count,
                    "total_duration_ms": total_ms,
                    "total_input_tokens": row.total_input_tokens or 0,
                    "total_output_tokens": row.total_output_tokens or 0,
                    "total_cache_creation_tokens": row.total_cache_creation_tokens or 0,
                    "total_cache_read_tokens": row.total_cache_read_tokens or 0,
                    "total_thinking_tokens": row.total_thinking_tokens or 0,
                    "total_cost_usd": round(float(row.total_cost_usd or 0), 6),
                    "error_count": row.error_count or 0,
                    "avg_duration_ms": total_ms // count if count else 0,
                }
            return agents

    async def _agent_performance_fallback(self, time_range_hours: int) -> dict[str, dict]:
        traces = await self.search_traces(time_range_hours=time_range_hours, limit=200)
        agents: dict[str, dict] = {}
        for trace in traces:
            for span in trace.get("spans", []):
                name = span.get("agent_name", "unknown")
                if name not in agents:
                    agents[name] = {
                        "call_count": 0,
                        "total_duration_ms": 0,
                        "total_input_tokens": 0,
                        "total_output_tokens": 0,
                        "error_count": 0,
                    }
                a = agents[name]
                a["call_count"] += 1
                a["total_duration_ms"] += span.get("duration_ms", 0)
                a["total_input_tokens"] += span.get("input_tokens", 0)
                a["total_output_tokens"] += span.get("output_tokens", 0)
                if span.get("error"):
                    a["error_count"] += 1
        for a in agents.values():
            if a["call_count"] > 0:
                a["avg_duration_ms"] = a["total_duration_ms"] // a["call_count"]
        return agents

    async def get_aggregate_metrics(
        self,
        user_id: str | None = None,
        time_range_hours: int = 24,
        workspace_id: str | None = None,
    ) -> dict:
        """Compute aggregate observability metrics from traces."""
        if self._db_factory:
            try:
                return await self._aggregate_metrics_db(user_id, time_range_hours, workspace_id)
            except Exception:
                logger.debug("DB aggregate metrics failed", exc_info=True)

        return self._aggregate_metrics_fallback(time_range_hours)

    async def _aggregate_metrics_db(
        self,
        user_id: str | None,
        time_range_hours: int,
        workspace_id: str | None = None,
    ) -> dict:
        from datetime import timedelta

        from sqlalchemy import case, func, select

        from src.models.traces import Trace

        cutoff = datetime.now(timezone.utc) - timedelta(hours=time_range_hours)
        async with self._db_factory() as db:
            stmt = select(
                func.count().label("total_traces"),
                func.count(case((Trace.status == "completed", 1))).label("completed"),
                func.count(case((Trace.status == "failed", 1))).label("failed"),
                func.avg(Trace.duration_ms).label("avg_duration_ms"),
                func.sum(Trace.total_input_tokens).label("total_input_tokens"),
                func.sum(Trace.total_output_tokens).label("total_output_tokens"),
                func.sum(Trace.total_cache_creation_tokens).label("total_cache_creation_tokens"),
                func.sum(Trace.total_cache_read_tokens).label("total_cache_read_tokens"),
                func.sum(Trace.total_thinking_tokens).label("total_thinking_tokens"),
                func.sum(Trace.total_cost_usd).label("total_cost_usd"),
                func.sum(Trace.error_count).label("total_errors"),
                func.sum(Trace.memory_writes).label("total_memory_writes"),
            ).where(Trace.started_at >= cutoff)

            if user_id:
                stmt = stmt.where(Trace.user_id == user_id)
            if workspace_id:
                stmt = stmt.where(Trace.workspace_id == workspace_id)

            row = (await db.execute(stmt)).one()
            total = row.total_traces or 0
            completed = row.completed or 0
            failed = row.failed or 0

            return {
                "total_traces": total,
                "completed": completed,
                "failed": failed,
                "success_rate": round(completed / total, 3) if total else 0.0,
                "failure_rate": round(failed / total, 3) if total else 0.0,
                "avg_duration_ms": int(row.avg_duration_ms or 0),
                "total_input_tokens": row.total_input_tokens or 0,
                "total_output_tokens": row.total_output_tokens or 0,
                "total_cache_creation_tokens": row.total_cache_creation_tokens or 0,
                "total_cache_read_tokens": row.total_cache_read_tokens or 0,
                "total_thinking_tokens": row.total_thinking_tokens or 0,
                "total_cost_usd": round(float(row.total_cost_usd or 0), 4),
                "total_errors": row.total_errors or 0,
                "total_memory_writes": row.total_memory_writes or 0,
                "time_range_hours": time_range_hours,
            }

    def _aggregate_metrics_fallback(self, time_range_hours: int) -> dict:
        cutoff = datetime.now(timezone.utc).timestamp() - (time_range_hours * 3600)
        total = 0
        completed = 0
        durations = []
        for t in self._fallback:
            started = t.get("started_at", "")
            if isinstance(started, str) and started:
                try:
                    ts = datetime.fromisoformat(started).timestamp()
                    if ts < cutoff:
                        continue
                except ValueError:
                    pass
            total += 1
            if t.get("ended_at"):
                completed += 1
            if t.get("duration_ms"):
                durations.append(t["duration_ms"])

        return {
            "total_traces": total,
            "completed": completed,
            "failed": total - completed,
            "success_rate": round(completed / total, 3) if total else 0.0,
            "failure_rate": round((total - completed) / total, 3) if total else 0.0,
            "avg_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost_usd": 0.0,
            "total_errors": 0,
            "total_memory_writes": 0,
            "time_range_hours": time_range_hours,
        }


def _trace_to_dict(trace) -> dict:
    """Convert a Trace model to dict matching the trace API response shape."""
    return {
        "trace_id": trace.trace_id,
        "user_id": trace.user_id,
        "trigger": trace.trigger,
        "status": trace.status,
        "started_at": trace.started_at.isoformat() if trace.started_at else None,
        "ended_at": trace.ended_at.isoformat() if trace.ended_at else None,
        "duration_ms": trace.duration_ms or 0,
        "total_input_tokens": trace.total_input_tokens or 0,
        "total_output_tokens": trace.total_output_tokens or 0,
        "total_cache_creation_tokens": getattr(trace, "total_cache_creation_tokens", 0) or 0,
        "total_cache_read_tokens": getattr(trace, "total_cache_read_tokens", 0) or 0,
        "total_thinking_tokens": getattr(trace, "total_thinking_tokens", 0) or 0,
        "total_cost_usd": float(trace.total_cost_usd or 0.0),
        "span_count": trace.span_count or 0,
        "error_count": trace.error_count or 0,
        "agents_invoked": trace.agents_invoked or [],
        "tools_called": trace.tools_called or [],
        "context_summary": trace.context_summary,
        "final_result": trace.final_result,
        "memory_writes": trace.memory_writes or 0,
        "approval_ids": trace.approval_ids,
        "spans": trace.spans_json or [],
        "metadata_json": trace.metadata_json,
    }
