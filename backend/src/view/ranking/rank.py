"""Order the feed. Pure, total, and with no way to read what anything says.

`rank()` takes a sequence of `RankFeatures` and returns handles. It performs
no I/O and makes no model call — every DB read lives in `build.py`. That
separation is not tidiness: it is what makes the ordering testable against
cases instead of eyeballed, and it is what keeps
a clock out of the ordering so the same feed twice produces the same feed.

Why an ORDER rather than a score
--------------------------------
An importance score is unfalsifiable: `0.9` is exactly as valid-looking as
`0.2`, because importance is precisely what was delegated. An ordering is
checkable — see `validate_permutation`, which is why a later list-ranking
model may **reorder** the baseline but can never author it.

The weights
-----------
The weighting is left open on purpose: soul says *"surface what matters, not
compete for presence"*, which is a constraint, not a formula.
The shape below is chosen so that every term answers **"does this need the
founder?"** rather than "how loud is it?":

* A **dated commitment** is the clearest form of needing someone, and it
  decays: something due today is not something due in a fortnight. Heaviest.
* A **goal match** is the founder's own declared priority, reached by an
  unforgeable graph join. Second heaviest.
* An **unresolved affordance** is literally a decision waiting on them.
* A **known counterparty** — evidence muldro accumulated, not a claim the
  message made.
* **Recency** decays over three days; it orders a feed but never rescues a
  stale item over a live commitment.
* **Thread depth** is the weakest positive: an active conversation is worth
  something, but volume is not importance and a loud thread must not win.

Two terms only ever SUBTRACT, and the sign is the guarantee:

* `bulk_mail` — rules-origin only, from headers an attacker can use only
  against themselves.
* `engagement_penalty` — **demotion only. Promotion by engagement is
  self-sealing**: rank drives visibility, visibility drives engagement, so a
  low-ranked type would never be seen, never engaged, and would sink
  permanently. Demotion has no such loop, because a thing had to be seen to be
  dismissed.

A reserved field contributes nothing when absent. `deadline_in_days=None` and
`you_replied=None` mean *no signal*, and score exactly as a far-off date and an
unanswered question do — never as a low value.

Ties
----
Never leave an order to dict or set iteration. The sort key is
`(-score, age_hours, key)`: score first, then the newest, then the handle
itself, which is total — so two items alike in every feature still come back
in the same order every time.
"""

from __future__ import annotations

from collections.abc import Sequence

from src.view.ranking.features import RankFeatures

__all__ = ["rank", "validate_permutation"]

# Weights. Positive terms are each normalised to [0, 1] before scaling, so the
# numbers below ARE the relative importance and can be read straight off.
W_DEADLINE = 3.0
W_GOAL = 2.0
W_RECENCY = 2.0
W_AFFORDANCE = 1.5
W_COUNTERPARTY = 1.5
W_YOU_REPLIED = 1.5
W_THREAD = 0.75

# Demotions. Subtracted, never added — see the module docstring.
W_BULK_MAIL = 2.5
W_ENGAGEMENT = 2.0

# A deadline a fortnight out is not pressing; inside that it ramps linearly to
# "due today". Past it the term is zero, which is also what `None` scores.
DEADLINE_URGENCY_DAYS = 14
# Three days: long enough that a Friday evening item is still live on Monday,
# short enough that recency cannot carry a stale card past a live commitment.
RECENCY_WINDOW_HOURS = 72.0
# Past these, more is not more. Saturation keeps one prolific counterparty or
# one runaway thread from dominating the whole feed.
INTERACTION_SATURATION = 20
THREAD_SATURATION = 10
RECENT_CONTACT_DAYS = 7


def _ramp(value: float, window: float) -> float:
    """1.0 at zero, decaying linearly to 0.0 at `window`. Never negative."""
    if window <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - value / window))


def _deadline_term(features: RankFeatures) -> float:
    days = features.deadline_in_days
    if days is None:
        return 0.0
    return _ramp(float(days), float(DEADLINE_URGENCY_DAYS))


