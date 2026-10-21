"""
Sign in with Google, and the admin session it starts. Google is not
called: the code-for-token trade is replaced by one that returns an ID
token made here.
"""

import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from api.dependencies import CurrentAdmin
from api.routes import auth
from core import config
from main import get_application
from services import admin_session, google_oauth

pytestmark = pytest.mark.anyio

CLIENT_ID = "test-client.apps.googleusercontent.com"
OWNER = "owner@example.com"
SITE = "https://site.example"


def id_token(**claims):
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": int(time.time()) + 300,
        "email": OWNER,
        "email_verified": True,
        "sub": "1234",
    }
    payload.update(claims)
    return jwt.encode(payload, "google-signs-these-not-us-0123456789", "HS256")


@pytest.fixture(autouse=True)
def google(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(config, "ADMIN_EMAILS", frozenset({OWNER}))
    monkeypatch.setattr(config, "SITE_URL", SITE)


class Google:
    """Stands in for Google's token endpoint: answers with an ID token
    for whichever nonce the sign-in sent, with any claims overridden."""

    def __init__(self) -> None:
        self.claims: dict[str, Any] = {}
        self.codes: list[tuple[str, str]] = []
        # Set by token_endpoint from the state the sign-in kept.
        self.nonce = ""

    async def exchange(self, client, code, verifier, http):
        self.codes.append((code, verifier))
        return id_token(**{"nonce": self.nonce, **self.claims})


@pytest.fixture
def token_endpoint(monkeypatch, redis):
    fake = Google()
    take_state = google_oauth.take_state

    async def remember_nonce(redis_, state):
        kept = await take_state(redis_, state)
        fake.nonce = kept["nonce"]
        return kept

    monkeypatch.setattr(google_oauth, "take_state", remember_nonce)
    monkeypatch.setattr(google_oauth, "exchange_code", fake.exchange)
    return fake


@pytest.fixture
def app(redis):
    application = get_application()
    application.state.redis = redis
    # An admin route that changes something, to check the cookie's rules.
    router = APIRouter()

    @router.post("/api/v1/admin/test-write")
    async def write(admin: CurrentAdmin):
        return {"admin": admin}

    application.include_router(router)
    return application


@pytest.fixture
async def client(app):
    # https: the cookies are Secure.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        yield client


async def start(client, next_path=None):
    params = {"next": next_path} if next_path else {}
    response = await client.get("/api/v1/auth/google/login", params=params)
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    return response, parse_qs(location.query)


async def sign_in(client, next_path=None):
    _, query = await start(client, next_path)
    return await client.get(
        "/api/v1/auth/google/callback",
        params={"code": "the-code", "state": query["state"][0]},
    )


def login_error(response):
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"{SITE}/admin/login?error=")
    return parse_qs(urlsplit(location).query)["error"][0]


# Starting


async def test_start_sends_the_browser_to_google_with_pkce(client):
    response, query = await start(client)

    assert response.headers["location"].startswith(google_oauth.AUTHORIZE_URL)
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [config.GOOGLE_REDIRECT_URI]
    assert query["scope"] == ["openid email"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    assert query["nonce"][0]
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{auth.STATE_COOKIE}={query['state'][0]};")
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert attribute in cookie


async def test_start_without_configuration_goes_back_to_the_login_page(
    client, monkeypatch
):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "")
    response = await client.get("/api/v1/auth/google/login")
    assert login_error(response) == "not_configured"


# Finishing


async def test_signing_in_starts_a_session(client, token_endpoint):
    response = await sign_in(client, "/admin/notes")

    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/admin/notes"
    session = next(
        c
        for c in response.headers.get_list("set-cookie")
        if c.startswith(admin_session.COOKIE)
    )
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert attribute in session
    assert "Domain" not in session  # __Host- cookies are for this host only
    # The verifier Google got is the one behind the challenge it was shown.
    assert token_endpoint.codes[0][0] == "the-code"

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json() == {"email": OWNER}


async def test_the_session_is_kept_hashed_in_redis(client, token_endpoint, redis):
    await sign_in(client)
    token = client.cookies[admin_session.COOKIE]

    keys = await redis.keys("admin:session:*")
    assert keys == [admin_session.key(token)]
    assert token not in keys[0]
    assert 0 < await redis.ttl(keys[0]) <= config.ADMIN_SESSION_HOURS * 3600


