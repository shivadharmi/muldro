"""B4/A2: the real per-connector read-back ``read_fn`` for the deep runtime.

Step 7C wired the inline read-back middleware (``middleware/readback.py``) with
``read_fn=None`` — the deferred-tick template where every irreversible write resolves
UNVERIFIED, never CONTRADICTED. This module builds the REAL ``read_fn`` that routes the
post-condition read through the CENTRAL tool dispatcher (``ToolExecutor.execute_tool``),
mirroring the autonomous path's already-correct ``step_runner.run_readback``.

Safety here is NOT from capability-scope (that is a SEPARATE outer middleware; this read_fn
calls ``execute_tool`` DIRECTLY, bypassing the middleware chain). It is safe because the
read capability is POST-CONDITION-DERIVED (never LLM-selected), side-effect-free, and
``execute_tool`` re-resolves the tool workspace-scoped at dispatch.

Wired at ``agent_invoker._build_deep_agent_for`` ONLY when ``settings.deep_readback_enabled``
(default OFF) — so with the flag off the real read_fn is never constructed and the chain is
byte-neutral.

Fail-safe design (mirrors ``run_readback``):
  1. A ``read_capability`` in ``_READBACK_UNSERVABLE_CAPABILITIES`` (imported — SINGLE SOURCE
     — from ``step_runner``; a drifted copy would silently re-open the false-CONTRADICT hole)
     is REFUSED -> the verifier fails safe to UNVERIFIED, never a false CONTRADICTED. On this
     branch ``calendar.get`` is backed only by ``query_freebusy`` (free/busy ranges, not an
     event-by-id lookup), so a LIVE read of the mock-only ``calendar.create`` post-condition
     would false-CONTRADICT a correct write without this guard.
  2. Resolve ``read_capability`` -> ``tool.name`` via ``list_tools(enabled_only=True)`` — NAME
     resolution only; ``execute_tool`` re-resolves the tool workspace-scoped at dispatch, and
     reads are side-effect-free, so there is no cross-tenant effect from the name lookup.
  3. Dispatch through ``execute_tool`` (the dispatcher). Reads bypass the idempotency ledger
     (side-effect-free), so no double-fire in any ``execute_tool`` variant (real /
     ledger-wrapped). Any exception here -> the verifier resolves UNVERIFIED (readback.py).
"""

from __future__ import annotations

from src.deep_runtime.middleware.muldro_tool_dispatcher import ExecuteToolFn
from src.services.step_runner import _READBACK_UNSERVABLE_CAPABILITIES  # single source
from src.services.verification.readback import ReadFn


class FreshSessionToolLister:
    """Lists enabled tools via a fresh short-lived DB session per call.

    The autonomous path (``GraphExecutor``/``StepRunner``) holds a per-run ``ToolRegistry``
    bound to a live request-scoped session; the chat/deep path has no such per-turn registry,
    so the read-back opens a session per read — the same pattern ``ToolExecutor.execute_tool``
    uses for its own per-dispatch ``ToolRegistry``. Duck-types the ``tool_registry`` seam
    ``make_readback_read_fn`` needs (an async ``list_tools(enabled_only=...)`` returning tool
    records carrying ``.name`` + ``.capability``)."""

    def __init__(self, db_factory, workspace_id: str):
        self._db_factory = db_factory
        self._workspace_id = workspace_id

    async def list_tools(self, enabled_only: bool = True):
        from src.services.tool_registry import ToolRegistry

        async with self._db_factory() as db:
            # ``list_tools`` is workspace-AGNOSTIC (a global catalog query) — the
            # ``workspace_id or None`` here is a no-op for it and used for NAME resolution
            # only. Real workspace scoping happens later in ``execute_tool``'s ``get_tool``
            # at dispatch.
            registry = ToolRegistry(db, workspace_id=self._workspace_id or None)
            return await registry.list_tools(enabled_only=enabled_only)


def make_readback_read_fn(
    *,
    execute_tool: ExecuteToolFn,
    tool_registry,
    user_id: str,
    workspace_id: str,
) -> ReadFn:
    """Build the deep-runtime read-back ``read_fn`` (``ReadFn = (read_capability, read_args)
    -> result``). Mirrors ``step_runner.run_readback``: unservable-guard -> name-resolve ->
    dispatch through ``execute_tool``.

    ``execute_tool`` is the BUILD's resolved dispatcher fn (the real
    ``ToolExecutor.execute_tool`` for chat/autonomous, or the ledger-wrapped variant
    for autonomous turns). It is invoked with the same POSITIONAL convention as
    ``muldro_tool_dispatcher`` / ``ExecuteToolFn`` (``name, args, user_id, workspace_id``), so
    every variant honors it. ``tool_registry`` only needs an async
    ``list_tools(enabled_only=True)`` -> records carrying ``.name`` + ``.capability``.
    """

    async def _read_back(read_capability: str, read_args: dict) -> object:
        # (1) fail-safe: a read capability whose resolved tool cannot serve the required read
        # shape is REFUSED -> raising makes the verifier resolve UNVERIFIED, never a false
        # CONTRADICTED. Single source of truth: imported from step_runner (no drifted copy).
        if read_capability in _READBACK_UNSERVABLE_CAPABILITIES:
            raise RuntimeError(
                f"read capability {read_capability} has no tool that serves the required "
                "read shape on this branch — failing safe to unverified"
            )

        # (2) resolve read_capability -> tool.name (name resolution ONLY; execute_tool
        # re-resolves workspace-scoped at dispatch, reads are side-effect-free).
        all_tools = await tool_registry.list_tools(enabled_only=True)
        tool = next((t for t in all_tools if t.capability == read_capability), None)
        if tool is None:
            raise RuntimeError(f"no tool serves read capability {read_capability}")

        # (3) dispatch through the central execute_tool dispatcher. Positional call convention
        # == muldro_tool_dispatcher / ExecuteToolFn.
        result = await execute_tool(tool.name, read_args, user_id, workspace_id)

        # (4) fail-safe on the executor's ERROR CONTRACT: execute_tool NEVER raises on a read
        # failure — it CATCHES and RETURNS an error dict ({"error": ...}, {..., "blocked": True},
        # {"status": "error", ...}). Returning that verbatim would let a post-condition assertion
        # see a non-matching result and false-CONTRADICT a correct write — the exact false-fail a
        # verification OUTAGE must never cause. RAISE instead -> the verifier resolves UNVERIFIED.
        # Guarded on the error markers ONLY (a legitimate success dict — status "ok" / raw content
        # — never trips this).
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

    return _read_back
