import threading

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config.settings import get_settings

_local = threading.local()


def get_engine():
    engine = getattr(_local, "engine", None)
    if engine is None:
        settings = get_settings()
        engine = create_async_engine(settings.database_url, echo=settings.debug, pool_size=10)
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
        yield session
