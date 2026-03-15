"""Tests for ProcedureLibrary."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.procedure_library import ProcedureLibrary


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.fixture
def library(mock_db):
    return ProcedureLibrary(mock_db)


def _make_procedure(
    procedure_id="proc_001",
    status="active",
    trigger_pattern=None,
    confidence=0.8,
):
    p = MagicMock()
    p.procedure_id = procedure_id
    p.user_id = "usr_default"
    p.name = "Test procedure"
    p.status = status
    p.trigger_pattern = trigger_pattern or {}
    p.confidence = confidence
    p.usage_count = 0
    p.last_used_at = None
    return p


class TestFindMatching:
    async def test_matches_event_type(self, library, mock_db):
        proc = _make_procedure(trigger_pattern={"event_type": "email_received"})
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [proc]
        mock_db.execute = AsyncMock(return_value=result_mock)

        matched = await library.find_matching("usr_default", "email_received")
        assert len(matched) == 1

    async def test_no_match(self, library, mock_db):
        proc = _make_procedure(trigger_pattern={"event_type": "pr_opened"})
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [proc]
        mock_db.execute = AsyncMock(return_value=result_mock)

        matched = await library.find_matching("usr_default", "email_received")
        assert len(matched) == 0

    async def test_sorted_by_confidence(self, library, mock_db):
        proc1 = _make_procedure("proc_1", confidence=0.5)
        proc2 = _make_procedure("proc_2", confidence=0.9)
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [proc1, proc2]
        mock_db.execute = AsyncMock(return_value=result_mock)

        matched = await library.find_matching("usr_default", "any")
        assert matched[0].confidence == 0.9


class TestRecordUsage:
    async def test_increments_usage(self, library, mock_db):
        proc = _make_procedure(confidence=0.5, procedure_id="proc_1")
        proc.usage_count = 3
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = proc
        mock_db.execute = AsyncMock(return_value=result_mock)

        await library.record_usage("proc_1", success=True)
        assert proc.usage_count == 4
        assert proc.confidence == 0.55

    async def test_decreases_confidence_on_failure(self, library, mock_db):
        proc = _make_procedure(confidence=0.5)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = proc
        mock_db.execute = AsyncMock(return_value=result_mock)

        await library.record_usage("proc_001", success=False)
        assert proc.confidence == 0.4


class TestGeneralizeInput:
    def test_keeps_short_values(self):
        result = ProcedureLibrary._generalize_input({"tone": "professional"})
        assert result["tone"] == "professional"

    def test_replaces_long_values(self):
        long_text = "x" * 60
        result = ProcedureLibrary._generalize_input({"context": long_text})
        assert result["context"] == "{{context}}"

    def test_handles_none(self):
        assert ProcedureLibrary._generalize_input(None) == {}


class TestActivateProcedure:
    async def test_activates_draft(self, library, mock_db):
        proc = _make_procedure(status="draft")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = proc
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await library.activate_procedure("proc_001", "usr_default")
        assert proc.status == "active"

    async def test_not_found(self, library, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert not await library.activate_procedure("missing", "usr_default")
