import threading

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config.settings import get_settings

_local = threading.local()


def get_engine():
    engine = getattr(_local, "engine", None)
    if engine is None:
        settings = get_settings()
        # Connection self-protection: bound idle-in-transaction and single
        # statement duration on EVERY connection via asyncpg server_settings.
        # A leaked transaction (e.g. a perception tick holding a row lock) is
        # then reaped by Postgres itself, an env-agnostic backstop independent
        # of any application-level timeout. Values are settings-overridable;
        # the defaults (900s idle / 120s statement) are sane for this workload.
        # The 900s idle ceiling must exceed the longest legitimate idle window:
        # GraphExecutor holds one session idle-in-transaction across a whole DAG
        # while the agent loop runs on separate sessions, and a background run is
        # capped at 600s. A smaller ceiling would kill the executor's connection
        # mid-run. See settings.db_idle_in_transaction_timeout_ms.
        idle_ms = getattr(settings, "db_idle_in_transaction_timeout_ms", 900_000)
        statement_ms = getattr(settings, "db_statement_timeout_ms", 120_000)
        engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_size=10,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            connect_args={
                "server_settings": {
                    "idle_in_transaction_session_timeout": str(idle_ms),
                    "statement_timeout": str(statement_ms),
                }
            },
        )
        _local.engine = engine
    return engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    factory = getattr(_local, "session_factory", None)
    if factory is None:
        factory = async_sessionmaker(get_engine(), expire_on_commit=False)
        _local.session_factory = factory
    return factory


async def get_db() -> AsyncSession:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
