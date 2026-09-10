"""
Shared pytest fixtures for unit and integration tests.

Design rules
------------
1. Unit tests (tests/unit/) never touch a real database.
   Services and repositories receive a MagicMock() as their ``db`` argument.

2. Integration tests (tests/integration/) use a real SQLite in-memory database
   so the full FastAPI→service→repository→DB stack is exercised.
   Only the LLM calls are mocked (to avoid API costs and flakiness).

3. The test client is created fresh for each test function to prevent
   state leakage between tests.

Fixtures available
------------------
  db              SQLite in-memory Session (integration tests)
  client          FastAPI TestClient with overridden get_db
  auth_headers    Authorization header for a pre-created admin user
  mock_llm        Monkeypatch that makes llm_service return a fixed string
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Point at SQLite in-memory for tests — never touch the real PostgreSQL DB.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests-only")
os.environ.setdefault("APP_ENV", "development")

# Point away from DIS for tests — never reach a real Source Library backend.
#
# Without this the suite inherits DIS_API_BASE_URL from the developer's .env,
# which is the Compose service hostname (http://dis_backend:8000/v1). Tests run
# outside the container, so every DIS call spent ~12s failing to resolve that
# name, times three connect retries, times each call a test makes. One 8-test
# file cost 442s and the full suite could not finish inside 15 minutes.
#
# dis_enabled=False makes DISClient.request_sync short-circuit to the same empty
# result the failure path already produced ("" context, no source units), so the
# observable behaviour of every caller is unchanged — only the wait disappears.
# setdefault, not assignment: exporting DIS_ENABLED=true still lets someone run
# a deliberate live-DIS check against a reachable backend.
os.environ.setdefault("DIS_ENABLED", "false")

# NOTE: models live on promptops_app.database's Base — app.core.database's
# Base is an empty DeclarativeBase; create_all on it would create no tables.
from promptops_app.database import Base
from app.core.dependencies import get_db
from app.main import app


# ---------------------------------------------------------------------------
# In-memory SQLite engine and session factory
# ---------------------------------------------------------------------------

_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    # SQLite-specific: allow the same connection to be used across threads
    # (needed because FastAPI runs route handlers in threads).
    connect_args={"check_same_thread": False},
    # An in-memory SQLite DB exists per-connection; without StaticPool every
    # new pooled connection would see a fresh, EMPTY database (tables created
    # on one connection are invisible on the next). StaticPool pins a single
    # shared connection so all sessions/threads see the same schema + data.
    poolclass=StaticPool,
)

_TestSessionLocal = sessionmaker(
    bind=_TEST_ENGINE,
    autocommit=False,
    autoflush=False,
)


def _create_test_tables() -> None:
    """Create all tables in the test database from the ORM metadata."""
    # Import database.py so all models are registered on Base.metadata.
    import promptops_app.database  # noqa: F401
    Base.metadata.create_all(bind=_TEST_ENGINE)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _setup_test_db():
    """Create all tables before each test and drop them after.

    Function-scoped (not session-scoped) because fixtures COMMIT rows (users,
    blocks, ...) and the StaticPool engine shares one in-memory DB across the
    whole session — without a per-test rebuild, committed rows leak between
    tests (e.g. UNIQUE username collisions). create_all/drop_all on in-memory
    SQLite is fast enough that per-test isolation costs almost nothing.
    """
    _create_test_tables()
    yield
    Base.metadata.drop_all(bind=_TEST_ENGINE)


@pytest.fixture()
def db():
    """
    Yield a fresh SQLAlchemy session for each test.

    Rolls back all changes at the end of the test so tests are isolated.
    Use this fixture directly in integration tests or pass it to services.
    """
    session = _TestSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client(db):
    """
    Yield a FastAPI TestClient with the database dependency overridden.

    All requests made via this client use the in-memory SQLite database
    instead of the real PostgreSQL instance.
    """
    def _override_get_db():
        try:
            yield db
        finally:
            pass  # session lifecycle managed by the db fixture

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def admin_user(db):
    """
    Create and return a test admin user in the test database.

    Used by auth_headers and any test that needs a real user record.

    Platform admin (is_platform_admin=True) so login doesn't need an
    organization_code — but that also means tenant_scope_condition returns
    "no restriction" for this identity, so the ~60+ tests sharing this
    fixture never exercise tenant boundaries. That's intentional: they
    predate multi-tenancy and mostly assert platform-wide behavior (e.g.
    test_projects.py's "admin sees all active projects"). Tests that need to
    verify real cross-tenant isolation use their own dedicated
    non-platform-admin fixtures instead (see two_tenants in
    test_prompt_library_tenant_isolation.py) rather than this one.
    """
    from app.core.security import hash_password
    from promptops_app.database import User

    user = User(
        username="test_admin",
        password_hash=hash_password("test_password"),
        role="admin",
        is_active=True,
        is_platform_admin=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def author_user(db):
    """Create and return a test author (ID) user."""
    from app.core.security import hash_password
    from promptops_app.database import User

    user = User(
        username="test_author",
        password_hash=hash_password("test_password"),
        role="author",
        is_active=True,
        is_platform_admin=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def auth_headers(client, admin_user) -> dict:
    """
    Return Authorization headers for the test admin user.

    Logs in via the real /auth/login endpoint so the full auth pipeline
    is exercised (password hashing, JWT creation, etc.).
    """
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "test_admin", "password": "test_password", "platform_admin": True},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def author_headers(client, author_user) -> dict:
    """Return Authorization headers for the test author user."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "test_author", "password": "test_password", "platform_admin": True},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _tenant_headers(db, *, username: str, project_id=None, role: str = "admin", is_platform_admin: bool = False):
    """Build auth headers for a fabricated user, without a real login round-trip.

    Shared by any tenant-isolation test that needs two or more distinct
    tenants at once (auth_headers/author_headers above are single-tenant,
    platform-admin-only). See ``two_tenants`` below.
    """
    from app.core.security import create_access_token, hash_password
    from promptops_app.database import TenantMembership, User

    user = User(
        username=username, password_hash=hash_password("test_password"),
        role=role, is_active=True, is_platform_admin=is_platform_admin, project_id=project_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    if not is_platform_admin and project_id is not None:
        db.add(TenantMembership(user_id=user.id, project_id=project_id, role=role, active=True))
        db.commit()
    token = create_access_token(user.username, role, project_id=project_id, is_platform_admin=is_platform_admin)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def two_tenants(db):
    """Two isolated tenants (projects) plus a genuine platform admin.

    Generic across resource types (prompts, CDDs, blueprints, styles, …) — any
    tenant-isolation test can request this instead of hand-rolling its own
    two-project setup. Returns the two Project rows and three sets of auth
    headers: ``headers_a``/``headers_b`` (tenant-scoped admins) and
    ``headers_platform`` (sees every tenant unless explicitly scoped).
    """
    from promptops_app.database import Project

    a = Project(name="Tenant A", slug="isolation-tenant-a", is_active=True, status="active")
    b = Project(name="Tenant B", slug="isolation-tenant-b", is_active=True, status="active")
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)
    return {
        "a": a, "b": b,
        "headers_a": _tenant_headers(db, username="tenant_a_admin", project_id=a.id),
        "headers_b": _tenant_headers(db, username="tenant_b_admin", project_id=b.id),
        "headers_platform": _tenant_headers(db, username="tenant_platform_admin", is_platform_admin=True),
    }


@pytest.fixture()
def mock_llm(monkeypatch):
    """
    Patch the LLM service to return a fixed string instead of calling the API.

    This prevents real API calls during tests, making tests fast, free,
    and deterministic regardless of network availability.

    The fixed response is valid Markdown that the CDD/Blueprint parsers
    can process without errors.
    """
    fixed_response = (
        "## Course Details\n\nThis is a test course about clinical nursing.\n\n"
        "## Course Structure\n\nModule 1: Introduction\nModule 2: Advanced Topics\n\n"
        "## Course Level Assessment\n\nFinal exam covering all modules."
    )

    from unittest.mock import MagicMock

    mock_result = MagicMock()
    mock_result.text = fixed_response
    mock_result.status = "success"
    mock_result.model = "gpt-4o-test"
    mock_result.prompt_tokens = 100
    mock_result.completion_tokens = 200
    mock_result.error_type = None

    monkeypatch.setattr(
        "promptops_app.services.llm_service.generate_with_metadata",
        lambda *args, **kwargs: mock_result,
    )
    monkeypatch.setattr(
        "promptops_app.services.llm_service.generate_text",
        lambda *args, **kwargs: fixed_response,
    )
    return fixed_response
