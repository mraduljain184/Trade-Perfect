"""Shared test setup.

Tests run against a SEPARATE database (<your db name>_test) so they never touch demo data.
It is created automatically, rebuilt with Alembic and seeded once per test run.
"""
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "Test-Pass#2026"


def _test_database_url() -> str:
    if os.environ.get("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    from app.core.config import Settings

    url = make_url(Settings().database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


TEST_DB_URL = _test_database_url()
# Must happen BEFORE anything imports app.core.database (it builds its engine at import time).
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["SEED_DEFAULT_PASSWORD"] = TEST_PASSWORD
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-1234")

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()


def _create_database_if_missing() -> None:
    url = make_url(TEST_DB_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database})
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database():
    """Fresh schema (downgrade -> upgrade, which also tests the migration) + seed, once per run."""
    from alembic import command
    from alembic.config import Config

    _create_database_if_missing()
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["db_url"] = TEST_DB_URL
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    from app.core.database import SessionLocal, engine
    from scripts.seed import run_seed

    with SessionLocal() as db:
        run_seed(db, password=TEST_PASSWORD)
    yield
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _reports_in_tmp(tmp_path_factory):
    """Rejection reports and audit PDFs written by tests go to a temp folder, never into backend/reports."""
    from app.services import upload_service

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(upload_service, "REPORT_DIR", tmp_path_factory.mktemp("reports"))
        yield


@pytest.fixture()
def db():
    from app.core.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(scope="session")
def client(_database):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def login(client):
    """login("priya.sharma") -> {"Authorization": "Bearer ..."}  (tokens are cached per user)."""
    cache: dict[str, dict] = {}

    def _login(username: str) -> dict:
        if username not in cache:
            response = client.post("/api/v1/auth/login", json={"username": username, "password": TEST_PASSWORD})
            assert response.status_code == 200, response.text
            cache[username] = {"Authorization": f"Bearer {response.json()['accessToken']}"}
        return cache[username]

    return _login


# One representative user per role.
ROLE_USERS = {
    "TRADER": "priya.sharma",
    "OPS_USER": "ops.user1",
    "OPS_MANAGER": "ops.manager",
    "ML_ADMIN": "ml.admin",
    "SYS_ADMIN": "sys.admin",
}


def assert_error_shape(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert isinstance(body["error"]["fields"], list)
    return body["error"]
