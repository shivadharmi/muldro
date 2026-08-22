"""The only object the ranker may see.

`ranker-interface.md` §0: **the ranker reads only values muldro computed about
its own history — never a value an outside party wrote, and never a value a
model inferred *from* what an outside party wrote.**

The consequence is visible in the type: there is no headline here, no summary,
no snippet and no body. `key` is an opaque handle. The ranker orders handles;
it never learns what they say, so there is nothing on this record to prompt it
with. That is invariant 1 (§4), and `tests/view/ranking/test_features.py`
enforces it by walking these annotations rather than by listing the fields it
expects — a listing test passes the day someone adds `headline: str`.

`Opaque` is how a string field states its own provenance. A `str` on this
record is either a handle muldro minted (`frame.key`), a connector name code
chose (`source`), or a closed vocabulary muldro's own graph writes
(`EntityRelationship.relation_type`). None of them is prose, and the marker is
what lets a test tell them apart from prose it has never seen.

Fields explicitly NOT here, and why they look safe
--------------------------------------------------
`NormalizedEvent.importance_score`, `urgency_score` and every key under
`importance_signals` except the rules-origin triage flag are an LLM's
assertions over the raw subject and body (`ranker-interface.md` §1).
`Entity.importance_score` is also a stored score whose writer has not been
audited. None of them may enter, however typed they look.

A reserved field is None, never a guess
---------------------------------------
Three fields are typed to accept a prerequisite that has not landed, and all
three carry *no signal* rather than a defaulted value that would read as a
fact (invariant 6):

* `ThreadState.you_replied` — there is no sent-mail ingestion and no
  `email_sent` event type. `False` would read as *"you ignored them"*.
* `Counterparty.prior_threads` — "distinct threads seen from this
  counterparty" needs an actor-indexed query over `normalized_events`, and
  `actor_entities` is unindexed JSONB. `0` on a `known=True` counterparty
  would assert *"you have never corresponded"*, which is the same failure.
  This widens §2's draft type (`int = 0`) for exactly that reason.
* `RankFeatures.matched_goal_ids` — empty when no goal memory references the
  counterparty. This one IS populated (a graph join, `build.py`); empty means
  no match, not no capability.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from src.view.contracts import FrameKind


class CodeAuthored:
    """Marker: this string is a handle or a closed vocabulary, never prose."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "CODE_AUTHORED"


CODE_AUTHORED = CodeAuthored()

# A string whose every possible value was chosen by muldro's own code or
# written by muldro's own graph. Declaring it is a claim the structural test in
# `test_features.py` checks; a bare `str` on any of these models fails it.
Opaque = Annotated[str, CODE_AUTHORED]


class Counterparty(BaseModel):
    """Resolved against muldro's own graph. Never parsed out of the message.

    Resolution is an `EntityAlias` **lookup, not a judgement**: the table
    carries a uniqueness constraint that a strong identifier (email/handle)
    maps to exactly one entity per workspace, so an attacker cannot make
    themselves resolve to somebody the founder trusts. The alternative — a
    model asserting `from_priority_person` off the `From` line — is the
    channel this record exists to close.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    known: bool
    relationship: Opaque | None = None
    prior_threads: int | None = Field(default=None, ge=0)
    interaction_count: int = Field(default=0, ge=0)
    days_since_last_seen: int | None = Field(default=None, ge=0)


class ThreadState(BaseModel):
    """Counts of rows muldro wrote, and one fact it cannot know yet."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message_count: int = Field(default=1, ge=1)
    # None means NOT KNOWABLE (no sent-mail ingestion), never "no".
    you_replied: bool | None = None
    hours_since_last: float = Field(default=0.0, ge=0.0)


class RankFeatures(BaseModel):
    """Everything the ranker may see. No field here is external or model-asserted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: Opaque = Field(min_length=1)
    kind: FrameKind
    source: Opaque = Field(min_length=1)
    counterparty: Counterparty
    thread: ThreadState

    has_unresolved_affordance: bool = False
    # Rules-origin triage only (`TriageResult.origin == "rules"`). The headers
    # it reads are attacker-writable but only in the DEMOTING direction:
    # adding `List-Unsubscribe` demotes the sender, and omitting it declines a
    # demotion they could not have claimed anyway. An LLM-chosen category
    # never sets this.
    bulk_mail: bool = False
    # 0..1, DEMOTION ONLY. See `rank.py` for why promotion is self-sealing.
    engagement_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    suppressed: bool = False
    age_hours: float = Field(default=0.0, ge=0.0)

    # A typed date, parsed deterministically from verbatim text or read off a
    # structured provider field — never a model's assertion about prose. None
    # means the source offers neither (`build.py::DEADLINE_SOURCE`).
    deadline_in_days: int | None = Field(default=None, ge=0)
    # Matched by joining `Memory.entity_ids` against the counterparty's entity
    # — a graph join, which is unforgeable. NEVER by embedding the subject and
    # vector-searching goals: a crafted subject resembling the founder's goals
    # would raise its own rank.
    matched_goal_ids: tuple[Opaque, ...] = ()
