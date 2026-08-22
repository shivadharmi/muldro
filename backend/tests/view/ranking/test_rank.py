"""`rank()` is ordering cases, not eyeballing.

Two properties carry the design and are asserted rather than described:
`rank()` is a pure, total permutation of its input handles, and engagement can
only ever push an item DOWN. `validate_permutation` is the checkable half —
the reason a later list-ranking model may reorder but never author.
"""

import ast
import pathlib
import random

import pytest

from src.view.ranking import rank as rank_module
from src.view.ranking.features import Counterparty, RankFeatures, ThreadState
from src.view.ranking.rank import rank, validate_permutation


def feat(key: str, **overrides) -> RankFeatures:
    base = dict(
        key=key,
        kind="proposal",
        source="gmail",
        counterparty=Counterparty(known=False),
        thread=ThreadState(),
    )
    counterparty = overrides.pop("counterparty_kwargs", None)
    if counterparty is not None:
        base["counterparty"] = Counterparty(**counterparty)
    thread = overrides.pop("thread_kwargs", None)
    if thread is not None:
        base["thread"] = ThreadState(**thread)
    base.update(overrides)
    return RankFeatures(**base)


# --- purity -----------------------------------------------------------------

_FORBIDDEN_IMPORT_ROOTS = {
    "sqlalchemy",
    "datetime",
    "time",
    "random",
    "os",
    "httpx",
    "anthropic",
}
_FORBIDDEN_IMPORT_PREFIXES = ("src.models", "src.services", "src.llm", "src.deep_runtime")


def test_rank_module_imports_no_clock_no_db_and_no_model():
    """Purity asserted at the import graph, not by reading the functions.

    A clock or a session reaching this module is what would make `rank()`
    untestable against ordering cases — which is the whole reason the DB work
    lives in `build.py`.
    """
    source = pathlib.Path(rank_module.__file__).read_text()
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            assert name.split(".")[0] not in _FORBIDDEN_IMPORT_ROOTS, f"impure import: {name}"
            assert not name.startswith(_FORBIDDEN_IMPORT_PREFIXES), f"impure import: {name}"


def test_rank_is_deterministic_regardless_of_input_order():
    items = [feat(f"k{i}", age_hours=float(i % 5), deadline_in_days=i % 7) for i in range(20)]
    expected = rank(items)
    shuffled = list(items)
    random.Random(7).shuffle(shuffled)
    assert rank(shuffled) == expected


def test_rank_never_depends_on_set_or_dict_iteration_order():
    """Two items alike in every scoring input still get a stable order."""
    a, b = feat("bbb"), feat("aaa")
    assert rank([a, b]) == ["aaa", "bbb"] == rank([b, a])


# --- totality ---------------------------------------------------------------


def _random_features(rng: random.Random, n: int) -> list[RankFeatures]:
    return [
        feat(
            f"key-{i}",
            source=rng.choice(["gmail", "slack", "calendar", "github", "notion"]),
            kind=rng.choice(["proposal", "finding", "run", "record", "briefing"]),
            age_hours=rng.uniform(0, 400),
            engagement_penalty=rng.choice([0.0, 0.2, 1.0]),
            bulk_mail=rng.random() < 0.3,
            has_unresolved_affordance=rng.random() < 0.3,
            deadline_in_days=rng.choice([None, 0, 1, 3, 30, 300]),
            matched_goal_ids=tuple(f"mem_{j}" for j in range(rng.randint(0, 2))),
            counterparty_kwargs={
                "known": rng.random() < 0.5,
                "interaction_count": rng.randint(0, 50),
                "days_since_last_seen": rng.choice([None, 0, 3, 90]),
                "relationship": rng.choice([None, "works_on", "reports_to"]),
            },
            thread_kwargs={
                "message_count": rng.randint(1, 30),
                "hours_since_last": rng.uniform(0, 400),
                "you_replied": rng.choice([None, True, False]),
            },
        )
        for i in range(n)
    ]


@pytest.mark.parametrize("seed", range(12))
def test_rank_returns_every_input_key_exactly_once(seed):
    rng = random.Random(seed)
    items = _random_features(rng, rng.randint(1, 25))
    ordered = rank(items)
    assert sorted(ordered) == sorted(f.key for f in items)
    assert len(set(ordered)) == len(ordered)


def test_rank_of_nothing_is_nothing():
    assert rank([]) == []


def test_a_repeated_key_appears_once():
    assert rank([feat("k"), feat("k", age_hours=99.0)]) == ["k"]


def test_suppressed_items_are_dropped_before_ranking():
    ordered = rank([feat("live"), feat("dead", suppressed=True)])
    assert ordered == ["live"]


# --- engagement demotes only ------------------------------------------------


