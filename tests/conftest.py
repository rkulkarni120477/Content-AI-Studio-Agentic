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

# Point at SQLite in-memory for tests — never touch the real PostgreSQL DB.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests-only")
os.environ.setdefault("APP_ENV", "development")

from app.core.database import Base
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

@pytest.fixture(scope="session", autouse=True)
def _setup_test_db():
    """Create all tables once per test session, then drop them at teardown."""
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
    """
    from app.core.security import hash_password
    from promptops_app.database import User

    user = User(
        username="test_admin",
        password=hash_password("test_password"),
        role="admin",
        is_active=True,
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
        password=hash_password("test_password"),
        role="author",
        is_active=True,
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
        json={"username": "test_admin", "password": "test_password"},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def author_headers(client, author_user) -> dict:
    """Return Authorization headers for the test author user."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "test_author", "password": "test_password"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


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
