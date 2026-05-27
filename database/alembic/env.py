import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make the project root importable from within the alembic/ directory.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Import Base and all models so their metadata is registered before
# autogenerate inspects it.  Import order matters: models must be imported
# before Base.metadata is passed to Alembic.
from database.connection import Base  # noqa: E402
from database.models import User, UserVocabulary, Word  # noqa: F401, E402

# Alembic Config object — gives access to values in alembic.ini.
config = context.config

# Override the sqlalchemy.url from the environment variable if present,
# so CI/CD and Docker environments don't need to edit alembic.ini.
db_url = os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))
# asyncpg driver is required at runtime, but Alembic's sync autogenerate
# needs a sync-compatible URL for the `run_migrations_offline` path.
sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
config.set_main_option("sqlalchemy.url", sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection needed)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode using the async engine."""
    # Re-use the async URL for the actual connection.
    async_url = db_url
    connectable = async_engine_from_config(
        {"sqlalchemy.url": async_url, "sqlalchemy.poolclass": pool.NullPool},
        prefix="sqlalchemy.",
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
