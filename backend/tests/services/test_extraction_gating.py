"""Tests for triage-gated worker extraction (Phase-2 cost gate) + calendar-recurrence dedup.

Extraction cost must be proportional to the triage tier persisted on each
``NormalizedEvent`` (``importance_signals.tier``, set by ``TriageResult.to_signals()``):
  - skip  -> no entity extraction, no memory extraction
  - light -> memory extraction ONLY (founder spend/receipt ledger)
  - full  -> both entity + memory extraction (current behavior)

All gating is behind ``settings.perception_triage_enabled`` (default False = unchanged
behavior). Additionally, a full-tier ``calendar_invite`` whose meeting entity already
exists (recurring series) skips re-extraction.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


def _make_bus_event(payload: dict, event_type: str = "event_processed") -> MagicMock:
    ev = MagicMock()
    ev.user_id = TEST_USER_ID
    ev.workspace_id = TEST_WORKSPACE_ID
    ev.event_type = event_type
    ev.payload = payload
    ev.message_id = "1234567890-0"
    return ev


def _make_normalized_event(
    tier: str | None = "full",
    category: str = "",
    title: str = "Some Event",
    summary: str = "Summary text",
) -> MagicMock:
    ev = MagicMock()
    ev.title = title
    ev.summary = summary
    if tier is None:
        ev.importance_signals = None
    else:
        ev.importance_signals = {
            "category": category,
            "tier": tier,
            "actionable": True,
            "triage_origin": "rules",
        }
    return ev


def _make_manager(**settings_overrides) -> "StreamConsumerManager":  # noqa: F821
    from src.services.worker import StreamConsumerManager

    settings_overrides.setdefault("neo4j_url", "")
    settings = make_mock_settings(**settings_overrides)
    return StreamConsumerManager(settings)


def _mock_db_with_event(ev) -> AsyncMock:
    """A fake async-context-manager DB session whose single ``execute()``
    resolves to ``ev`` via ``scalar_one_or_none()`` — mirrors the
    ``select(NormalizedEvent)`` fetch both handlers perform."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=ev)
    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.commit = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


def _patch_factory(mock_db):
    mock_factory_instance = MagicMock()
    mock_factory_instance.return_value = mock_db
    return patch("src.services.worker.get_session_factory", return_value=mock_factory_instance)


class TestEventTierHelper:
    """``_event_tier`` reads the persisted triage tier; unrecognized -> 'full' (recall)."""

    def test_reads_tier_from_signals(self):
        from src.services.worker import _event_tier

        ev = MagicMock()
        ev.importance_signals = {"tier": "light"}
        assert _event_tier(ev) == "light"

    def test_missing_signals_defaults_full(self):
        from src.services.worker import _event_tier

        ev = MagicMock()
        ev.importance_signals = None
        assert _event_tier(ev) == "full"

    def test_missing_tier_key_defaults_full(self):
        from src.services.worker import _event_tier

        ev = MagicMock()
        ev.importance_signals = {"category": "email_received"}
        assert _event_tier(ev) == "full"

    def test_garbled_tier_defaults_full(self):
        from src.services.worker import _event_tier

        ev = MagicMock()
        ev.importance_signals = {"tier": "urgent!!"}
        assert _event_tier(ev) == "full"


class TestSkipTier:
    @pytest.mark.asyncio
    async def test_skip_tier_no_entity_extraction(self):
        mgr = _make_manager(perception_triage_enabled=True)
        event = _make_bus_event({"event_id": "evt_skip_1"})
        ev = _make_normalized_event(tier="skip")
        mock_db = _mock_db_with_event(ev)

        mock_world_model = MagicMock()
        mock_world_model.extract_from_event = AsyncMock(return_value=[])
        mock_world_model.find_entity = AsyncMock(return_value=[])

        with (
            _patch_factory(mock_db),
            patch("src.services.world_model.WorldModel", return_value=mock_world_model),
        ):
            await mgr._handle_entity_extraction(event)

        mock_world_model.extract_from_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skip_tier_no_memory_extraction(self):
        mgr = _make_manager(perception_triage_enabled=True)
        event = _make_bus_event({"event_id": "evt_skip_2"})
        ev = _make_normalized_event(tier="skip")
        mock_db = _mock_db_with_event(ev)

        mock_memory_service = MagicMock()
        mock_memory_service.extract_and_store = AsyncMock(return_value=[])

        with (
            _patch_factory(mock_db),
            patch("src.services.memory_service.MemoryService", return_value=mock_memory_service),
        ):
            await mgr._handle_memory_extraction(event)

        mock_memory_service.extract_and_store.assert_not_awaited()


