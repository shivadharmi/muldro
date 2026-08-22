"""`build_features` is the only place the ranker touches the database.

The tests that matter here are not "does it read the column" but "does it
refuse to read the wrong one". Three properties carry `ranker-interface.md`
§1 and §1a: the LLM-authored scores are never read at all; a deadline is
sourced PER CONNECTOR and fails closed on an unlisted one (calendar in
particular never sees the text extractor); and one malformed unit costs its
own features, never the feed.
"""

import ast
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.entities import Entity, EntityAlias, EntityRelationship
from src.models.memory import Memory
from src.view.contracts import Affordance, Frame, Quote, Unit
from src.view.ranking import build as build_module
from src.view.ranking.build import DEADLINE_SOURCE, build_features
from tests.conftest import make_filtering_db

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
USER = "usr_rank"
WORKSPACE = "ws_rank"


# ── doubles ─────────────────────────────────────────────────────────────


def make_multi_db(**rows_by_table):
    """One `make_filtering_db` per table, dispatched on the statement's FROM.

    `make_filtering_db` evaluates the statement's own WHERE against in-memory
    rows, which is what makes a dropped scope filter visible to a test. It
    holds ONE row list though, and this module queries five tables whose
    column names collide (`entities.entity_id` and `normalized_events`'
    both exist), so the only addition here is routing — the filtering itself
    is still conftest's.
    """
    subs = {table: make_filtering_db(rows) for table, rows in rows_by_table.items()}
    empty = make_filtering_db([])

    async def _execute(stmt):
        froms = stmt.get_final_froms()
        table = froms[0].name if froms else None
        return await subs.get(table, empty).execute(stmt)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.add = MagicMock()
    return db


class FakeEngagement:
    def __init__(self):
        self.penalty = 0.0
        self.suppressed = False
        self.calls: list[tuple[str, str]] = []

    async def get_relevance_penalty(self, source, category):
        self.calls.append((source, category))
        return self.penalty

    async def is_suppressed(self, source, category):
        return self.suppressed


@pytest.fixture(autouse=True)
def engagement(monkeypatch):
    fake = FakeEngagement()
    monkeypatch.setattr(build_module, "EngagementService", lambda db, workspace_id: fake)
    return fake


# ── fixtures ────────────────────────────────────────────────────────────


def _frame(
    *,
    source: str = "gmail",
    entity_type: str = "email_thread",
    entity_id: str = "t1",
    occurred_at: datetime | None = None,
    updated_at: datetime | None = None,
    affordances: tuple[Affordance, ...] = (),
    kind: str = "proposal",
) -> Frame:
    when = occurred_at or NOW - timedelta(hours=2)
    return Frame(
        key=f"{source}:{entity_type}:{entity_id}",
        kind=kind,
        status="needs_you",
        headline="Sarah - quarterly numbers",
        source=source,
        entity_type=entity_type,
        occurred_at=when,
        updated_at=updated_at or when,
        affordances=affordances,
    )


def _unit(*, quotes: tuple[Quote, ...] = (), **frame_kwargs) -> Unit:
    return Unit(frame=_frame(**frame_kwargs), body="", quotes=quotes)


def _quote(text: str) -> Quote:
    return Quote(text=text, who="Sarah", when=NOW - timedelta(hours=2))


def _event(
    *,
    source: str = "gmail",
    entity_type: str = "email_thread",
    entity_id: str = "t1",
    event_type: str = "email_received",
    actor: dict | None = None,
    importance_signals: dict | None = None,
):
    return SimpleNamespace(
        source=source,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        occurred_at=NOW - timedelta(hours=2),
        actor_entities=[actor] if actor else None,
        importance_signals=importance_signals,
        importance_score=0.99,
        urgency_score=0.99,
        title="quarterly numbers",
        summary="please send by tomorrow",
    )


def _alias(alias: str, entity_id: str = "ent_sarah", alias_type: str = "email") -> EntityAlias:
    row = EntityAlias()
    row.id = 1
    row.entity_id = entity_id
    row.workspace_id = WORKSPACE
    row.alias = alias
    row.alias_type = alias_type
    return row


def _entity(
    entity_id: str = "ent_sarah",
    *,
    interaction_count: int = 7,
    last_seen_at: datetime | None = None,
) -> Entity:
    row = Entity()
    row.entity_id = entity_id
    row.user_id = USER
    row.workspace_id = WORKSPACE
    row.entity_type = "person"
    row.canonical_name = "Sarah"
    row.interaction_count = interaction_count
    row.last_seen_at = last_seen_at if last_seen_at is not None else NOW - timedelta(days=3)
    row.importance_score = 0.97
    return row


