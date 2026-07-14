"""
Test fixtures — a REAL POSTGRES, in docker, per test session.

WHY NOT SQLITE (it used to be): the guarantees this app depends on are Postgres
features — composite FKs that block cross-user key use, GENERATED columns that
cannot drift, triggers that maintain is_common/is_reachable, and the is_callable()
function. SQLite has none of them.

Testing on SQLite would mean the suite exercises a DIFFERENT schema than
production: a test could happily insert Alice's deployment using Bob's key and
PASS, while prod rejects it. A test that cannot fail the way prod fails is worse
than no test.

The database is built by applying schema.sql — the same file production uses.
"""

import os
import subprocess
import time
import uuid

import pytest

PG_IMAGE = "postgres:16-alpine"
PG_PORT = 55555
PG_URL = f"postgresql+psycopg2://postgres:postgres@localhost:{PG_PORT}/gateway_test"

os.environ["DATABASE_URL"] = PG_URL
os.environ["REQUIRE_GATEWAY_AUTH"] = "true"   # pin auth ON so RBAC tests are real
os.environ["DEBUG"] = "false"


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True)


@pytest.fixture(scope="session", autouse=True)
def postgres():
    """Start a throwaway Postgres, apply schema.sql, tear it down after."""
    if _docker("info").returncode != 0:
        pytest.skip("Docker is not available; these tests need a real Postgres.")

    name = f"gateway_test_{uuid.uuid4().hex[:8]}"
    _docker(
        "run", "--rm", "-d", "--name", name,
        "-e", "POSTGRES_PASSWORD=postgres",
        "-e", "POSTGRES_DB=gateway_test",
        "-p", f"{PG_PORT}:5432",
        PG_IMAGE,
    )
    try:
        for _ in range(60):
            if _docker("exec", name, "pg_isready", "-U", "postgres").returncode == 0:
                break
            time.sleep(1)
        else:
            pytest.fail("Postgres did not become ready in time.")

        from app.core.database import init_schema
        init_schema()
        yield PG_URL
    finally:
        _docker("rm", "-f", name)


@pytest.fixture(scope="session")
def app_client(postgres):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def db(postgres):
    from app.core.database import SessionLocal
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(scope="session")
def admin_key(postgres, db):
    """The bootstrap admin and a live gateway key for them."""
    from app.models.user import User
    from app.services import gateway_keys

    user = db.query(User).filter(User.is_admin.is_(True)).first()
    if not user:
        user = User(email="admin@test", is_admin=True)
        db.add(user)
        db.commit()
        db.refresh(user)
    token, _ = gateway_keys.mint(db, user.id, name="test-admin")
    return token


@pytest.fixture(scope="session")
def user_key(postgres, db):
    """A NON-admin user and their gateway key — used to prove RBAC actually bites."""
    from app.models.user import User
    from app.services import gateway_keys

    user = User(email="user@test", is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    token, _ = gateway_keys.mint(db, user.id, name="test-user")
    return token
