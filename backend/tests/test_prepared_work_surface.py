"""Tests for the standing prepared-work review queue (single-lead cutover, tasks 10+11).

The queue is the ONLY place a prepared action can be acted on: when a write needs a
human and none is on the turn, both write gates record an ``Approval`` row
(``approval_type == "prepared_action"``, ``artifact_refs["prepared"] is True``) and
let the turn finish. These tests pin the grid card, the single ``queue`` detail tab,
and the taxonomy wiring that makes both reachable.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.api.routes_surface_detail import _PREFIX_MAP
from src.models.approvals import Approval
from src.services.surface_builder import SurfaceService
from src.services.surface_detail_builders import TAB_BUILDERS, build_prepared_work_queue
from src.services.surface_mapping import MAX_WORKSPACE_SURFACES, apply_surface_cap
from src.ui.contracts import SYSTEM_SURFACE_KINDS
from src.ui.renderer import _TABS_BY_KIND, build_detail_config

USER = "usr_prep"
WORKSPACE = "ws_prep"


# ── fixtures ────────────────────────────────────────────────────────────


def _approval(
    approval_id: str = "apr_prep1",
    *,
    capability: str = "email.send",
    tool_input: dict | None = None,
    truncated: bool = False,
    risk: str = "high",
    prepared_error: str | None = None,
    created_at: datetime | None = None,
    workspace: str = WORKSPACE,
) -> Approval:
    apr = Approval()
    apr.approval_id = approval_id
    apr.user_id = USER
    apr.workspace_id = workspace
    apr.approval_type = "prepared_action"
    apr.status = "pending"
    apr.title = f"Approve: {capability}"
    apr.summary = "Send the launch note to the investor list."
    apr.risk_level = risk
    apr.created_at = created_at or datetime(2026, 8, 19, 9, 30, tzinfo=timezone.utc)
    apr.updated_at = apr.created_at
    refs = {
        "prepared": True,
        "capability": capability,
        "tool_input": json.dumps(tool_input if tool_input is not None else {"to": "a@b.com"}),
        "tool_input_truncated": truncated,
        "effective_presence": "absent",
    }
    if prepared_error:
        refs["prepared_error"] = prepared_error
    apr.artifact_refs = refs
    return apr


def _db(rows: list[Approval]) -> AsyncMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = rows
    result.scalars.return_value = scalars
    db = AsyncMock()
    db.execute.return_value = result
    return db


def _filtering_db(rows: list[Approval]) -> AsyncMock:
    """A db whose ``execute`` actually honours the statement's user/workspace binds.

    The plain ``_db`` above returns its rows regardless of the query, which cannot tell a
    correctly-scoped query from an unscoped one. These tests are about the scoping, so the
    mock reads the compiled bind params and filters in Python.
    """
    db = AsyncMock()

    async def _execute(stmt):
        params = stmt.compile().params
        uid = params.get("user_id_1")
        wid = params.get("workspace_id_1")
        matched = [
            row
            for row in rows
            if (uid is None or row.user_id == uid) and (wid is None or row.workspace_id == wid)
        ]
        result = MagicMock()
        result.scalars.return_value.all.return_value = matched
        return result

    db.execute = _execute
    return db


def _surface(surface_id: str = f"prepared_work_{WORKSPACE}") -> MagicMock:
    s = MagicMock()
    s.surface_id = surface_id
    s.surface_type = "prepared_work"
    s.payload = {}
    return s


def _walk(components) -> list:
    out = []
    for c in components:
        out.append(c)
        out.extend(_walk(getattr(c, "children", None) or []))
    return out


def _all_components(tab) -> list:
    out = []
    for section in tab.sections:
        out.extend(_walk(section.children))
    return out


def _approval_ids(tab) -> set[str]:
    """Approval ids the tab's controls actually carry — the identity that reaches the API."""
    return {
        a.payload["approval_id"]
        for c in _all_components(tab)
        if c.type == "Button"
        for a in (c.actions or [])
    }


def _texts(tab) -> str:
    return " ".join(
        str(
            c.properties.get("text")
            or c.properties.get("label")
            or c.properties.get("message")
            or c.properties.get("code")
            or ""
        )
        for c in _all_components(tab)
        if c.properties
    )


# ── taxonomy (task 10) ──────────────────────────────────────────────────


def test_prepared_work_is_a_system_surface_kind():
    assert "prepared_work" in SYSTEM_SURFACE_KINDS


def test_prepared_work_has_exactly_one_queue_tab():
    assert _TABS_BY_KIND["prepared_work"] == [("queue", "Queue")]


def test_prefix_map_routes_prepared_work_ids():
    assert _PREFIX_MAP["prepared_work_"] == ("prepared_work", "surface_id")


def test_queue_tab_builder_is_registered():
    assert TAB_BUILDERS[("prepared_work", "queue")] is build_prepared_work_queue


# ── the grid card (task 11) ─────────────────────────────────────────────


