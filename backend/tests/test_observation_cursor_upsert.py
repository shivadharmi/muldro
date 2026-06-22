"""Verify observation_cursors writers use an atomic Postgres upsert
(``INSERT ... ON CONFLICT DO UPDATE``) rather than a racy check-then-insert.

These tests compile the statement issued to ``db.execute`` against the
postgresql dialect and inspect the rendered SQL — no live database needed.
Catching this at the SQL-shape level is what prevents a future refactor
from silently regressing to the old ``SELECT then INSERT`` pattern that
caused ``uq_cursor_ws_user_source`` violations under concurrency.

Workspace-scoping tests (TestCursorWorkspaceScoping) verify that the unique
constraint key is ``(workspace_id, user_id, source)`` so a user who belongs
to multiple workspaces cannot bleed cursor state across them.

Atomic-ingest tests (TestAtomicIngestCursorAdvance) verify the P4 invariant:
the cursor upsert is issued within the SAME db session/commit as the event
loop, so the cursor only advances if ingestion reached completion.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings, make_raw_event


def _compile(stmt) -> str:
    """Render a Core statement as parameterized Postgres SQL."""
    return str(stmt.compile(dialect=postgresql.dialect()))


class TestJarvisUpdateCursor:
    """``ConnectorPoller.update_cursor`` must upsert atomically."""

    @pytest.mark.asyncio
    async def test_uses_on_conflict_do_update(self):
        """The statement passed to ``db.execute`` must target the
        ``uq_cursor_ws_user_source`` constraint with DO UPDATE semantics."""
        from src.orchestrator.connector_poller import ConnectorPoller

        # Capture the statement without wiring a real DB
        captured: dict = {}

        mock_db = MagicMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_db.execute = AsyncMock(side_effect=lambda stmt: captured.setdefault("stmt", stmt))
        mock_db.commit = AsyncMock()

        mock_factory = MagicMock(return_value=mock_db)

        # Build a minimal poller-like object with just _db_factory set
        poller = ConnectorPoller.__new__(ConnectorPoller)
        poller._db_factory_provider = lambda: mock_factory

        await ConnectorPoller.update_cursor(
            poller,
            source="gmail",
            user_id="usr_test",
            workspace_id="ws_test",
            new_cursor="20141",
            cursor_type="history_id",
        )

        assert mock_db.execute.await_count == 1
        sql = _compile(captured["stmt"]).upper()
        assert "INSERT INTO OBSERVATION_CURSORS" in sql
        assert "ON CONFLICT" in sql
        assert "DO UPDATE" in sql
        assert "UQ_CURSOR_WS_USER_SOURCE" in sql

    @pytest.mark.asyncio
    async def test_noop_when_new_cursor_is_falsy(self):
        """Early-return branch: no DB work when there's nothing to persist."""
        from src.orchestrator.connector_poller import ConnectorPoller

        mock_db = MagicMock()
        mock_factory = MagicMock(return_value=mock_db)
        poller = ConnectorPoller.__new__(ConnectorPoller)
        poller._db_factory_provider = lambda: mock_factory

        await ConnectorPoller.update_cursor(
            poller,
            source="gmail",
            user_id="usr_test",
            workspace_id="ws_test",
            new_cursor=None,
        )

        mock_factory.assert_not_called()


class TestIntegrationManagerUpdateCursor:
    """``IntegrationManager._update_cursor`` must use the same upsert."""

    @pytest.mark.asyncio
    async def test_uses_on_conflict_do_update(self):
        from src.services.integration_manager import IntegrationManager

        mock_db = MagicMock()
        captured: dict = {}
        mock_db.execute = AsyncMock(side_effect=lambda stmt: captured.setdefault("stmt", stmt))

        mgr = IntegrationManager.__new__(IntegrationManager)
        mgr._db = mock_db

        await IntegrationManager._update_cursor(
            mgr,
            user_id="usr_test",
            provider="github",
            value="sync_tok_123",
            workspace_id="ws_test",
        )

        assert mock_db.execute.await_count == 1
        sql = _compile(captured["stmt"]).upper()
        assert "INSERT INTO OBSERVATION_CURSORS" in sql
        assert "ON CONFLICT" in sql
        assert "DO UPDATE" in sql
        assert "UQ_CURSOR_WS_USER_SOURCE" in sql


