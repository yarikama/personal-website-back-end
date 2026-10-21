import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import Secret

from core import events
from main import get_application
from services.rate_limit import RateLimiter


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_lifespan_without_redis_leaves_requests_unlimited(monkeypatch):
    monkeypatch.setattr(events, "REDIS_URL", Secret(""))
    app = FastAPI()

    async with events.lifespan(app):
        assert getattr(app.state, "rate_limiter", None) is None


@pytest.mark.anyio
async def test_lifespan_with_redis_sets_up_the_rate_limiter(monkeypatch):
    # Connecting is lazy: nothing listens here, and nothing needs to.
    monkeypatch.setattr(events, "REDIS_URL", Secret("redis://127.0.0.1:1/0"))
    app = FastAPI()

    async with events.lifespan(app):
        assert isinstance(app.state.rate_limiter, RateLimiter)


def test_get_application():
    app = get_application()
    assert isinstance(app, FastAPI)


def test_health_returns_ok():
    client = TestClient(get_application())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_answers_head():
    client = TestClient(get_application())

    assert client.head("/health").status_code == 200


def test_the_old_ml_predictor_is_gone():
    client = TestClient(get_application())

    assert client.post("/api/v1/predict", json={}).status_code == 404
    assert client.get("/api/v1/health").status_code == 404
