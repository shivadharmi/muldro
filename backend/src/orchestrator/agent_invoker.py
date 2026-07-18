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

import json
import logging
from collections.abc import AsyncGenerator, Sequence
from datetime import datetime, timezone
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy import update

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
from src.deep_runtime.middleware.jarvis_tool_dispatcher import (
    ExecuteToolFn,
    make_jarvis_tool_dispatcher,
)
from src.deep_runtime.middleware.librarian_extract import make_librarian_extract_middleware
from src.deep_runtime.middleware.permission_gate import make_permission_gate_middleware
from src.deep_runtime.middleware.readback import make_readback_middleware
from src.deep_runtime.middleware.trust_gate import _resolve_tool_def, make_trust_gate_middleware
from src.deep_runtime.middleware.unavailable_server import make_unavailable_server_middleware
from src.deep_runtime.middleware.write_lock import make_write_lock_middleware
from src.deep_runtime.model_factory import MODEL_TIER_IDS
from src.deep_runtime.prompt_bridge import build_system_message
from src.deep_runtime.readback_readfn import FreshSessionToolLister, make_readback_read_fn
from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.deep_runtime.thread_identity import make_thread_id, workspace_of_thread_id
from src.deep_runtime.tool_bridge import build_tool_shells
from src.errors import _GENERIC_CODE, _GENERIC_MESSAGE, new_correlation_id
from src.middleware.observability import get_correlation_id
from src.models.approvals import Approval
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
from src.orchestrator.divergence import ShadowDecision
from src.orchestrator.prompts import (
    DEEP_DELEGATION_INSTRUCTION,
    JARVIS_SOUL_CORE,
    PRESENTER_VOICE,
)
from src.orchestrator.services import ServiceContainer
from src.orchestrator.tool_executor import ToolExecutor
from src.services.metrics_service import AGENT_RUNTIME_CALLS
from src.services.runtime_gate import effective_runtime

logger = logging.getLogger(__name__)


def _is_reply_lead(agent_name: str) -> bool:
    """Return True iff ``agent_name`` is the reply-producing lead — the single agent whose
    output becomes the user-facing reply. Today that is the Presenter: chat_processor's final
    turn output comes from ``call_agent_stream("presenter", ...)``, while the ``planner``
    (emits PlanOutput JSON) and the routed per-step read/execute agents (Perceiver /
    Executor / Librarian) are non-responding steps that must NOT carry surface-generation
    rules. Both the live seam (``call_agent_stream``) and the shadow seam
    (``run_shadow_turn``) derive the lead flag from this same pure function on the same
    ``agent_name``, so they can never diverge on the augmentation for an equivalent turn.

    Step-10 note: when the separate presenter step is dropped (chat_processor change,
    tracked separately) the reply lead's name changes and this predicate moves with it.
    """
    return agent_name == "presenter"


def _augment_system_blocks_for_inline(
    system_blocks: list[dict], inline_format: bool, *, is_reply_lead: bool = False
) -> list[dict]:
    """Deep-only: append the Presenter voice so a deep agent formats the user-facing reply
    inline (Fork-1, Step 7B1). Off by default; when on, returns a NEW list (legacy blocks
    untouched). Idempotent: an agent whose base prompt already carries PRESENTER_VOICE (the
    presenter itself) is not double-injected.

    Lead-scoped (A-3/B2): the voice is appended ONLY when ``is_reply_lead`` is True (the
    single reply-producing lead). ``call_agent_stream``/``run_shadow_turn`` also build
    non-reply agents (planner, Perceiver reads, Executor); those must NOT receive
    surface-generation rules. The default is the SAFE value (no append), so a caller that
    omits the flag never leaks the voice into a non-lead prompt.

    Immutable: never mutates ``system_blocks`` — the same list object feeds the legacy
    agent_loop, which must stay byte-identical. When ``inline_format`` is False (or the
    agent is not the reply lead) the input is returned unchanged (identity), so the deep
    prompt is byte-neutral by default.
    """
    if not (inline_format and is_reply_lead):
        return system_blocks
    if any(PRESENTER_VOICE in b.get("text", "") for b in system_blocks):
        return system_blocks
    return [*system_blocks, {"type": "text", "text": PRESENTER_VOICE}]


def _augment_system_blocks_for_delegation(
    system_blocks: list[dict], *, has_delegates: bool
) -> list[dict]:
    """Deep-only: append ``DEEP_DELEGATION_INSTRUCTION`` so a deep agent that has a Perceiver
    delegate registered on its built-in ``task`` tool is told to route read-only research to
    it (Step 10 A-4 / B3). The delegate scaffolding (``_build_delegate_subagents`` +
    ``subagents=``) existed already; this is the live routing INSTRUCTION that turns the
    dormant scaffolding into an actual routing decision.

    NOT lead-scoped — gated SOLELY on ``has_delegates`` (unlike A-3's
    ``_augment_system_blocks_for_inline``, which is gated on ``_is_reply_lead``). The
    instruction is offered to ANY deep agent for which a delegate was built, NOT only the
    reply lead. Today (``deep_delegates_enabled`` off) that is a dormant no-op; when the flag
    flips on, the planner and every routed step whose build produced a delegate get the
    instruction too — not just the reply lead. Lead-scoping the offering is a DELIBERATE
    Step-10 Part-B activation refinement: the correct "lead" identity is the post-B5 deep lead
    (which does research inline), NOT the current reply-lead (=presenter, which only formats
    already-gathered data), so scoping to the presenter now would bake in a soon-to-be-wrong
    assumption. The current not-yet-scoped behavior is pinned by a test in
    test_lead_delegate_routing.py so Part-B's scoping is a visible, deliberate change.

    Gated on ``has_delegates`` — True ONLY when a delegate was actually built (a non-empty
    ``subagents`` list). When the flag is off (``subagents == ()``) OR the delegate build
    degraded to ``[]`` (10A A4 path), the caller passes ``has_delegates=False`` and this is
    the IDENTITY — no instruction is added, so the prompt is byte-identical to today's
    no-delegate turn. Never advertises a delegate that is not registered.

    Composes cleanly with ``_augment_system_blocks_for_inline`` (A-3): both take a block list
    and return a NEW list (or the input unchanged), never mutating in place, so wrapping one
    around the other is order-independent and neither clobbers the other. Idempotent: a block
    list already carrying the instruction is not doubled. The immutable input keeps the legacy
    ``agent_loop`` path — which reads the same ``system_blocks`` — byte-identical.
    """
    if not has_delegates:
        return system_blocks
    if any(DEEP_DELEGATION_INSTRUCTION in b.get("text", "") for b in system_blocks):
        return system_blocks
    return [*system_blocks, {"type": "text", "text": DEEP_DELEGATION_INSTRUCTION}]


