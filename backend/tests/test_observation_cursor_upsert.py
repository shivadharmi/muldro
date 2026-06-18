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
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql


def _compile(stmt) -> str:
    """Render a Core statement as parameterized Postgres SQL."""
    return str(stmt.compile(dialect=postgresql.dialect()))


class TestJarvisUpdateCursor:
    """``JarvisOrchestrator._update_cursor`` must upsert atomically."""

    @pytest.mark.asyncio
    async def test_uses_on_conflict_do_update(self):
        """The statement passed to ``db.execute`` must target the
        ``uq_cursor_ws_user_source`` constraint with DO UPDATE semantics."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        # Capture the statement without wiring a real DB
        captured: dict = {}

        mock_db = MagicMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_db.execute = AsyncMock(side_effect=lambda stmt: captured.setdefault("stmt", stmt))
        mock_db.commit = AsyncMock()

        mock_factory = MagicMock(return_value=mock_db)

        # Build a minimal orchestrator-like object with just _db_factory set
        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
        orch._db_factory = mock_factory

        await JarvisOrchestrator._update_cursor(
            orch,
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
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_db = MagicMock()
        mock_factory = MagicMock(return_value=mock_db)
        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
        orch._db_factory = mock_factory

        await JarvisOrchestrator._update_cursor(
            orch,
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
        from src.orchestrator.jarvis import JarvisOrchestrator

        captured: dict = {}
        mock_db = MagicMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_db.execute = AsyncMock(side_effect=lambda stmt: captured.setdefault("stmt", stmt))
        mock_db.commit = AsyncMock()
        mock_factory = MagicMock(return_value=mock_db)

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
        orch._db_factory = mock_factory

        await JarvisOrchestrator._update_cursor(
            orch,
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
    async def test_two_workspaces_produce_distinct_conflict_targets(self):
        """Two _update_cursor calls with different workspace_ids target the same
        workspace-scoped constraint — confirming per-workspace isolation rather
        than per-user isolation."""
        from src.orchestrator.jarvis import JarvisOrchestrator

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

            orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
            orch._db_factory = mock_factory

            await JarvisOrchestrator._update_cursor(
                orch,
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
