import os

import pytest
from core import config
from db.session import async_url
from fakeredis import FakeAsyncRedis
from redis.asyncio import Redis
from services import admin_session
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

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
    points at one, as in CI. That database is flushed.
    """
    url = os.environ.get("REDIS_TEST_URL")
    client = Redis.from_url(url) if url else FakeAsyncRedis()
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
def async_sessions():
    """
    Async sessions against DATABASE_URL, like db.session.AsyncSessionLocal.
    NullPool, as TestClient (or asyncio.run) runs in an event loop of its own
    and a pooled connection cannot be reused from another loop.
    """
    engine = create_async_engine(async_url(DATABASE_URL), poolclass=NullPool)
    return async_sessionmaker(engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def async_db(async_sessions):
    """
    An override for get_async_db:
    app.dependency_overrides[get_async_db] = async_db.
    """

    async def override():
        async with async_sessions() as db:
            yield db

    return override
