"""AgentInvoker — the shared sub-agent invocation engine.

Extracted from ``JarvisOrchestrator`` (god-object decomposition, 2026-06-19).
This is the primitive that runs a single sub-agent through the agent loop, used by
BOTH the chat path (streaming) and the perception path (non-streaming). Extracting
it as its own collaborator is what breaks the would-be chat<->perception cycle:
both depend downward on AgentInvoker instead of on each other.

Depends on ToolExecutor (tools + dispatch) and ContextAssembler (ambient context),
plus the agent-execution resources (client, budget, circuit breaker) owned by the
orchestrator. The current agent set is pushed in via ``set_agents`` so the
orchestrator stays the single source of truth across ``load_agents_from_db``.
"""

import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.config.models import BEDROCK_MODEL_TIERS, MODEL_TIERS
from src.config.settings import Settings
from src.deep_runtime.agent_builder import build_deep_agent
from src.deep_runtime.authorization import AuthorizationSource
from src.deep_runtime.middleware.jarvis_tool_dispatcher import make_jarvis_tool_dispatcher
from src.deep_runtime.middleware.trust_gate import make_trust_gate_middleware
from src.deep_runtime.prompt_bridge import build_system_message
from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.deep_runtime.tool_bridge import build_tool_shells
from src.errors import _GENERIC_CODE, _GENERIC_MESSAGE, new_correlation_id
from src.middleware.observability import get_correlation_id
from src.models.approvals import Approval
from src.models.ids import generate_id
from src.orchestrator.agent_loop import (
    LoopAgentStart,
    LoopDone,
    LoopError,
    LoopTextDelta,
    LoopThinking,
    LoopToolCall,
    LoopToolResult,
    agent_loop,
)
from src.orchestrator.agents import SubAgent
from src.orchestrator.budget import BudgetTracker
from src.orchestrator.context_assembler import ContextAssembler
from src.orchestrator.prompts import JARVIS_SOUL_CORE
from src.orchestrator.services import ServiceContainer
from src.orchestrator.tool_executor import ToolExecutor
from src.services.metrics_service import AGENT_RUNTIME_CALLS

logger = logging.getLogger(__name__)


