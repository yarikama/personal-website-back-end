import os

import pytest
from fakeredis import FakeAsyncRedis
from redis.asyncio import Redis
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from core import config
from db.session import Base, psycopg_url
from services import admin_session

ADMIN = "owner@example.com"
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/test_db"
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def redis():
    """
    fakeredis (with real Lua) by default; a real Redis when REDIS_TEST_URL
    points at one, as in CI. That database is flushed. Replies are str, as
    core.events.connect_redis makes them.
    """
    url = os.environ.get("REDIS_TEST_URL")
    client = (
        Redis.from_url(url, decode_responses=True)
        if url
        else FakeAsyncRedis(decode_responses=True)
    )
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def sign_in(redis, monkeypatch):
    """
    Signs the admin in: returns the headers a page of the site sends, the
    Cookie of a new session in `redis` and the site's Origin (needed on
    anything but GET). The app under test must use that Redis
    (app.state.redis = redis).
    """
    monkeypatch.setattr(config, "ADMIN_EMAILS", frozenset({ADMIN}))

    async def cookie(email: str = ADMIN) -> dict[str, str]:
        token = await admin_session.create(redis, email, 3600)
        return {
            "Cookie": f"{admin_session.COOKIE}={token}",
            "Origin": "https://yarikama.com",
        }

    return cookie


@pytest.fixture
def sync_sessions():
    """
    Sync sessions against DATABASE_URL, for a test to add its rows and clean
    them up without an event loop. The tables are created if missing (of the
    models imported so far).
    """
    engine = create_engine(psycopg_url(DATABASE_URL))
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine, autoflush=False)
    engine.dispose()


@pytest.fixture
def async_sessions():
    """
    Async sessions against DATABASE_URL, like db.session.SessionLocal.
    NullPool, as TestClient (or asyncio.run) runs in an event loop of its own
    and a pooled connection cannot be reused from another loop.
    """
    engine = create_async_engine(psycopg_url(DATABASE_URL), poolclass=NullPool)
    return async_sessionmaker(engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def db_override(async_sessions):
    """
    The app's database session in a test:
    app.dependency_overrides[get_db] = db_override.
    """

    async def override():
        async with async_sessions() as db:
            yield db

    return override