class TestCursorWorkspaceScoping:
    """Verify that cursor reads and writes are keyed by workspace_id.

    Cross-tenant cursor bleed: a user in two workspaces shared ONE cursor row
    (old key: user_id + source).  After this fix the key is
    ``(workspace_id, user_id, source)``, so each workspace maintains its own
    independent stream position.
    """

    @pytest.mark.asyncio
    async def test_update_cursor_includes_workspace_id_in_values(self):
        """workspace_id must appear in the INSERT VALUES, not just be silently dropped."""
        from src.orchestrator.connector_poller import ConnectorPoller

        captured: dict = {}
        mock_db = MagicMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_db.execute = AsyncMock(side_effect=lambda stmt: captured.setdefault("stmt", stmt))
        mock_db.commit = AsyncMock()
        mock_factory = MagicMock(return_value=mock_db)

        poller = ConnectorPoller.__new__(ConnectorPoller)
        poller._db_factory_provider = lambda: mock_factory

        await ConnectorPoller.update_cursor(
            poller,
            source="gmail",
            user_id="usr_multi",
            workspace_id="ws_alpha",
            new_cursor="99999",
            cursor_type="history_id",
        )

        sql = _compile(captured["stmt"]).upper()
        # The workspace_id column must be present in the INSERT
        assert "WORKSPACE_ID" in sql

    @pytest.mark.asyncio
    async def test_two_workspaces_both_target_workspace_scoped_constraint(self):
        """Two update_cursor calls with different workspace_ids both target the
        ``uq_cursor_ws_user_source`` constraint and include workspace_id in the
        INSERT — confirming the SQL shape uses workspace-scoped conflict detection."""
        from src.orchestrator.connector_poller import ConnectorPoller

        stmts: list = []

        async def capture(stmt):
            stmts.append(stmt)

        for ws in ("ws_alpha", "ws_beta"):
            mock_db = MagicMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=False)
            mock_db.execute = AsyncMock(side_effect=capture)
            mock_db.commit = AsyncMock()
            mock_factory = MagicMock(return_value=mock_db)

            poller = ConnectorPoller.__new__(ConnectorPoller)
            poller._db_factory_provider = lambda: mock_factory

            await ConnectorPoller.update_cursor(
                poller,
                source="gmail",
                user_id="usr_shared",
                workspace_id=ws,
                new_cursor=f"cursor_{ws}",
                cursor_type="history_id",
            )

        assert len(stmts) == 2
        for stmt in stmts:
            sql = _compile(stmt).upper()
            # Both upserts target the workspace-scoped constraint
            assert "UQ_CURSOR_WS_USER_SOURCE" in sql
            assert "WORKSPACE_ID" in sql

    @pytest.mark.asyncio
    async def test_integration_manager_get_cursor_includes_workspace_id(self):
        """_get_cursor must filter by workspace_id to avoid cross-workspace reads."""
        from src.services.integration_manager import IntegrationManager

        captured: dict = {}
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)

        async def capture_and_return(stmt):
            captured["stmt"] = stmt
            return mock_result

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(side_effect=capture_and_return)

        mgr = IntegrationManager.__new__(IntegrationManager)
        mgr._db = mock_db

        await IntegrationManager._get_cursor(mgr, "usr_test", "github", "ws_test")

        assert mock_db.execute.await_count == 1
        # The WHERE clause must include workspace_id so reads are workspace-scoped
        sql = _compile(captured["stmt"]).upper()
        assert "WORKSPACE_ID" in sql


