"""JarvisOrchestrator — the consciousness of Jarvis.

Routes user messages and system events to the right sub-agents,
manages traces, enforces budgets, and coordinates the intelligence loop.
This is the main entry point for all Jarvis interactions.
"""

import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import anthropic

from src.config.settings import Settings, get_anthropic_client
from src.orchestrator.agents import AGENTS, SubAgent
from src.orchestrator.budget import BudgetTracker
from src.orchestrator.hooks import (
    audit_post_tool_hook,
    governor_pre_tool_hook,
)
from src.orchestrator.prompts import JARVIS_SOUL
from src.orchestrator.tracing import TraceManager
from src.services.trace_store import TraceStore

logger = logging.getLogger(__name__)

# Event types published to the agent events stream
AGENT_EVENT_TYPES = {
    "plan_generated",
    "research_started",
    "research_completed",
    "approval_requested",
    "execution_started",
    "execution_completed",
    "memory_updated",
    "entity_created",
    "briefing_generated",
    "perception_completed",
}

# Model IDs for each tier
MODEL_TIERS = {
    "opus": "claude-opus-4-20250514",
    "sonnet": "claude-sonnet-4-20250514",
    "haiku": "claude-haiku-4-20250514",
}

# Agents that benefit from context enrichment (read-heavy agents)
CONTEXT_ENRICHED_AGENTS = {"planner", "presenter", "researcher", "librarian"}


