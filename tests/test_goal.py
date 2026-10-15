"""The current goal on the admin's welcome page (/admin/goal), against
PostgreSQL like the other database tests. The row is restored afterwards."""

import os

import pytest
from api.dependencies.auth import get_current_admin
from db.dependency import get_async_db
from db.models.admin_setting import AdminSetting
from db.session import Base, async_url
from fastapi.testclient import TestClient
from main import get_application
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/test_db"
)


@pytest.fixture
def factory():
    """Sync sessions, to set the table up and clean it."""
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        db.query(AdminSetting).filter(AdminSetting.key == "goal").delete()
        db.commit()
    yield factory
    with factory() as db:
        db.query(AdminSetting).filter(AdminSetting.key == "goal").delete()
        db.commit()


@pytest.fixture
def client(factory):
    # NullPool: TestClient runs each request in an event loop of its own, and
    # a pooled connection cannot be reused from another loop.
    engine = create_async_engine(async_url(DATABASE_URL), poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_async_db():
        async with sessions() as db:
            yield db

    app = get_application()
    app.dependency_overrides[get_async_db] = override_get_async_db
    app.dependency_overrides[get_current_admin] = lambda: "owner@example.com"
    return TestClient(app)


def test_there_is_no_goal_at_first(client):
    assert client.get("/api/v1/admin/goal").json()["data"] == {
        "text": "",
        "updatedAt": None,
    }


def test_a_goal_is_kept_and_replaced(client):
    client.put("/api/v1/admin/goal", json={"text": "  拿到 offer  "})
    first = client.get("/api/v1/admin/goal").json()["data"]
    client.put("/api/v1/admin/goal", json={"text": "Ship the thesis"})
    second = client.get("/api/v1/admin/goal").json()["data"]

    assert first["text"] == "拿到 offer"
    assert first["updatedAt"] is not None
    assert second["text"] == "Ship the thesis"


def test_an_empty_goal_clears_it(client):
    client.put("/api/v1/admin/goal", json={"text": "Something"})
    client.put("/api/v1/admin/goal", json={"text": ""})
    assert client.get("/api/v1/admin/goal").json()["data"]["text"] == ""


def test_a_goal_is_a_few_words(client):
    assert client.put("/api/v1/admin/goal", json={"text": "x" * 121}).status_code == 422


def test_only_the_admin_has_it(client):
    client.app.dependency_overrides.pop(get_current_admin)
    assert client.get("/api/v1/admin/goal").status_code == 401
