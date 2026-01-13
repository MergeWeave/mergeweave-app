"""
Alembic Migration Environment

Configures Alembic for async migrations with SQLAlchemy 2.0.
"""

import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

# Import all models to ensure they're registered with Base.metadata
from app.models.base import Base
from app.models.user import User
from app.models.installation import GitHubInstallation
from app.models.repository import GitHubRepository
from app.models.branch import GitHubBranch
from app.models.conflict import ConflictDetection
from app.models.resolution import ResolutionResult, ResolutionStatus
from app.models.resolution_package import ResolutionPackage
from app.config import get_config

# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set target metadata for autogenerate support
target_metadata = Base.metadata

# Get database URL from app config
app_config = get_config()
config.set_main_option("sqlalchemy.url", app_config.database_url)


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine.
    Calls to context.execute() here emit the given string to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        version_table=config.get_main_option("version_table"),
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Run migrations with given connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        version_table=config.get_main_option("version_table"),
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in async mode."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.

    In this scenario we need to create an Engine and associate a connection with the context.
    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