async def test_empty_queue_produces_no_card():
    """An empty queue is ABSENT from the workspace — not a card saying there is nothing to do."""
    service = SurfaceService(db=_db([]), workspace_id=WORKSPACE)
    assert await service._build_prepared_work_surface(USER) is None


async def test_populated_queue_counts_and_carries_the_queue_tab():
    rows = [_approval("apr_1"), _approval("apr_2", risk="medium")]
    service = SurfaceService(db=_db(rows), workspace_id=WORKSPACE)

    push = await service._build_prepared_work_surface(USER)

    assert push is not None
    assert push.kind == "prepared_work"
    assert push.id == f"prepared_work_{WORKSPACE}"
    preview = push.preview
    assert preview["title"] == "Prepared for your review"
    assert "2" in preview["subtitle"]
    # The card must say plainly that nothing ran.
    assert "not" in preview["subtitle"].lower() or "nothing" in preview["subtitle"].lower()
    assert preview["status"] == "awaiting_approval"
    # Priority reflects the HIGHEST risk present, not the newest row's.
    assert preview["priority"] == "high"
    tabs = push.detail_config["tabs"]
    assert [t["id"] for t in tabs] == ["queue"]
    assert tabs[0]["endpoint"] == f"/v1/surfaces/prepared_work_{WORKSPACE}/detail/queue"


async def test_card_matches_build_detail_config():
    service = SurfaceService(db=_db([_approval()]), workspace_id=WORKSPACE)
    push = await service._build_prepared_work_surface(USER)
    expected = build_detail_config("prepared_work", f"prepared_work_{WORKSPACE}")
    assert push.detail_config == expected.model_dump(mode="json")


async def test_build_workspace_surfaces_includes_the_queue():
    rows = [_approval("apr_1")]
    service = SurfaceService(db=_db([]), workspace_id=WORKSPACE)

    async def _queue(user_id):
        populated = SurfaceService(db=_db(rows), workspace_id=WORKSPACE)
        return await populated._build_prepared_work_surface(user_id)

    service._build_run_surfaces = AsyncMock(return_value=[])
    service._build_briefing_surface = AsyncMock(return_value=None)
    service._build_insight_surfaces = AsyncMock(return_value=[])
    service._build_alert_surfaces = AsyncMock(return_value=[])
    service._build_recommendation_surfaces = AsyncMock(return_value=[])
    service._load_persisted_surfaces = AsyncMock(return_value=[])
    service._build_prepared_work_surface = _queue

    surfaces = await service.build_workspace_surfaces(USER)
    assert [s.kind for s in surfaces] == ["prepared_work"]


async def test_build_workspace_surfaces_skips_an_empty_queue():
    service = SurfaceService(db=_db([]), workspace_id=WORKSPACE)
    service._build_run_surfaces = AsyncMock(return_value=[])
    service._build_briefing_surface = AsyncMock(return_value=None)
    service._build_insight_surfaces = AsyncMock(return_value=[])
    service._build_alert_surfaces = AsyncMock(return_value=[])
    service._build_recommendation_surfaces = AsyncMock(return_value=[])
    service._load_persisted_surfaces = AsyncMock(return_value=[])
    service._build_prepared_work_surface = AsyncMock(return_value=None)

    assert await service.build_workspace_surfaces(USER) == []


# ── the queue tab (task 11) ─────────────────────────────────────────────


async def test_tab_shows_capability_payload_risk_and_controls():
    apr = _approval("apr_9", capability="email.send", tool_input={"to": "board@acme.com"})
    tab = await build_prepared_work_queue(_db([apr]), _surface(), user_id=USER)

    assert tab.tab_id == "queue"
    blob = _texts(tab)
    assert "email.send" in blob
    assert "board@acme.com" in blob  # the RECORDED payload, verbatim
    assert "high" in blob
    assert "2026-08-19" in blob  # age

    buttons = [c for c in _all_components(tab) if c.type == "Button"]
    payloads = [a.payload for b in buttons for a in (b.actions or [])]
    assert {"type": "approval.approve", "approval_id": "apr_9"} in payloads
    assert {"type": "approval.reject", "approval_id": "apr_9"} in payloads


async def test_first_row_expanded_rest_collapsed():
    rows = [_approval(f"apr_{i}") for i in range(3)]
    tab = await build_prepared_work_queue(_db(rows), _surface(), user_id=USER)

    assert len(tab.sections) == 3
    assert tab.sections[0].collapsed is False
    assert all(s.collapsed is True for s in tab.sections[1:])


async def test_truncated_payload_is_labelled_not_shown_as_whole():
    apr = _approval("apr_t", tool_input={"body": "x" * 20}, truncated=True)
    tab = await build_prepared_work_queue(_db([apr]), _surface(), user_id=USER)

    blob = _texts(tab).lower()
    assert "clip" in blob or "truncat" in blob


