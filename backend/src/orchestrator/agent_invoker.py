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
from collections.abc import AsyncGenerator, Sequence
from datetime import datetime, timezone
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.config.models import BEDROCK_MODEL_TIERS, MODEL_TIERS
from src.config.settings import Settings
from src.deep_runtime.agent_builder import build_deep_agent
from src.deep_runtime.authorization import AuthorizationSource
from src.deep_runtime.checkpoint_reaper import reap_thread
from src.deep_runtime.middleware.budget import make_budget_middleware
from src.deep_runtime.middleware.governor_audit import make_governor_audit_middleware
from src.deep_runtime.middleware.governor_delegate_critique import (
    make_governor_delegate_critique_middleware,
)
from src.deep_runtime.middleware.jarvis_tool_dispatcher import make_jarvis_tool_dispatcher
from src.deep_runtime.middleware.librarian_extract import make_librarian_extract_middleware
from src.deep_runtime.middleware.readback import make_readback_middleware
from src.deep_runtime.middleware.trust_gate import _resolve_tool_def, make_trust_gate_middleware
from src.deep_runtime.middleware.unavailable_server import make_unavailable_server_middleware
from src.deep_runtime.middleware.write_lock import make_write_lock_middleware
from src.deep_runtime.model_factory import MODEL_TIER_IDS
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
from src.orchestrator.prompts import JARVIS_SOUL_CORE, PRESENTER_VOICE
from src.orchestrator.services import ServiceContainer
from src.orchestrator.tool_executor import ToolExecutor
from src.services.metrics_service import AGENT_RUNTIME_CALLS

logger = logging.getLogger(__name__)


