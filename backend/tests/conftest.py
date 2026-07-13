"""
Test Configuration — shared fixtures for the entire test suite.

WHY CONFTEST:
  pytest automatically discovers conftest.py files and makes their fixtures
  available to all tests in the same directory (and subdirectories).
  This means you define your TestClient ONCE and every test file can use it
  without importing anything.

WHAT HAPPENS HERE:
  1. We override the DATABASE_URL to use an in-memory SQLite database
     so tests never touch your real data.
  2. We create all tables fresh for each test session.
  3. We create a test user and generate a valid JWT token.
  4. We provide a `client` fixture that has the auth header pre-set.
"""

import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Override settings BEFORE importing the app (os env wins over .env).
os.environ["DATABASE_URL"] = "sqlite:///./test_gateway.db"
# Pin auth ON so the gateway-key tests are deterministic regardless of .env.
os.environ["REQUIRE_GATEWAY_AUTH"] = "true"

from app.main import app
from app.core.database import Base, get_db

# ── Test Database Setup ─────────────────────────────────
TEST_DATABASE_URL = "sqlite:///./test_gateway.db"
test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Create all tables once for the entire test session."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    # Clean up test database file
    if os.path.exists("./test_gateway.db"):
        try:
            os.remove("./test_gateway.db")
        except PermissionError:
            pass


@pytest.fixture(scope="session")
def client():
    """
    A plain TestClient for the gateway.

    The native-UI JWT auth was removed in the single-model consolidation; the /v1
    endpoints use a gateway API key (open by default in tests, since
    REQUIRE_GATEWAY_AUTH defaults to False).

    Usage in tests:
      def test_something(client):
          response = client.get("/health")
          assert response.status_code == 200
    """
    with TestClient(app) as c:
        yield c