async def test_unknown_outcome_asks_for_a_check_not_a_retry():
    """An in-flight ledger row can never be re-fired — say so, do not offer a retry."""
    apr = _approval("apr_x", prepared_error="a prior attempt is still in flight — not re-fired")
    tab = await build_prepared_work_queue(_db([apr]), _surface(), user_id=USER)

    blob = _texts(tab).lower()
    assert "unknown" in blob
    assert "check" in blob

    buttons = [c for c in _all_components(tab) if c.type == "Button"]
    payloads = [a.payload["type"] for b in buttons for a in (b.actions or [])]
    # No approve control: confirming again returns 503 forever.
    assert "approval.approve" not in payloads
    # But the row must remain clearable.
    assert "approval.reject" in payloads


async def test_retryable_error_keeps_the_approve_control():
    apr = _approval("apr_r", prepared_error="another write to this capability is in progress")
    tab = await build_prepared_work_queue(_db([apr]), _surface(), user_id=USER)

    buttons = [c for c in _all_components(tab) if c.type == "Button"]
    payloads = [a.payload["type"] for b in buttons for a in (b.actions or [])]
    assert "approval.approve" in payloads
    assert "another write to this capability is in progress" in _texts(tab)


async def test_empty_queue_tab_still_renders_its_empty_state():
    tab = await build_prepared_work_queue(_db([]), _surface(), user_id=USER)
    assert tab.tab_id == "queue"
    assert tab.sections  # not nothing
    assert "waiting" in _texts(tab).lower()


async def test_tab_without_user_id_renders_empty_rather_than_someone_elses_queue():
    """The surface id embeds no record reference, so ``_verify_ephemeral_ownership`` has
    nothing to check — the builder does the scoping, and refuses to guess."""
    db = _db([_approval()])
    tab = await build_prepared_work_queue(db, _surface())
    assert tab.tab_id == "queue"
    db.execute.assert_not_awaited()


# ── discoverability: the cap must not evict the queue (fix 1) ───────────


def _capped(kind: str, when: str) -> MagicMock:
    m = MagicMock()
    m.kind = kind
    m.created_at = when
    return m


def test_prepared_work_card_survives_the_surface_cap():
    """A busy workspace is exactly when staged writes pile up. If twenty newer summaries
    can evict the queue card, the founder loses the only path to writes the system is
    blocked on."""
    surfaces = [_capped("prepared_work", "2026-08-19T09:00:00Z")]
    surfaces += [
        _capped("summary", f"2026-08-19T1{i // 60}:{i % 60:02d}:00Z")
        for i in range(MAX_WORKSPACE_SURFACES + 5)
    ]

    kept = apply_surface_cap(surfaces)

    assert len(kept) == MAX_WORKSPACE_SURFACES
    assert sum(1 for s in kept if s.kind == "prepared_work") == 1
    # And it outranks every other kind, so it is never the eviction candidate.
    assert kept[0].kind == "prepared_work"


def test_prepared_work_outranks_every_other_kind():
    from src.services.surface_mapping import PRIORITY_TIERS

    others = {k: v for k, v in PRIORITY_TIERS.items() if k != "prepared_work"}
    assert PRIORITY_TIERS["prepared_work"] < min(others.values())


# ── the tab agrees with the card (fix 2) ────────────────────────────────


async def test_tab_returns_only_the_addressed_workspace():
    """One founder, two workspaces. The card counts one workspace; the tab must match it."""
    mine = _approval("apr_here", workspace=WORKSPACE)
    other = _approval("apr_elsewhere", workspace="ws_other")
    db = _filtering_db([mine, other])

    tab = await build_prepared_work_queue(db, _surface(), user_id=USER)

    assert _approval_ids(tab) == {"apr_here"}
    assert len(tab.sections) == 1


async def test_card_count_and_tab_row_count_agree():
    rows = [_approval("apr_a"), _approval("apr_b"), _approval("apr_c", workspace="ws_other")]
    service = SurfaceService(db=_filtering_db(rows), workspace_id=WORKSPACE)

    push = await service._build_prepared_work_surface(USER)
    tab = await build_prepared_work_queue(_filtering_db(rows), _surface(), user_id=USER)

    assert push.preview["subtitle"].startswith("2 ")
    assert len(tab.sections) == 2


async def test_forged_workspace_in_the_surface_id_returns_empty_not_everything():
    """The workspace comes from the URL, so it is untrusted — but it only ever subtracts
    from an already user-scoped set, so a guess yields fewer rows, never someone else's."""
    db = _filtering_db([_approval("apr_here"), _approval("apr_elsewhere", workspace="ws_other")])

    tab = await build_prepared_work_queue(db, _surface("prepared_work_ws_guessed"), user_id=USER)

    assert _approval_ids(tab) == set()
    assert "waiting" in _texts(tab).lower()


async def test_surface_id_without_a_workspace_renders_empty():
    db = _filtering_db([_approval()])
    tab = await build_prepared_work_queue(db, _surface("surf_01H"), user_id=USER)
    assert "waiting" in _texts(tab).lower()