def _augment_system_blocks_for_inline(system_blocks: list[dict], inline_format: bool) -> list[dict]:
    """Deep-only: append the Presenter voice so a deep agent formats the user-facing reply
    inline (Fork-1, Step 7B1). Off by default; when on, returns a NEW list (legacy blocks
    untouched). Idempotent: an agent whose base prompt already carries PRESENTER_VOICE (the
    presenter itself) is not double-injected.

    Immutable: never mutates ``system_blocks`` — the same list object feeds the legacy
    agent_loop, which must stay byte-identical. When ``inline_format`` is False the input
    is returned unchanged (identity), so the deep prompt is byte-neutral by default.

    ACTIVATION NOTE (Step-10): today this is applied to every deep call_agent_stream agent
    when the flag is on. At live activation it MUST be restricted to the single reply-producing
    lead — the planner (emits PlanOutput JSON) and non-responding agents should NOT receive
    surface-generation rules — and land together with chat_processor dropping the separate
    presenter step.
    """
    if not inline_format:
        return system_blocks
    if any(PRESENTER_VOICE in b.get("text", "") for b in system_blocks):
        return system_blocks
    return [*system_blocks, {"type": "text", "text": PRESENTER_VOICE}]


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

    @property
    def checkpointer(self):
        """The durable LangGraph checkpointer for this turn (or None on the legacy runtime).

        Public accessor so the scheduler's retention-sweep tick can reach the durable saver
        without a double-private reach into ``_checkpointer_provider``. Returns ``None`` when
        no durable saver is wired (legacy runtime, or a worker built without one)."""
        return self._checkpointer_provider()

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
        context_block: str = "",
        subagents: Sequence[Any] = (),
    ):
        """Build a compiled deep agent WITH the full gated middleware chain:
        capability_scope (installed by ``build_deep_agent`` when ``db_factory`` is given)
        → governor_audit → trust_gate → write_lock → jarvis_tool_dispatcher. Shared by the
        resume path (Task 4) and the live seam (Task 5) so both rebuild identically.
        governor_audit (Step 7B1) audit-logs every tool call and blocks disabled tools; the
        trust_gate short-circuits ``direct_user_request`` (dormant), a gated
        ``authorization_source`` activates it.

        ``subagents`` (Step 7B2 P4) are read-only Jarvis delegates (CompiledSubAgent dicts)
        registered on the lead so its built-in ``task`` tool can route to them. Empty by
        default (``()``) → forwarded straight to ``build_deep_agent`` which passes
        ``subagents or None`` to ``create_deep_agent`` — byte-identical to 7B1 when no
        delegates are wired. The resume path (Task 4) never passes any, keeping its rebuild
        delegate-free.
        """
        shells = build_tool_shells(tools)

        # 6C #1 fold: resolve each tool's ToolDef ONCE per turn, shared by governor_audit +
        # trust_gate + write_lock (was 3 lookups + 3 sessions per gated write). The cached
        # value is the resolved ToolDef (or None) — NO DB session is held in the cache;
        # _resolve_tool_def opens+closes its own short-lived session per distinct tool name.
        # Each consumer derives its OWN projection AND keeps its OWN fail-on-error behavior
        # (governor_audit + write_lock fail OPEN, trust_gate fails CLOSED).
        _tool_def_cache: dict[str, tuple[bool, Any]] = {}

        async def _resolve_tool_def_shared(name: str) -> tuple[bool, Any]:
            if name not in _tool_def_cache:
                _tool_def_cache[name] = await _resolve_tool_def(
                    name, workspace_id, self._db_factory
                )
            return _tool_def_cache[name]

        # Step 7B1: audit-only Governor middleware — logs every tool call and blocks disabled
        # tools. Placed FIRST in extra_middleware so it runs before the gate/lock/dispatch.
        # Fails OPEN: the shared resolver returns (False, None) on a lookup error → allow.
        governor_audit = make_governor_audit_middleware(
            agent_name=agent.name,
            workspace_id=workspace_id,
            resolve_tool_def=_resolve_tool_def_shared,
        )

        async def _assess_risk(capability, tool_input):
            from src.services.risk_assessor import RiskAssessment, get_or_assess_risk

            try:
                return await get_or_assess_risk(
                    capability=capability,
                    step_input=tool_input,
                    user_context={"user_id": user_id},
                    workspace_id=workspace_id,
                    client=self._client,
                    # redis lives in services.extras (runtime.py stores it there); a typed
                    # ``redis`` field never existed, so the old getattr was always None →
                    # risk assessment never cached. None-safe: services may be None in tests.
                    redis=self._services.extras.get("redis") if self._services else None,
                )
            except Exception:
                return RiskAssessment(
                    risk_level="high",
                    reasoning="risk assessment unavailable — failing closed to high",
                    reversible=False,
                )

        # trust_gate FAILS CLOSED on a lookup error: (False, None) → block the gated write.
        async def _gate_cap(name: str) -> tuple[bool, Any]:
            ok, td = await _resolve_tool_def_shared(name)
            return (ok, getattr(td, "capability", None) if td else None)

        trust_gate = make_trust_gate_middleware(
            authorization_source=authorization_source,
            workspace_id=workspace_id,
            user_id=user_id,
            thread_id=thread_id,
            agent_name=agent.name,
            db_factory=self._db_factory,
            assess_risk=_assess_risk,
            resolve_capability=_gate_cap,
            context_block=context_block,
        )
        dispatcher = make_jarvis_tool_dispatcher(
            execute_tool=self._tool_executor.execute_tool,
            user_id=user_id,
            workspace_id=workspace_id,
        )

        # Step 6C: cross-path write lock, placed INNER of trust_gate, OUTER of dispatcher.
        # Resolve capability from the SAME shared per-turn ToolDef trust_gate uses, so the lock
        # key matches the autonomous path exactly. Because trust_gate calls its handler only
        # AFTER approval, the lock is entered around the actual execute — never held across the
        # interrupt wait. FAILS OPEN on a lookup error: (False, None) → cap None → no lock.
        async def _resolve_cap(name: str):
            ok, td = await _resolve_tool_def_shared(name)
            return getattr(td, "capability", None) if (ok and td) else None

        write_lock = make_write_lock_middleware(
            workspace_id=workspace_id,
            # redis lives in services.extras (runtime.py stores it there); a typed ``redis``
            # field never existed, so the old getattr was always None → the 6C cross-path
            # write lock silently never engaged. None-safe: services may be None in tests.
            redis=self._services.extras.get("redis") if self._services else None,
            resolve_capability=_resolve_cap,
        )

        # Step 7B1 P3: Librarian → extraction-middleware collapse. This @after_model hook
        # relocates the chat InteractionLearner's entity/memory extraction into the deep turn
        # itself, firing ONCE per turn (terminal round only). WIRED-BUT-DORMANT: active=False
        # so it NEVER double-fires with the still-live InteractionLearner (chat_processor's
        # background spawn). The learn closure adapts the existing, tested InteractionLearner
        # (fresh DB session, cooldown, memory + world-model extraction) — nothing re-implemented.
        # Ctor deps match jarvis.py's live InteractionLearner construction: vector_store from
        # the typed ServiceContainer field; redis/event_bus resolve to None via getattr (they
        # live in services.extras, not typed attrs) — matching the live path's redis=None.
        # Live activation (flip active + skip InteractionLearner on runtime=deep) is a Step-10
        # gate.
        async def _librarian_learn(user_message: str, agent_response: str) -> None:
            from src.services.interaction_learner import InteractionLearner

            learner = InteractionLearner(
                self._settings,
                self._db_factory,
                vector_store=getattr(self._services, "vector_store", None),
                # INTENTIONALLY getattr → None (NOT services.extras): this mirrors the live
                # InteractionLearner construction at jarvis.py:172 (redis=None). Keep it in
                # lock-step with live — do NOT "fix" it to services.extras and diverge.
                redis=getattr(self._services, "redis", None),
                event_bus=getattr(self._services, "event_bus", None),
            )
            await learner.learn(
                user_id=user_id,
                workspace_id=workspace_id,
                user_message=user_message,
                agent_response=agent_response,
                intent=None,
                trace_id=thread_id,
            )

        librarian_extract = make_librarian_extract_middleware(
            workspace_id=workspace_id,
            user_id=user_id,
            learn=_librarian_learn,
            active=False,
        )

        # Step 7C: MCP-server-down breaker (per-turn, self-contained). OUTER of trust_gate so a
        # known-down WRITE tool is short-circuited before it is prompted for approval.
        unavailable_server = make_unavailable_server_middleware(
            workspace_id=workspace_id,
            db_factory=self._db_factory,
        )

        # Step 7C: re-home the legacy agent_loop authoritative cost record (@after_model).
        # ADDITIVE — the deep path recorded no TokenUsage before. model = the direct-Anthropic id
        # (MODEL_TIER_IDS), NOT get_model_for_agent (Bedrock-tainted).
        budget_mw = make_budget_middleware(
            agent_name=agent.name,
            model=MODEL_TIER_IDS[agent.model_tier],
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            budget=self._budget,
            trace_id=thread_id,  # per-turn correlation, matching librarian_extract in this method
            trigger="chat",
        )

        # Step 7C: inline read-back (DORMANT behind deep_readback_enabled). read_fn=None
        # (deferred-tick template). Reuses _resolve_cap (fail-open cap|None, same as write_lock) +
        # _assess_risk. CONFIRMED + gated → the deep trust-increment helper.
        gated_chain: tuple[Any, ...] = (write_lock, dispatcher)
        if self._settings.deep_readback_enabled:

            async def _record_confirmed_outcome(*, capability, risk_level):
                from src.deep_runtime.trust_increment import record_deep_confirmed_outcome

                await record_deep_confirmed_outcome(
                    db_factory=self._db_factory,
                    workspace_id=workspace_id,
                    capability=capability,
                    risk_level=risk_level,
                )

            read_back = make_readback_middleware(
                workspace_id=workspace_id,
                authorization_source=authorization_source,
                resolve_capability=_resolve_cap,
                assess_risk=_assess_risk,
                read_fn=None,
                record_confirmed_outcome=_record_confirmed_outcome,
            )
            gated_chain = (write_lock, read_back, dispatcher)

        # Order (outer→inner). capability_scope is installed FIRST by build_deep_agent, so the full
        # tool chain is:
        #   capability_scope → governor_audit → unavailable_server → trust_gate → write_lock
        #     [→ read_back (only when deep_readback_enabled)] → dispatcher
        # librarian_extract + budget_mw are @after_model (tuple position irrelevant to tool chain).
        extra_middleware: tuple[Any, ...] = (
            governor_audit,
            unavailable_server,
            trust_gate,
            *gated_chain,
            librarian_extract,
            budget_mw,
        )

        # Step 7B2 P5 (DORMANT behind deep_delegates_enabled): the Governor LLM
        # delegate-summary critique. It is the ONE lead-side wrap_tool_call that does NOT skip
        # the built-in ``task`` tool: it runs the read-only delegate, side-calls Haiku to
        # critique the returned summary, and annotates it (fail-OPEN for reads — never blocks).
        # PREPENDED so it is OUTERMOST — it wraps the whole ``task`` call, unwrapping the
        # delegate's Command after the inner chain returns. redis is sourced from
        # services.extras (the 6C carry-fix pattern; a typed ``redis`` attr never existed).
        # Flag OFF → the 7B1 5-tuple is UNCHANGED (byte-identical); even wired the critique acts
        # on ``task`` only, and the resume path (no delegates) never fires it.
        if self._settings.deep_delegates_enabled:
            critique = make_governor_delegate_critique_middleware(
                client=self._client,
                redis=self._services.extras.get("redis") if self._services else None,
                is_read_only_delegate=True,
            )
            extra_middleware = (critique, *extra_middleware)

        return await build_deep_agent(
            agent,
            shells,
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            extra_middleware=extra_middleware,
            subagents=subagents,
            system_prompt=system_prompt,
            checkpointer=self._checkpointer_provider() or MemorySaver(),
        )

    async def _build_delegate_subagents(
        self, lead_agent: SubAgent, *, workspace_id: str, user_id: str
    ) -> list[Any]:
        """DORMANT (``deep_delegates_enabled``): build the read-only Perceiver delegate list
        for a deep lead + disable the ambient general-purpose ``task`` child on BOTH the lead's
        and the delegate's built models. Called from the live seam ONLY when the flag is on.

        The Perceiver config is sourced from the in-memory ``build_agent_set(AGENTS, cheap_mode)``
        singleton — NOT ``self._agents`` — because ``self._agents`` may be overwritten at runtime
        by ``load_as_sub_agents()`` (jarvis.py), which DROPS per-agent ``thinking``. Sourcing from
        the singleton preserves the Perceiver's sonnet/6144 thinking AND applies the SAME cheap-mode
        transform the lead received.

        GP-disable keys off ``MODEL_TIER_IDS[<tier>]`` — the direct-Anthropic id the deep
        runtime always builds via ``build_chat_model`` (deepagents derives the harness-profile
        key from that built model) — NOT ``get_model_for_agent`` (which returns a Bedrock id
        when ``use_bedrock``). Disabling GP on BOTH models, BEFORE either is built, stops the
        sonnet delegate — itself a deep lead — from getting its own ungated general-purpose
        child. Both calls are idempotent + process-global.

        The delegate carries its OWN role prompt (``perceiver_cfg.prompt``, the default inside
        ``build_read_only_delegate``) — never the lead's Presenter-voice inline-format augmentation.
        Composing ``JARVIS_SOUL_CORE`` + ambient context into the delegate prompt is a Step-10
        activation refinement (dormant scaffold; the forced-on e2e uses fake models, so delegate
        prompt content is behavior-neutral there).
        """
        from src.deep_runtime.delegates import (
            build_read_only_delegate,
            disable_general_purpose_subagent,
        )
        from src.orchestrator.agents import AGENTS, build_agent_set

        perceiver_cfg = build_agent_set(AGENTS, self._settings.cheap_mode)["perceiver"]
        disable_general_purpose_subagent(MODEL_TIER_IDS[lead_agent.model_tier])
        disable_general_purpose_subagent(MODEL_TIER_IDS[perceiver_cfg.model_tier])
        tools = await self._resolve_tools(perceiver_cfg, workspace_id, None)
        delegate = await build_read_only_delegate(
            perceiver_cfg,
            tools,
            workspace_id=workspace_id,
            user_id=user_id,
            db_factory=self._db_factory,
            execute_tool=self._tool_executor.execute_tool,
        )
        return [delegate]

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
            # Step 7B2 P4 (DORMANT behind deep_delegates_enabled): build the read-only
            # Perceiver delegate list so the lead's built-in ``task`` tool can route reads to
            # it. Flag OFF → ``()`` → _build_deep_agent_for forwards subagents=() →
            # build_deep_agent(subagents=()) → create_deep_agent(subagents=None) = byte-identical
            # to 7B1 (no delegate build, no GP-disable). No live lead→delegate routing exists
            # yet; that is a Step-8/10 gate.
            subagents = (
                await self._build_delegate_subagents(
                    agent, workspace_id=workspace_id, user_id=user_id
                )
                if self._settings.deep_delegates_enabled
                else ()
            )
            deep_agent = await self._build_deep_agent_for(
                agent,
                tools,
                user_id=user_id,
                workspace_id=workspace_id,
                thread_id=thread_id,
                authorization_source=AuthorizationSource.DIRECT_USER_REQUEST,
                subagents=subagents,
                # Step 7B1 P4 (Fork-1): deep-only, off-by-default inline-format
                # augmentation. Builds a NEW block list (legacy agent_loop below keeps the
                # ORIGINAL system_blocks) so the deep lead can format the reply inline. A
                # no-op identity when deep_inline_format is False → deep prompt unchanged.
                system_prompt=build_system_message(
                    _augment_system_blocks_for_inline(
                        system_blocks, self._settings.deep_inline_format
                    )
                ),
                # CF-1: persist the assembled ContextPack on any Approval this turn pauses
                # on, so the resume path can re-inject it (dormant on direct chat — the gate
                # short-circuits before persisting — but threaded uniformly through the seam).
                context_block=context_block,
            )
            config = {"configurable": {"thread_id": thread_id}}
            graph_input = {"messages": [{"role": "user", "content": message}]}
            # durability="sync" keeps the build/stream path uniform across direct and gated
            # turns (required so a gated interrupt's checkpoint commits BEFORE the
            # approval_needed frame). It is frame-neutral and a no-op on the live MemorySaver
            # default; with a durable saver a non-pausing direct turn commits each superstep
            # synchronously — a minor, accepted latency cost for one shared stream path.
            # Step 6C CF-4: reap this thread's durable checkpoints once the turn finishes
            # WITHOUT pausing. A paused turn emits an ``approval_needed`` frame and keeps its
            # checkpoint until the resume path runs — reaping it here would strand the resume.
            paused = False
            async for frame in stream_deep_agent_events(
                deep_agent,
                graph_input,
                config,
                agent_name=agent_name,
                model=model,
                durability="sync",
            ):
                if isinstance(frame, dict) and frame.get("event") == "approval_needed":
                    paused = True
                yield frame
            if not paused:
                await reap_thread(self._checkpointer_provider(), thread_id)
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
            # Tenant-isolation (IDOR) guard: an approval is resumable ONLY by its owning
            # workspace. Return the SAME "not found" for a missing OR cross-tenant approval
            # so existence is never leaked across tenants. workspace_id is resolved from the
            # caller's auth context by the (deferred) HTTP endpoint — never LLM-supplied.
            if approval is None or approval.workspace_id != workspace_id:
                yield {"event": "error", "message": "approval not found"}
                return
            # Only a still-pending approval may be resumed — blocks re-resuming (and thus
            # re-executing) an already-decided approval.
            if approval.status != "pending":
                yield {"event": "error", "message": "approval not pending"}
                return
            refs = approval.artifact_refs or {}
            thread_id = refs.get("thread_id")
            agent_name = refs.get("agent_name")
            # CF-1: re-inject the ContextPack the original turn assembled (persisted onto the
            # Approval at pause time). Without this the continuation would rebuild with an
            # EMPTY context block and lose the turn's ambient entities/memories/preferences.
            persisted_context = refs.get("context_block", "")
            # CF-5: validate the rebuild inputs BEFORE consuming (flipping + committing) the
            # approval, so a malformed approval stays pending and re-resumable — not stranded.
            if not thread_id or not agent_name:
                yield {"event": "error", "message": "approval missing thread_id/agent_name"}
                return
            agent = self._agents.get(agent_name)
            if agent is None:
                yield {"event": "error", "message": f"Unknown agent: {agent_name}"}
                return
            approval.status = "approved" if decision == "approve" else "rejected"
            approval.decided_at = datetime.now(timezone.utc)
            approval.approved_by = user_id
            await db.commit()

        model = self.get_model_for_agent(agent)
        tools = await self._resolve_tools(agent, workspace_id, None)
        system_blocks = self.build_system_prompt(agent, persisted_context)
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
            # CF-1: thread the original turn's context forward so a CHAINED approval created
            # if this resumed continuation pauses AGAIN (a second write) carries the same
            # context — otherwise the trust_gate would persist context_block="" for it.
            context_block=persisted_context,
        )
        config = {"configurable": {"thread_id": thread_id}}
        # Step 6C CF-4: same reap-on-non-paused-completion rule as the initial turn. A resume
        # that pauses AGAIN (re-interrupts on a later write) keeps its checkpoint for the next
        # resume; a resume that runs to completion reaps the thread it just finished.
        paused = False
        async for frame in stream_deep_agent_events(
            deep_agent,
            Command(resume=decision),
            config,
            agent_name=agent_name,
            model=model,
            durability="sync",
        ):
            if isinstance(frame, dict) and frame.get("event") == "approval_needed":
                paused = True
            yield frame
        if not paused:
            await reap_thread(self._checkpointer_provider(), thread_id)

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
