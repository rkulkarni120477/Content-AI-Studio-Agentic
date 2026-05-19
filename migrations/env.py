"""
Alembic migration environment.

This file is run by Alembic for every migration command
(alembic upgrade, alembic revision --autogenerate, etc.).

How model discovery works
--------------------------
Alembic needs to import every ORM model class before it can detect
schema changes via autogenerate.  We import the entire promptops_app.database
module here because it contains all 20+ models in a single file.
When models are eventually split into app/models/, update the import below.

Usage
-----
    # Generate a new migration from model changes:
    alembic revision --autogenerate -m "add new column"

    # Apply all pending migrations:
    alembic upgrade head

    # Roll back the last migration:
    alembic downgrade -1

    # Show current database revision:
    alembic current
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the project root importable so app and promptops_app are found.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import all ORM models so Alembic can detect them for autogenerate.
# This triggers the entire model registry in database.py.
import promptops_app.database  # noqa: F401  — side effect: registers all models

from app.core.config import settings
from app.core.database import Base

# Alembic config object — provides access to alembic.ini values.
config = context.config

# Wire up Python logging from alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata object Alembic uses to compare against the live DB schema.
# Base.metadata contains all tables registered via DeclarativeBase.
target_metadata = Base.metadata


def get_url() -> str:
    """Return the database URL from application settings (never hardcode here)."""
    return settings.database_url


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    Generates SQL scripts without connecting to the database.
    Useful for reviewing migrations before applying them.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,        # detect column type changes
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode (connects to the live database).

    This is the standard mode for development and production deployment.
    """
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # use NullPool in migration scripts — no connection reuse
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
