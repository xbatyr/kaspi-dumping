"""Alembic environment: migrations run against DATABASE_URL."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from repricer.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
if not database_url:
    raise RuntimeError("set DATABASE_URL (or sqlalchemy.url in alembic.ini) before migrating")
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Catches a Numeric(12, 2) that silently became Numeric() in a model.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
