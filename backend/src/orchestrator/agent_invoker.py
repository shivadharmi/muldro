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
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from src.config.models import BEDROCK_MODEL_TIERS, MODEL_TIERS
from src.config.settings import Settings
from src.deep_runtime.agent_builder import build_deep_agent
from src.deep_runtime.prompt_bridge import flatten_system_blocks
from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.errors import _GENERIC_CODE, _GENERIC_MESSAGE, new_correlation_id
from src.middleware.observability import get_correlation_id
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
    ):
        self._settings = settings
        self._client = client
        self._services = services
        self._budget = budget
        self._circuit_breaker = circuit_breaker
        self._db_factory_provider = db_factory_provider
        self._tool_executor = tool_executor
        self._context = context
        self._agents = agents

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

        if self._settings.runtime == "deep":
            # Step 6A runtime foundation: run the already-routed chat agent on the Deep
            # Agents runtime instead of agent_loop. NOTE (6A limitation): `tools` here are
            # the legacy Anthropic tool-schema dicts (cache_control-tagged), NOT LangChain
            # tools — end-to-end tool execution on the deep path needs a Jarvis->LangChain
            # tool bridge (a later task, out of 6A scope). The branch is behind
            # JARVIS_RUNTIME=deep (default legacy), so this is dormant until explicitly flipped.
            deep_agent = await build_deep_agent(
                agent,
                tools,
                workspace_id=workspace_id,
                db_factory=self._db_factory,
                system_prompt=flatten_system_blocks(system_blocks),
                checkpointer=MemorySaver(),
            )
            config = {"configurable": {"thread_id": generate_id("chat")}}
            graph_input = {"messages": [{"role": "user", "content": message}]}
            async for frame in stream_deep_agent_events(
                deep_agent, graph_input, config, agent_name=agent_name, model=model
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
