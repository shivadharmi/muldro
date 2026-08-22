"""The feature record carries no prose — asserted structurally, not by listing.

`ranker-interface.md` §4 invariant 1: *`RankFeatures` holds no prose.* A test
that names the fields it expects passes the day someone adds `headline: str`,
so the test below walks the model's own annotations instead and refuses any
bare `str` leaf anywhere in the tree. A code-authored handle or closed
vocabulary must say so in its type (`Opaque`), which is the whole point: the
declaration is where the claim is made and where the test can check it.
"""

from typing import Annotated, Literal, Union, get_args, get_origin, get_type_hints

import pytest
from pydantic import BaseModel, ValidationError

from src.view.ranking.features import (
    CODE_AUTHORED,
    Counterparty,
    Opaque,
    RankFeatures,
    ThreadState,
)

_SCALARS = (bool, int, float, type(None))


def _assert_no_prose(annotation, path: str) -> None:
    """Every leaf is a number, a bool, None, a closed vocabulary or an Opaque handle."""
    origin = get_origin(annotation)

    if origin is Annotated:
        args = get_args(annotation)
        assert CODE_AUTHORED in args[1:] or args[0] is not str, (
            f"{path}: a str field must be declared Opaque"
        )
        if CODE_AUTHORED in args[1:]:
            assert args[0] is str, f"{path}: Opaque marks strings only"
            return
        _assert_no_prose(args[0], path)
        return

    if origin is Literal:
        for value in get_args(annotation):
            assert isinstance(value, (str, int, bool)), f"{path}: odd Literal member {value!r}"
        return

    if origin in (Union, type(int | None)) or str(origin) == "types.UnionType":
        for arg in get_args(annotation):
            _assert_no_prose(arg, f"{path}|")
        return

    if origin in (tuple, list, set, frozenset, dict):
        for arg in get_args(annotation):
            if arg is Ellipsis:
                continue
            _assert_no_prose(arg, f"{path}[]")
        return

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        for name, hint in get_type_hints(annotation, include_extras=True).items():
            _assert_no_prose(hint, f"{path}.{name}")
        return

    assert annotation in _SCALARS, f"{path}: {annotation!r} may carry prose"


def test_no_field_on_the_feature_record_can_carry_prose():
    _assert_no_prose(RankFeatures, "RankFeatures")


def test_the_walker_would_catch_a_prose_field():
    """The guard above is only worth having if it fails on the thing it guards."""

    class Sneaky(BaseModel):
        headline: str

    with pytest.raises(AssertionError):
        _assert_no_prose(Sneaky, "Sneaky")


def test_opaque_is_a_string_marked_code_authored():
    assert get_origin(Opaque) is Annotated
    assert get_args(Opaque)[0] is str
    assert CODE_AUTHORED in get_args(Opaque)[1:]


# --- the record refuses what it says it refuses ------------------------------


def _features(**overrides) -> RankFeatures:
    base = dict(
        key="gmail:email_thread:t1",
        kind="proposal",
        source="gmail",
        counterparty=Counterparty(known=False),
        thread=ThreadState(),
    )
    base.update(overrides)
    return RankFeatures(**base)


def test_every_model_is_frozen_and_forbids_extras():
    for model in (Counterparty, ThreadState, RankFeatures):
        assert model.model_config["frozen"] is True
        assert model.model_config["extra"] == "forbid"

    with pytest.raises(ValidationError):
        RankFeatures(
            key="k",
            kind="proposal",
            source="gmail",
            counterparty=Counterparty(known=False),
            thread=ThreadState(),
            headline="URGENT: wire the money",
        )


def test_you_replied_defaults_to_none_not_false():
    """`None` is *not knowable*; `False` would read as "you ignored them"."""
    assert ThreadState().you_replied is None


def test_the_reserved_fields_default_to_no_signal():
    f = _features()
    assert f.deadline_in_days is None
    assert f.matched_goal_ids == ()
    assert f.counterparty.prior_threads is None


def test_engagement_penalty_is_bounded_to_a_demotion_scale():
    _features(engagement_penalty=1.0)
    with pytest.raises(ValidationError):
        _features(engagement_penalty=1.5)
    with pytest.raises(ValidationError):
        _features(engagement_penalty=-0.1)


def test_a_past_deadline_is_not_representable():
    with pytest.raises(ValidationError):
        _features(deadline_in_days=-1)


def test_age_and_hours_since_last_cannot_be_negative():
    with pytest.raises(ValidationError):
        _features(age_hours=-1.0)
    with pytest.raises(ValidationError):
        ThreadState(hours_since_last=-1.0)


def test_message_count_starts_at_one():
    assert ThreadState().message_count == 1
    with pytest.raises(ValidationError):
        ThreadState(message_count=0)
