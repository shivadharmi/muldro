"""Research Agent workflow — search, cross-reference, report, push view."""

import logging

from src.workflows.workflow_registry import Workflow, WorkflowStep

logger = logging.getLogger(__name__)


async def search_memory_and_web(context: dict) -> dict:
    """Search memory and web for relevant information."""
    query = context.get("query", "")
    user_id = context.get("user_id", "")

    memory_results: list[dict] = []
    web_results: list[dict] = []

    # Search memory via MemoryService
    if user_id and query:
        try:
            from src.config.settings import get_settings
            from src.models.database import get_session_factory

            settings = get_settings()
            factory = get_session_factory()
            async with factory() as db:
                from src.services.memory_service import MemoryService

                ms = MemoryService(settings=settings, db=db)
                memories = await ms.retrieve(user_id, query, max_results=10)
                memory_results = [
                    {"fact": m.get("fact_text", ""), "type": m.get("memory_type", "")}
                    for m in memories
                ]
        except Exception:
            logger.debug("Memory search failed in research workflow", exc_info=True)

    # Search entities via WorldModel
    if user_id and query:
        try:
            from src.config.settings import get_settings
            from src.models.database import get_session_factory

            settings = get_settings()
            factory = get_session_factory()
            async with factory() as db:
                from src.services.world_model import WorldModel

                wm = WorldModel(settings=settings, db=db)
                entities = await wm.find_entity(user_id, query[:100])
                web_results = [
                    {"name": e.get("canonical_name", ""), "type": e.get("entity_type", "")}
                    for e in entities[:5]
                ]
        except Exception:
            logger.debug("Entity search failed in research workflow", exc_info=True)

    # Search via MCP bridge (web search if available)
    if query:
        try:
            from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool

            if is_mcp_tool("web_search"):
                result = await call_mcp_tool("web_search", {"query": query})
                if isinstance(result, dict):
                    web_results.extend(result.get("results", [])[:5])
        except Exception:
            logger.debug("Web search failed in research workflow", exc_info=True)

    return {"memory_results": memory_results, "web_results": web_results, "query": query}


async def cross_reference(context: dict) -> dict:
    """Cross-reference findings from memory and web sources."""
    memory_results = context.get("memory_results", [])
    web_results = context.get("web_results", [])

    if not memory_results and not web_results:
        return {"cross_referenced": [], "confidence": 0.0}

    # Use Claude to cross-reference findings
    try:
        from src.config.settings import get_anthropic_client, get_settings

        settings = get_settings()
        client = get_anthropic_client(settings)

        prompt_parts = [f"Query: {context.get('query', '')}"]
        if memory_results:
            prompt_parts.append(f"Memory findings: {memory_results[:5]}")
        if web_results:
            prompt_parts.append(f"External findings: {web_results[:5]}")
        prompt_parts.append(
            "Cross-reference these findings. Identify corroborations and contradictions. "
            'Respond with JSON: {"cross_referenced": [{"finding": "...", "sources": [...], '
            '"confidence": 0.0-1.0}], "confidence": 0.0-1.0}'
        )

        response = await client.messages.create(
            model=settings.resolved_model,
            max_tokens=1024,
            messages=[{"role": "user", "content": "\n\n".join(prompt_parts)}],
        )

        import json

        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
    except Exception:
        logger.debug("Cross-reference via Claude failed", exc_info=True)
        # Fallback: merge without analysis
        all_findings = [
            {"finding": m.get("fact", ""), "sources": ["memory"], "confidence": 0.7}
            for m in memory_results[:3]
        ] + [{"finding": str(w), "sources": ["web"], "confidence": 0.5} for w in web_results[:3]]
        return {"cross_referenced": all_findings, "confidence": 0.5}


async def generate_report(context: dict) -> dict:
    """Generate a structured research report from cross-referenced findings."""
    findings = context.get("cross_referenced", [])
    query = context.get("query", "")

    if not findings:
        return {"report": "No findings to report.", "artifact_id": None}

    try:
        from src.config.settings import get_anthropic_client, get_settings

        settings = get_settings()
        client = get_anthropic_client(settings)

        response = await client.messages.create(
            model=settings.resolved_model,
            max_tokens=2048,
            system=(
                "You are a research report generator. Produce a concise, well-structured "
                "report with sections: Executive Summary, Key Findings, Sources, and "
                "Recommendations. Use markdown formatting."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Research query: {query}\n\n"
                        f"Cross-referenced findings:\n{findings}\n\n"
                        "Generate a research report."
                    ),
                }
            ],
        )
        report = response.content[0].text
        return {"report": report, "artifact_id": None}
    except Exception:
        logger.debug("Report generation failed", exc_info=True)
        lines = [f"# Research: {query}", ""]
        for f in findings[:5]:
            lines.append(f"- {f.get('finding', '')}")
        return {"report": "\n".join(lines), "artifact_id": None}


async def push_view(context: dict) -> dict:
    """Push the research report to the user's active surface via SSE."""
    report = context.get("report", "")
    user_id = context.get("user_id", "")

    if user_id and report:
        try:
            import json

            import redis.asyncio as aioredis

            from src.config.settings import get_settings

            settings = get_settings()
            r = aioredis.from_url(settings.redis_url, decode_responses=True)
            try:
                await r.publish(
                    f"jarvis:realtime:{user_id}",
                    json.dumps(
                        {
                            "event_type": "surface.updated",
                            "surface_type": "research_report",
                            "preview": report[:200],
                        }
                    ),
                )
            finally:
                await r.aclose()
        except Exception:
            logger.debug("Failed to push research view", exc_info=True)

    return {"view_pushed": True}


research_workflow = Workflow(
    name="research",
    description="Search memory and web, cross-reference findings, generate report, push to user",
    steps=[
        WorkflowStep(name="search_memory_and_web", handler=search_memory_and_web),
        WorkflowStep(name="cross_reference", handler=cross_reference),
        WorkflowStep(name="generate_report", handler=generate_report),
        WorkflowStep(name="push_view", handler=push_view),
    ],
    tags=["research", "analysis"],
)