class AgentInvoker:
    """Runs a single sub-agent through the agent loop (streaming or batch)."""

    def __init__(
        self,
        settings: Settings,
        client,
        services: ServiceContainer | None,
        budget: BudgetTracker,
        circuit_breaker,
        db_factory_provider,
        tool_executor: ToolExecutor,
        context: ContextAssembler,
        agents: dict[str, SubAgent],
        checkpointer_provider=None,
    ):
        """Initialise the invoker.

        ``checkpointer_provider`` is a zero-arg callable that returns a LangGraph
        checkpointer for the deep runtime (Step 6A.5). Defaults to a no-op that lets
        the deep branch fall back to ``MemorySaver``; replaced at lifespan with a
        durable backend when 6B lands.
        """
        self._settings = settings
        self._client = client
        self._services = services
        self._budget = budget
        self._circuit_breaker = circuit_breaker
        self._db_factory_provider = db_factory_provider
        self._tool_executor = tool_executor
        self._context = context
        self._agents = agents
        self._checkpointer_provider = checkpointer_provider or (lambda: None)

    @property
    def _db_factory(self):
        """Resolve the current DB session factory live via the provider."""
        return self._db_factory_provider()

    def set_agents(self, agents: dict[str, SubAgent]) -> None:
        """Replace the agent set (called after a runtime DB agent reload)."""
        self._agents = agents

    def get_model_for_agent(self, agent: SubAgent) -> str:
        """Get the Claude model ID for an agent's tier."""
        if self._settings.use_bedrock:
            return BEDROCK_MODEL_TIERS.get(agent.model_tier, BEDROCK_MODEL_TIERS["sonnet"])
        return MODEL_TIERS.get(agent.model_tier, MODEL_TIERS["sonnet"])

    def build_system_prompt(
        self, agent: SubAgent, context: str = "", capability_summary: str = ""
    ) -> list[dict]:
        """Build system prompt with cache_control for prompt caching.

        For the Planner, injects the runtime capability summary into
        PLANNER_PROMPT_V2 (replacing the {capability_summary} placeholder).
        Other agents get JARVIS_SOUL_CORE + their role prompt unchanged.
        """
        soul = JARVIS_SOUL_CORE

        prompt = agent.prompt
        if agent.name == "planner":
            prompt = prompt.format(
                capability_summary=capability_summary or "No capabilities connected yet."
            )

        blocks = [
            {
                "type": "text",
                "text": f"{soul}\n\n--- YOUR ROLE ---\n{prompt}",
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if context:
            blocks.append({"type": "text", "text": context})
        return blocks

    async def _resolve_tools(
        self, agent: SubAgent, workspace_id: str, tools_override: list[dict] | None
    ) -> list[dict]:
        """Apply cache-control to either the override or the agent-scoped tool list."""
        if tools_override is not None:
            return self._tool_executor.apply_cache_control_to_tools(tools_override)
        return self._tool_executor.apply_cache_control_to_tools(
            await self._tool_executor.get_tools_for_agent(agent, workspace_id=workspace_id)
        )

    async def _maybe_capability_summary(
        self, agent_name: str, capability_summary: str, workspace_id: str
    ) -> str:
        """Auto-generate the planner capability summary when not supplied."""
        if agent_name == "planner" and not capability_summary:
            try:
                from src.orchestrator.capability_summary import generate_capability_summary

                async with self._db_factory() as db:
                    return await generate_capability_summary(db, workspace_id)
            except Exception:
                logger.debug("Failed to generate capability summary", exc_info=True)
        return capability_summary

    async def _build_deep_agent_for(
        self,
        agent: SubAgent,
        tools: list,
        *,
        user_id: str,
        workspace_id: str,
        thread_id: str,
        authorization_source: str,
        system_prompt,
    ):
        """Build a compiled deep agent WITH the full gated middleware chain:
        capability_scope (installed by ``build_deep_agent`` when ``db_factory`` is given)
        → trust_gate → jarvis_tool_dispatcher. Shared by the resume path (Task 4) and the
        live seam (Task 5) so both rebuild identically. The trust_gate short-circuits
        ``direct_user_request`` (dormant); a gated ``authorization_source`` activates it.
        """
        shells = build_tool_shells(tools)

        async def _assess_risk(capability, tool_input):
            from src.services.risk_assessor import RiskAssessment, get_or_assess_risk

            try:
                return await get_or_assess_risk(
                    capability=capability,
                    step_input=tool_input,
                    user_context={"user_id": user_id},
                    workspace_id=workspace_id,
                    client=self._client,
                    redis=getattr(self._services, "redis", None),
                )
            except Exception:
                return RiskAssessment(
                    risk_level="high",
                    reasoning="risk assessment unavailable — failing closed to high",
                    reversible=False,
                )

        trust_gate = make_trust_gate_middleware(
            authorization_source=authorization_source,
            workspace_id=workspace_id,
            user_id=user_id,
            thread_id=thread_id,
            agent_name=agent.name,
            db_factory=self._db_factory,
            assess_risk=_assess_risk,
        )
        dispatcher = make_jarvis_tool_dispatcher(
            execute_tool=self._tool_executor.execute_tool,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        # Order matters: extra_middleware=(trust_gate, dispatcher) puts the gate OUTER of
        # the dispatcher. build_deep_agent installs capability_scope first, so the full
        # chain is capability_scope → trust_gate → dispatcher.
        return await build_deep_agent(
            agent,
            shells,
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            extra_middleware=(trust_gate, dispatcher),
            system_prompt=system_prompt,
            checkpointer=self._checkpointer_provider() or MemorySaver(),
        )

    async def call_agent_stream(
        self,
        agent_name: str,
        message: str,
        user_id: str,
        trace=None,
        max_tool_rounds: int = 10,
        workspace_id: str = "",
        capability_summary: str = "",
        tools_override: list[dict] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Call a sub-agent with streaming, yielding SSE-compatible dicts."""
        agent = self._agents.get(agent_name)
        if not agent:
            yield {"event": "error", "message": f"Unknown agent: {agent_name}"}
            return

        model = self.get_model_for_agent(agent)
        tools = await self._resolve_tools(agent, workspace_id, tools_override)
        capability_summary = await self._maybe_capability_summary(
            agent_name, capability_summary, workspace_id
        )

        context_block = await self._context.assemble_context(
            agent_name, message, user_id=user_id, workspace_id=workspace_id
        )
        system_blocks = self.build_system_prompt(
            agent, context_block, capability_summary=capability_summary
        )

        AGENT_RUNTIME_CALLS.labels(runtime=self._settings.runtime).inc()
        if self._settings.runtime == "deep":
            # Step 6B: the routed chat agent runs on the Deep Agents runtime through the
            # single gated build path (``_build_deep_agent_for``, shared with the resume
            # seam). On live chat authorization_source is direct_user_request, so trust_gate
            # SHORT-CIRCUITS (dormant) — byte-identical to today; the gate only activates for
            # non-direct provenance (6C). thread_id is minted ONCE and shared by both the
            # graph config and the gate closure so a paused turn is resumable.
            thread_id = generate_id("chat")
            deep_agent = await self._build_deep_agent_for(
                agent,
                tools,
                user_id=user_id,
                workspace_id=workspace_id,
                thread_id=thread_id,
                authorization_source=AuthorizationSource.DIRECT_USER_REQUEST,
                system_prompt=build_system_message(system_blocks),
            )
            config = {"configurable": {"thread_id": thread_id}}
            graph_input = {"messages": [{"role": "user", "content": message}]}
            async for frame in stream_deep_agent_events(
                deep_agent,
                graph_input,
                config,
                agent_name=agent_name,
                model=model,
                durability="sync",
            ):
                yield frame
            return

        async for evt in agent_loop(
            client=self._client,
            agent=agent,
            model=model,
            system_blocks=system_blocks,
            tools=tools,
            message=message,
            user_id=user_id,
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            services=self._services,
            budget=self._budget,
            trace=trace,
            execute_tool_fn=self._tool_executor.execute_tool,
            max_tool_rounds=max_tool_rounds,
            stream=True,
            circuit_breaker=self._circuit_breaker,
        ):
            if isinstance(evt, LoopAgentStart):
                yield {"event": "agent_start", "agent": evt.agent, "model": evt.model}
            elif isinstance(evt, LoopThinking):
                yield {
                    "event": "thinking",
                    "agent": evt.agent,
                    "text": evt.text,
                    "is_thinking": evt.is_thinking,
                }
            elif isinstance(evt, LoopTextDelta):
                yield {"event": "text_delta", "agent": evt.agent, "text": evt.text}
            elif isinstance(evt, LoopToolCall):
                yield {
                    "event": "tool_call",
                    "agent": evt.agent,
                    "tool": evt.tool_name,
                    "input": evt.tool_input,
                }
            elif isinstance(evt, LoopToolResult):
                yield {
                    "event": "tool_result",
                    "agent": evt.agent,
                    "tool": evt.tool_name,
                    "result": evt.result,
                    "blocked": evt.blocked,
                    "latency_ms": evt.latency_ms,
                }
            elif isinstance(evt, LoopError):
                # evt.message may carry a raw upstream exception string (see
                # agent_loop LoopError(message=str(e))). Log it, but only emit a
                # client-safe generic frame — never the raw detail.
                logger.error("agent_loop error agent=%s: %s", evt.agent, evt.message)
                cid = get_correlation_id() or new_correlation_id()
                yield {
                    "event": "error",
                    "agent": evt.agent,
                    "code": _GENERIC_CODE,
                    "message": _GENERIC_MESSAGE,
                    "correlation_id": cid,
                }
            elif isinstance(evt, LoopDone):
                yield {
                    "event": "agent_done",
                    "agent": evt.agent,
                    "text": evt.text,
                    "input_tokens": evt.input_tokens,
                    "output_tokens": evt.output_tokens,
                    "cache_creation_tokens": evt.cache_creation_tokens,
                    "cache_read_tokens": evt.cache_read_tokens,
                    "tools_called": evt.tools_called,
                    "latency_ms": evt.latency_ms,
                    "cost_usd": round(evt.cost_usd, 6),
                }

    async def resume_deep_turn(
        self, *, approval_id: str, decision: str, user_id: str, workspace_id: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Re-enter a paused deep turn via ``Command(resume=decision)`` on the Approval's
        stored ``thread_id``. ``decision`` is ``"approve"`` or ``"reject"``. Marks the
        Approval approved/rejected, rebuilds the deep agent identically (a GATED
        ``authorization_source`` so the replayed gate re-reaches ``interrupt()`` and
        honors the verdict), and re-streams the continuation.
        """
        if decision not in ("approve", "reject"):
            cid = get_correlation_id() or new_correlation_id()
            yield {
                "event": "error",
                "code": _GENERIC_CODE,
                "message": _GENERIC_MESSAGE,
                "correlation_id": cid,
            }
            return

        async with self._db_factory() as db:
            approval = await db.get(Approval, approval_id)
            if approval is None:
                yield {"event": "error", "message": "approval not found"}
                return
            refs = approval.artifact_refs or {}
            thread_id = refs.get("thread_id")
            agent_name = refs.get("agent_name")
            approval.status = "approved" if decision == "approve" else "rejected"
            approval.decided_at = datetime.now(timezone.utc)
            approval.approved_by = user_id
            await db.commit()

        if not thread_id or not agent_name:
            yield {"event": "error", "message": "approval missing thread_id/agent_name"}
            return

        agent = self._agents.get(agent_name)
        if agent is None:
            yield {"event": "error", "message": f"Unknown agent: {agent_name}"}
            return

        model = self.get_model_for_agent(agent)
        tools = await self._resolve_tools(agent, workspace_id, None)
        system_blocks = self.build_system_prompt(agent, "")
        deep_agent = await self._build_deep_agent_for(
            agent,
            tools,
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            # Any gated source works; it must NOT be direct_user_request or the replayed
            # gate would short-circuit before interrupt() and a rejected write would
            # still execute.
            authorization_source=AuthorizationSource.AUTONOMOUS,
            system_prompt=build_system_message(system_blocks),
        )
        config = {"configurable": {"thread_id": thread_id}}
        async for frame in stream_deep_agent_events(
            deep_agent,
            Command(resume=decision),
            config,
            agent_name=agent_name,
            model=model,
            durability="sync",
        ):
            yield frame

    async def call_agent(
        self,
        agent_name: str,
        message: str,
        user_id: str,
        trace=None,
        max_tool_rounds: int = 10,
        workspace_id: str = "",
        capability_summary: str = "",
        tools_override: list[dict] | None = None,
    ) -> str:
        """Call a sub-agent (non-streaming). Returns final text response."""
        agent = self._agents.get(agent_name)
        if not agent:
            raise ValueError(f"Unknown agent: {agent_name}")

        model = self.get_model_for_agent(agent)
        tools = await self._resolve_tools(agent, workspace_id, tools_override)
        capability_summary = await self._maybe_capability_summary(
            agent_name, capability_summary, workspace_id
        )

        context_block = await self._context.assemble_context(
            agent_name, message, user_id=user_id, workspace_id=workspace_id
        )
        system_blocks = self.build_system_prompt(
            agent, context_block, capability_summary=capability_summary
        )

        text = ""
        error = None
        async for evt in agent_loop(
            client=self._client,
            agent=agent,
            model=model,
            system_blocks=system_blocks,
            tools=tools,
            message=message,
            user_id=user_id,
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            services=self._services,
            budget=self._budget,
            trace=trace,
            execute_tool_fn=self._tool_executor.execute_tool,
            max_tool_rounds=max_tool_rounds,
            stream=False,
            circuit_breaker=self._circuit_breaker,
        ):
            if isinstance(evt, LoopDone):
                text = evt.text
                logger.info(
                    "agent_call_complete",
                    extra={
                        "agent": agent_name,
                        "model": model,
                        "input_tokens": evt.input_tokens,
                        "output_tokens": evt.output_tokens,
                        "tools_called": evt.tools_called,
                        "latency_ms": evt.latency_ms,
                        "trace_id": trace.trace_id if trace else None,
                    },
                )
            elif isinstance(evt, LoopError):
                error = evt.message
                logger.warning(
                    "agent_call_failed",
                    extra={"agent": agent_name, "error": error},
                )

        if error and not text:
            return f"[Agent error: {error}]"
        return text
