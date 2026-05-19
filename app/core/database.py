"""
Database engine, session factory, and base model setup.

This module is the single place responsible for:
  - Creating the SQLAlchemy engine from the DATABASE_URL setting.
  - Providing the ``SessionLocal`` factory used by ``get_db()`` dependency.
  - Exporting the ``Base`` declarative base that all ORM models inherit from.

All ORM models are defined in ``app/models/`` and imported by Alembic via
``app/models/__init__.py``.

Usage
-----
    # In a repository function:
    from app.core.database import SessionLocal

    # Via FastAPI dependency injection (preferred):
    from app.core.dependencies import get_db
    def my_endpoint(db: Session = Depends(get_db)): ...
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQLAlchemy engine
# ---------------------------------------------------------------------------

engine = create_engine(
    settings.database_url,
    # Pool settings appropriate for a FastAPI app with async request handling.
    # Each uvicorn worker thread gets its own connection from the pool.
    pool_size=10,           # number of persistent connections
    max_overflow=20,        # extra connections allowed under load
    pool_pre_ping=True,     # test connection health before use (handles RDS failovers)
    pool_recycle=3600,      # recycle connections after 1 hour to avoid stale state
    echo=False,             # set True temporarily to log all SQL (never in production)
)


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

# autocommit=False : we control commits explicitly in service/repo layers.
# autoflush=False  : we flush manually before queries that need fresh data.
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy ORM models.

    All models in ``app/models/`` must inherit from this class so that
    Alembic can discover them for migration generation.
    """
    pass


# ---------------------------------------------------------------------------
# Health check helper
# ---------------------------------------------------------------------------

def check_database_connection() -> bool:
    """
    Verify that the database is reachable.

    Used by the ``/api/v1/health`` endpoint and the startup lifespan hook.
    Returns True on success, False on any connection error.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        _log.error("database_connection_failed  error=%s", exc)
        return False
