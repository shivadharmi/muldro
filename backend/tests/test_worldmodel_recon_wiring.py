"""reconcile_verdict is called from BOTH Step-3 hookpoints (auto-exec finalize +
deferred tick), and confidence is NEVER read by the gate (§4.3). Structural checks
via source inspection."""

import inspect


def test_dag_runner_calls_reconcile_on_the_autoexec_path():
    import src.services.dag_runner as dr

    assert "reconcile_verdict" in inspect.getsource(dr)


def test_deferred_tick_calls_reconcile():
    import src.services.scheduler.deferred_verification_tick as t

    assert "reconcile_verdict" in inspect.getsource(t)


def test_trust_engine_never_imports_entity_confidence():
    import src.services.trust_engine as te

    src = inspect.getsource(te)
    assert "entity_facts" not in src
    assert "reconcile_verdict" not in src
