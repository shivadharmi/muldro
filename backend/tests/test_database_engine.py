"""Engine-level connection self-protection (idle/statement timeouts).

Every asyncpg connection must carry server_settings that bound how long it may
sit idle-in-transaction or run a single statement, so a leaked transaction can
never freeze the worker indefinitely (env-agnostic backstop).
"""

from unittest.mock import MagicMock, patch

from src.models import database


def _fresh_engine(monkeypatch_settings):
    """Build an engine with a clean thread-local, capturing create_async_engine."""
    # Reset the thread-local so get_engine rebuilds.
    database._local = type(database._local)()

    captured = {}

    def fake_create_async_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return MagicMock()

    with patch.object(database, "create_async_engine", fake_create_async_engine):
        with patch.object(database, "get_settings", return_value=monkeypatch_settings):
            database.get_engine()
    return captured


def test_engine_sets_idle_and_statement_timeouts():
    settings = MagicMock()
    settings.database_url = "postgresql+asyncpg://test:test@localhost/test"
    # Production defaults: 900s idle (must exceed the 600s background-run cap so
    # GraphExecutor's long-lived session is never killed mid-DAG) / 120s statement.
    settings.db_idle_in_transaction_timeout_ms = 900_000
    settings.db_statement_timeout_ms = 120_000

    captured = _fresh_engine(settings)

    connect_args = captured["kwargs"].get("connect_args", {})
    server_settings = connect_args.get("server_settings", {})
    assert server_settings.get("idle_in_transaction_session_timeout") == "900000"
    assert server_settings.get("statement_timeout") == "120000"


def test_settings_default_idle_timeout_exceeds_run_cap():
    """The shipped default idle ceiling must exceed the 600s background-run cap
    so the executor's long-lived idle-in-transaction session is never reaped
    by Postgres mid-run."""
    from src.config.settings import Settings

    s = Settings()
    assert s.db_idle_in_transaction_timeout_ms == 900_000
    assert s.db_idle_in_transaction_timeout_ms > 600_000
    assert s.db_statement_timeout_ms == 120_000


def test_engine_timeouts_overridable_via_settings():
    settings = MagicMock()
    settings.database_url = "postgresql+asyncpg://test:test@localhost/test"
    settings.db_idle_in_transaction_timeout_ms = 5_000
    settings.db_statement_timeout_ms = 9_000

    captured = _fresh_engine(settings)

    server_settings = captured["kwargs"]["connect_args"]["server_settings"]
    assert server_settings["idle_in_transaction_session_timeout"] == "5000"
    assert server_settings["statement_timeout"] == "9000"
