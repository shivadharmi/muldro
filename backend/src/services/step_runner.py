"""StepRunner — agentic execution of a single DAG step.

Extracted from ``GraphExecutor`` (god-object decomposition, 2026-06-20). Given a
ready ``TaskStep``, it runs the Executor step on the durable deep step-executor
(``run_step_via_deep_agent`` → ``AgentInvoker.run_autonomous_deep_step``, with full
tool discovery, prior-step context injection, and per-run trace accumulation),
falling back to a minimal single-turn Claude call when no deep step-runner is wired.

It depends *downward* on ``StepGraphStore`` (to read sibling step outputs) and
``SurfaceEmitter`` (the ``tool_call_started`` event); it never imports
``graph_executor``. The ``db_factory`` is resolved live via a provider so the
coordinator stays the single source of truth (tests reassign ``_db_factory``),
and the per-run trace is read through ``active_traces_provider`` for the same
reason — the coordinator owns the ``_active_traces`` dict lifecycle.
"""

from __future__ import annotations

import logging

from src.config.settings import Settings
from src.llm.utility import complete_text
from src.llm_utils import parse_llm_object
from src.models.task_graph import TaskRun, TaskStep
from src.services.contention import (
    CONTENDED_MESSAGE,
    WRITE_LOCK_UNAVAILABLE_MESSAGE,
    blocked_body,
)
from src.services.execution_state import TERMINAL_SUCCESS
from src.services.execution_surface_emitter import SurfaceEmitter
from src.services.step_graph_store import StepGraphStore

logger = logging.getLogger(__name__)

# Read capabilities whose currently-resolved tool cannot serve the read shape a
# post-condition needs, so a LIVE read-back would risk a FALSE CONTRADICTED on a
# correct irreversible write. ``run_readback`` refuses these -> the verifier fails
# safe to UNVERIFIED (completed_unverified). On this branch ``calendar.get`` is
# backed by ``query_freebusy`` (free/busy ranges, not an event-by-id lookup); remove
# the capability from this set once a real get-event tool backs it. See D8 note in
# ``src/services/verification/post_conditions.py``.
_READBACK_UNSERVABLE_CAPABILITIES: frozenset[str] = frozenset({"calendar.get"})


def _should_build_write_lock_wrapper(redis, tool_registry, require_redis: bool) -> bool:
    """Build the cross-path write-lock wrapper when we can classify tools (registry present)
    AND either Redis is available (normal locking) OR the operator opted into fail-closed
    (require_redis → the in-wrapper redis-None branch refuses writes rather than run them
    unlocked). Byte-neutral when require_redis is off: reduces to the old
    `redis is not None and tool_registry is not None` gate."""
    return tool_registry is not None and (redis is not None or require_redis)


def make_lock_wrapped_execute_tool_fn(
    inner_fn, *, redis, workspace_id, resolve_capability, require_redis: bool = False
):
    """Wrap an execute_tool_fn so external WRITES acquire the cross-path write lock
    (src.services.write_lock) — same key as the deep-runtime middleware. Reads pass through.
    Layered OUTSIDE the idempotency ledger so the lock serializes the whole write attempt
    (idempotency check + execute).

    The wrapped fn matches the tool dispatcher's calling convention exactly:
    ``execute_tool_fn(tool_name, tool_input, user_id=..., workspace_id=...)`` — user_id and
    workspace_id are keyword-only, forwarded verbatim to ``inner_fn``. The LOCK KEY, however,
    is keyed on the CLOSURE-captured workspace_id (the run's workspace), never on the call's
    argument, mirroring the deep middleware's closure-captured workspace_id safety property.

    ``require_redis`` (Step-10A A3, default False): when True, a WRITE is REFUSED
    (fail-closed) rather than executed unlocked if Redis is unavailable. Default False
    preserves today's fail-OPEN behavior byte-for-byte — the ``redis is None`` early
    return below runs BEFORE capability resolution, so nothing about the flag-off path
    changes (not even an extra ``resolve_capability`` call).
    """
    from src.integrations.capabilities import is_read_only_capability
    from src.services.write_lock import WriteLockContended, acquire_write_lock

    lock_workspace_id = workspace_id  # closure-captured; the lock key never comes from the call

    async def _wrapped(tool_name, tool_input, *, user_id, workspace_id):
        if redis is None and not require_redis:
            return await inner_fn(tool_name, tool_input, user_id=user_id, workspace_id=workspace_id)
        capability = await resolve_capability(tool_name)
        if redis is None:
            # require_redis True + Redis down: refuse writes (fail-closed), reads pass.
            if not is_read_only_capability(capability):
                return blocked_body(WRITE_LOCK_UNAVAILABLE_MESSAGE)
            return await inner_fn(tool_name, tool_input, user_id=user_id, workspace_id=workspace_id)
        if not capability or is_read_only_capability(capability):
            return await inner_fn(tool_name, tool_input, user_id=user_id, workspace_id=workspace_id)
        try:
            async with acquire_write_lock(redis, lock_workspace_id, capability):
                return await inner_fn(
                    tool_name, tool_input, user_id=user_id, workspace_id=workspace_id
                )
        except WriteLockContended:
            return blocked_body(CONTENDED_MESSAGE)

    return _wrapped


