import os

os.environ["HRSWI_DATABASE_URL"] = os.getenv(
    "HRSWI_TEST_DATABASE_URL", "postgresql+psycopg://hrswi:hrswi@localhost:5432/hrswi_test"
)
os.environ.setdefault("HRSWI_ENV", "test")

from datetime import date

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic import command
from app.core.db import SessionLocal, engine

AS_OF = date(2026, 10, 31)
PASSWORD = "Demo!2345"


@pytest.fixture(scope="session", autouse=True)
def database():
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")  # migrations are exercised on every test run
    from app.seed.synthetic import generate
    from tests.factories import build_exact_tenant

    with SessionLocal() as db:
        demo = generate(db, employees=220, subcontractors=5, months=26, as_of=AS_OF, seed=7, tenant_code="DEMO")
    with SessionLocal() as db:
        exact = build_exact_tenant(db)
    yield {"demo": demo, "exact": exact}


@pytest.fixture(scope="session")
def ids(database):
    return database


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="session")
def client():
    from app.main import app

    return TestClient(app)


_tokens: dict[str, str] = {}


@pytest.fixture(scope="session")
def auth(client):
    def _h(email: str) -> dict:
        if email not in _tokens:
            r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
            assert r.status_code == 200, r.text
            _tokens[email] = r.json()["access_token"]
        return {"Authorization": f"Bearer {_tokens[email]}"}

    return _h
