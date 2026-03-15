"""TraceStore — persists JarvisTrace objects for search and replay.

Uses Elasticsearch when configured, falls back to in-memory ring buffer
for development without ES.
"""

import logging
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class TraceStore:
    """Persists and queries orchestrator traces."""

    def __init__(self, elasticsearch_url: str = ""):
        self._es_url = elasticsearch_url
        self._es = None
        self._fallback: deque[dict] = deque(maxlen=500)

        if elasticsearch_url:
            try:
                from elasticsearch import AsyncElasticsearch

                self._es = AsyncElasticsearch(elasticsearch_url)
                logger.info("TraceStore using Elasticsearch at %s", elasticsearch_url)
            except ImportError:
                logger.warning("elasticsearch package not installed, using in-memory fallback")

    async def store_trace(self, trace_dict: dict) -> str:
        """Persist a completed trace. Returns trace_id."""
        trace_id = trace_dict.get("trace_id", "")
        if self._es:
            try:
                await self._es.index(
                    index="jarvis-traces",
                    id=trace_id,
                    document=trace_dict,
                )
                return trace_id
            except Exception:
                logger.exception("Failed to store trace in ES, using fallback")

        self._fallback.append(trace_dict)
        return trace_id

    async def get_trace(self, trace_id: str) -> dict | None:
        """Retrieve a single trace by ID."""
        if self._es:
            try:
                result = await self._es.get(index="jarvis-traces", id=trace_id)
                return result["_source"]
            except Exception:
                logger.debug("Trace %s not found in ES", trace_id)

        for t in self._fallback:
            if t.get("trace_id") == trace_id:
                return t
        return None

    async def search_traces(
        self,
        user_id: str | None = None,
        trigger: str | None = None,
        agent_name: str | None = None,
        time_range_hours: int = 24,
        limit: int = 50,
    ) -> list[dict]:
        """Search traces with optional filters."""
        if self._es:
            try:
                return await self._search_es(user_id, trigger, agent_name, time_range_hours, limit)
            except Exception:
                logger.exception("ES search failed, using fallback")

        return self._search_fallback(user_id, trigger, agent_name, time_range_hours, limit)

    async def _search_es(
        self,
        user_id: str | None,
        trigger: str | None,
        agent_name: str | None,
        time_range_hours: int,
        limit: int,
    ) -> list[dict]:
        must = []
        if user_id:
            must.append({"term": {"user_id": user_id}})
        if trigger:
            must.append({"term": {"trigger": trigger}})
        if agent_name:
            must.append(
                {
                    "nested": {
                        "path": "spans",
                        "query": {"term": {"spans.agent_name": agent_name}},
                    }
                }
            )
        must.append({"range": {"started_at": {"gte": f"now-{time_range_hours}h"}}})

        result = await self._es.search(
            index="jarvis-traces",
            body={
                "query": {"bool": {"must": must}} if must else {"match_all": {}},
                "sort": [{"started_at": "desc"}],
                "size": limit,
            },
        )
        return [hit["_source"] for hit in result["hits"]["hits"]]

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

    async def get_agent_performance(self, time_range_hours: int = 24) -> dict[str, dict]:
        """Aggregate performance metrics per agent."""
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
