"""muldro notices a pattern and ASKS. It does not decide.

The soul's initiative sequence is observe -> interpret -> surface selectively ->
propose before overcommitting -> act within established boundaries. A filter is
an authority, so it is proposed once, answered once, and only then exists.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as N

from src.services.filter_proposals import (
    MIN_EVENTS_TO_PROPOSE,
    SenderCandidate,
    find_sender_candidates,
    open_or_recent_proposal,
    proposal_title,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


@dataclass
class _Rows:
    events: list
    rules: list = None
    approvals: list = None


class _DB:
    """Answers each select by the model it targets, so one fake serves the
    three queries the proposer makes."""

    def __init__(self, rows: _Rows):
        self._rows = rows
        self.added = []

    async def execute(self, stmt):
        text = str(stmt)
        if "filter_rules" in text:
            payload = self._rows.rules or []
        elif "approvals" in text:
            payload = self._rows.approvals or []
        else:
            payload = self._rows.events
        return N(
            scalars=lambda: N(all=lambda: payload, first=lambda: payload[0] if payload else None)
        )

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _ev(address, *, actionable=False, hours_ago=1, title="Subject", source="gmail"):
    return N(
        source=source,
        actor_entities=[{"email": address}],
        importance_signals={"actionable": actionable},
        occurred_at=NOW - timedelta(hours=hours_ago),
        title=title,
    )


class TestEvidence:
    async def _find(self, events, rules=None):
        db = _DB(_Rows(events=events, rules=rules or []))
        return await find_sender_candidates(db, workspace_id="ws_1", user_id="u_1", now=NOW)

    async def test_a_repeatedly_unactionable_sender_is_a_candidate(self):
        out = await self._find([_ev("alerts@axisbank.com") for _ in range(MIN_EVENTS_TO_PROPOSE)])
        assert [c.address for c in out] == ["alerts@axisbank.com"]
        assert out[0].event_count == MIN_EVENTS_TO_PROPOSE

    async def test_too_few_messages_is_a_coincidence_not_a_pattern(self):
        """Proposing on a coincidence teaches the founder to dismiss proposals."""
        out = await self._find([_ev("a@b.com") for _ in range(MIN_EVENTS_TO_PROPOSE - 1)])
        assert out == []

    async def test_one_actionable_message_disqualifies_the_sender(self):
        """A counterparty who has once needed the founder can need them again,
        and the cost of a wrong rule is mail they never see."""
        events = [_ev("a@b.com") for _ in range(MIN_EVENTS_TO_PROPOSE)]
        events.append(_ev("a@b.com", actionable=True))
        assert await self._find(events) == []

    async def test_an_unscored_message_also_disqualifies(self):
        """`None` means triage never judged it, which is not "unactionable"."""
        events = [_ev("a@b.com") for _ in range(MIN_EVENTS_TO_PROPOSE)]
        events.append(N(**{**vars(_ev("a@b.com")), "importance_signals": {}}))
        assert await self._find(events) == []

    async def test_a_sender_already_ruled_on_is_never_re_proposed(self):
        """A revoked rule is an answer too: re-proposing asks the founder to
        repeat themselves."""
        rules = [N(source="gmail", match_value="a@b.com")]
        out = await self._find([_ev("a@b.com") for _ in range(MIN_EVENTS_TO_PROPOSE)], rules)
        assert out == []

    async def test_candidates_are_ordered_by_how_much_attention_they_cost(self):
        events = [_ev("quiet@x.com") for _ in range(MIN_EVENTS_TO_PROPOSE)]
        events += [_ev("loud@x.com") for _ in range(MIN_EVENTS_TO_PROPOSE + 4)]
        out = await self._find(events)
        assert [c.address for c in out] == ["loud@x.com", "quiet@x.com"]

    async def test_an_event_with_no_sender_is_skipped_not_grouped(self):
        events = [
            N(
                source="gmail",
                actor_entities=[],
                importance_signals={"actionable": False},
                occurred_at=NOW,
                title="x",
            )
        ] * MIN_EVENTS_TO_PROPOSE
        assert await self._find(events) == []

    async def test_a_read_failure_proposes_nothing(self):
        class _Boom:
            async def execute(self, *_a, **_k):
                raise RuntimeError("db down")

        out = await find_sender_candidates(_Boom(), workspace_id="ws_1", user_id="u_1", now=NOW)
        assert out == []


class TestThrottle:
    async def test_an_open_proposal_blocks_another(self):
        db = _DB(_Rows(events=[], approvals=[N(approval_id="apr_1")]))
        assert await open_or_recent_proposal(db, workspace_id="ws_1", now=NOW) is True

    async def test_nothing_recent_lets_one_through(self):
        db = _DB(_Rows(events=[], approvals=[]))
        assert await open_or_recent_proposal(db, workspace_id="ws_1", now=NOW) is False

    async def test_a_failed_check_blocks_rather_than_proposes(self):
        """Never propose on a check that did not run."""

        class _Boom:
            async def execute(self, *_a, **_k):
                raise RuntimeError("db down")

        assert await open_or_recent_proposal(_Boom(), workspace_id="ws_1", now=NOW) is True


class TestTheCard:
    def test_it_names_the_count_and_the_cost(self):
        title = proposal_title(
            [
                SenderCandidate("gmail", "a@b.com", 7, "x"),
                SenderCandidate("gmail", "c@d.com", 5, "y"),
            ]
        )
        assert "2 senders" in title and "12 messages" in title

    def test_one_sender_reads_singular(self):
        assert "1 sender quiet?" in proposal_title([SenderCandidate("gmail", "a@b.com", 6, "x")])
