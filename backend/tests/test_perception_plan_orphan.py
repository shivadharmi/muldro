"""A perception plan whose run cannot be created must not stay silently `created`.

`_queue_perception_plan` commits the Plan in one transaction and creates its
TaskRun in a SEPARATE one. The run creation was wrapped in a bare
`except: log warning`, so any failure there left a durable plan with no run, no
approval and no card — invisible work that cannot be opened or retried. The
founder's workspace holds exactly one such plan: six tasks, status `created`,
zero runs, and nothing recording why.

An hourly TTL reaper does eventually relabel it "failed", which is worse than it
sounds: by then the reason is gone and the relabel is indistinguishable from a
plan that ran and failed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.contracts import PlanOutput, PlanStep


class _FakeDB:
    def __init__(self, recorder: list):
        self._recorder = recorder
        self.committed = False

    async def execute(self, stmt):
        self._recorder.append(stmt)
        return MagicMock()

    async def commit(self):
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _runner(db_factory, plans):
    from src.orchestrator.perception_runner import PerceptionRunner

    return PerceptionRunner(
        settings=MagicMock(),
        client=MagicMock(),
        budget=MagicMock(),
        trace_manager=MagicMock(),
        db_factory_provider=lambda: db_factory,
        poller=MagicMock(),
        invoker=MagicMock(),
        events=MagicMock(),
        plans=plans,
        system_capability_handler=MagicMock(),
        spawn_background=MagicMock(),
    )


def _write_plan(plan_id: str | None = None) -> PlanOutput:
    # PlanOutput is frozen, so the persisted id is passed at construction.
    return PlanOutput(
        plan_id=plan_id,
        goal="Reduce inbox noise by setting filters",
        steps=[
            PlanStep(
                step_id="s1",
                description="Create a filter",
                capability="email.filter",
                actor="muldro",
                risk="medium",
            )
        ],
    )


async def test_a_plan_whose_run_cannot_be_created_is_marked_failed():
    """The defect: the plan stays `created` for ever, with nothing saying why."""
    statements: list = []
    db_factory = lambda: _FakeDB(statements)  # noqa: E731

    persisted = _write_plan("plan_orphan")
    plans = MagicMock()
    plans.persist_plan_record = AsyncMock(return_value=persisted)

    runner = _runner(db_factory, plans)

    with (
        patch(
            "src.orchestrator.perception_runner.extract_plan",
            MagicMock(return_value=_write_plan()),
        ),
        patch(
            "src.services.graph_executor.create_graph_executor",
            AsyncMock(side_effect=RuntimeError("executor unavailable")),
        ),
    ):
        await runner._queue_perception_plan("{}", "gmail", "usr_1", "ws_1", "trace_1")

    # An UPDATE was issued against the plan. Compiling it is the only way to
    # assert on the value without reaching into SQLAlchemy internals.
    compiled = [str(s.compile(compile_kwargs={"literal_binds": True})) for s in statements]
    updates = [c for c in compiled if c.lstrip().upper().startswith("UPDATE PLANS")]
    assert updates, f"no UPDATE issued against plans; got {compiled}"
    assert "status='failed'" in updates[0].replace(" = ", "=")
    # Guarded on the current status so a plan that DID start is never clobbered.
    assert "status='created'" in updates[0].replace(" = ", "=")


async def test_a_successful_run_leaves_the_plan_alone():
    """Only the failure path writes; a healthy queue must not touch the status."""
    statements: list = []
    db_factory = lambda: _FakeDB(statements)  # noqa: E731

    persisted = _write_plan("plan_ok")
    plans = MagicMock()
    plans.persist_plan_record = AsyncMock(return_value=persisted)

    executor = MagicMock()
    executor.create_run = AsyncMock(return_value=MagicMock(run_id="run_1"))

    runner = _runner(db_factory, plans)

    with (
        patch(
            "src.orchestrator.perception_runner.extract_plan",
            MagicMock(return_value=_write_plan()),
        ),
        patch(
            "src.services.graph_executor.create_graph_executor",
            AsyncMock(return_value=executor),
        ),
    ):
        await runner._queue_perception_plan("{}", "gmail", "usr_1", "ws_1", "trace_1")

    compiled = [str(s.compile(compile_kwargs={"literal_binds": True})) for s in statements]
    assert not [c for c in compiled if c.lstrip().upper().startswith("UPDATE PLANS")]
    executor.create_run.assert_awaited_once()
