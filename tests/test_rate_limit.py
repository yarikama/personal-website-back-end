"""
Rate limits. The Lua scripts run in fakeredis (with real Lua) by default,
and against a real Redis when REDIS_TEST_URL points at one (CI does).
"""

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from db.dependency import get_db
from main import get_application
from services.rate_limit import (
    CONTACT,
    PUBLIC,
    Algorithm,
    RateLimiter,
    Rule,
    client_id,
)

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def limiter(redis, clock):
    return RateLimiter(redis, clock=clock)


SLIDING = Rule("test-sliding", 3, 60, Algorithm.SLIDING_LOG, fail_open=True)
BUCKET = Rule("test-bucket", 3, 60, Algorithm.TOKEN_BUCKET, fail_open=True)


async def test_sliding_log_allows_the_limit_then_rejects(limiter, clock):
    remaining = []
    for _ in range(3):
        decision = await limiter.hit(SLIDING, "a")
        assert decision.allowed
        remaining.append(decision.remaining)
        clock.now += 10

    rejected = await limiter.hit(SLIDING, "a")

    assert remaining == [2, 1, 0]
    assert not rejected.allowed
    # The first request (at 0 s) leaves the window at 60 s; it is now 30 s.
    assert rejected.retry_after == 30


async def test_sliding_log_window_slides_instead_of_resetting(limiter, clock):
    for _ in range(3):
        await limiter.hit(SLIDING, "a")
        clock.now += 10
    clock.now = 1_000_000.0 + 60  # the first request just left the window

    assert (await limiter.hit(SLIDING, "a")).allowed
    # The second and third are still in it.
    assert not (await limiter.hit(SLIDING, "a")).allowed


async def test_rejected_requests_do_not_extend_the_wait(limiter, clock):
    for _ in range(3):
        await limiter.hit(SLIDING, "a")
    for _ in range(10):
        clock.now += 5
        await limiter.hit(SLIDING, "a")
    clock.now = 1_000_000.0 + 60

    assert (await limiter.hit(SLIDING, "a")).allowed


async def test_token_bucket_allows_a_burst_then_refills_steadily(limiter, clock):
    burst = [(await limiter.hit(BUCKET, "a")).allowed for _ in range(4)]
    rejected = await limiter.hit(BUCKET, "a")
    clock.now += 20  # 3 per 60 s: one token every 20 s

    assert burst == [True, True, True, False]
    assert rejected.retry_after == 20
    assert (await limiter.hit(BUCKET, "a")).allowed
    assert not (await limiter.hit(BUCKET, "a")).allowed


async def test_token_bucket_never_holds_more_than_its_capacity(limiter, clock):
    await limiter.hit(BUCKET, "a")
    clock.now += 3600

    burst = [(await limiter.hit(BUCKET, "a")).allowed for _ in range(4)]

    assert burst == [True, True, True, False]


@pytest.mark.parametrize("rule", [SLIDING, BUCKET], ids=lambda rule: rule.name)
async def test_clients_have_separate_budgets(limiter, rule):
    for _ in range(3):
        await limiter.hit(rule, "a")

    assert not (await limiter.hit(rule, "a")).allowed
    assert (await limiter.hit(rule, "b")).allowed


@pytest.mark.parametrize("rule", [SLIDING, BUCKET], ids=lambda rule: rule.name)
async def test_concurrent_requests_cannot_share_the_last_slot(limiter, rule):
    decisions = await asyncio.gather(*(limiter.hit(rule, "a") for _ in range(20)))

    assert sum(decision.allowed for decision in decisions) == 3


@pytest.mark.parametrize("rule", [SLIDING, BUCKET], ids=lambda rule: rule.name)
async def test_redis_clock_is_used_by_default(redis, rule):
    limiter = RateLimiter(redis)

    decisions = [(await limiter.hit(rule, "a")).allowed for _ in range(4)]

    assert decisions == [True, True, True, False]


async def test_keys_expire_after_the_window(limiter, redis):
    await limiter.hit(SLIDING, "a")
    await limiter.hit(BUCKET, "a")

    for rule in (SLIDING, BUCKET):
        ttl = await redis.pttl(RateLimiter.key(rule, "a"))
        assert 0 < ttl <= 60_000


async def test_reset_forgets_a_client(limiter):
    for _ in range(3):
        await limiter.hit(SLIDING, "a")
    await limiter.reset(SLIDING, "a")

    assert (await limiter.hit(SLIDING, "a")).allowed


async def test_unreachable_redis_fails_open_or_closed_per_rule():
    from services.rate_limit import LimiterUnavailableError

    down = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.25)
    limiter = RateLimiter(down)

    closed = Rule("test-closed", 3, 60, Algorithm.SLIDING_LOG, fail_open=False)

    assert (await limiter.hit(CONTACT, "a")).allowed
    with pytest.raises(LimiterUnavailableError):
        await limiter.hit(closed, "a")
    await limiter.reset(closed, "a")  # does not raise
    await down.aclose()


@pytest.mark.parametrize(
    ("header", "peer", "expected"),
    [
        ("203.0.113.9", "10.42.0.7", "203.0.113.9"),
        (None, "198.51.100.4", "198.51.100.4"),
        ("2001:db8:1:2:3:4:5:6", "10.42.0.7", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:ffff::1", "10.42.0.7", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.9", None, "203.0.113.9"),
        (" 203.0.113.9 ", None, "203.0.113.9"),
        ("not-an-ip", None, "not-an-ip"),
        (None, None, "unknown"),
    ],
)
def test_client_id(header, peer, expected):
    headers = {"cf-connecting-ip": header} if header else {}

    assert client_id(headers, peer) == expected


# Through the API


@pytest.fixture
def app(limiter):
    application = get_application()
    application.state.rate_limiter = limiter
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class FakeSession:
    def add(self, contact):
        self.contact = contact

    async def commit(self):
        self.contact.id = uuid.uuid4()

    async def refresh(self, contact):
        pass


MESSAGE = {
    "name": "Visitor",
    "email": "visitor@example.com",
    "subject": "Hello there",
    "message": "Just saying hello.",
}


async def test_contact_form_takes_three_messages_an_hour(app, client):
    app.dependency_overrides[get_db] = FakeSession

    codes = [
        (await client.post("/api/v1/contact", json=MESSAGE)).status_code
        for _ in range(4)
    ]

    assert codes == [201, 201, 201, 429]


async def test_public_requests_share_a_bucket(app, client):
    app.dependency_overrides[get_db] = FakeSession
    for _ in range(PUBLIC.limit):
        await client.post("/api/v1/contact", json={})  # 422, still counted

    response = await client.get(
        "/api/v1/lab-notes", headers={"Origin": "https://yarikama.com"}
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    # Wrapped by CORS, so the page can read the status and the wait.
    assert response.headers["access-control-allow-origin"] == "https://yarikama.com"
    assert "retry-after" in response.headers["access-control-expose-headers"].lower()


async def test_admin_routes_and_preflights_are_not_counted(client, redis):
    for _ in range(PUBLIC.limit + 5):
        await client.get("/api/v1/admin/lab-notes")
        await client.options(
            "/api/v1/lab-notes",
            headers={
                "Origin": "https://yarikama.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert await redis.exists(RateLimiter.key(PUBLIC, "127.0.0.1")) == 0


def test_every_counter_series_exists_from_the_start():
    from prometheus_client import REGISTRY

    for rule in ("contact", "public", "ask", "ask_all"):
        for decision in ("allowed", "rejected"):
            labels = {"rule": rule, "decision": decision}
            assert (
                REGISTRY.get_sample_value("rate_limit_decisions_total", labels)
                is not None
            )
