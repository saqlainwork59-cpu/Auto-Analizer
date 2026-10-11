"""Alembic environment: uses DATABASE_URL from app settings (async engine)."""
import asyncio
from logging.config import fileConfig

from alembic import context
from app import models  # noqa: F401 - register tables
from app.config import get_settings
from app.db import Base, init_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=get_settings().database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = init_engine()  # same driver options as the app (e.g. PgBouncer-safe statement cache)
    async with engine.connect() as conn:
        await conn.run_sync(do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
