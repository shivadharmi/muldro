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
