import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

# Ensure the backend directory is on sys.path so `src` is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from src.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from environment if available (.env or systemd)
env_db_url = os.environ.get("MULDRO_DATABASE_URL")
if env_db_url:
    config.set_main_option("sqlalchemy.url", env_db_url)

target_metadata = Base.metadata

# LangGraph's AsyncPostgresSaver manages its own checkpoint tables directly (via
# saver.setup(), outside Alembic — they appear once the Step-1 spike or the
# Step-10 autonomous cutover has run). Exclude them from autogenerate so
# `alembic check` / autogenerate never try to DROP the durable-execution
# substrate.
_LANGGRAPH_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


def _include_object(object, name, type_, reflected, compare_to):
    if type_ == "table" and name in _LANGGRAPH_TABLES:
        return False
    if type_ == "index":
        table = getattr(object, "table", None)
        if table is not None and table.name in _LANGGRAPH_TABLES:
            return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