def _parse_tool_result_content(content: Any) -> dict | None:
    """Best-effort parse a deep ``tool_result`` frame's ``result`` into a dict, or None.

    The deep dispatcher (``jarvis_tool_dispatcher``) serializes a tool's dict result via
    ``json.dumps`` into the ``ToolMessage`` content, so the frame's ``result`` is usually a
    JSON string; a plain-string / non-JSON / non-dict payload yields None. Used by
    ``run_autonomous_deep_step`` to detect the ``auth_required`` envelope.
    """
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


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

    def has_durable_checkpointer(self) -> bool:
        """True iff a DURABLE checkpointer (an ``AsyncPostgresSaver``) is wired.

        A chat permission pause spans TWO HTTP requests — the turn suspends on one
        request, and a separate resume request re-enters the same LangGraph thread — so
        it can only be served by a checkpointer that survives across requests. The
        per-build ``MemorySaver`` fallback (used when the durable saver is absent /
        degraded) lives only in-process and CANNOT resume a paused turn; treat it, and a
        ``None`` provider, as non-durable. Used by the chat single-lead path to downgrade
        an ``ask``/``auto`` turn to the legacy path when no durable saver exists (a pause
        would otherwise orphan the checkpoint). None-safe.
        """
        return isinstance(self._checkpointer_provider(), AsyncPostgresSaver)

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
        execute_tool: ExecuteToolFn | None = None,
        pre_approved_capabilities: frozenset[str] = frozenset(),
        require_write_lock: bool = False,
        permission_mode: str | None = None,
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

        ``execute_tool`` (Step 10B Task 3b, ADDITIVE): the dispatcher's tool-execution
        function. Defaults to ``None``, which falls back to
        ``self._tool_executor.execute_tool`` — byte-identical to before this param
        existed. The shadow-compare harness (``run_shadow_turn``) injects a
        ``ShadowToolExecutor`` so a shadow WRITE never reaches the real executor, and the
        autonomous step seam (``run_autonomous_deep_step``) injects the ledger-wrapped
        adapter. The two chat callers (the chat build below and the resume build) never
        pass it.

        ``pre_approved_capabilities`` (Step 10C SQ2 Branch C, ADDITIVE): forwarded verbatim
        to the trust_gate so a capability already gated at the STEP level is not double-
        prompted at the tool-call level. Defaults to the empty frozenset — the chat + resume
        + shadow callers never pass it, so the gate is byte-identical for them.

        ``require_write_lock`` (Step 10D P1 A3, ADDITIVE): fail the write lock CLOSED for this
        build, OR'd with the per-caller ``write_lock_require_redis`` setting. The ungated chat
        single-lead path (``stream_deep_lead``) passes True so its writes are NEVER executed
        unserialized while Redis is down. Defaults to False, keeping ALL other callers
        (``call_agent_stream`` deep branch, ``resume_deep_turn``, ``run_autonomous_deep_step``,
        ``run_shadow_turn``) byte-identical to before this param existed.

        ``permission_mode`` (P2.1, ADDITIVE): the chat permission model (``bypass``/``ask``/
        ``auto``). When ``"ask"`` or ``"auto"`` the action-time ``permission_gate`` is inserted
        immediately AFTER ``trust_gate`` (SEPARATE from it — auth-source-independent, gating on
        mode × risk). When ``None`` or ``"bypass"`` the gate is NOT installed and
        ``extra_middleware`` is byte-identical to before this param existed — the
        byte-neutrality guarantee for all existing callers.
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
            pre_approved_capabilities=pre_approved_capabilities,
        )

        # P2.1: action-time chat permission gate — installed ONLY for ask/auto, immediately
        # AFTER trust_gate. SEPARATE from trust_gate (it never consults authorization_source):
        # on the chat single-lead path trust_gate is dormant (short-circuits) and this gate
        # does the confirmation. None/bypass → not installed → extra_middleware byte-identical.
        # Reuses the SAME per-turn resolvers as trust_gate: _gate_cap (fail-closed cap lookup)
        # + _assess_risk (fails closed to high).
        permission_gate_chain: tuple[Any, ...] = ()
        if permission_mode in ("ask", "auto"):
            permission_gate = make_permission_gate_middleware(
                permission_mode=permission_mode,
                workspace_id=workspace_id,
                user_id=user_id,
                thread_id=thread_id,
                agent_name=agent.name,
                db_factory=self._db_factory,
                assess_risk=_assess_risk,
                resolve_capability=_gate_cap,
                context_block=context_block,
                lead_scope=agent.capability_scope,
            )
            permission_gate_chain = (permission_gate,)

        dispatcher = make_jarvis_tool_dispatcher(
            execute_tool=execute_tool or self._tool_executor.execute_tool,
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
            # Step 10D P1 A3: OR in the per-build override so the ungated chat single-lead path
            # fail-closes its writes even when the global setting default is fail-open.
            require_redis=self._settings.write_lock_require_redis or require_write_lock,
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
            model=MODEL_TIER_IDS.get(agent.model_tier, MODEL_TIER_IDS["sonnet"]),
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            budget=self._budget,
            trace_id=thread_id,  # per-turn correlation, matching librarian_extract in this method
            trigger="chat",
        )

        # Step 7C / B4: inline read-back (DORMANT behind deep_readback_enabled). B4 replaced the
        # deferred-tick read_fn=None with a REAL read_fn that routes the post-condition read
        # through the central execute_tool dispatcher and reproduces the unservable denylist (so
        # calendar.create cannot false-CONTRADICT). Safety is NOT from capability-scope (a separate
        # outer middleware the read_fn bypasses) — the read capability is post-condition-derived,
        # side-effect-free, and workspace-scoped at dispatch. It uses the BUILD's execute_tool (the
        # same resolved dispatcher fn the tool chain uses — real / shadow / ledger-wrapped), so a
        # read honors the turn's execution context. Reuses _resolve_cap (fail-open cap|None, same
        # as write_lock) + _assess_risk. CONFIRMED + gated → the deep trust-increment helper.
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
                read_fn=make_readback_read_fn(
                    execute_tool=execute_tool or self._tool_executor.execute_tool,
                    tool_registry=FreshSessionToolLister(self._db_factory, workspace_id),
                    user_id=user_id,
                    workspace_id=workspace_id,
                ),
                record_confirmed_outcome=_record_confirmed_outcome,
            )
            gated_chain = (write_lock, read_back, dispatcher)

        # Order (outer→inner). capability_scope is installed FIRST by build_deep_agent, so the full
        # tool chain is:
        #   capability_scope → governor_audit → unavailable_server → trust_gate
        #     [→ permission_gate (only for ask/auto)] → write_lock
        #     [→ read_back (only when deep_readback_enabled)] → dispatcher
        # librarian_extract + budget_mw are @after_model (tuple position irrelevant to tool chain).
        extra_middleware: tuple[Any, ...] = (
            governor_audit,
            unavailable_server,
            trust_gate,
            *permission_gate_chain,
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

        GP-disable keys off ``MODEL_TIER_IDS.get(<tier>, MODEL_TIER_IDS["sonnet"])`` — the
        direct-Anthropic model id (a malformed tier degrades to the sonnet id, never a tier
        name) the deep
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

        # A4 (Step-10A): a malformed model_tier (DB corruption/bad migration) or any
        # failure while resolving tools / building the delegate must not crash a turn
        # the lead can otherwise serve alone — degrade to no delegates instead.
        try:
            perceiver_cfg = build_agent_set(AGENTS, self._settings.cheap_mode)["perceiver"]
            disable_general_purpose_subagent(
                MODEL_TIER_IDS.get(lead_agent.model_tier, MODEL_TIER_IDS["sonnet"])
            )
            disable_general_purpose_subagent(
                MODEL_TIER_IDS.get(perceiver_cfg.model_tier, MODEL_TIER_IDS["sonnet"])
            )
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
        except Exception:
            logger.warning(
                "[deep_runtime] delegate build failed — degrading to no delegates (lead=%s)",
                getattr(lead_agent, "name", "?"),
                exc_info=True,
            )
            return []

    async def effective_chat_runtime(self) -> str:
        """Resolve the chat surface's effective runtime (override > breaker > enabled >
        static settings.runtime). Centralizes the resolution call_agent_stream does inline so
        the chat_processor single-lead branch (5b) can gate on the SAME resolved value."""
        return await effective_runtime(
            "chat",
            redis=self._services.extras.get("redis") if self._services else None,
            settings=self._settings,
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

        # Step 10B Phase 4: resolve the chat surface's runtime ONCE, up front, instead of
        # reading self._settings.runtime at each of the three points below. Priority
        # override > breaker > enabled > static settings.runtime (redis=None or any Redis
        # error falls through to static, never to an accidental "deep" — see
        # runtime_gate.effective_runtime). With no Redis keys set this returns
        # self._settings.runtime unchanged, so the seam stays byte-neutral.
        runtime = await effective_runtime(
            "chat",
            redis=self._services.extras.get("redis") if self._services else None,
            settings=self._settings,
        )

        model = self.get_model_for_agent(agent)
        tools = await self._resolve_tools(agent, workspace_id, tools_override)
        capability_summary = await self._maybe_capability_summary(
            agent_name, capability_summary, workspace_id
        )

        context_block = await self._context.assemble_context(
            agent_name,
            message,
            user_id=user_id,
            workspace_id=workspace_id,
            # Step 8 P2: the slim JIT context pack only ever applies on the deep
            # runtime AND behind its own flag (dormant by default). Per-agent
            # gating (JIT_ENABLED_AGENTS) happens inside assemble_context itself.
            # Step 10B Phase 4: gated on the resolved runtime, not the static setting, so a
            # gate-flipped surface gets the JIT pack too.
            jit=(runtime == "deep" and self._settings.deep_context_jit),
        )
        system_blocks = self.build_system_prompt(
            agent, context_block, capability_summary=capability_summary
        )

        AGENT_RUNTIME_CALLS.labels(runtime=runtime).inc()
        if runtime == "deep":
            # Step 6B: the routed chat agent runs on the Deep Agents runtime through the
            # single gated build path (``_build_deep_agent_for``, shared with the resume
            # seam). On live chat authorization_source is direct_user_request, so trust_gate
            # SHORT-CIRCUITS (dormant) — byte-identical to today; the gate only activates for
            # non-direct provenance (6C). thread_id is minted ONCE and shared by both the
            # graph config and the gate closure so a paused turn is resumable. A6 (Step-10A):
            # the thread_id embeds workspace_id (make_thread_id) so the checkpointer's
            # identity is workspace-bound — the resume path below asserts it as
            # defense-in-depth on top of the existing approval.workspace_id IDOR guard.
            thread_id = make_thread_id(workspace_id)
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
                # A-3/B2: lead-scoped — the Presenter voice is appended ONLY for the
                # reply-producing lead, never the planner or a routed read/execute step.
                # A-4/B3: the delegation routing instruction is appended ONLY when a delegate
                # was actually built (``bool(subagents)`` — flag on AND non-empty). It is
                # offered to ANY deep agent that built a delegate — NOT lead-scoped (unlike the
                # Presenter voice above, which is gated on ``_is_reply_lead``). Lead-scoping the
                # offering to the post-B5 deep lead is a DELIBERATE Step-10 Part-B activation
                # refinement (scoping to the current reply-lead=presenter now would encode a
                # transitional assumption — the not-yet-scoped behavior is pinned by a test).
                # Both augmentations compose immutably; flag off (subagents==()) OR a degraded
                # build (subagents==[]) → identity → byte-neutral.
                system_prompt=build_system_message(
                    _augment_system_blocks_for_delegation(
                        _augment_system_blocks_for_inline(
                            system_blocks,
                            self._settings.deep_inline_format,
                            is_reply_lead=_is_reply_lead(agent_name),
                        ),
                        has_delegates=bool(subagents),
                    )
                ),
                # CF-1: persist the assembled ContextPack on any Approval this turn pauses
                # on, so the resume path can re-inject it (dormant on direct chat — the gate
                # short-circuits before persisting — but threaded uniformly through the seam).
                context_block=context_block,
            )
            graph_input = {"messages": [{"role": "user", "content": message}]}
            async for frame in self._stream_and_reap(
                deep_agent,
                graph_input,
                thread_id=thread_id,
                agent_name=agent_name,
                model=model,
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

    async def build_chat_lead(self, steps, workspace_id: str) -> SubAgent:
        """Build the synthetic single-lead SubAgent for a plan (Step 10D chat permission model, P1).

        Delegates to lead_builder.build_chat_lead (which derives the plan-union capability_scope
        via derive_lead_scope), using the invoker's own agent set + cheap_mode + db_factory.
        Fail-closed + plan-bounded by construction (see lead_builder)."""
        from src.orchestrator.lead_builder import build_chat_lead as _build_chat_lead

        return await _build_chat_lead(
            self._db_factory, workspace_id, steps, self._agents, self._settings.cheap_mode
        )

    async def _stream_and_reap(
        self,
        deep_agent,
        graph_input,
        *,
        thread_id: str,
        agent_name: str,
        model: str,
    ):
        """Stream a deep-agent turn (or ``Command(resume=…)`` re-entry), yield every frame, and
        reap the durable checkpoint IFF the turn ran to non-paused completion. The shared tail of
        every deep streaming path whose reap is CONDITIONAL on not-pausing: the four passthrough
        sites (``call_agent_stream`` routed per-step, ``stream_deep_lead`` chat single-lead,
        ``resume_deep_turn`` per-step resume, ``resume_deep_lead`` chat resume) plus
        ``run_shadow_turn``, which CONSUMES the yielded frames (capturing ``final_text``) instead of
        re-yielding them. Only ``run_autonomous_deep_step`` stays inline — its reap is UNCONDITIONAL
        (Branch C never pauses, so it reaps on EVERY terminal outcome), which this helper's
        ``if not paused`` guard would break.

        ``durability="sync"`` keeps the build/stream path uniform across direct and gated turns
        (frame-neutral, a no-op on the live ``MemorySaver`` default): a gated interrupt's
        checkpoint must commit BEFORE the ``approval_needed`` frame is emitted. Step 6C CF-4: a
        turn that pauses emits ``approval_needed`` and KEEPS its checkpoint for the resume path —
        reaping it here would strand the resume; a turn that completes without pausing reaps the
        thread it just finished.
        """
        config = {"configurable": {"thread_id": thread_id}}
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

    async def stream_deep_lead(
        self,
        lead: "SubAgent",
        tools: list[dict] | None = None,
        *,
        message: str,
        context_block: str,
        user_id: str,
        workspace_id: str,
        intent: str | None = None,
        trace=None,
        permission_mode: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run ONE synthetic deep lead over a whole user goal, streaming SSE-compatible frames
        (Step 10D A-5). Generalizes the ``call_agent_stream`` ``runtime=="deep"`` branch for a
        single lead that gathers, acts, and replies inline — replacing the per-step loop +
        presenter step on the deep single-lead chat path (wired in 5b).

        Differences from the routed deep branch: (1) PRESENTER_VOICE is ALWAYS applied
        (``is_reply_lead=True``), decoupling the reply lead from ``name=="presenter"`` — the
        lead is named ``"lead"``. (2) The RAW user ``message`` is the human turn input;
        ``context_block`` goes into the SYSTEM prompt (not the human message) so any
        extraction middleware sees a clean source. Authorization is DIRECT_USER_REQUEST
        (user's message = authorization; trust_gate stays dormant). ``intent``/``trace`` are
        accepted for the 5c librarian-fidelity wiring; unused in 5a.

        Delegation is DELIBERATELY not composed here (only ``_augment_system_blocks_for_inline``
        is applied, never ``_augment_system_blocks_for_delegation``): the A-5 single lead does
        its research INLINE, so it hosts no Perceiver delegate even under
        ``deep_delegates_enabled``. Composing delegates onto the lead is a Step-10 Part-B (5d)
        refinement, not a missed parity.

        ``tools`` (P1 A2): when ``None`` (omitted) the tool set is resolved INTERNALLY via
        ``_resolve_tools(lead, ws, None)`` → ``get_tools_for_agent(lead)``, which offers EXACTLY
        the tools whose capability ∈ the lead's scope, so offered-tools ⊆ enforced-scope BY
        CONSTRUCTION (the caller cannot pass an inconsistent tool set). An EXPLICIT ``[]`` still
        means "no tools" — only ``None`` triggers the resolve.

        The write lock is forced fail-CLOSED here (``require_write_lock=True`` into
        ``_build_deep_agent_for``): the single-lead path is ungated, so its writes MUST be
        serialized and never execute unserialized while Redis is down (P1 A3).

        ``permission_mode`` (P2.1): forwarded verbatim into ``_build_deep_agent_for`` — ``ask``/
        ``auto`` install the action-time permission gate, ``None`` (default) / ``bypass`` leave
        the chain byte-identical. Current 5b callers pass nothing; P2.3 wires the per-turn mode.
        """
        if tools is None:
            tools = await self._resolve_tools(lead, workspace_id, None)
        # This method IS the deep single-lead path — it is only ever reached when the chat
        # runtime resolves to "deep" (the 5b caller gates on it). Increment with the fixed
        # "deep" label so a live single-lead turn is counted in the Step-10B rollback/adoption
        # signal, mirroring ``call_agent_stream``'s per-call increment (and UNLIKE the shadow
        # turn, which is not live authoritative traffic and deliberately omits it). Dormant in
        # 5a (no live caller), so this is byte-neutral on legacy.
        AGENT_RUNTIME_CALLS.labels(runtime="deep").inc()
        model = self.get_model_for_agent(lead)
        system_blocks = self.build_system_prompt(lead, context_block)
        # A-5: PRESENTER_VOICE always on the lead — inline_format=True AND is_reply_lead=True
        # force the append regardless of the deep_inline_format flag (single-lead subsumes it).
        augmented = _augment_system_blocks_for_inline(system_blocks, True, is_reply_lead=True)
        thread_id = make_thread_id(workspace_id)
        deep_agent = await self._build_deep_agent_for(
            lead,
            tools,
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            authorization_source=AuthorizationSource.DIRECT_USER_REQUEST,
            system_prompt=build_system_message(augmented),
            context_block=context_block,
            # A3: the ungated single-lead path fail-closes its writes on Redis-down.
            require_write_lock=True,
            # P2.1: the chat permission mode (ask/auto installs the action-time gate; None/
            # bypass leaves the chain byte-identical). Current 5b callers pass nothing yet;
            # P2.3 wires the real per-turn mode.
            permission_mode=permission_mode,
        )
        graph_input = {"messages": [{"role": "user", "content": message}]}
        async for frame in self._stream_and_reap(
            deep_agent,
            graph_input,
            thread_id=thread_id,
            agent_name=lead.name,
            model=model,
        ):
            yield frame

    async def _load_pending_approval(
        self, db, approval_id: str, workspace_id: str
    ) -> tuple[Approval | None, dict | None]:
        """Load + guard a pending Approval for a resume, SHARED by ``resume_deep_turn`` (the
        routed per-step lead) and ``resume_deep_lead`` (the synthetic chat single-lead) so no
        tenant-isolation / replay guard is ever dropped from one path (Sec-N5).

        Runs the four resume guards that are IDENTICAL across both paths:

        (a) load the Approval by id;
        (b) tenant-isolation (IDOR): a missing OR cross-tenant approval returns the SAME
            generic "approval not found" so existence is never leaked across tenants
            (``workspace_id`` is resolved from the caller's auth context, never LLM-supplied);
        (c) already-decided: ``status != "pending"`` → "approval not pending", which blocks
            re-resuming (and thus re-executing) an already-decided approval;
        (d) A6 (Step-10A): the stored ``thread_id`` MUST embed the caller's workspace — a
            thread minted for another tenant, a legacy colonless id, or a MISSING thread_id
            (→ ``workspace_of_thread_id`` None) is refused with the SAME generic not-found
            envelope (no existence leak), before any state change.

        Returns ``(approval, None)`` when all four guards pass, else ``(None, error_frame)`` —
        the caller yields the frame and returns. NO status mutation happens here, so a rejected
        guard leaves the row untouched (pending + re-inspectable). Caller-specific refs
        validation (routed ``agent_name`` vs. plan-derived ``lead_scope``) stays with each
        caller so each keeps its own message + fail-closed semantics.
        """
        approval = await db.get(Approval, approval_id)
        if approval is None or approval.workspace_id != workspace_id:
            return None, {"event": "error", "message": "approval not found"}
        if approval.status != "pending":
            return None, {"event": "error", "message": "approval not pending"}
        thread_id = (approval.artifact_refs or {}).get("thread_id")
        if workspace_of_thread_id(thread_id) != workspace_id:
            return None, {"event": "error", "message": "approval not found"}
        return approval, None

    async def _cas_flip_pending(self, db, approval_id: str, values: dict) -> bool:
        """Atomically consume a PENDING approval (I1). A conditional
        ``UPDATE approvals SET … WHERE approval_id=:id AND status='pending'`` +
        ``commit``; returns True iff THIS caller won the flip (``rowcount == 1``).

        The read-side ``status != "pending"`` check in ``_load_pending_approval`` is
        ADVISORY only — two concurrent resumes (a double-click, or approve racing reject)
        can BOTH pass it before either flips. This CAS is the authoritative interlock:
        Postgres serializes the two conditional UPDATEs on the row lock, so the loser
        re-evaluates ``status='pending'`` against the already-decided row → matches 0 rows
        → ``rowcount 0`` → False. The caller then yields "approval not pending" and aborts
        WITHOUT streaming, so the paused write is replayed exactly once (never twice).
        Shared by ``resume_deep_turn`` (autonomous per-step lead) and ``resume_deep_lead``
        (chat single-lead); each builds its own ``values`` so its field-set is preserved.
        """
        result = await db.execute(
            update(Approval)
            .where(Approval.approval_id == approval_id, Approval.status == "pending")
            .values(**values)
        )
        await db.commit()
        return result.rowcount == 1

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
            # Shared load + tenant-isolation + status + A6 guards (Sec-N5).
            approval, guard_error = await self._load_pending_approval(db, approval_id, workspace_id)
            if guard_error is not None:
                yield guard_error
                return
            refs = approval.artifact_refs or {}
            thread_id = refs.get("thread_id")
            agent_name = refs.get("agent_name")
            # CF-1: re-inject the ContextPack the original turn assembled (persisted onto the
            # Approval at pause time). Without this the continuation would rebuild with an
            # EMPTY context block and lose the turn's ambient entities/memories/preferences.
            persisted_context = refs.get("context_block", "")
            # CF-5: validate the routed-agent rebuild inputs BEFORE consuming (flipping +
            # committing) the approval, so a malformed approval stays pending and re-resumable.
            # A missing/ws-mismatched thread_id is already refused by the shared guard's A6
            # check above; this keeps the agent_name guard (the routed lead's identity).
            if not thread_id or not agent_name:
                yield {"event": "error", "message": "approval missing thread_id/agent_name"}
                return
            agent = self._agents.get(agent_name)
            if agent is None:
                yield {"event": "error", "message": f"Unknown agent: {agent_name}"}
                return
            # I1: atomic compare-and-swap flip (closes the double-resume TOCTOU). The
            # read-side pending check above is advisory; only ONE concurrent resume wins
            # this conditional UPDATE.
            now = datetime.now(timezone.utc)
            new_status = "approved" if decision == "approve" else "rejected"
            if not await self._cas_flip_pending(
                db,
                approval_id,
                {"status": new_status, "decided_at": now, "approved_by": user_id},
            ):
                yield {"event": "error", "message": "approval not pending"}
                return
            # Keep the loaded row consistent with the committed CAS (the bulk UPDATE does
            # not touch the in-memory object).
            approval.status = new_status
            approval.decided_at = now
            approval.approved_by = user_id

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
        async for frame in self._stream_and_reap(
            deep_agent,
            Command(resume=decision),
            thread_id=thread_id,
            agent_name=agent_name,
            model=model,
        ):
            yield frame

    async def resume_deep_lead(
        self,
        *,
        approval_id: str,
        decision: str,
        reason: str | None = None,
        user_id: str,
        workspace_id: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Re-enter a paused CHAT single-lead turn (P2.2a) via ``Command(resume=decision)`` on
        the paused Approval's stored ``thread_id``. ``decision`` is ``"approve"`` or
        ``"reject"``; ``reason`` is the user's optional decline/modify note.

        The sibling of ``resume_deep_turn`` for the SYNTHETIC lead: that lead is NOT registered
        in ``self._agents`` and its ``capability_scope`` is plan-derived, so it is rebuilt from
        the plan scope persisted on the Approval (``lead_scope``) via ``_make_lead`` rather than
        looked up. Two differences from ``resume_deep_turn`` are load-bearing:

        * ``authorization_source`` is ``DIRECT_USER_REQUEST`` (NEVER autonomous) — the chat
          lead's turn is user-authorized, so the ``trust_gate`` stays DORMANT (short-circuits);
        * the action-time ``permission_gate`` is ALWAYS re-installed, FAIL-CLOSED. A PENDING
          chat Approval PROVES the first pass interrupted, so resume MUST re-interrupt to honor
          the verdict — the gate installation is NEVER keyed on the persisted mode. The
          fail-closed ``resume_mode`` coerces anything outside ``ask``/``auto`` to ``ask`` so
          ``_build_deep_agent_for`` always installs the gate. Rebuilding with
          ``permission_mode=None`` would leave BOTH gates inactive → a REJECTED write would
          execute (fail-OPEN). That is the invariant the mandatory reject-doesn't-fire test
          pins.
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

        # Lazy import (mirrors build_chat_lead's style) — rebuild the synthetic lead from a scope.
        from src.orchestrator.lead_builder import _make_lead

        async with self._db_factory() as db:
            # Shared load + tenant-isolation + status + A6 guards (Sec-N5).
            approval, guard_error = await self._load_pending_approval(db, approval_id, workspace_id)
            if guard_error is not None:
                yield guard_error
                return
            refs = approval.artifact_refs or {}
            thread_id = refs.get("thread_id")
            lead_scope = refs.get("lead_scope")
            # Graceful fail-CLOSED on the rebuild inputs, validated BEFORE flipping status so a
            # malformed approval stays pending + re-inspectable. A missing lead_scope must DENY
            # (never fall back to a broad scope): the synthetic lead's authority IS exactly the
            # persisted plan-bounded scope, so with no scope there is nothing safe to rebuild.
            # (A missing/ws-mismatched thread_id is already refused by the shared guard's A6.)
            if not thread_id or not isinstance(lead_scope, list) or not lead_scope:
                # not-a-list (corrupted refs) is denied too: frozenset("email.send") would
                # silently yield a CHARACTER set (garbage scope) — deny rather than mis-rebuild.
                yield {"event": "error", "message": "approval not resumable"}
                return
            # Rebuild the synthetic lead from the persisted plan-bounded scope. offered-tools ⊆
            # enforced-scope is reproduced by construction (_resolve_tools →
            # get_tools_for_agent offers only tools whose capability ∈ this scope).
            lead = _make_lead(frozenset(lead_scope), self._settings.cheap_mode)
            # CF-1: re-inject the ContextPack the original turn assembled.
            persisted_context = refs.get("context_block", "")
            # FAIL-CLOSED resume mode (THE load-bearing invariant): a PENDING chat Approval
            # proves the first pass interrupted, so the gate MUST be re-installed to re-reach
            # interrupt() and honor the verdict. NEVER key installation on the persisted mode —
            # coerce anything outside ask/auto to "ask" so _build_deep_agent_for installs the
            # gate. Rebuilding with permission_mode=None would leave BOTH gates inactive
            # (trust_gate dormant on direct_user_request + no permission_gate) → a REJECTED
            # write would EXECUTE (fail-open).
            resume_mode = refs.get("permission_mode")
            if resume_mode not in ("ask", "auto"):
                resume_mode = "ask"
            # Flip + persist BEFORE streaming so the replayed gate reads the decided row (the
            # permission_gate's CF-2 replay branch quotes decision_reason on reject).
            #
            # I1: atomic compare-and-swap flip (closes the double-resume TOCTOU). The
            # read-side pending check in _load_pending_approval is advisory; only ONE
            # concurrent resume wins this conditional UPDATE. The loser aborts here WITHOUT
            # streaming, so the paused write replays exactly once.
            now = datetime.now(timezone.utc)
            new_status = "approved" if decision == "approve" else "rejected"
            values: dict[str, Any] = {
                "status": new_status,
                "decided_at": now,
                "approved_by": user_id,
                "decision_reason": reason,
            }
            if decision == "approve":
                # A-7 convention (routes_approvals): a reason on an approve = a "modified"
                # decision, else a plain "approved". Stamped so the verified-outcome hook can
                # use it (parity with the click-through approve path).
                values["artifact_refs"] = {
                    **refs,
                    "decision_type": "modified" if reason else "approved",
                }
            if not await self._cas_flip_pending(db, approval_id, values):
                yield {"event": "error", "message": "approval not pending"}
                return
            # Keep the loaded row consistent with the committed CAS (the bulk UPDATE does not
            # touch the in-memory object); downstream + the replayed gate read the decided row.
            approval.status = new_status
            approval.decided_at = now
            approval.approved_by = user_id
            approval.decision_reason = reason
            if decision == "approve":
                approval.artifact_refs = values["artifact_refs"]

        model = self.get_model_for_agent(lead)
        tools = await self._resolve_tools(lead, workspace_id, None)
        system_blocks = self.build_system_prompt(lead, persisted_context)
        # PRESENTER_VOICE (parity with stream_deep_lead): the RESUMED lead IS the reply-producing
        # lead — it emits the post-decision user-facing confirmation — so it MUST carry the same
        # inline/reply-lead augmentation the pause path applies. Omitting it drops the surface
        # contract from the reply (the PRESENTER_VOICE surface-drop regression class). Mirror
        # stream_deep_lead exactly (is_reply_lead=True forces PRESENTER_VOICE regardless of flag).
        augmented = _augment_system_blocks_for_inline(system_blocks, True, is_reply_lead=True)
        deep_agent = await self._build_deep_agent_for(
            lead,
            tools,
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            # NEVER autonomous for the chat lead: direct_user_request keeps the trust_gate
            # dormant (it short-circuits), so the permission_gate below is the sole active gate.
            authorization_source=AuthorizationSource.DIRECT_USER_REQUEST,
            system_prompt=build_system_message(augmented),
            # CF-1: thread the original turn's context forward so a CHAINED approval (a 2nd write
            # this resumed continuation pauses on) carries the same context.
            context_block=persisted_context,
            # A3: the ungated single-lead path fail-closes its writes on Redis-down.
            require_write_lock=True,
            # THE invariant: ALWAYS install the permission_gate on resume (fail-closed ask/auto).
            permission_mode=resume_mode,
        )
        async for frame in self._stream_and_reap(
            deep_agent,
            Command(resume=decision),
            thread_id=thread_id,
            agent_name=lead.name,
            model=model,
        ):
            yield frame

    async def run_autonomous_deep_step(
        self,
        *,
        executor: SubAgent,
        tools: list,
        message: str,
        context_block: str,
        user_id: str,
        workspace_id: str,
        run_id: str,
        step_id: str,
        pre_approved_capabilities: frozenset[str],
        model: str | None = None,
        cancel_event=None,
    ) -> dict:
        """Execute one approved autonomous step via a durable deep agent (Step 10C).

        Provenance is the literal AuthorizationSource.AUTONOMOUS captured HERE (never
        LLM-supplied). The dispatcher's execute_tool is wrapped with the per-step
        idempotency ledger so LangGraph's at-least-once replay fires each external write
        EXACTLY ONCE. pre_approved_capabilities (the step's already-step-gated capability)
        short-circuits the deep trust_gate so it never double-prompts (SQ2 Branch C).
        Returns the {status, result, tools_called, errors}(+auth_required) dict that
        dag_runner._finalize_with_verification consumes — identical to the legacy path.
        """
        # LOW-1 (P1a security review): fail CLOSED on an empty tenant. CLAUDE.md requires an
        # explicit workspace_id; an empty ws would make make_thread_id("") -> "c::{ulid}" whose
        # A6 round-trip degenerates to "" == "" (the guard below becomes a no-op) AND the ledger
        # reserve would FK-violate (silent no-dedup -> a resumed write could double-fire). A
        # legitimate run never has an empty workspace_id (TaskRun.workspace_id is NOT NULL);
        # refuse up front so a future/buggy caller cannot slip past the tenant guards.
        if not (workspace_id or "").strip():
            logger.warning(
                "[deep_runtime] run_autonomous_deep_step refusing empty workspace_id "
                "run=%s step=%s",
                run_id,
                step_id,
            )
            return {
                "status": "error",
                "result": "",
                "tools_called": [],
                "errors": ["missing workspace_id"],
            }

        from src.services.idempotency import (
            IdempotencyContext,
            IdempotencyLedger,
            make_idempotent_execute_tool_fn,
        )

        thread_id = make_thread_id(workspace_id or "")

        # A6 (Step-10A carry): the thread_id we just minted MUST embed this workspace.
        # We just minted it, so it matches — but this defense-in-depth guard is the seam's
        # carry of the A6 invariant: never build/stream on a ws-mismatched checkpoint thread.
        if workspace_of_thread_id(thread_id) != (workspace_id or ""):
            logger.warning(
                "[deep_runtime] run_autonomous_deep_step refusing ws-mismatched thread_id "
                "run=%s step=%s",
                run_id,
                step_id,
            )
            return {
                "status": "error",
                "result": "",
                "tools_called": [],
                "errors": ["workspace thread mismatch"],
            }

        # THE CRUX (Step 10C P1a): wrap the dispatcher's execute_tool with the per-step
        # idempotency ledger. The deep chain wires its dispatcher to the RAW executor, so
        # without this wrap a resumed autonomous step would double-fire external writes under
        # LangGraph's at-least-once replay. The ledger dedups writes and BYPASSES reads. Do
        # NOT also lock-wrap here — the deep chain's write_lock middleware already fences
        # writes; the NET-NEW layer for the deep build is the LEDGER only.
        idem_fn = make_idempotent_execute_tool_fn(
            self._tool_executor.execute_tool,
            IdempotencyContext(
                ledger=IdempotencyLedger(self._db_factory),
                run_id=run_id,
                step_id=step_id,
                workspace_id=workspace_id or "",
                db_factory=self._db_factory,
            ),
        )

        # jarvis_tool_dispatcher calls execute_tool(name, args, user_id, workspace_id)
        # POSITIONALLY, but make_idempotent_execute_tool_fn returns a fn with KEYWORD-ONLY
        # user_id/workspace_id — bridge the two calling conventions here.
        async def _ledgered_execute_tool(name, args, user_id, workspace_id):
            return await idem_fn(name, args, user_id=user_id, workspace_id=workspace_id)

        system_blocks = self.build_system_prompt(executor, context_block)
        deep_agent = await self._build_deep_agent_for(
            executor,
            tools,
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            # AUTONOMOUS activates the deep trust_gate; pre_approved_capabilities short-circuits
            # the step's already-step-gated capability so it is not double-prompted (Branch C).
            authorization_source=AuthorizationSource.AUTONOMOUS,
            system_prompt=build_system_message(system_blocks),
            context_block=context_block,
            execute_tool=_ledgered_execute_tool,
            pre_approved_capabilities=pre_approved_capabilities,
        )

        result = ""
        tools_called: list[str] = []
        errors: list[str] = []
        auth_required: dict | None = None
        approval_blocked = False
        error_occurred = False

        async for frame in stream_deep_agent_events(
            deep_agent,
            {"messages": [{"role": "user", "content": message}]},
            {"configurable": {"thread_id": thread_id}},
            agent_name=executor.name,
            model=model,
            durability="sync",
        ):
            event = frame.get("event") if isinstance(frame, dict) else None
            if event == "agent_done":
                result = frame.get("text", "") or ""
                tools_called = frame.get("tools_called") or []
            elif event == "tool_result":
                parsed = _parse_tool_result_content(frame.get("result"))
                if isinstance(parsed, dict) and parsed.get("error_code") == "auth_required":
                    if auth_required is None:
                        auth_required = parsed
                elif frame.get("blocked"):
                    errors.append(str(frame.get("result")))
            elif event == "error":
                errors.append(frame.get("message") or "")
                error_occurred = True
            elif event == "approval_needed":
                # Branch C: the step's PRE-APPROVED capability never reaches here; only an
                # UN-approved within-step capability expansion would. 10C has NO
                # GraphInterrupt→run-pause bridge, so fail-block the step (do not pause/bridge).
                approval_blocked = True
                errors.append("unapproved within-step capability required approval")

        # Step 10C P5: the autonomous per-step thread is never resumed (run-level durable
        # resume is via P4's reconcile), so reap its durable checkpoints the moment the step
        # finishes — mirrors the chat path's reap-on-non-paused-completion (resume_deep_turn).
        # Best-effort + no-op on a saverless/MemorySaver process (dormant-safe). ws-scoped by
        # construction: reap_thread deletes ONLY this ws-embedded thread_id. Placed after the
        # stream loop so it runs on EVERY terminal outcome (agent_done / auth_required /
        # approval-blocked / error) — under Branch C the step never pauses/interrupts, so the
        # thread is done regardless of which outcome fired.
        await reap_thread(self._checkpointer_provider(), thread_id)

        # Mirror step_runner.run_step_via_agent_loop's output shape EXACTLY.
        output: dict = {
            "status": "completed",
            "result": result,
            "tools_called": tools_called,
            "errors": errors,
        }
        if auth_required is not None:
            # auth_required passthrough — surfaced so DagRunner._defer_for_reauth parks the run.
            output["status"] = "error"
            output["error_code"] = "auth_required"
            output["provider"] = auth_required.get("provider", "")
            output["server"] = auth_required.get("server", "")
            output["auth_required"] = auth_required
        elif approval_blocked or error_occurred:
            output["status"] = "error"
        return output

    async def run_shadow_turn(
        self,
        agent_name: str,
        message: str,
        *,
        user_id: str,
        workspace_id: str,
        runtime: str,
        tool_executor,
    ) -> ShadowDecision:
        """Run one NON-authoritative turn on ``runtime`` (``"deep"`` | ``"legacy"``) with
        ALL tool dispatch routed through the injected ``tool_executor`` — a
        ``ShadowToolExecutor``-shaped wrapper (``execute_tool(name, input, user_id,
        workspace_id) -> dict``) that hard-suppresses writes. Step 10B Task 3b: this is
        the method the shadow-compare harness (``ShadowRunner.maybe_run_shadow``) calls;
        it is NEVER invoked from the live seam (``call_agent_stream``) — the live path is
        unaffected byte-for-byte.

        Builds fresh, throwaway state on every call: a brand-new deep-agent thread_id
        (reaped on non-paused completion, mirroring ``call_agent_stream``) and no
        persisted Plan / InteractionLog / A2UI surface / idempotency wrap on either
        branch — this is an OBSERVATION run, not a real turn. Reuses the SAME assembly
        helpers ``call_agent_stream`` uses (``_resolve_tools``, ``ContextAssembler
        .assemble_context``, ``build_system_prompt``, ``get_model_for_agent``,
        ``_maybe_capability_summary``) so the shadow decision reflects the real runtime
        build, not a hand-rolled approximation.

        Deliberately does NOT increment ``AGENT_RUNTIME_CALLS`` — that counter tracks
        live authoritative traffic for the Step-10 rollback/adoption signal; counting
        shadow runs against it would corrupt that signal.
        """
        agent = self._agents.get(agent_name)
        if not agent:
            return ShadowDecision(route=agent_name, final_text="", write_intents=frozenset())

        model = self.get_model_for_agent(agent)
        tools = await self._resolve_tools(agent, workspace_id, None)
        capability_summary = await self._maybe_capability_summary(agent_name, "", workspace_id)
        context_block = await self._context.assemble_context(
            agent_name,
            message,
            user_id=user_id,
            workspace_id=workspace_id,
            jit=(runtime == "deep" and self._settings.deep_context_jit),
        )
        system_blocks = self.build_system_prompt(
            agent, context_block, capability_summary=capability_summary
        )

        final_text = ""
        if runtime == "deep":
            thread_id = make_thread_id(workspace_id)
            # SAFETY INVARIANT (delegate-free shadow lead): we deliberately pass NO
            # ``subagents`` here, so this defaults to () even when
            # ``deep_delegates_enabled`` is ON. This is load-bearing, NOT an incidental
            # omission: ``_build_delegate_subagents`` (this file, ~:511) wires the REAL
            # ``self._tool_executor.execute_tool`` into ``build_read_only_delegate`` —
            # the injected shadow ``execute_tool`` reaches ONLY the lead's own
            # dispatcher, not a delegate's. A shadow lead that built delegates would
            # therefore LEAK a real write for any non-read-only delegate call, defeating
            # the whole point of the shadow harness. If shadow delegate-fidelity is ever
            # needed (10C/10D), the injected shadow executor MUST first be threaded into
            # the delegate build (build_read_only_delegate(..., execute_tool=<shadow>))
            # before subagents may be passed here.
            deep_agent = await self._build_deep_agent_for(
                agent,
                tools,
                user_id=user_id,
                workspace_id=workspace_id,
                thread_id=thread_id,
                # Matches the live chat build's authorization_source: keeps trust_gate
                # dormant (short-circuits before any interrupt/DB) so an observation run
                # never pauses waiting on an approval nobody will ever answer.
                authorization_source=AuthorizationSource.DIRECT_USER_REQUEST,
                # A-3/B2: the shadow lead must get the SAME lead flag the live lead gets for
                # the equivalent turn — both derive it from the same ``agent_name`` via
                # ``_is_reply_lead`` — so a shadow/live mismatch can never poison the
                # divergence signal.
                # A-4/B3: ``_augment_system_blocks_for_delegation`` is intentionally NOT
                # applied here — the shadow lead is deliberately delegate-free (``subagents=()``,
                # per the SAFETY INVARIANT above), so ``has_delegates`` would be False anyway.
                # If shadow delegate-fidelity is ever wired (10C/10D), add the delegation
                # augment here in the same composition the live seam uses, gated on the same
                # ``bool(subagents)``.
                system_prompt=build_system_message(
                    _augment_system_blocks_for_inline(
                        system_blocks,
                        self._settings.deep_inline_format,
                        is_reply_lead=_is_reply_lead(agent_name),
                    )
                ),
                context_block=context_block,
                execute_tool=tool_executor.execute_tool,
            )
            graph_input = {"messages": [{"role": "user", "content": message}]}
            async for frame in self._stream_and_reap(
                deep_agent,
                graph_input,
                thread_id=thread_id,
                agent_name=agent_name,
                model=model,
            ):
                if isinstance(frame, dict) and frame.get("event") == "agent_done":
                    final_text = frame.get("text", "")
        else:
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
                trace=None,
                execute_tool_fn=tool_executor.execute_tool,
                max_tool_rounds=10,
                stream=False,
                circuit_breaker=self._circuit_breaker,
            ):
                if isinstance(evt, LoopDone):
                    final_text = evt.text

        # Read the injected executor's recorded write-intents AFTER the run completes —
        # it accumulates them as the turn's tool calls happen.
        write_intents = frozenset(getattr(tool_executor, "write_intents", ()))
        return ShadowDecision(route=agent_name, final_text=final_text, write_intents=write_intents)

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