class JarvisOrchestrator:
    """The Jarvis brain — orchestrates sub-agents via Claude API.

    This is NOT a ClaudeSDKClient wrapper (SDK not yet stable enough).
    Instead, we use the Anthropic API directly with structured prompts
    to simulate sub-agent routing. Each sub-agent call is a separate
    Claude API call with the agent's specific prompt and tool scope.
    """

    def __init__(
        self,
        settings: Settings,
        db_factory,
        services: dict,
    ):
        self._settings = settings
        self._db_factory = db_factory
        self._services = services
        self._client = get_anthropic_client(settings)
        self._trace_store = TraceStore(elasticsearch_url=settings.elasticsearch_url)
        self._trace_manager = TraceManager(trace_store=self._trace_store)
        self._budget = BudgetTracker(daily_limit_usd=settings.daily_token_budget_usd)
        self._tools = self._build_tool_definitions()
        self._event_bus = None  # Lazy-init when Redis available

    def _build_tool_definitions(self) -> list[dict]:
        """Build Claude tool definitions from intelligence server tools."""
        # These are the internal tools available to sub-agents.
        # External MCP tools are added dynamically based on agent scope.
        return [
            {
                "name": "ingest_event",
                "description": "Ingest an event into the Jarvis intelligence pipeline.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "event_type": {"type": "string"},
                        "entity_type": {"type": "string"},
                        "entity_id": {"type": "string"},
                        "title": {"type": "string"},
                        "summary": {"type": "string", "default": ""},
                        "actor_email": {"type": "string", "default": ""},
                        "actor_name": {"type": "string", "default": ""},
                        "occurred_at": {"type": "string", "default": ""},
                    },
                    "required": ["source", "event_type", "entity_type", "entity_id", "title"],
                },
            },
            {
                "name": "search_memory",
                "description": "Search Jarvis knowledge: memories, entities, events.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "scope": {"type": "string", "default": "all"},
                        "limit": {"type": "integer", "default": 10},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "get_entities",
                "description": "Get entities from the world model.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "default": ""},
                        "entity_type": {"type": "string", "default": ""},
                        "limit": {"type": "integer", "default": 20},
                    },
                },
            },
            {
                "name": "plan_command",
                "description": "Process a command through the Jarvis planner.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "context": {"type": "string", "default": ""},
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "evaluate_policy",
                "description": "Evaluate governance policy for a plan.",
                "input_schema": {
                    "type": "object",
                    "properties": {"plan_id": {"type": "string"}},
                    "required": ["plan_id"],
                },
            },
            {
                "name": "get_briefing",
                "description": "Generate or fetch the daily briefing.",
                "input_schema": {
                    "type": "object",
                    "properties": {"date": {"type": "string", "default": "today"}},
                },
            },
            {
                "name": "get_observation_cursor",
                "description": "Get the last observation checkpoint for a source.",
                "input_schema": {
                    "type": "object",
                    "properties": {"source": {"type": "string"}},
                    "required": ["source"],
                },
            },
            {
                "name": "update_observation_cursor",
                "description": "Update observation checkpoint after successful observation.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "cursor_type": {"type": "string"},
                        "cursor_value": {"type": "string"},
                    },
                    "required": ["source", "cursor_type", "cursor_value"],
                },
            },
            {
                "name": "report_observation",
                "description": "Report observation cycle results for health tracking.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "items_found": {"type": "integer", "default": 0},
                        "items_ingested": {"type": "integer", "default": 0},
                        "status": {"type": "string", "default": "ok"},
                        "error_message": {"type": "string", "default": ""},
                    },
                    "required": ["source"],
                },
            },
            {
                "name": "approve_action",
                "description": "Approve or reject a pending action.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "approval_id": {"type": "string"},
                        "decision": {"type": "string", "enum": ["approved", "rejected"]},
                        "reason": {"type": "string", "default": ""},
                    },
                    "required": ["approval_id", "decision"],
                },
            },
            {
                "name": "update_execution",
                "description": "Update the status of an execution.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "execution_id": {"type": "string"},
                        "status": {"type": "string"},
                        "result_summary": {"type": "string", "default": ""},
                        "error_message": {"type": "string", "default": ""},
                    },
                    "required": ["execution_id", "status"],
                },
            },
            {
                "name": "update_entity",
                "description": "Update an entity's attributes or add an alias.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string"},
                        "attributes": {"type": "string", "default": ""},
                        "add_alias": {"type": "string", "default": ""},
                    },
                    "required": ["entity_id"],
                },
            },
            {
                "name": "get_active_plans",
                "description": "Get currently active plans.",
                "input_schema": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 10}},
                },
            },
            {
                "name": "extract_preferences",
                "description": "Extract and store user preferences from interaction text.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "source_text": {"type": "string"},
                    },
                    "required": ["source_text"],
                },
            },
        ]

    def _get_tools_for_agent(self, agent: SubAgent) -> list[dict]:
        """Filter tool definitions to only those the agent can use."""
        return [t for t in self._tools if agent.can_use_tool(t["name"])]

    def _get_model_for_agent(self, agent: SubAgent) -> str:
        """Get the Claude model ID for an agent's tier."""
        model = MODEL_TIERS.get(agent.model_tier, MODEL_TIERS["sonnet"])
        # Use Bedrock model IDs if configured
        if self._settings.use_bedrock:
            return f"anthropic.{model}-v1:0"
        return model

    async def process_message(
        self,
        message: str,
        conversation_id: str | None = None,
        surface: str = "api",
        context: dict | None = None,
    ) -> dict:
        """Process a user message through the orchestrator.

        This is the main entry point for user interactions.
        The orchestrator decides which sub-agents to invoke.
        """
        trace = self._trace_manager.start_trace("user_message")

        try:
            # Step 1: Route to Planner for intent determination
            plan_result = await self._call_agent(
                "planner",
                message=f"User message: {message}\n\nContext: {json.dumps(context or {})}",
                trace=trace,
            )

            decision = self._extract_decision(plan_result)

            # Step 2: Based on decision, route to appropriate agents
            result = {
                "trace_id": trace.trace_id,
                "decision": decision.get("decision", "acknowledge"),
                "summary": decision.get("reasoning", plan_result),
            }

            # Publish plan event
            await self._publish_event(
                "plan_generated",
                "usr_default",
                {"decision": decision, "trace_id": trace.trace_id},
                trace_id=trace.trace_id,
            )

            if decision.get("decision") == "create_task":
                # Route to Governor for policy check
                gov_result = await self._call_agent(
                    "governor",
                    message=f"Evaluate this plan: {json.dumps(decision)}",
                    trace=trace,
                )
                result["governance"] = gov_result

            if decision.get("decision") in ("ask_user", "recommend", "summarize"):
                # Route to Presenter for formatting
                present_result = await self._call_agent(
                    "presenter",
                    message=f"Format this for the user ({surface}): {json.dumps(decision)}",
                    trace=trace,
                )
                result["presentation"] = present_result

            # Step 3: Persona learns from this interaction (fire-and-forget)
            try:
                await self._call_agent(
                    "persona",
                    message=f"Observe this user interaction on {surface}:\n"
                    f"User said: {message}\n"
                    f"Decision: {decision.get('decision', 'unknown')}\n"
                    f"Extract any preference signals.",
                    trace=trace,
                )
            except Exception:
                logger.debug("Persona reflection skipped", exc_info=True)

            return result

        except Exception as e:
            logger.error("process_message failed: %s", e, exc_info=True)
            return {
                "trace_id": trace.trace_id,
                "decision": "error",
                "summary": f"Error processing message: {e}",
            }
        finally:
            await self._trace_manager.finish_trace(trace.trace_id)

    async def process_message_stream(
        self,
        message: str,
        surface: str = "web",
        context: dict | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream events while processing a user message through the orchestrator.

        Yields SSE-compatible dicts with event types:
          agent_start, thinking, tool_call, tool_result, agent_done,
          response, error, done
        """
        trace = self._trace_manager.start_trace("user_message")

        try:
            yield {"event": "trace", "trace_id": trace.trace_id}

            # Step 1: Planner determines intent
            plan_text = ""
            async for evt in self._call_agent_stream(
                "planner",
                message=f"User message: {message}\n\nContext: {json.dumps(context or {})}",
                trace=trace,
            ):
                yield evt
                if evt.get("event") == "agent_done":
                    plan_text = evt.get("text", "")

            decision = self._extract_decision(plan_text)
            yield {"event": "decision", "decision": decision}

            # Step 2: Route based on decision
            if decision.get("decision") == "create_task":
                async for evt in self._call_agent_stream(
                    "governor",
                    message=f"Evaluate this plan: {json.dumps(decision)}",
                    trace=trace,
                ):
                    yield evt

            # Always route through presenter for user-facing response
            presenter_text = ""
            async for evt in self._call_agent_stream(
                "presenter",
                message=(
                    f"Format this for the user ({surface}). Be conversational and helpful.\n\n"
                    f"Original user message: {message}\n"
                    f"Planner decision: {json.dumps(decision)}\n"
                    f"Planner analysis: {plan_text[:2000]}"
                ),
                trace=trace,
            ):
                yield evt
                if evt.get("event") == "agent_done":
                    presenter_text = evt.get("text", "")
                    yield {"event": "response", "text": presenter_text}

            # Fire-and-forget persona learning (no streaming needed)
            try:
                await self._call_agent(
                    "persona",
                    message=f"Observe this user interaction on {surface}:\n"
                    f"User said: {message}\n"
                    f"Decision: {decision.get('decision', 'unknown')}\n"
                    f"Extract any preference signals.",
                    trace=trace,
                )
            except Exception:
                pass

            yield {"event": "done", "trace_id": trace.trace_id}

        except Exception as e:
            logger.error("process_message_stream failed: %s", e, exc_info=True)
            yield {"event": "error", "message": str(e)}
        finally:
            await self._trace_manager.finish_trace(trace.trace_id)

    async def _call_agent_stream(
        self,
        agent_name: str,
        message: str,
        trace=None,
        max_tool_rounds: int = 10,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Call a sub-agent, yielding events as it thinks, calls tools, etc."""
        agent = AGENTS.get(agent_name)
        if not agent:
            yield {"event": "error", "message": f"Unknown agent: {agent_name}"}
            return

        model = self._get_model_for_agent(agent)
        tools = self._apply_cache_control_to_tools(self._get_tools_for_agent(agent))
        span = trace.start_span(agent_name) if trace else None

        yield {"event": "agent_start", "agent": agent_name, "model": model}

        # Assemble context (memories + entities) for enriched agents
        context_block = await self._assemble_context(agent_name, message)
        system_blocks = self._build_system_prompt(agent, context_block)

        messages = [{"role": "user", "content": message}]

        total_input = 0
        total_output = 0
        tools_called = []
        text = ""
        start_time = time.time()

        try:
            for _round in range(max_tool_rounds):
                api_kwargs = {
                    "model": model,
                    "max_tokens": agent.max_tokens,
                    "temperature": agent.temperature,
                    "system": system_blocks,
                    "messages": messages,
                }
                if tools:
                    api_kwargs["tools"] = tools

                response = await self._client.messages.create(**api_kwargs)
                total_input += response.usage.input_tokens
                total_output += response.usage.output_tokens

                # Extract any thinking/text blocks
                text_blocks = [b for b in response.content if b.type == "text"]
                tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

                for tb in text_blocks:
                    if tb.text.strip():
                        yield {
                            "event": "thinking",
                            "agent": agent_name,
                            "text": tb.text,
                        }

                if not tool_use_blocks:
                    text = "".join(b.text for b in text_blocks)
                    break

                # Process tool calls
                tool_results = []
                for tool_block in tool_use_blocks:
                    tool_name = tool_block.name
                    tool_input = tool_block.input
                    tools_called.append(tool_name)

                    yield {
                        "event": "tool_call",
                        "agent": agent_name,
                        "tool": tool_name,
                        "input": tool_input,
                    }

                    # Governor pre-hook
                    pre_result = await governor_pre_tool_hook(
                        tool_name,
                        tool_input,
                        agent_name,
                        db_factory=self._db_factory,
                        services=self._services,
                    )

                    if not pre_result.get("allowed", True):
                        blocked_msg = {
                            "error": pre_result.get("reason", "Blocked by policy"),
                            "approval_required": pre_result.get("approval_required", False),
                            "approval_id": pre_result.get("approval_id"),
                        }
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_block.id,
                                "content": json.dumps(blocked_msg),
                            }
                        )
                        yield {
                            "event": "tool_result",
                            "agent": agent_name,
                            "tool": tool_name,
                            "result": blocked_msg,
                            "blocked": True,
                        }
                        continue

                    tool_start = time.time()
                    result = await self._execute_tool(tool_name, tool_input)
                    tool_latency = int((time.time() - tool_start) * 1000)

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": json.dumps(result)
                            if isinstance(result, dict)
                            else str(result),
                        }
                    )

                    yield {
                        "event": "tool_result",
                        "agent": agent_name,
                        "tool": tool_name,
                        "result": result,
                        "latency_ms": tool_latency,
                    }

                    await audit_post_tool_hook(
                        tool_name,
                        tool_input,
                        result,
                        agent_name,
                        trace_id=trace.trace_id if trace else None,
                        span_id=span.span_id if span else None,
                        latency_ms=tool_latency,
                        db_factory=self._db_factory,
                    )

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
            else:
                text = f"[Agent {agent_name} hit max tool rounds ({max_tool_rounds})]"

        except anthropic.APIError as e:
            logger.error("Claude API error in %s: %s", agent_name, e)
            text = f"[Agent {agent_name} API error: {e}]"
            yield {"event": "error", "agent": agent_name, "message": str(e)}
        except Exception as e:
            logger.error("Agent %s failed: %s", agent_name, e, exc_info=True)
            text = f"[Agent {agent_name} error: {e}]"
            yield {"event": "error", "agent": agent_name, "message": str(e)}

        latency_ms = int((time.time() - start_time) * 1000)

        # Record usage
        try:
            db = self._db_factory()
            await self._budget.record_usage(
                db,
                agent_name=agent_name,
                model=model,
                input_tokens=total_input,
                output_tokens=total_output,
                trigger=trace.trigger if trace else "unknown",
                trace_id=trace.trace_id if trace else None,
            )
        except Exception as e:
            logger.error("Failed to record token usage: %s", e)

        if span and trace:
            trace.end_span(
                span.span_id,
                input_tokens=total_input,
                output_tokens=total_output,
                tools_called=tools_called,
            )

        yield {
            "event": "agent_done",
            "agent": agent_name,
            "text": text,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "tools_called": tools_called,
            "latency_ms": latency_ms,
        }

    async def run_perception_cycle(self, source: str) -> dict:
        """Run a perception cycle for a specific data source.

        Observer reads new data -> Librarian extracts entities/memories ->
        Planner evaluates importance -> Presenter notifies if needed.
        """
        trace = self._trace_manager.start_trace(f"perception_{source}")

        try:
            # Check budget
            db = self._db_factory()
            budget_status = await self._budget.get_budget_status(db)
            if not self._budget.should_allow_perception(budget_status):
                logger.warning(
                    "perception_skipped_budget",
                    extra={"source": source, "mode": budget_status.budget_mode},
                )
                return {"status": "skipped", "reason": "budget_exhausted"}

            # Step 1: Observer reads new data from source
            observer_result = await self._call_agent(
                "observer",
                message=f"Observe {source} for new activity. Use get_observation_cursor "
                f"to find where we left off, then fetch only new data.",
                trace=trace,
            )

            # Step 2: Librarian extracts entities and memories
            librarian_result = await self._call_agent(
                "librarian",
                message=f"Process these observations from {source} and extract "
                f"entities and memories: {observer_result}",
                trace=trace,
            )

            # Step 3: Planner evaluates if any action is needed
            planner_result = await self._call_agent(
                "planner",
                message=f"Evaluate these observations from {source}. "
                f"Create plans for anything important: {observer_result}",
                trace=trace,
            )

            # Publish perception completed event
            await self._publish_event(
                "perception_completed",
                "usr_default",
                {"source": source, "trace_id": trace.trace_id},
                trace_id=trace.trace_id,
            )

            return {
                "status": "completed",
                "source": source,
                "trace_id": trace.trace_id,
                "observer": observer_result,
                "librarian": librarian_result,
                "planner": planner_result,
            }

        except Exception as e:
            logger.error("perception_cycle failed: %s", e, exc_info=True)
            return {"status": "error", "source": source, "error": str(e)}
        finally:
            await self._trace_manager.finish_trace(trace.trace_id)

    async def generate_briefing(self) -> dict:
        """Generate the daily briefing through the Presenter agent."""
        trace = self._trace_manager.start_trace("scheduled_briefing")
        try:
            result = await self._call_agent(
                "presenter",
                message="Generate the daily briefing. Include top priorities, "
                "changes since last briefing, pending approvals, and recommended actions.",
                trace=trace,
            )

            await self._publish_event(
                "briefing_generated",
                "usr_default",
                {"trace_id": trace.trace_id},
                trace_id=trace.trace_id,
            )

            return {"status": "completed", "trace_id": trace.trace_id, "briefing": result}
        except Exception as e:
            logger.error("generate_briefing failed: %s", e, exc_info=True)
            return {"status": "error", "error": str(e)}
        finally:
            await self._trace_manager.finish_trace(trace.trace_id)

    async def _publish_event(
        self, event_type: str, user_id: str, payload: dict, trace_id: str | None = None
    ) -> None:
        """Publish an agent action event to the event bus (best-effort)."""
        try:
            if self._event_bus is None:
                import redis.asyncio as aioredis

                self._event_bus_redis = aioredis.from_url(
                    self._settings.redis_url, decode_responses=True
                )
                from src.services.event_bus import EventBus

                self._event_bus = EventBus(self._event_bus_redis)

            stream = self._event_bus.agent_stream(user_id)
            metadata = {"trace_id": trace_id} if trace_id else {}
            await self._event_bus.publish(stream, event_type, payload, user_id, metadata)
        except Exception:
            logger.debug("Failed to publish event %s to bus", event_type, exc_info=True)

    async def _assemble_context(self, agent_name: str, message: str) -> str:
        """Pre-load relevant memories and entities for context-enriched agents.

        Returns a context block to append to the system prompt, giving the
        agent ambient awareness of the user's world without requiring it to
        explicitly call search_memory.
        """
        if agent_name not in CONTEXT_ENRICHED_AGENTS:
            return ""

        parts = []
        try:
            memory_svc = self._services.get("memory") or self._services.get("memory_service")
            if memory_svc:
                memories = await memory_svc.retrieve(
                    user_id="usr_default",
                    query=message[:500],
                    max_results=5,
                )
                if memories:
                    mem_lines = [f"- [{m['memory_type']}] {m['fact_text']}" for m in memories]
                    parts.append("RELEVANT MEMORIES:\n" + "\n".join(mem_lines))
        except Exception:
            logger.debug("Context assembly: memory retrieval failed", exc_info=True)

        try:
            world_model = self._services.get("world_model")
            if world_model:
                entities = await world_model.find_entity("usr_default", message[:200])
                if entities:
                    ent_lines = [
                        f"- [{e.get('entity_type', '?')}] {e.get('name', '?')}"
                        for e in entities[:5]
                    ]
                    parts.append("RELEVANT ENTITIES:\n" + "\n".join(ent_lines))
        except Exception:
            logger.debug("Context assembly: entity retrieval failed", exc_info=True)

        if not parts:
            return ""
        return "\n\n--- CONTEXT ---\n" + "\n\n".join(parts)

    def _build_system_prompt(self, agent: SubAgent, context: str = "") -> list[dict]:
        """Build system prompt with cache_control for prompt caching.

        Uses structured system blocks so the static soul + role prompt is cached
        across calls (5-min TTL), saving ~90% on re-reads of the system prompt.
        """
        blocks = [
            {
                "type": "text",
                "text": f"{JARVIS_SOUL}\n\n--- YOUR ROLE ---\n{agent.prompt}",
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if context:
            blocks.append({"type": "text", "text": context})
        return blocks

    def _apply_cache_control_to_tools(self, tools: list[dict]) -> list[dict]:
        """Mark the last tool definition with cache_control for tool caching."""
        if not tools:
            return tools
        tools = [dict(t) for t in tools]
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        return tools

    async def _call_agent(
        self,
        agent_name: str,
        message: str,
        trace=None,
        max_tool_rounds: int = 10,
    ) -> str:
        """Call a sub-agent with the Claude API.

        Handles tool use loops: the agent may call tools, we execute them
        and feed results back until the agent produces a final text response.
        """
        agent = AGENTS.get(agent_name)
        if not agent:
            raise ValueError(f"Unknown agent: {agent_name}")

        model = self._get_model_for_agent(agent)
        tools = self._apply_cache_control_to_tools(self._get_tools_for_agent(agent))
        span = trace.start_span(agent_name) if trace else None

        # Assemble context (memories + entities) for enriched agents
        context_block = await self._assemble_context(agent_name, message)
        system_blocks = self._build_system_prompt(agent, context_block)

        messages = [{"role": "user", "content": message}]

        total_input = 0
        total_output = 0
        tools_called = []
        start_time = time.time()

        try:
            for _round in range(max_tool_rounds):
                api_kwargs = {
                    "model": model,
                    "max_tokens": agent.max_tokens,
                    "temperature": agent.temperature,
                    "system": system_blocks,
                    "messages": messages,
                }
                if tools:
                    api_kwargs["tools"] = tools

                response = await self._client.messages.create(**api_kwargs)
                total_input += response.usage.input_tokens
                total_output += response.usage.output_tokens

                # Check if the response contains tool use
                tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

                if not tool_use_blocks:
                    # Final text response
                    text = "".join(b.text for b in response.content if b.type == "text")
                    break

                # Process tool calls
                tool_results = []
                for tool_block in tool_use_blocks:
                    tool_name = tool_block.name
                    tool_input = tool_block.input
                    tools_called.append(tool_name)

                    # Governor pre-hook for write tools
                    pre_result = await governor_pre_tool_hook(
                        tool_name,
                        tool_input,
                        agent_name,
                        db_factory=self._db_factory,
                        services=self._services,
                    )

                    if not pre_result.get("allowed", True):
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_block.id,
                                "content": json.dumps(
                                    {
                                        "error": pre_result.get("reason", "Blocked by policy"),
                                        "approval_required": pre_result.get(
                                            "approval_required", False
                                        ),
                                        "approval_id": pre_result.get("approval_id"),
                                    }
                                ),
                            }
                        )
                        continue

                    # Execute the tool
                    tool_start = time.time()
                    result = await self._execute_tool(tool_name, tool_input)
                    tool_latency = int((time.time() - tool_start) * 1000)

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": json.dumps(result)
                            if isinstance(result, dict)
                            else str(result),
                        }
                    )

                    # Audit post-hook
                    await audit_post_tool_hook(
                        tool_name,
                        tool_input,
                        result,
                        agent_name,
                        trace_id=trace.trace_id if trace else None,
                        span_id=span.span_id if span else None,
                        latency_ms=tool_latency,
                        db_factory=self._db_factory,
                    )

                # Add assistant response + tool results to conversation
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
            else:
                text = f"[Agent {agent_name} hit max tool rounds ({max_tool_rounds})]"

        except anthropic.APIError as e:
            logger.error("Claude API error in %s: %s", agent_name, e)
            text = f"[Agent {agent_name} API error: {e}]"
        except Exception as e:
            logger.error("Agent %s failed: %s", agent_name, e, exc_info=True)
            text = f"[Agent {agent_name} error: {e}]"

        latency_ms = int((time.time() - start_time) * 1000)

        # Record token usage
        try:
            db = self._db_factory()
            await self._budget.record_usage(
                db,
                agent_name=agent_name,
                model=model,
                input_tokens=total_input,
                output_tokens=total_output,
                trigger=trace.trigger if trace else "unknown",
                trace_id=trace.trace_id if trace else None,
            )
        except Exception as e:
            logger.error("Failed to record token usage: %s", e)

        # End span
        if span and trace:
            trace.end_span(
                span.span_id,
                input_tokens=total_input,
                output_tokens=total_output,
                tools_called=tools_called,
            )

        logger.info(
            "agent_call_complete",
            extra={
                "agent": agent_name,
                "model": model,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "tools_called": tools_called,
                "latency_ms": latency_ms,
                "trace_id": trace.trace_id if trace else None,
            },
        )

        return text

    async def _execute_tool(self, tool_name: str, tool_input: dict) -> dict:
        """Execute an internal tool by name."""
        from src.tools import intelligence_server

        tool_map = {
            "ingest_event": intelligence_server.ingest_event,
            "search_memory": intelligence_server.search_memory,
            "get_entities": intelligence_server.get_entities,
            "update_entity": intelligence_server.update_entity,
            "plan_command": intelligence_server.plan_command,
            "get_active_plans": intelligence_server.get_active_plans,
            "evaluate_policy": intelligence_server.evaluate_policy,
            "approve_action": intelligence_server.approve_action,
            "get_briefing": intelligence_server.get_briefing,
            "get_observation_cursor": intelligence_server.get_observation_cursor,
            "update_observation_cursor": intelligence_server.update_observation_cursor,
            "report_observation": intelligence_server.report_observation,
            "update_execution": intelligence_server.update_execution,
            "extract_preferences": intelligence_server.extract_preferences,
        }

        handler = tool_map.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            return await handler(**tool_input)
        except TypeError as e:
            # Handle mismatched arguments gracefully
            logger.warning("Tool %s argument error: %s", tool_name, e)
            return {"error": f"Invalid arguments for {tool_name}: {e}"}

    def _extract_decision(self, response_text: str) -> dict:
        """Extract structured decision from planner response."""
        # Try to parse JSON from the response
        try:
            # Look for JSON block in the response
            if "{" in response_text:
                start = response_text.index("{")
                # Find matching closing brace
                depth = 0
                for i, ch in enumerate(response_text[start:], start):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            json_str = response_text[start : i + 1]
                            return json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: extract what we can
        return {
            "decision": "acknowledge",
            "reasoning": response_text[:500],
        }