@pytest.mark.parametrize("seed", range(12))
def test_engagement_penalty_can_never_raise_an_item(seed):
    rng = random.Random(seed)
    items = _random_features(rng, 15)
    items = [f.model_copy(update={"engagement_penalty": 0.0}) for f in items]
    baseline = rank(items)

    for index, target in enumerate(items):
        penalised = list(items)
        penalised[index] = target.model_copy(update={"engagement_penalty": 1.0})
        after = rank(penalised)
        assert after.index(target.key) >= baseline.index(target.key)


def test_full_penalty_sinks_an_otherwise_identical_item():
    ordered = rank([feat("penalised", engagement_penalty=1.0), feat("clean")])
    assert ordered == ["clean", "penalised"]


# --- a reserved field contributes nothing when absent ------------------------


def test_no_deadline_is_no_signal_not_a_low_one():
    """`None` must score as *nothing said*, not as a far-off date."""
    beyond = 3650  # far past any urgency window
    first = rank([feat("aaa", deadline_in_days=None), feat("bbb", deadline_in_days=beyond)])
    second = rank([feat("aaa", deadline_in_days=beyond), feat("bbb", deadline_in_days=None)])
    assert first == second == ["aaa", "bbb"]


def test_unknown_reply_is_no_signal_not_a_no():
    unknown = rank([feat("aaa", thread_kwargs={"you_replied": None}), feat("bbb")])
    explicit_no = rank([feat("aaa", thread_kwargs={"you_replied": False}), feat("bbb")])
    assert unknown == explicit_no == ["aaa", "bbb"]


# --- ordering cases ---------------------------------------------------------


def test_a_sooner_deadline_outranks_a_later_one():
    assert rank([feat("later", deadline_in_days=10), feat("sooner", deadline_in_days=0)]) == [
        "sooner",
        "later",
    ]


def test_a_goal_match_outranks_no_match():
    assert rank([feat("plain"), feat("goal", matched_goal_ids=("mem_1",))]) == ["goal", "plain"]


def test_an_unresolved_affordance_outranks_nothing_to_do():
    assert rank([feat("nothing"), feat("decide", has_unresolved_affordance=True)]) == [
        "decide",
        "nothing",
    ]


def test_a_known_counterparty_outranks_a_stranger():
    known = feat("known", counterparty_kwargs={"known": True, "interaction_count": 40})
    assert rank([feat("stranger"), known]) == ["known", "stranger"]


def test_bulk_mail_sinks_below_ordinary_mail():
    assert rank([feat("promo", bulk_mail=True), feat("human")]) == ["human", "promo"]


def test_recency_breaks_a_tie():
    assert rank([feat("old", age_hours=100.0), feat("new", age_hours=1.0)]) == ["new", "old"]


# --- validate_permutation ---------------------------------------------------

EXPECTED = ["a", "b", "c", "d"]


def test_the_identity_order_is_accepted():
    assert validate_permutation(EXPECTED, EXPECTED, max_displacement=1) == EXPECTED


def test_a_reorder_within_the_bound_is_accepted():
    assert validate_permutation(["b", "a", "d", "c"], EXPECTED, max_displacement=1) == [
        "b",
        "a",
        "d",
        "c",
    ]


def test_a_dropped_key_is_rejected():
    assert validate_permutation(["a", "b", "c"], EXPECTED, max_displacement=4) is None


def test_an_invented_key_is_rejected():
    assert validate_permutation(["a", "b", "c", "zz"], EXPECTED, max_displacement=4) is None


def test_a_duplicated_key_is_rejected():
    assert validate_permutation(["a", "a", "b", "c"], EXPECTED, max_displacement=4) is None


def test_an_over_displaced_item_is_rejected():
    assert validate_permutation(["d", "a", "b", "c"], EXPECTED, max_displacement=2) is None
    assert validate_permutation(["d", "a", "b", "c"], EXPECTED, max_displacement=3) is not None


def test_a_non_string_entry_is_rejected():
    assert validate_permutation(["a", "b", "c", 4], EXPECTED, max_displacement=4) is None


def test_a_longer_proposal_is_rejected():
    assert validate_permutation([*EXPECTED, "e"], EXPECTED, max_displacement=4) is None


def test_an_empty_expected_order_accepts_only_an_empty_proposal():
    assert validate_permutation([], [], max_displacement=1) == []
    assert validate_permutation(["a"], [], max_displacement=1) is None


def test_a_negative_bound_is_a_caller_bug_and_raises():
    """A malformed model response returns None; a malformed CALL is not data."""
    with pytest.raises(ValueError):
        validate_permutation(EXPECTED, EXPECTED, max_displacement=-1)


def test_duplicate_expected_keys_are_a_caller_bug_and_raise():
    with pytest.raises(ValueError):
        validate_permutation(["a", "a"], ["a", "a"], max_displacement=1)


def test_validate_permutation_composes_with_rank():
    items = _random_features(random.Random(3), 10)
    baseline = rank(items)
    assert validate_permutation(baseline, baseline, max_displacement=0) == baseline
