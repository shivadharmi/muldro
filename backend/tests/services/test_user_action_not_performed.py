"""A step the FOUNDER must perform is not muldro's to run.

`plan_store` persists a `user` actor step as PlanTask task_type="user_action",
status="awaiting_input" — correctly. That distinction reached the execution
layer only inside `input_data`, and nothing branched on it, so the DAG ran the
step through an agent and marked it COMPLETED.

Observed on a real autonomous run: "User reviews the triage summary and
confirms which items are important" — completed, with nobody having reviewed
anything — followed by "Create planning goals for the APPROVED important
items". The run acted on a confirmation that never happened. That is worse than
a missing prompt: it manufactures the appearance of one.
"""

from types import SimpleNamespace

import pytest

from src.services.user_action_steps import USER_ACTION_TASK_TYPE, is_user_action_step


def _step(*, step_type=None, payload=None):
    return SimpleNamespace(step_type=step_type, input_data=payload)


class TestRecognisingAUserAction:
    def test_the_column_says_so(self):
        assert is_user_action_step(_step(step_type=USER_ACTION_TASK_TYPE)) is True

    def test_a_run_created_before_the_column_was_populated_still_reads(self):
        """Older runs carry the fact only in input_data. A step nobody can
        classify must not be executed on the strength of where a field lived."""
        assert is_user_action_step(_step(payload={"task_type": USER_ACTION_TASK_TYPE})) is True

    @pytest.mark.parametrize(
        "step",
        [
            _step(step_type="email.send", payload={"task_type": "email.send"}),
            _step(payload={"capability": "email.send"}),
            _step(),
            _step(payload={}),
        ],
    )
    def test_an_agent_step_is_not_one(self, step):
        assert is_user_action_step(step) is False

    def test_a_malformed_payload_costs_nothing(self):
        assert is_user_action_step(SimpleNamespace(step_type=None, input_data=None)) is False


class TestTheStepTypeSurvivesIntoTheRun:
    def test_step_graph_store_populates_the_column(self):
        """The column existed and was never written, which is how the one fact
        that distinguishes a founder action from an agent action was lost."""
        import inspect

        from src.services import step_graph_store

        source = inspect.getsource(step_graph_store)
        assert "step_type=task.task_type" in source


class TestParkingARunBlockedOnTheFounder:
    """A skipped step is not in TERMINAL_SUCCESS, so its dependents can never
    become ready. The DAG loop then finds nothing ready, sees pending steps and
    no failures, and falls out through a bare `break` that performs NO terminal
    transition — the run sat in `running` for ever, indistinguishable from work
    still in progress.
    """

    @staticmethod
    def _run(status="running"):
        return SimpleNamespace(run_id="run_1", user_id="u_1", workspace_id="ws_1", status=status)

    @staticmethod
    def _skipped_user_action():
        return SimpleNamespace(
            step_id="s1", status="skipped", step_type=USER_ACTION_TASK_TYPE, input_data=None
        )

    @staticmethod
    def _step(status, step_type=None):
        return SimpleNamespace(
            step_id=f"s_{status}", status=status, step_type=step_type, input_data=None
        )

    async def _park(self, run, steps):
        from unittest.mock import AsyncMock

        from src.services.user_action_steps import park_if_blocked_on_founder

        emitter = AsyncMock()
        parked = await park_if_blocked_on_founder(run, steps, emitter)
        return parked, emitter

    @pytest.mark.asyncio
    async def test_it_parks_and_says_so(self):
        run = self._run()
        parked, emitter = await self._park(
            run, [self._skipped_user_action(), self._step("pending")]
        )
        assert parked is True
        assert run.status == "awaiting_input"
        emitter.emit_event.assert_awaited_once()
        assert emitter.emit_event.call_args.args[0] == "run.awaiting_input"

    @pytest.mark.asyncio
    async def test_an_approval_wait_is_not_relabelled_as_ours(self):
        """Those paths park the run themselves; re-parking would take credit
        for someone else's wait."""
        run = self._run()
        parked, _ = await self._park(
            run,
            [self._skipped_user_action(), self._step("waiting_approval"), self._step("pending")],
        )
        assert parked is False
        assert run.status == "running"

    @pytest.mark.asyncio
    async def test_a_reauth_wait_is_left_alone(self):
        run = self._run()
        parked, _ = await self._park(
            run, [self._skipped_user_action(), self._step("awaiting_reauth")]
        )
        assert parked is False

    @pytest.mark.asyncio
    async def test_a_run_with_nothing_left_to_do_is_not_parked(self):
        """Nothing pending means the loop's completion branch owns it."""
        run = self._run()
        parked, _ = await self._park(run, [self._skipped_user_action(), self._step("completed")])
        assert parked is False
        assert run.status == "running"

    @pytest.mark.asyncio
    async def test_a_run_blocked_for_some_other_reason_is_not_parked(self):
        """No user action was skipped, so this wait is not the founder's."""
        run = self._run()
        parked, _ = await self._park(run, [self._step("pending"), self._step("completed")])
        assert parked is False

    @pytest.mark.asyncio
    async def test_a_run_that_is_not_running_is_left_alone(self):
        run = self._run(status="failed")
        parked, _ = await self._park(run, [self._skipped_user_action(), self._step("pending")])
        assert parked is False
        assert run.status == "failed"

    @pytest.mark.asyncio
    async def test_the_parked_status_is_one_the_feed_already_surfaces(self):
        """`awaiting_input` must reach the founder, or parking just hides the
        run somewhere new."""
        from src.view.domain_units import _ACTIVE, _RUN_STATUS

        assert "awaiting_input" in _ACTIVE
        assert _RUN_STATUS["awaiting_input"] == "needs_you"

    def test_running_to_awaiting_input_is_a_legal_transition(self):
        from src.services.execution_state import RUN_TRANSITIONS

        assert "awaiting_input" in RUN_TRANSITIONS["running"]