def _relationship(relation_type: str = "works_on", *, to_entity: str = "ent_sarah"):
    row = EntityRelationship()
    row.relation_id = f"rel_{relation_type}"
    row.user_id = USER
    row.workspace_id = WORKSPACE
    row.from_entity_id = "ent_founder"
    row.to_entity_id = to_entity
    row.relation_type = relation_type
    row.strength = 1.0
    row.active = True
    return row


def _goal(memory_id: str = "mem_g1", *, entity_ids: list[str] | None = None) -> Memory:
    row = Memory()
    row.memory_id = memory_id
    row.user_id = USER
    row.workspace_id = WORKSPACE
    row.memory_type = "goal"
    row.status = "active"
    row.fact_text = "Close the seed round"
    row.entity_ids = entity_ids if entity_ids is not None else ["ent_sarah"]
    return row


def _norm_event(entity_id: str = "t1", source: str = "gmail", event_id: str = "evt_1"):
    row = MagicMock()
    row.event_id = event_id
    row.user_id = USER
    row.source = source
    row.entity_id = entity_id
    return row


async def _build(units, *, db=None, events_by_key=None):
    return await build_features(
        units,
        db=db if db is not None else make_multi_db(),
        workspace_id=WORKSPACE,
        user_id=USER,
        now=NOW,
        events_by_key=events_by_key,
    )


# ── the fields that must never be read ──────────────────────────────────

_FORBIDDEN_READS = (
    "importance_score",
    "urgency_score",
    "from_priority_person",
    "related_to_active_project",
)


