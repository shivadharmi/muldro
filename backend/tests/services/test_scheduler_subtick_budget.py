"""The sub-tick budget must be one number, not two.

90s was too tight for perception, and the failure was silent in the worst way:
the cancellation landed after the poll had ingested, triaged and published its
cards but before the Planner gate, so cards kept appearing while no plan and no
task run was ever created.

The budget is read through `getattr(settings, ..., <fallback>)`. That fallback
is a SECOND definition of the same default, and a drift between the two would
reinstate the old ceiling for any settings object missing the field — which is
precisely the shape of bug that is invisible until the loop stops planning.
"""

import inspect

from src.config.settings import Settings
from src.services.scheduler import _base


def test_the_declared_default_is_five_minutes():
    assert Settings.model_fields["scheduler_subtick_timeout_s"].default == 300.0


def test_the_getattr_fallback_matches_the_declared_default():
    source = inspect.getsource(_base)
    declared = Settings.model_fields["scheduler_subtick_timeout_s"].default
    assert f'"scheduler_subtick_timeout_s", {declared})' in source, (
        "the fallback in _base.py disagrees with Settings' default"
    )


def test_a_perception_tick_has_room_for_its_provider_round_trips():
    """Not an arbitrary number: a tick is a poll plus triage plus relevance plus
    up to MAX_BODIES_PER_POLL body calls, each a provider round trip."""
    from src.view.body_fill import MAX_BODIES_PER_POLL

    measured_call_seconds = 3.5  # observed p90 for the fast tier on this stack
    worst_case = (MAX_BODIES_PER_POLL + 2) * measured_call_seconds
    assert Settings.model_fields["scheduler_subtick_timeout_s"].default > worst_case