class TestTrustCeilingUpsert:
    """``TrustEngine.set_ceiling`` upserts rather than check-then-insert."""

    @pytest.mark.asyncio
    async def test_uses_on_conflict_do_update(self):
        from src.services.trust_engine import TrustEngine

        mock_db = MagicMock()
        captured: dict = {}
        mock_db.execute = AsyncMock(side_effect=lambda stmt: captured.setdefault("stmt", stmt))
        mock_db.flush = AsyncMock()

        engine = TrustEngine.__new__(TrustEngine)
        engine._db = mock_db
        engine._workspace_id = "ws_test"

        await TrustEngine.set_ceiling(engine, "email.send", "trusted")

        assert mock_db.execute.await_count == 1
        sql = _compile(captured["stmt"]).upper()
        assert "INSERT INTO TRUST_CEILINGS" in sql
        assert "ON CONFLICT" in sql
        assert "UQ_TRUST_CEILING" in sql


class TestUserSettingsUpsert:
    """``SettingsService.set`` upserts via the ``ix_user_settings_unique`` index."""

    @pytest.mark.asyncio
    async def test_uses_on_conflict_do_update(self):
        from src.services.settings_service import SettingsService

        mock_db = MagicMock()
        captured: dict = {}
        mock_db.execute = AsyncMock(side_effect=lambda stmt: captured.setdefault("stmt", stmt))
        mock_db.flush = AsyncMock()

        svc = SettingsService.__new__(SettingsService)
        svc._db = mock_db

        await SettingsService.set(
            svc,
            user_id="usr_test",
            category="notification",
            key="digest_cadence",
            value={"frequency": "daily"},
        )

        assert mock_db.execute.await_count == 1
        sql = _compile(captured["stmt"]).upper()
        assert "INSERT INTO USER_SETTINGS" in sql
        assert "ON CONFLICT" in sql
        assert "DO UPDATE" in sql


class TestBuildCursorUpsertStmt:
    """``ConnectorPoller.build_cursor_upsert_stmt`` is a pure builder."""

    def test_returns_correct_sql_shape(self):
        """The builder returns an INSERT … ON CONFLICT DO UPDATE statement
        that targets ``uq_cursor_ws_user_source`` and includes workspace_id."""
        from src.orchestrator.connector_poller import ConnectorPoller

        stmt = ConnectorPoller.build_cursor_upsert_stmt(
            source="gmail",
            user_id="usr_test",
            workspace_id="ws_test",
            new_cursor="hist_42",
            cursor_type="history_id",
        )
        sql = _compile(stmt).upper()
        assert "INSERT INTO OBSERVATION_CURSORS" in sql
        assert "ON CONFLICT" in sql
        assert "DO UPDATE" in sql
        assert "UQ_CURSOR_WS_USER_SOURCE" in sql
        assert "WORKSPACE_ID" in sql

    def test_builder_is_deterministic(self):
        """Two calls with the same args produce structurally identical SQL
        (cursor_id differs via ULID but the rest of the shape is stable)."""
        from src.orchestrator.connector_poller import ConnectorPoller

        def shape(stmt) -> str:
            """Strip the ULID-containing cursor_id value before comparing."""
            import re

            sql = _compile(stmt).upper()
            # Remove the cursor_id literal so ULIDs don't break equality
            return re.sub(r"'CUR_[A-Z0-9]+'", "'CUR_PLACEHOLDER'", sql)

        s1 = ConnectorPoller.build_cursor_upsert_stmt(
            "gmail", "usr_x", "ws_x", "cursor_1", "opaque"
        )
        s2 = ConnectorPoller.build_cursor_upsert_stmt(
            "gmail", "usr_x", "ws_x", "cursor_1", "opaque"
        )
        assert shape(s1) == shape(s2)