def test_build_never_names_an_llm_authored_score():
    """§1's forbidden list, asserted at the syntax tree rather than by review.

    Each of these is an LLM's assertion over the attacker's subject and body
    wearing a typed name. `Entity.importance_score` is on the list too: it is
    a stored score whose writer has not been audited, and this module is not
    that audit.
    """
    source = pathlib.Path(build_module.__file__).read_text()
    tree = ast.parse(source)
    named = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    named |= {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for forbidden in _FORBIDDEN_READS:
        assert forbidden not in named, f"build.py reads {forbidden}"


# ── deadline: a per-source question with a fail-closed answer ───────────


async def test_gmail_extracts_a_deadline_from_verbatim_text():
    features = await _build([_unit(source="gmail", quotes=(_quote("please review by tomorrow"),))])
    assert features[0].deadline_in_days == 1


async def test_slack_extracts_a_deadline_from_verbatim_text():
    unit = _unit(
        source="slack",
        entity_type="message_thread",
        quotes=(_quote("need this by 2026-03-05"),),
    )
    assert (await _build([unit]))[0].deadline_in_days == 4


async def test_calendar_reads_the_structured_field_and_never_the_extractor(monkeypatch):
    """The meeting's own start IS the deadline.

    Its `summary` is muldro's OWN composed prose, so parsing it would be
    strictly worse than reading the typed value sitting beside it — and a
    parser pointed at composed prose is a parser pointed at nothing.
    """
    called = []
    monkeypatch.setattr(
        build_module,
        "extract_deadline",
        lambda text, *, now: called.append(text),
    )
    unit = _unit(
        source="calendar",
        entity_type="meeting",
        occurred_at=NOW + timedelta(days=2),
        quotes=(_quote("by tomorrow"),),
    )
    features = await _build([unit])
    assert features[0].deadline_in_days == 2
    assert called == []


@pytest.mark.parametrize("source", ["github", "notion", "linear", ""])
async def test_an_unlisted_source_yields_no_deadline_even_with_dated_text(source):
    """Fail closed. Forgetting a connector costs a signal, never opens a hole."""
    unit = _unit(
        source=source or "unknown",
        entity_type="thing",
        quotes=(_quote("must ship by tomorrow"),),
    )
    assert (await _build([unit]))[0].deadline_in_days is None


def test_the_deadline_map_lists_only_the_sources_that_can_answer():
    assert DEADLINE_SOURCE == {
        "calendar": "structured",
        "gmail": "verbatim_text",
        "slack": "verbatim_text",
    }


async def test_a_past_meeting_is_not_a_deadline():
    unit = _unit(source="calendar", entity_type="meeting", occurred_at=NOW - timedelta(days=3))
    assert (await _build([unit]))[0].deadline_in_days is None


async def test_a_meeting_past_the_horizon_is_not_a_deadline():
    unit = _unit(source="calendar", entity_type="meeting", occurred_at=NOW + timedelta(days=900))
    assert (await _build([unit]))[0].deadline_in_days is None


async def test_a_gmail_unit_with_no_quote_has_no_deadline():
    assert (await _build([_unit(source="gmail")]))[0].deadline_in_days is None


# ── counterparty: a lookup, not a judgement ─────────────────────────────


async def test_a_strong_identifier_resolves_the_counterparty():
    db = make_multi_db(
        entity_aliases=[_alias("sarah@acme.com")],
        entities=[_entity()],
        entity_relationships=[_relationship("reports_to")],
    )
    events = {"gmail:email_thread:t1": [_event(actor={"name": "Sarah", "email": "sarah@acme.com"})]}
    party = (await _build([_unit()], db=db, events_by_key=events))[0].counterparty

    assert party.known is True
    assert party.interaction_count == 7
    assert party.days_since_last_seen == 3
    assert party.relationship == "reports_to"


async def test_an_unmatched_identifier_leaves_the_counterparty_unknown():
    db = make_multi_db(entity_aliases=[_alias("someone.else@acme.com")], entities=[_entity()])
    events = {"gmail:email_thread:t1": [_event(actor={"email": "sarah@acme.com"})]}
    party = (await _build([_unit()], db=db, events_by_key=events))[0].counterparty
    assert party.known is False
    assert party.interaction_count == 0


async def test_an_actor_with_no_strong_identifier_is_not_resolved():
    """github attributes to a repository and notion to a display name only."""
    db = make_multi_db(entity_aliases=[_alias("Sarah", alias_type="name")], entities=[_entity()])
    events = {"github:pullrequest:pr1": [_event(source="github", actor={"name": "Sarah"})]}
    unit = _unit(source="github", entity_type="pullrequest", entity_id="pr1")
    assert (await _build([unit], db=db, events_by_key=events))[0].counterparty.known is False


async def test_a_slack_handle_resolves_through_the_handle_alias_type():
    db = make_multi_db(
        entity_aliases=[_alias("U0123", alias_type="handle")],
        entities=[_entity()],
    )
    events = {
        "slack:message_thread:t1": [
            _event(source="slack", entity_type="message_thread", actor={"slack_id": "U0123"})
        ]
    }
    unit = _unit(source="slack", entity_type="message_thread")
    assert (await _build([unit], db=db, events_by_key=events))[0].counterparty.known is True


async def test_an_email_alias_matches_case_insensitively():
    db = make_multi_db(entity_aliases=[_alias("sarah@acme.com")], entities=[_entity()])
    events = {"gmail:email_thread:t1": [_event(actor={"email": "Sarah@ACME.com"})]}
    assert (await _build([_unit()], db=db, events_by_key=events))[0].counterparty.known is True


async def test_prior_threads_is_none_because_it_is_not_knowable():
    db = make_multi_db(entity_aliases=[_alias("sarah@acme.com")], entities=[_entity()])
    events = {"gmail:email_thread:t1": [_event(actor={"email": "sarah@acme.com"})]}
    assert (await _build([_unit()], db=db, events_by_key=events))[0].counterparty.prior_threads is (
        None
    )


# ── goals: a graph join, never a text similarity ────────────────────────


async def test_a_goal_referencing_the_counterparty_matches():
    db = make_multi_db(
        entity_aliases=[_alias("sarah@acme.com")],
        entities=[_entity()],
        memories=[_goal("mem_g1"), _goal("mem_g2", entity_ids=["ent_other"])],
    )
    events = {"gmail:email_thread:t1": [_event(actor={"email": "sarah@acme.com"})]}
    assert (await _build([_unit()], db=db, events_by_key=events))[0].matched_goal_ids == ("mem_g1",)


async def test_no_counterparty_means_no_goal_match():
    db = make_multi_db(memories=[_goal("mem_g1")])
    assert (await _build([_unit()], db=db))[0].matched_goal_ids == ()


# ── thread, affordance, recency ─────────────────────────────────────────


async def test_message_count_comes_from_the_stored_events_for_that_thing():
    db = make_multi_db(
        normalized_events=[
            _norm_event(event_id="e1"),
            _norm_event(event_id="e2"),
            _norm_event(entity_id="other", event_id="e3"),
        ]
    )
    assert (await _build([_unit()], db=db))[0].thread.message_count == 2


async def test_message_count_is_at_least_one_even_with_nothing_stored():
    assert (await _build([_unit()]))[0].thread.message_count == 1


async def test_you_replied_is_never_defaulted_to_false():
    assert (await _build([_unit()]))[0].thread.you_replied is None


async def test_age_and_thread_recency_are_measured_against_the_supplied_now():
    unit = _unit(occurred_at=NOW - timedelta(hours=5), updated_at=NOW - timedelta(hours=1))
    features = (await _build([unit]))[0]
    assert features.age_hours == pytest.approx(5.0)
    assert features.thread.hours_since_last == pytest.approx(1.0)


async def test_a_future_timestamp_does_not_produce_a_negative_age():
    unit = _unit(occurred_at=NOW + timedelta(hours=5))
    assert (await _build([unit]))[0].age_hours == 0.0


async def test_an_affordance_marks_an_unresolved_decision():
    unit = _unit(affordances=(Affordance(capability="email.send", label="Reply"),))
    assert (await _build([unit]))[0].has_unresolved_affordance is True
    assert (await _build([_unit()]))[0].has_unresolved_affordance is False


# ── bulk mail: rules origin only ────────────────────────────────────────


async def test_rules_origin_marketing_marks_bulk_mail():
    events = {
        "gmail:email_thread:t1": [
            _event(importance_signals={"triage_origin": "rules", "category": "marketing"})
        ]
    }
    assert (await _build([_unit()], events_by_key=events))[0].bulk_mail is True


async def test_an_llm_chosen_marketing_category_is_ignored():
    """The provenance flag is the whole point — a model's category is not evidence."""
    events = {
        "gmail:email_thread:t1": [
            _event(importance_signals={"triage_origin": "llm", "category": "marketing"})
        ]
    }
    assert (await _build([_unit()], events_by_key=events))[0].bulk_mail is False


async def test_a_rules_origin_non_marketing_category_is_not_bulk():
    events = {
        "gmail:email_thread:t1": [
            _event(importance_signals={"triage_origin": "rules", "category": "work_thread"})
        ]
    }
    assert (await _build([_unit()], events_by_key=events))[0].bulk_mail is False


# ── engagement ──────────────────────────────────────────────────────────


async def test_the_dismissal_penalty_is_read_per_source_and_event_type(engagement):
    engagement.penalty = 0.2
    events = {"gmail:email_thread:t1": [_event()]}
    features = (await _build([_unit()], events_by_key=events))[0]
    assert features.engagement_penalty == 0.2
    assert engagement.calls == [("gmail", "email_received")]


async def test_suppression_is_carried_onto_the_record(engagement):
    engagement.suppressed = True
    events = {"gmail:email_thread:t1": [_event()]}
    assert (await _build([_unit()], events_by_key=events))[0].suppressed is True


async def test_engagement_is_not_consulted_without_an_event_type(engagement):
    """`EngagementHistory` is keyed on the EVENT type; a frame carries the
    ENTITY type. Substituting one for the other would silently key the penalty
    on a different taxonomy, so a missing event yields no penalty instead."""
    engagement.penalty = 1.0
    features = (await _build([_unit()]))[0]
    assert features.engagement_penalty == 0.0
    assert engagement.calls == []


# ── totality: one bad unit costs its own features, never the feed ───────


async def test_a_malformed_unit_is_skipped_and_the_rest_survive():
    broken = SimpleNamespace(frame=None, quotes=())
    features = await _build([broken, _unit()])
    assert [f.key for f in features] == ["gmail:email_thread:t1"]


async def test_a_database_failure_degrades_to_frame_only_features():
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("postgres is gone"))
    features = await _build([_unit()], db=db)
    assert len(features) == 1
    assert features[0].key == "gmail:email_thread:t1"
    assert features[0].counterparty.known is False
    assert features[0].thread.message_count == 1


async def test_a_malformed_event_payload_does_not_stop_the_unit():
    events = {"gmail:email_thread:t1": ["not an event at all"]}
    features = await _build([_unit()], events_by_key=events)
    assert features[0].key == "gmail:email_thread:t1"
    assert features[0].bulk_mail is False


async def test_no_units_is_no_features():
    assert await _build([]) == []


async def test_the_record_keeps_the_frames_own_kind_and_source():
    features = (await _build([_unit(kind="finding")]))[0]
    assert features.kind == "finding"
    assert features.source == "gmail"
