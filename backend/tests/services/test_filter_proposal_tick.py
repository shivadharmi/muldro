"""The proposer runs on a cadence, and is quiet almost always.

Wired hourly rather than per poll: the evidence is a fortnight of triage
verdicts and moves slowly, `find_sender_candidates` reads the whole window each
time, and the proposal itself is throttled to one open card per workspace.
"""

from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch

import pytest

from src.services.scheduler.filter_proposal_tick import (
    FILTER_PROPOSAL_TICK_EVERY,
    FilterProposalTickMixin,
)


class _Loop(FilterProposalTickMixin):
    def __init__(self, tick_count):
        self._tick_count = tick_count


class _DB:
    def __init__(self, members):
        self._members = members
        self.committed = False

    async def execute(self, _stmt):
        members = self._members
        return N(scalars=lambda: N(all=lambda: members))

    def add(self, _obj):
        pass

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True


class _Ctx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *_a):
        return False


def _factory(db):
    return lambda: _Ctx(db)


@pytest.mark.asyncio
async def test_it_does_nothing_on_an_off_tick():
    """Otherwise a fortnight-wide scan would run every 30 seconds."""
    loop = _Loop(FILTER_PROPOSAL_TICK_EVERY - 1)
    with patch("src.services.filter_proposals.find_sender_candidates", AsyncMock()) as find:
        await loop._tick_filter_proposals(_factory(_DB([])))
    find.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_open_proposal_stops_another_being_offered():
    loop = _Loop(FILTER_PROPOSAL_TICK_EVERY)
    db = _DB([N(workspace_id="ws_1", user_id="u_1")])
    with (
        patch(
            "src.services.filter_proposals.open_or_recent_proposal",
            AsyncMock(return_value=True),
        ),
        patch("src.services.filter_proposals.find_sender_candidates", AsyncMock()) as find,
    ):
        await loop._tick_filter_proposals(_factory(db))
    find.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_throttle_is_checked_per_workspace():
    """One founder having an open proposal must not silence everyone else's."""
    loop = _Loop(FILTER_PROPOSAL_TICK_EVERY)
    db = _DB([N(workspace_id="ws_1", user_id="u_1"), N(workspace_id="ws_2", user_id="u_2")])
    seen: list[str] = []

    async def _open(_db, *, workspace_id, now):
        seen.append(workspace_id)
        return True

    with (
        patch("src.services.filter_proposals.open_or_recent_proposal", _open),
        patch("src.services.filter_proposals.find_sender_candidates", AsyncMock()),
    ):
        await loop._tick_filter_proposals(_factory(db))
    assert seen == ["ws_1", "ws_2"]


@pytest.mark.asyncio
async def test_no_candidates_means_no_card():
    loop = _Loop(FILTER_PROPOSAL_TICK_EVERY)
    db = _DB([N(workspace_id="ws_1", user_id="u_1")])
    with (
        patch(
            "src.services.filter_proposals.open_or_recent_proposal",
            AsyncMock(return_value=False),
        ),
        patch(
            "src.services.filter_proposals.find_sender_candidates",
            AsyncMock(return_value=[]),
        ),
        patch("src.services.filter_proposals.create_filter_proposal", AsyncMock()) as create,
    ):
        await loop._tick_filter_proposals(_factory(db))
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidates_become_exactly_one_card():
    from src.services.filter_proposals import SenderCandidate

    loop = _Loop(FILTER_PROPOSAL_TICK_EVERY)
    db = _DB([N(workspace_id="ws_1", user_id="u_1")])
    cands = [SenderCandidate("gmail", "a@b.com", 7, "x")]
    with (
        patch(
            "src.services.filter_proposals.open_or_recent_proposal",
            AsyncMock(return_value=False),
        ),
        patch(
            "src.services.filter_proposals.find_sender_candidates",
            AsyncMock(return_value=cands),
        ),
        patch("src.services.filter_proposals.create_filter_proposal", AsyncMock()) as create,
    ):
        await loop._tick_filter_proposals(_factory(db))
    create.assert_awaited_once()
    assert create.await_args.kwargs["candidates"] == cands


@pytest.mark.asyncio
async def test_a_failure_costs_the_proposal_not_the_tick():
    """Everything else in the cycle still runs, and the next hour tries again."""
    loop = _Loop(FILTER_PROPOSAL_TICK_EVERY)

    class _Boom:
        async def execute(self, *_a, **_k):
            raise RuntimeError("db down")

    await loop._tick_filter_proposals(_factory(_Boom()))  # must not raise