class TestLightTier:
    @pytest.mark.asyncio
    async def test_light_tier_memory_but_no_entities(self):
        mgr = _make_manager(perception_triage_enabled=True)
        ev = _make_normalized_event(tier="light")

        # Entity handler: no extraction.
        entity_event = _make_bus_event({"event_id": "evt_light_1"})
        mock_db_entity = _mock_db_with_event(ev)
        mock_world_model = MagicMock()
        mock_world_model.extract_from_event = AsyncMock(return_value=[])
        with (
            _patch_factory(mock_db_entity),
            patch("src.services.world_model.WorldModel", return_value=mock_world_model),
        ):
            await mgr._handle_entity_extraction(entity_event)
        mock_world_model.extract_from_event.assert_not_awaited()

        # Memory handler: extraction proceeds.
        memory_event = _make_bus_event({"event_id": "evt_light_2"})
        mock_db_memory = _mock_db_with_event(ev)
        mock_memory_service = MagicMock()
        mock_memory_service.extract_and_store = AsyncMock(return_value=["mem_1"])
        with (
            _patch_factory(mock_db_memory),
            patch("src.services.memory_service.MemoryService", return_value=mock_memory_service),
        ):
            await mgr._handle_memory_extraction(memory_event)
        mock_memory_service.extract_and_store.assert_awaited_once()


class TestFullTier:
    @pytest.mark.asyncio
    async def test_full_tier_extracts_both(self):
        mgr = _make_manager(perception_triage_enabled=True)
        ev = _make_normalized_event(tier="full", category="email_received", title="Note")

        entity_event = _make_bus_event({"event_id": "evt_full_1"})
        mock_db_entity = _mock_db_with_event(ev)
        mock_world_model = MagicMock()
        mock_world_model.extract_from_event = AsyncMock(return_value=[])
        mock_world_model.find_entity = AsyncMock(return_value=[])
        with (
            _patch_factory(mock_db_entity),
            patch("src.services.world_model.WorldModel", return_value=mock_world_model),
        ):
            await mgr._handle_entity_extraction(entity_event)
        mock_world_model.extract_from_event.assert_awaited_once()

        memory_event = _make_bus_event({"event_id": "evt_full_2"})
        mock_db_memory = _mock_db_with_event(ev)
        mock_memory_service = MagicMock()
        mock_memory_service.extract_and_store = AsyncMock(return_value=["mem_1"])
        with (
            _patch_factory(mock_db_memory),
            patch("src.services.memory_service.MemoryService", return_value=mock_memory_service),
        ):
            await mgr._handle_memory_extraction(memory_event)
        mock_memory_service.extract_and_store.assert_awaited_once()


class TestFlagDisabled:
    @pytest.mark.asyncio
    async def test_disabled_flag_extracts_everything(self):
        """Flag off = old behavior, even for a skip-tier event."""
        mgr = _make_manager(perception_triage_enabled=False)
        ev = _make_normalized_event(tier="skip")

        entity_event = _make_bus_event({"event_id": "evt_disabled_1"})
        mock_db_entity = _mock_db_with_event(ev)
        mock_world_model = MagicMock()
        mock_world_model.extract_from_event = AsyncMock(return_value=[])
        with (
            _patch_factory(mock_db_entity),
            patch("src.services.world_model.WorldModel", return_value=mock_world_model),
        ):
            await mgr._handle_entity_extraction(entity_event)
        mock_world_model.extract_from_event.assert_awaited_once()

        memory_event = _make_bus_event({"event_id": "evt_disabled_2"})
        mock_db_memory = _mock_db_with_event(ev)
        mock_memory_service = MagicMock()
        mock_memory_service.extract_and_store = AsyncMock(return_value=["mem_1"])
        with (
            _patch_factory(mock_db_memory),
            patch("src.services.memory_service.MemoryService", return_value=mock_memory_service),
        ):
            await mgr._handle_memory_extraction(memory_event)
        mock_memory_service.extract_and_store.assert_awaited_once()