def _counterparty_term(features: RankFeatures) -> float:
    """Evidence muldro accumulated about this person, normalised to [0, 1]."""
    party = features.counterparty
    if not party.known:
        return 0.0
    score = 0.35
    if party.relationship:
        score += 0.20
    score += 0.30 * min(party.interaction_count, INTERACTION_SATURATION) / INTERACTION_SATURATION
    if party.days_since_last_seen is not None and party.days_since_last_seen <= RECENT_CONTACT_DAYS:
        score += 0.15
    return min(1.0, score)


def _thread_term(features: RankFeatures) -> float:
    depth = min(features.thread.message_count, THREAD_SATURATION) / THREAD_SATURATION
    return max(0.0, (depth - 1.0 / THREAD_SATURATION) / (1.0 - 1.0 / THREAD_SATURATION))


def _score(features: RankFeatures) -> float:
    """Attention-worthiness. Higher is sooner. Deterministic in its input alone."""
    positive = (
        W_DEADLINE * _deadline_term(features)
        + W_GOAL * (1.0 if features.matched_goal_ids else 0.0)
        + W_RECENCY * _ramp(features.age_hours, RECENCY_WINDOW_HOURS)
        + W_AFFORDANCE * (1.0 if features.has_unresolved_affordance else 0.0)
        + W_COUNTERPARTY * _counterparty_term(features)
        # None is NOT KNOWABLE and scores as nothing said, exactly as False does.
        + W_YOU_REPLIED * (1.0 if features.thread.you_replied else 0.0)
        + W_THREAD * _thread_term(features)
    )
    demotion = W_BULK_MAIL * (1.0 if features.bulk_mail else 0.0) + (
        W_ENGAGEMENT * features.engagement_penalty
    )
    return positive - demotion


def rank(features: Sequence[RankFeatures]) -> list[str]:
    """Return the input's handles, most-attention-worthy first.

    Pure and total: same features in, same order out. No I/O, no model call,
    no clock, and no reliance on dict or set iteration order.

    The result is a permutation of the input keys with two documented
    exclusions, both of which are decisions and not losses:

    * a key repeated in the input appears **once** (the first occurrence
      wins), because a handle names one thing;
    * a `suppressed` item is **dropped before ranking** — the founder
      dismissed that `(source, category)` five times running, and ordering it
      lower would still spend a row on it.
    """
    seen: set[str] = set()
    live: list[RankFeatures] = []
    for item in features:
        if item.key in seen:
            continue
        seen.add(item.key)
        if item.suppressed:
            continue
        live.append(item)

    live.sort(key=lambda f: (-_score(f), f.age_hours, f.key))
    return [f.key for f in live]


def validate_permutation(
    proposed: Sequence[object],
    expected_keys: Sequence[str],
    *,
    max_displacement: int,
) -> list[str] | None:
    """Accept `proposed` only if it REORDERS `expected_keys` within the bound.

    This is the checkable contract that lets a list-ranking model touch the
    feed at all. It is not built on detecting a successful injection — a
    verifier prompt faces the same unfalsifiable question as the scorer.
    It is built on the output alphabet being fixed to the input:

    1. **It must be a permutation.** Every expected key exactly once, nothing
       invented, nothing dropped, nothing duplicated. A model that has been
       successfully instructed still cannot add an item, remove one, or
       smuggle a payload through this return value.
    2. **Displacement is bounded.** No item may move more than
       `max_displacement` from its deterministic position, so a maximally
       fooled model moves an item a few places rather than to the top.
    3. **Failure is total.** Anything else returns `None` and the caller keeps
       the deterministic order. There is no partial trust and no repair.

    A malformed *response* is data and returns `None`. A malformed *call* — a
    negative bound, a non-unique baseline — is a bug in muldro's own code and
    raises, because silently accepting it would disable the check.
    """
    if max_displacement < 0:
        raise ValueError("max_displacement must not be negative")

    expected = list(expected_keys)
    positions = {key: index for index, key in enumerate(expected)}
    if len(positions) != len(expected):
        raise ValueError("expected_keys must be unique")

    candidate = list(proposed)
    if len(candidate) != len(expected):
        return None
    if any(not isinstance(key, str) for key in candidate):
        return None
    if len(set(candidate)) != len(candidate):
        return None

    for index, key in enumerate(candidate):
        baseline = positions.get(key)  # type: ignore[arg-type]
        if baseline is None:
            return None
        if abs(index - baseline) > max_displacement:
            return None

    return [str(key) for key in candidate]