@pytest.mark.parametrize(
    "next_path",
    [
        "//evil.example",
        "https://evil.example",
        "/admin/../notes",
        "/notes",
        "/admin\\x",
    ],
)
async def test_only_admin_pages_are_returned_to(client, token_endpoint, next_path):
    response = await sign_in(client, next_path)
    assert response.headers["location"] == f"{SITE}/admin"


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"email": "stranger@example.com"}, "not_allowed"),
        ({"email_verified": False}, "not_allowed"),
        ({"aud": "another-client"}, "failed"),
        ({"iss": "https://evil.example"}, "failed"),
        ({"exp": int(time.time()) - 600}, "failed"),
        ({"nonce": "replayed"}, "failed"),
    ],
)
async def test_a_refused_account_gets_no_session(
    client, token_endpoint, claims, reason
):
    token_endpoint.claims = claims

    response = await sign_in(client)

    assert login_error(response) == reason
    assert admin_session.COOKIE not in client.cookies
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_the_email_is_compared_without_case(client, token_endpoint):
    token_endpoint.claims = {"email": "Owner@Example.com"}
    response = await sign_in(client)
    assert response.headers["location"] == f"{SITE}/admin"


async def test_a_callback_from_another_browser_is_refused(client, token_endpoint):
    _, query = await start(client)
    client.cookies.delete(auth.STATE_COOKIE)

    response = await client.get(
        "/api/v1/auth/google/callback",
        params={"code": "the-code", "state": query["state"][0]},
    )

    assert login_error(response) == "expired"
    assert token_endpoint.codes == []


async def test_a_state_works_once(client, token_endpoint):
    _, query = await start(client)
    params = {"code": "the-code", "state": query["state"][0]}
    await client.get("/api/v1/auth/google/callback", params=params)
    client.cookies.delete(admin_session.COOKIE)
    client.cookies.set(auth.STATE_COOKIE, query["state"][0], domain="test.local")

    response = await client.get("/api/v1/auth/google/callback", params=params)

    assert login_error(response) == "expired"


async def test_cancelling_at_google_goes_back_to_the_login_page(client):
    _, query = await start(client)
    response = await client.get(
        "/api/v1/auth/google/callback",
        params={"error": "access_denied", "state": query["state"][0]},
    )
    assert login_error(response) == "cancelled"


# Using the session


async def test_a_change_needs_the_sites_own_origin(client, token_endpoint):
    await sign_in(client)

    without = await client.post("/api/v1/admin/test-write")
    foreign = await client.post(
        "/api/v1/admin/test-write", headers={"Origin": "https://evil.example"}
    )
    own = await client.post(
        "/api/v1/admin/test-write", headers={"Origin": "https://www.yarikama.com"}
    )

    assert (without.status_code, foreign.status_code) == (401, 401)
    assert own.status_code == 200
    assert own.json() == {"admin": OWNER}


async def test_signing_out_ends_the_session(client, token_endpoint, redis):
    await sign_in(client)
    token = client.cookies[admin_session.COOKIE]

    response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    assert await redis.exists(admin_session.key(token)) == 0
    # Even a copy of the cookie no longer works.
    me = await client.get(
        "/api/v1/auth/me", headers={"Cookie": f"{admin_session.COOKIE}={token}"}
    )
    assert me.status_code == 401


async def test_an_account_removed_from_the_list_is_out_at_once(
    client, token_endpoint, monkeypatch
):
    await sign_in(client)
    monkeypatch.setattr(config, "ADMIN_EMAILS", frozenset({"someone@example.com"}))
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_a_made_up_cookie_is_nobody(client):
    me = await client.get(
        "/api/v1/auth/me", headers={"Cookie": f"{admin_session.COOKIE}=guess"}
    )
    assert me.status_code == 401


async def test_redis_down_means_unavailable_not_signed_in(client, app):
    class Down:
        async def get(self, key):
            raise ConnectionError("redis is down")

    app.state.redis = Down()
    me = await client.get(
        "/api/v1/auth/me", headers={"Cookie": f"{admin_session.COOKIE}=anything"}
    )
    assert me.status_code == 503


async def test_redis_down_at_the_start_means_unavailable(client, monkeypatch):
    async def down(*args, **kwargs):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(google_oauth, "start", down)
    response = await client.get("/api/v1/auth/google/login")
    assert login_error(response) == "unavailable"


async def test_a_callback_without_configuration_goes_back_to_the_login_page(
    client, token_endpoint, monkeypatch
):
    _, query = await start(client)
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "")
    response = await client.get(
        "/api/v1/auth/google/callback",
        params={"code": "the-code", "state": query["state"][0]},
    )
    assert login_error(response) == "not_configured"


async def test_a_failure_while_finishing_means_unavailable(
    client, token_endpoint, monkeypatch
):
    async def broken(*args, **kwargs):
        raise RuntimeError("Google answered something unexpected")

    monkeypatch.setattr(google_oauth, "exchange_code", broken)
    response = await sign_in(client)
    assert login_error(response) == "unavailable"
    assert admin_session.COOKIE not in client.cookies


async def test_signing_out_clears_the_cookie_even_if_redis_is_down(
    client, token_endpoint, app
):
    await sign_in(client)

    class Down:
        async def delete(self, key):
            raise ConnectionError("redis is down")

    app.state.redis = Down()
    response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 204
    assert admin_session.COOKIE not in client.cookies


async def test_signing_out_without_a_session_is_fine(client):
    response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 204