class StepRunner:
    """Runs one step via the Executor agent loop (or a minimal Claude fallback)."""

    def __init__(
        self,
        *,
        settings: Settings,
        client,
        store: StepGraphStore,
        emitter: SurfaceEmitter,
        db_factory_provider,
        active_traces_provider,
        tool_registry=None,
        context_builder=None,
        execute_tool_fn=None,
        redis=None,
        deep_step_runner=None,
    ):
        self._settings = settings
        self._client = client
        self._store = store
        self._emitter = emitter
        self._db_factory_provider = db_factory_provider
        self._active_traces_provider = active_traces_provider
        self._tool_registry = tool_registry
        self._context_builder = context_builder
        self._execute_tool_fn = execute_tool_fn
        self._redis = redis
        # Step 10C P1b: the autonomous durable deep step-executor callable
        # (``AgentInvoker.run_autonomous_deep_step``), injected by GraphExecutor's
        # worker lifespan (P2). ``None`` in tests/legacy → the deep branch in
        # ``run_step_action`` is unreachable → byte-neutral.
        self._deep_step_runner = deep_step_runner

    @property
    def _db_factory(self):
        """Resolve the current DB session factory live via the provider."""
        return self._db_factory_provider()

    @property
    def _active_traces(self):
        """Resolve the coordinator's live per-run trace map via the provider."""
        return self._active_traces_provider()

    async def run_step_action(
        self,
        step: TaskStep,
        run: TaskRun,
        cancel_event=None,
    ) -> dict:
        """Execute the actual action for a step.

        Routes to agent loop if dependencies are available, otherwise uses
        a minimal single-turn Claude fallback.
        """
        input_data = step.input_data or {}
        task_type = input_data.get("capability", input_data.get("task_type", "unknown"))

        await self._emitter.emit_event(
            "tool_call_started",
            run.user_id,
            {"run_id": run.run_id, "step_id": step.step_id, "tool_name": task_type},
            workspace_id=run.workspace_id,
        )

        # Deep is the ONLY runtime (Step 11 Phase 4). Use the durable deep step runner
        # when it (+ a db_factory) was injected; otherwise the minimal single-turn fallback.
        if self._deep_step_runner is not None and self._db_factory:
            return await self.run_step_via_deep_agent(step, run, cancel_event=cancel_event)

        # Fallback: minimal single-turn Claude call (no deep step runner injected).
        return await self.minimal_claude_action(step, run)

    async def minimal_claude_action(self, step: TaskStep, run: TaskRun) -> dict:
        """Minimal single-turn Claude action without tool discovery.

        Used as fallback when agent loop dependencies are not available.
        """
        input_data = step.input_data or {}
        task_type = input_data.get("capability", input_data.get("task_type", "unknown"))
        context_prompt = await self.build_step_context(run, step)

        goal = input_data.get("goal", input_data.get("context", ""))
        parts = [f"Task type: {task_type}"]
        if goal:
            parts.append(f"Goal: {goal}")
        for key, value in input_data.items():
            if key not in ("task_type", "goal", "context"):
                parts.append(f"{key}: {value}")
        if context_prompt:
            parts.append(f"\n--- Background ---\n{context_prompt}")

        system = (
            f"You are Muldro's task execution engine handling a '{task_type}' step. "
            "Complete the task described below. "
            'Respond with JSON: {"status": "completed", "result": "...", "details": {...}}'
        )

        raw = await complete_text(
            system=system,
            user="\n".join(parts),
            tier="resolved",
            max_tokens=1024,
            workspace_id=run.workspace_id,
        )

        # `parse_llm_object`, not `parse_llm_json`: a JSON ARRAY parses SUCCESSFULLY, so the
        # raw-text fallback below would be skipped and a list would escape to a caller that
        # reads this as a step-result dict.
        return parse_llm_object(raw, default={"status": "completed", "result": raw})

    async def build_executor_tools(self, step_capability: str, workspace_id: str) -> list[dict]:
        """Offer ONLY the current step's capability tools (its primary tool + same-family
        read-only tools), NOT the executor's full write union. The per-step scope security
        win of Step 6C: an ``email.send`` step is never offered ``calendar.create``'s tool.
        Delegates to the workspace-scoped ``CapabilityResolver.resolve_for_step``."""
        from src.services.capability_resolver import CapabilityResolver

        async with self._db_factory() as db:
            resolver = CapabilityResolver(db, workspace_id=workspace_id)
            return await resolver.resolve_for_step(step_capability)

    async def run_readback(self, read_capability: str, read_args: dict, run: TaskRun) -> object:
        """Invoke a READ capability (post-condition read-back) via the tool path and
        return its raw result. Best-effort: raises on any failure so ReadBackVerifier
        resolves it to UNVERIFIED (never a false CONTRADICTED). Reads never go through
        the idempotency ledger, so this is side-effect free.

        Resolution note: ``build_executor_tools(step_capability, workspace_id)`` returns
        capability-stripped ``{"name", "description", "input_schema"}`` dicts (no
        ``.capability``), so we resolve ``read_capability`` -> tool independently via the
        registry's ``ToolDefinition`` objects (which carry ``.capability`` and ``.name``).

        Production-safety guard (D8 footgun): a read capability whose currently-resolved
        tool cannot actually serve the required read shape (e.g. ``calendar.get`` is
        backed by ``query_freebusy`` — free/busy ranges, NOT an event-by-id lookup) is
        REFUSED here. Serving it live would let a non-matching result flip a correct
        irreversible write to CONTRADICTED (a false ``partially_completed``). Raising
        instead makes the verifier fail SAFE to UNVERIFIED (``completed_unverified``).
        Tests inject their own mocked ``read_fn`` and never reach this path, so the
        mechanism stays fully proven in ``test_readback.py``."""
        if read_capability in _READBACK_UNSERVABLE_CAPABILITIES:
            raise RuntimeError(
                f"read capability {read_capability} has no tool that serves an "
                "event-by-id read on this branch — failing safe to unverified"
            )
        if self._execute_tool_fn is None:
            raise RuntimeError("no execute_tool_fn available for read-back")
        if self._tool_registry is None:
            raise RuntimeError("no tool_registry available for read-back")

        # This list_tools() lookup is workspace-agnostic and used for NAME resolution
        # ONLY (capability -> tool.name). execute_tool re-resolves the tool
        # workspace-scoped at dispatch time, and reads are side-effect-free, so there is
        # no cross-tenant effect from resolving the name against the global list here.
        all_tools = await self._tool_registry.list_tools(enabled_only=True)
        tool = next((t for t in all_tools if t.capability == read_capability), None)
        if tool is None:
            raise RuntimeError(f"no tool serves read capability {read_capability}")

        result = await self._execute_tool_fn(
            tool.name,
            read_args,
            user_id=run.user_id,
            workspace_id=run.workspace_id or "",
        )

        # Fail-safe on the executor's ERROR CONTRACT: execute_tool NEVER raises on a read
        # failure — it CATCHES and RETURNS an error dict ({"error": ...}, {..., "blocked": True},
        # {"status": "error", ...}). Returning it verbatim would let the post-condition assertion
        # see a non-matching result and false-CONTRADICT a correct write — the exact false-fail a
        # verification OUTAGE must never cause. RAISE instead -> ReadBackVerifier resolves it to
        # UNVERIFIED. Error markers ONLY (a legitimate success dict never trips this).
        if isinstance(result, dict) and (
            result.get("error") is not None
            or result.get("status") == "error"
            or result.get("blocked")
        ):
            raise RuntimeError(
                f"read-back for {read_capability} returned a tool error — failing safe "
                "to unverified"
            )
        return result

    async def _build_step_message(self, step: TaskStep, run: TaskRun) -> str:
        """Build the executor's task message from step input + completed predecessor outputs.

        Shared by the durable deep path (``run_step_via_deep_agent``) and the minimal
        fallback so both hand the executor the same message.
        """
        input_data = step.input_data or {}
        task_type = input_data.get("capability", input_data.get("task_type", "unknown"))
        goal = input_data.get("goal", input_data.get("context", ""))

        # Build message from step input
        message_parts = [f"Task type: {task_type}"]
        if goal:
            message_parts.append(f"Goal: {goal}")
        for key, value in input_data.items():
            if key not in ("task_type", "goal", "context"):
                message_parts.append(f"{key}: {value}")

        message = "\n".join(message_parts)

        # Inject completed predecessor step outputs so the executor sees
        # what earlier agents (e.g. Perceiver) read or produced.
        all_steps = await self._store.get_all_steps(run.run_id)
        prior_parts: list[str] = []
        for s in all_steps:
            if s.step_id == step.step_id:
                continue
            if s.status not in TERMINAL_SUCCESS or not s.output_data:
                continue
            result_text = s.output_data.get("result", "")
            if not result_text:
                continue
            cap = (s.input_data or {}).get("capability", "unknown")
            desc = (s.input_data or {}).get("goal", cap)
            prior_parts.append(f"[{desc}]:\n{str(result_text)}")
        if prior_parts:
            message += (
                "\n\n--- Prior step results ---\n"
                + "\n\n".join(prior_parts)
                + "\n--- End of prior step results ---\n"
            )
        return message

    async def run_step_via_deep_agent(self, step, run, cancel_event=None) -> dict:
        """Execute a step via the durable deep agent (Step 10C, dormant). Builds the SAME
        message/context/per-step tools the legacy path builds, then delegates to the injected
        deep_step_runner (AgentInvoker.run_autonomous_deep_step), which owns the ledger-wrapped
        build + durable invoke + output mapping. pre_approved_capabilities = the step's single
        already-step-gated capability (the dag_runner step gate approved it) — never a broad set."""
        from src.orchestrator.agents import AGENTS

        message = await self._build_step_message(step, run)
        context_prompt = await self.build_step_context(run, step)
        executor = AGENTS.get("executor")
        if not executor:
            return {
                "status": "completed",
                "result": "Executor agent not found",
                "errors": ["Executor agent not configured"],
            }
        step_capability = (step.input_data or {}).get(
            "capability", (step.input_data or {}).get("task_type", "unknown")
        )
        tools = await self.build_executor_tools(step_capability, run.workspace_id or "")
        return await self._deep_step_runner(
            executor=executor,
            tools=tools,
            message=message,
            context_block=context_prompt,
            user_id=run.user_id,
            workspace_id=run.workspace_id or "",
            run_id=run.run_id,
            step_id=step.step_id,
            pre_approved_capabilities=frozenset({step_capability}),
            cancel_event=cancel_event,
        )

    async def build_step_context(self, run: TaskRun, step: TaskStep) -> str:
        """Build context prompt for a step using ContextBuilder.

        Step 10C P6: this EPHEMERAL context (feeds the executor prompt, never persisted)
        slims to the JIT core when ``deep_context_jit`` is on (default off → eager pack).
        ``jit`` threads into BOTH ``build`` and ``to_prompt``, mirroring the chat seam's
        ``assemble_context``.
        """
        if not self._context_builder:
            return ""
        try:
            input_data = step.input_data or {}
            query = input_data.get("goal", input_data.get("context", ""))
            task_type = input_data.get("task_type")
            # Deep is the only runtime; JIT slimming follows the flag alone (Phase 4).
            jit = self._settings.deep_context_jit
            pack = await self._context_builder.build(
                user_id=run.user_id,
                query=query or "",
                task_type=task_type,
                jit=jit,
            )
            from src.services.context_builder import ContextBuilder

            return ContextBuilder.to_prompt(pack, jit=jit)
        except Exception:
            logger.debug("ContextBuilder failed for step %s", step.step_id, exc_info=True)
            return ""