class TestCalendarRecurrenceDedup:
    @pytest.mark.asyncio
    async def test_recurring_calendar_invite_skips_when_meeting_exists(self):
        mgr = _make_manager(perception_triage_enabled=True)
        event = _make_bus_event({"event_id": "evt_cal_1"})
        ev = _make_normalized_event(tier="full", category="calendar_invite", title="HMI Jour Fixe")
        mock_db = _mock_db_with_event(ev)

        mock_world_model = MagicMock()
        mock_world_model.extract_from_event = AsyncMock(return_value=[])
        mock_world_model.find_entity = AsyncMock(
            return_value=[
                {
                    "entity_id": "ent_meeting_1",
                    "entity_type": "meeting",
                    "canonical_name": "HMI Jour Fixe",
                }
            ]
        )

        with (
            _patch_factory(mock_db),
            patch("src.services.world_model.WorldModel", return_value=mock_world_model),
        ):
            await mgr._handle_entity_extraction(event)

        mock_world_model.find_entity.assert_awaited_once()
        mock_world_model.extract_from_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_first_calendar_invite_extracts_when_no_existing_meeting(self):
        mgr = _make_manager(perception_triage_enabled=True)
        event = _make_bus_event({"event_id": "evt_cal_2"})
        ev = _make_normalized_event(
            tier="full", category="calendar_invite", title="New Onboarding Call"
        )
        mock_db = _mock_db_with_event(ev)

        mock_world_model = MagicMock()
        mock_world_model.extract_from_event = AsyncMock(return_value=[])
        mock_world_model.find_entity = AsyncMock(return_value=[])

        with (
            _patch_factory(mock_db),
            patch("src.services.world_model.WorldModel", return_value=mock_world_model),
        ):
            await mgr._handle_entity_extraction(event)

        mock_world_model.extract_from_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dedup_lookup_failure_falls_back_to_extraction(self):
        """A lookup error must not block extraction — fail defensively open."""
        mgr = _make_manager(perception_triage_enabled=True)
        event = _make_bus_event({"event_id": "evt_cal_3"})
        ev = _make_normalized_event(tier="full", category="calendar_invite", title="Weekly Sync")
        mock_db = _mock_db_with_event(ev)

        mock_world_model = MagicMock()
        mock_world_model.extract_from_event = AsyncMock(return_value=[])
        mock_world_model.find_entity = AsyncMock(side_effect=RuntimeError("db down"))

        with (
            _patch_factory(mock_db),
            patch("src.services.world_model.WorldModel", return_value=mock_world_model),
        ):
            await mgr._handle_entity_extraction(event)

        mock_world_model.extract_from_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_calendar_full_tier_does_not_call_find_entity(self):
        """Dedup lookup only fires for calendar_invite — no needless calls elsewhere."""
        mgr = _make_manager(perception_triage_enabled=True)
        event = _make_bus_event({"event_id": "evt_email_1"})
        ev = _make_normalized_event(tier="full", category="email_received", title="Investor note")
        mock_db = _mock_db_with_event(ev)

        mock_world_model = MagicMock()
        mock_world_model.extract_from_event = AsyncMock(return_value=[])
        mock_world_model.find_entity = AsyncMock(return_value=[])

        with (
            _patch_factory(mock_db),
            patch("src.services.world_model.WorldModel", return_value=mock_world_model),
        ):
            await mgr._handle_entity_extraction(event)

        mock_world_model.find_entity.assert_not_awaited()
        mock_world_model.extract_from_event.assert_awaited_once()


class TestEntityHandlerMissingNormalizedEvent:
    @pytest.mark.asyncio
    async def test_returns_early_when_event_not_found(self):
        mgr = _make_manager(perception_triage_enabled=True)
        event = _make_bus_event({"event_id": "evt_missing"})
        mock_db = _mock_db_with_event(None)

        mock_world_model = MagicMock()
        mock_world_model.extract_from_event = AsyncMock(return_value=[])

        with (
            _patch_factory(mock_db),
            patch("src.services.world_model.WorldModel", return_value=mock_world_model),
        ):
            await mgr._handle_entity_extraction(event)

        mock_world_model.extract_from_event.assert_not_awaited()
