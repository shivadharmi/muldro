"""Confirming a PREPARED action: run it, record what happened, announce it.

Split out of ``routes_approvals`` for two reasons that turned out to be one. The route module
crossed the 800-line hard cap; and the replay needs a TOOL DISPATCHER, which it was getting by
reaching into ``routes_chat``'s process-wide orchestrator singleton for a private attribute.
That reach was not just ugly — calling ``_get_orchestrator(settings)`` with no ``app`` BUILDS
the singleton with ``checkpointer_provider=(lambda: None)`` and caches it for the life of the
worker, so ``has_durable_checkpointer()`` then answers False forever. Every later chat turn is
downgraded to ``absent`` by ``_resolve_effective_presence``, and no chat pause can resume —
triggered by nothing more than the founder approving something before chatting. So this module
builds its OWN ``ToolExecutor`` (the thing ``orchestrator._execute_tool`` is a facade over) and
never touches the chat singleton.

THE RULE THIS MODULE ENCODES: a prepared action only leaves ``pending`` when we KNOW what
happened to it. Executed or permanently refused — both terminal, both legible. Anything else —
an infrastructure failure, a lock held by a concurrent write, an attempt still in flight — must
leave the row confirmable, because ``_get_approval`` refuses every status that is not
``pending`` or the intended terminal state. A row parked in ``approved`` or ``failed`` can
never be retried: the second confirm returns 200 from the idempotent early-return while
nothing runs, and nothing ever will.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from src.services.prepared_actions import execute_prepared_action

logger = logging.getLogger(__name__)

# The message the founder sees for anything retryable. Says the two things they need: it did
# NOT run, and the row is still there to confirm again.
_RETRY_DETAIL = (
    "Could not run this action right now — it is still waiting and can be confirmed again."
)

_dispatcher = None


def get_prepared_dispatcher(settings):
    """The tool dispatcher a replay executes through, built once per process.

    A ``ToolExecutor`` needs only an ``EventPublisher`` (for tool.started/completed events) and
    a live DB-session-factory provider — none of the orchestrator's other collaborators. So the
    replay path owns one directly instead of borrowing the chat orchestrator's, which keeps this
    path from constructing (and permanently miscaching) that singleton. External MCP dispatch
    goes through the module-level ``call_mcp_tool`` bridge, so nothing here is orchestrator state.
    """
    global _dispatcher
    if _dispatcher is None:
        from src.models.database import get_session_factory
        from src.orchestrator.event_publisher import EventPublisher
        from src.orchestrator.tool_executor import ToolExecutor

        def _db_factory_provider():
            return get_session_factory()

        _dispatcher = ToolExecutor(
            EventPublisher(settings, None, _db_factory_provider), _db_factory_provider
        )
    return _dispatcher


async def run_prepared_action(approval, *, user_id: str, db, settings):
    """Execute a confirmed PREPARED action and record the outcome on the Approval.

    The status moves off ``approved`` to whatever actually happened, so the prepared-work queue
    drops the row only when it should and the founder can see which way it went:

    - executed / already executed → ``executed``
    - permanently refused, or the tool itself returned an error → ``failed`` + ``prepared_error``
    - transient (write-lock contention, an attempt still in flight) → stays ``pending``, records
      ``prepared_error``, and raises 503 — the founder can confirm again, and the message says so
    - the replay RAISED (redis down, DB gone, anything infrastructural) → stays ``pending``,
      records ``prepared_error``, raises 503

    The two ``pending`` cases are the whole point. ``execute_prepared_action`` never raises for a
    policy refusal, but it raises freely for infrastructure — ``aioredis.from_url`` connects
    LAZILY, so a dead redis surfaces as a ``ConnectionError`` from inside the write lock, long
    after the client was constructed. Letting that escape leaves the row committed ``approved``,
    and every later confirm hits the idempotent early-return: HTTP 200, nothing runs, no reason
    recorded. Loud once, then silently reassuring forever.

    ``redis`` and ``ledger`` are passed explicitly because ``execute_prepared_action`` requires
    them: without the ledger a double-confirm double-fires an external write, and without redis
    a prepared confirm does not mutually exclude with a concurrent chat write to the same
    capability.

    ``user_id`` is the CONFIRMER, used for audit. The action itself replays as the PREPARER
    (``execute_prepared_action`` reads ``approval.user_id``) — a replay runs with the authority
    of the turn that produced it, not the authority of whoever clicked approve.
    """
    from src.models.database import get_session_factory
    from src.services.idempotency import IdempotencyLedger

    db_factory = get_session_factory()
    execute_tool = get_prepared_dispatcher(settings).execute_tool
    redis = None
    try:
        import redis.asyncio as aioredis

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        # Note this catches only CONSTRUCTION; the connection is lazy, so a dead redis lands in
        # the handler below instead. Kept because a malformed URL does fail here.
        logger.warning(
            "prepared action %s running WITHOUT the cross-path write lock — redis unavailable",
            approval.approval_id,
            exc_info=True,
        )

    try:
        outcome = await execute_prepared_action(
            approval,
            execute_tool=execute_tool,
            db_factory=db_factory,
            redis=redis,
            ledger=IdempotencyLedger(db_factory),
        )
    except Exception as exc:
        # An INFRASTRUCTURE failure, not a policy refusal. The action has not run, so the row
        # must stay actionable — leaving it `approved` makes every later confirm hit the
        # idempotent early-return and silently succeed while nothing ever executes.
        logger.exception("prepared action %s failed to execute", approval.approval_id)
        approval.status = "pending"
        approval.artifact_refs = {
            **(approval.artifact_refs or {}),
            "prepared_error": f"could not run: {exc.__class__.__name__} — not yet executed",
        }
        await db.commit()
        raise HTTPException(status_code=503, detail=_RETRY_DETAIL) from exc
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                logger.debug("redis close failed", exc_info=True)

    refs = {**(approval.artifact_refs or {})}
    if outcome.error:
        refs["prepared_error"] = outcome.error

    if outcome.outcome == "transient":
        # Retryable BY CONSTRUCTION: a write lock held for seconds, or an attempt still in
        # flight. Marking it `failed` would tell the founder to try again while `_get_approval`
        # refuses to let them — the row must stay `pending`.
        approval.artifact_refs = refs
        approval.status = "pending"
        await db.commit()
        logger.info("[prepared] %s not run (transient): %s", approval.approval_id, outcome.error)
        raise HTTPException(status_code=503, detail=_RETRY_DETAIL)

    approval.status = "executed" if outcome.executed else "failed"
    approval.artifact_refs = refs
    logger.info(
        "[prepared] %s confirmed by %s -> %s (%s)",
        approval.approval_id,
        user_id,
        approval.status,
        outcome.outcome,
    )
    return outcome


async def publish_prepared_decision(
    approval, *, user_id: str, workspace_id: str, event: str, settings
) -> None:
    """Announce a prepared-action decision on the workspace agent stream.

    Same event names and payload keys as the normal approve/reject publishes, with
    ``run_id: None`` — a prepared action has no run, and inventing one would be a lie. The
    prepared-work queue is a LIVE surface: without this it silently keeps showing work that
    has already run until someone refreshes by hand.

    Best-effort, like the paths it mirrors. By the time this fires the external write has
    already happened, so a dead event bus must never turn a success into an error — losing a
    notification is not losing the action.
    """
    redis = None
    try:
        import redis.asyncio as aioredis

        from src.services.event_bus import EventBus

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        bus = EventBus(redis)
        stream = bus.agent_stream(workspace_id)
        await bus.publish(
            stream,
            event,
            {"approval_id": approval.approval_id, "run_id": None},
            user_id,
            workspace_id=workspace_id,
        )
    except Exception:
        logger.debug("Failed to publish %s event", event, exc_info=True)
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                logger.debug("redis close failed", exc_info=True)
