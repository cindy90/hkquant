"""Alembic migration environment for HK IPO Cornerstone Agent.

Resolves DB URL from `hk_ipo_agent.common.settings.Settings` so the same
.env / YAML / env-var precedence applies as for the runtime app.

Imports all v1.0 ORM models so ``autogenerate`` sees the full schema.
v1.1 / v1.2 / v1.2.1 models will be added in their respective phases per
ADR 0006 (then their imports go here too).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import the metadata via the project package so all v1.0 tables are registered.
from hk_ipo_agent.common.settings import get_settings
from hk_ipo_agent.data.models import Base
from hk_ipo_agent.data.models import metadata as target_metadata

# this is the Alembic Config object
config = context.config

# Override sqlalchemy.url with the resolved sync (psycopg) URL from Settings.
_settings = get_settings()
config.set_main_option("sqlalchemy.url", _settings.database.sync_url)

# Set up loggers
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout, no DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (live DB connection)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