def _make_ingest_mocks():
    """Return a wired-up (poller, db, db_factory) triple for ingest_raw_events tests.

    ``poller`` is a bare ``ConnectorPoller`` (connector I/O moved off PerceptionRunner).
    ``_db_factory`` is a property, so we inject via the provider. ``ensure_event_bus``
    lives on the injected EventPublisher collaborator.
    """
    from src.orchestrator.connector_poller import ConnectorPoller

    captured_stmts: list = []

    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.execute = AsyncMock(side_effect=lambda stmt: captured_stmts.append(stmt))
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.add = MagicMock()

    mock_factory = MagicMock(return_value=mock_db)

    poller = ConnectorPoller.__new__(ConnectorPoller)
    poller._db_factory_provider = lambda: mock_factory
    poller._settings = make_mock_settings()
    poller._events = MagicMock()
    poller._events.ensure_event_bus = AsyncMock(return_value=MagicMock())

    return poller, mock_db, mock_factory, captured_stmts


class TestAtomicIngestCursorAdvance:
    """P4: cursor upsert shares the ingestion unit of work.

    Goal: the cursor is not advanced unless the event loop ran to completion.
    EventProcessor commits per-event internally; the cursor upsert is executed
    on the same session after the loop and committed by the trailing commit.
    """

    @pytest.mark.asyncio
    async def test_successful_ingest_advances_cursor_in_same_session(self):
        """(a) Non-empty poll: ingest + cursor upsert both happen inside the
        SAME db session (same mock_db.execute).  ``_update_cursor``'s own
        db_factory is NOT called on the non-empty path."""
        from src.orchestrator.connector_poller import ConnectorPoller

        poller, mock_db, mock_factory, captured_stmts = _make_ingest_mocks()

        # Stub out the collaborators that _ingest_raw_events calls internally.
        # EventProcessor / DeadLetterService are imported locally inside the
        # method, so we must patch them at their source modules.
        mock_processor = MagicMock()
        mock_processor.process = AsyncMock(return_value="evt_001")

        mock_req = MagicMock()
        mock_req.world_model = MagicMock()
        mock_req.memory_service = MagicMock()
        mock_req.notifier = MagicMock()
        mock_req.vector_store = MagicMock()
        mock_req.extras = {}

        with (
            patch.object(poller, "_request_services", return_value=mock_req),
            patch("src.services.event_processor.EventProcessor", return_value=mock_processor),
            patch("src.services.dead_letter.DeadLetterService"),
        ):
            raw = make_raw_event()
            await ConnectorPoller.ingest_raw_events(
                poller,
                [raw],
                TEST_USER_ID,
                TEST_WORKSPACE_ID,
                source="gmail",
                new_cursor="hist_99",
                cursor_type="history_id",
            )

        # db_factory was called ONCE (the ingest session — not a second time for the cursor)
        assert mock_factory.call_count == 1, (
            "cursor advance must share the ingest session, not open a new one"
        )

        # The cursor upsert was issued on the shared db session
        assert len(captured_stmts) == 1, "expected exactly one execute() call (the cursor upsert)"
        sql = _compile(captured_stmts[0]).upper()
        assert "INSERT INTO OBSERVATION_CURSORS" in sql
        assert "ON CONFLICT" in sql
        assert "UQ_CURSOR_WS_USER_SOURCE" in sql

        # Trailing commit after the loop (EventProcessor per-event commits are
        # on the same mock session but the final commit is the one at the end
        # of _ingest_raw_events; here the mock captures all awaited calls)
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ingest_failure_before_loop_does_not_advance_cursor(self):
        """(b) If the session construction / EventProcessor init raises before
        the event loop, the cursor must NOT advance (no execute, no commit)."""
        from src.orchestrator.connector_poller import ConnectorPoller

        poller, mock_db, mock_factory, captured_stmts = _make_ingest_mocks()

        # Make _request_services explode — simulates a pre-loop failure
        with patch.object(poller, "_request_services", side_effect=RuntimeError("db setup failed")):
            with pytest.raises(RuntimeError, match="db setup failed"):
                await ConnectorPoller.ingest_raw_events(
                    poller,
                    [make_raw_event()],
                    TEST_USER_ID,
                    TEST_WORKSPACE_ID,
                    source="gmail",
                    new_cursor="hist_99",
                    cursor_type="history_id",
                )

        # No cursor write should have happened
        assert len(captured_stmts) == 0, "cursor must not advance if pre-loop setup raises"
        mock_db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_poll_path_still_advances_cursor(self):
        """(c) Empty-poll path: ``_update_cursor`` is called (separate session)
        and still issues the ON CONFLICT DO UPDATE for the cursor."""
        from src.orchestrator.connector_poller import ConnectorPoller

        poller, mock_db, mock_factory, captured_stmts = _make_ingest_mocks()

        await ConnectorPoller.update_cursor(
            poller,
            source="gmail",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            new_cursor="hist_77",
            cursor_type="history_id",
        )

        assert mock_factory.call_count == 1
        assert len(captured_stmts) == 1
        sql = _compile(captured_stmts[0]).upper()
        assert "INSERT INTO OBSERVATION_CURSORS" in sql
        assert "ON CONFLICT" in sql
        assert "UQ_CURSOR_WS_USER_SOURCE" in sql
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_per_event_failure_sends_to_dlq_cursor_still_advances(self):
        """(d) Per-event failure path: failed events land in DLQ but the loop
        completes and the cursor IS advanced on the shared session."""
        from src.orchestrator.connector_poller import ConnectorPoller

        poller, mock_db, mock_factory, captured_stmts = _make_ingest_mocks()

        # EventProcessor.process raises for every event
        mock_processor = MagicMock()
        mock_processor.process = AsyncMock(side_effect=Exception("score api down"))

        mock_dlq = MagicMock()
        mock_dlq.enqueue = AsyncMock()

        mock_req = MagicMock()
        mock_req.world_model = MagicMock()
        mock_req.memory_service = MagicMock()
        mock_req.notifier = MagicMock()
        mock_req.vector_store = MagicMock()
        mock_req.extras = {}

        with (
            patch.object(poller, "_request_services", return_value=mock_req),
            patch("src.services.event_processor.EventProcessor", return_value=mock_processor),
            patch("src.services.dead_letter.DeadLetterService", return_value=mock_dlq),
        ):
            summaries = await ConnectorPoller.ingest_raw_events(
                poller,
                [make_raw_event()],
                TEST_USER_ID,
                TEST_WORKSPACE_ID,
                source="gmail",
                new_cursor="hist_55",
                cursor_type="history_id",
            )

        # Summary reflects the failure
        assert len(summaries) == 1
        assert "ingest error" in summaries[0]

        # DLQ enqueue was attempted
        mock_dlq.enqueue.assert_awaited_once()

        # Cursor upsert was still executed (loop completed, cursor advances)
        assert len(captured_stmts) == 1
        sql = _compile(captured_stmts[0]).upper()
        assert "INSERT INTO OBSERVATION_CURSORS" in sql

        # Single trailing commit
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ingest_without_cursor_params_does_not_execute_cursor_upsert(self):
        """Backward-compat: calling _ingest_raw_events without new_cursor leaves
        the cursor table untouched (no spurious execute calls)."""
        from src.orchestrator.connector_poller import ConnectorPoller

        poller, mock_db, mock_factory, captured_stmts = _make_ingest_mocks()

        mock_processor = MagicMock()
        mock_processor.process = AsyncMock(return_value="evt_002")

        mock_req = MagicMock()
        mock_req.world_model = MagicMock()
        mock_req.memory_service = MagicMock()
        mock_req.notifier = MagicMock()
        mock_req.vector_store = MagicMock()
        mock_req.extras = {}

        with (
            patch.object(poller, "_request_services", return_value=mock_req),
            patch("src.services.event_processor.EventProcessor", return_value=mock_processor),
            patch("src.services.dead_letter.DeadLetterService"),
        ):
            await ConnectorPoller.ingest_raw_events(
                poller,
                [make_raw_event()],
                TEST_USER_ID,
                TEST_WORKSPACE_ID,
                # no source / new_cursor passed
            )

        # No cursor statement should have been executed
        assert len(captured_stmts) == 0, "no cursor write expected when new_cursor is not given"
        # But commit still fires for the event loop
        mock_db.commit.assert_awaited_once()
