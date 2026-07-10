"""Step 10B Phase 1: the 5 net-net cutover-control-plane metrics must be DEFINED
(registered + emit + readable) before the flip, even though only the double-fire
emitter is wired live (at the idempotency wrapper). The other 4 are dormant until
their respective activation points (see metrics_service.py docstrings)."""

from unittest.mock import AsyncMock

from src.services.idempotency.ledger import ReserveOutcome
from src.services.idempotency.wrapper import IdempotencyContext, make_idempotent_execute_tool_fn
from src.services.metrics_service import DOUBLE_FIRE, MetricsService


async def test_rollback_metrics_registered_and_emit():
    MetricsService.record_double_fire(surface="autonomous", kind="already_done")
    MetricsService.record_verification_false_negative(surface="chat")
    MetricsService.record_double_prompt(surface="chat")
    MetricsService.record_ungated_perception_write(surface="perception")
    MetricsService.record_shadow_divergence(kind="write_intent_set")
    body = MetricsService.generate_metrics().decode()
    for name in (
        "jarvis_double_fire_total",
        "jarvis_verification_false_negative_total",
        "jarvis_double_prompt_total",
        "jarvis_ungated_perception_write_total",
        "jarvis_shadow_divergence_total",
    ):
        assert name in body


async def test_read_counter_total_reads_a_delta_not_an_absolute():
    # prometheus_client Counters are process-global singletons: other tests in this
    # process may have already incremented DOUBLE_FIRE with these labels, so this
    # test reads a BEFORE/AFTER delta with a distinctive label combo instead of
    # asserting an absolute value.
    label_kwargs = {"surface": "autonomous", "kind": "phase1_delta_probe"}
    before = MetricsService.read_counter_total(DOUBLE_FIRE, **label_kwargs)
    n = 3
    for _ in range(n):
        MetricsService.record_double_fire(**label_kwargs)
    after = MetricsService.read_counter_total(DOUBLE_FIRE, **label_kwargs)
    assert after - before == n


def _ctx(ledger):
    from unittest.mock import MagicMock

    return IdempotencyContext(
        ledger=ledger, run_id="r", step_id="st", workspace_id="ws", db_factory=MagicMock()
    )


def _resolver(is_write, capability="email.send"):
    async def _resolve(tool_name, db_factory, workspace_id):
        return (capability, is_write)

    return _resolve


async def test_idempotency_wrapper_emits_double_fire_on_already_done_and_in_flight():
    """Mirrors tests/idempotency/test_idempotent_wrapper.py's already-done and
    in-flight-conflict setups, but asserts the DOUBLE_FIRE counter moved instead
    of (only) the log line — this is the one live emitter Phase 1 wires."""
    already_done_labels = {"surface": "autonomous", "kind": "already_done"}
    in_flight_labels = {"surface": "autonomous", "kind": "in_flight_conflict"}
    before_already_done = MetricsService.read_counter_total(DOUBLE_FIRE, **already_done_labels)
    before_in_flight = MetricsService.read_counter_total(DOUBLE_FIRE, **in_flight_labels)

    inner = AsyncMock(return_value={"status": "sent", "id": "msg_1"})
    ledger = AsyncMock()
    ledger.reserve = AsyncMock(
        return_value=ReserveOutcome(
            already_done=True,
            in_flight_conflict=False,
            result={"status": "sent", "id": "msg_1"},
            identity_key="k",
            ledger_id="idem_1",
        )
    )
    fn = make_idempotent_execute_tool_fn(inner, _ctx(ledger), resolve_capability=_resolver(True))
    out = await fn(
        "send_gmail_message",
        {"to": "b@x.com", "subject": "s", "body": "RECOMPOSED on resume"},
        user_id="u",
        workspace_id="ws",
    )
    inner.assert_not_awaited()
    assert out == {"status": "sent", "id": "msg_1"}
    after_already_done = MetricsService.read_counter_total(DOUBLE_FIRE, **already_done_labels)
    assert after_already_done - before_already_done == 1

    ledger2 = AsyncMock()
    ledger2.reserve = AsyncMock(
        return_value=ReserveOutcome(
            already_done=False,
            in_flight_conflict=True,
            result=None,
            identity_key="k",
            ledger_id="idem_1",
        )
    )
    fn2 = make_idempotent_execute_tool_fn(inner, _ctx(ledger2), resolve_capability=_resolver(True))
    out2 = await fn2(
        "send_gmail_message",
        {"to": "b@x.com", "subject": "s", "body": "x"},
        user_id="u",
        workspace_id="ws",
    )
    inner.assert_not_awaited()
    assert out2.get("idempotent_uncertain") is True
    after_in_flight = MetricsService.read_counter_total(DOUBLE_FIRE, **in_flight_labels)
    assert after_in_flight - before_in_flight == 1
