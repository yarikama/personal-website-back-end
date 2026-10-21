"""
The writing MCP server (mcp_server/) and its sign-in (services/mcp_oauth.py),
over HTTP as Claude uses them: /register, /authorize, Sign in with Google
(Google faked as in test_google_login.py), /token, then JSON-RPC on /mcp.
Notes, projects, categories and clients are rows in PostgreSQL like the other
database tests: every note's and project's slug and every category's name
starts with PREFIX, and the MCP clients (a table only these tests use) are
all deleted afterwards.
"""

import base64
import datetime
import hashlib
import io
import re
import secrets
import shlex
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from loguru import logger
from mcp.server.auth.provider import TokenError
from PIL import Image
from sqlalchemy import delete

from core import config
from db.dependency import get_db
from db.models.category import Category
from db.models.lab_notes import LabNote
from db.models.mcp_oauth import McpClient
from db.models.projects import Project
from main import get_application
from mcp_server import server as mcp_server
from services import google_oauth, remote_images, upload_tokens
from services.content.lab_notes import read_time
from services.image_jobs import STREAM, queue_variants
from services.mcp_oauth import WritingOAuth, pending_key, redirect_allowed
from services.storage import storage_service

pytestmark = pytest.mark.anyio

ISSUER = "https://test"
RESOURCE = f"{ISSUER}/mcp"
CLAUDE = "https://claude.ai/api/mcp/auth_callback"
OWNER = "owner@example.com"
GOOGLE_CLIENT_ID = "test-client.apps.googleusercontent.com"
SITE = "https://site.example"
PREFIX = "test-mcp-"


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", GOOGLE_CLIENT_ID)
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(config, "ADMIN_EMAILS", frozenset({OWNER}))
    monkeypatch.setattr(config, "SITE_URL", SITE)
    monkeypatch.setattr(mcp_server, "MCP_ISSUER_URL", ISSUER)


class Google:
    """Google's token endpoint: an ID token for `email`, for whichever
    nonce the sign-in sent."""

    def __init__(self) -> None:
        self.email = OWNER
        self.nonce = ""

    async def exchange(self, client, code, verifier, http):
        claims = {
            "iss": "https://accounts.google.com",
            "aud": GOOGLE_CLIENT_ID,
            "exp": int(time.time()) + 300,
            "email": self.email,
            "email_verified": True,
            "nonce": self.nonce,
        }
        return jwt.encode(claims, "google-signs-these-not-us-0123456789", "HS256")


@pytest.fixture
def google(monkeypatch):
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
def tables(sync_sessions):
    def clean():
        with sync_sessions() as db:
            db.execute(delete(LabNote).where(LabNote.slug.startswith(PREFIX)))
            db.execute(delete(Project).where(Project.slug.startswith(PREFIX)))
            db.execute(delete(Category).where(Category.name.startswith(PREFIX)))
            db.execute(delete(McpClient))
            db.commit()

    clean()
    yield
    clean()


@pytest.fixture
def app(redis, async_sessions, db_override, tables, google):
    app = get_application()
    app.state.redis = redis
    # The public routes, to check what visitors see.
    app.dependency_overrides[get_db] = db_override
    mcp_server.mount(app, sessions=async_sessions)
    return app


@pytest.fixture
def provider(app) -> WritingOAuth:
    return app.state.mcp_oauth


@pytest.fixture
async def client(app):
    async with app.state.mcp.session_manager.run():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=ISSUER) as client:
            yield client


# The steps of signing in, as Claude takes them


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def register(client, *redirect_uris):
    return await client.post(
        "/register",
        json={
            "redirect_uris": list(redirect_uris or [CLAUDE]),
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "test",
        },
    )


async def authorize(client, client_id, challenge, **params):
    return await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CLAUDE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "claude-state",
            "resource": RESOURCE,
            **params,
        },
    )


async def consent(client, consent_url, decision="allow"):
    """Answers the consent page /authorize sent the browser to, as the owner
    would: the form's answer."""
    page = await client.get(consent_url)
    assert page.status_code == 200, page.text
    fields = dict(re.findall(r'name="(mcp|token)" value="([^"]*)"', page.text))
    return await client.post(
        "/api/v1/auth/mcp/consent", data={**fields, "decision": decision}
    )


async def allow(client, asked):
    """Allows the request on the consent page: Google sign-in's start."""
    assert asked.status_code == 302, asked.text
    answered = await consent(client, asked.headers["location"])
    assert answered.status_code == 303, answered.text
    return answered.headers["location"]


async def through_google(client, login_url):
    """Follows /authorize's redirect to Google sign-in and back: the
    callback's answer."""
    started = await client.get(login_url)
    assert started.status_code == 303, started.text
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    return await client.get(
        "/api/v1/auth/google/callback",
        params={"code": "google-code", "state": state},
    )


async def code_for(client) -> tuple[str, str, str]:
    """A registered client's id, its PKCE verifier and a fresh code."""
    registered = await register(client)
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]
    verifier, challenge = pkce()
    asked = await authorize(client, client_id, challenge)
    back = await through_google(client, await allow(client, asked))
    assert back.status_code == 303, back.text
    location = back.headers["location"]
    assert location.startswith(f"{CLAUDE}?")
    query = parse_qs(urlsplit(location).query)
    assert query["state"] == ["claude-state"]
    return client_id, verifier, query["code"][0]


async def trade(client, client_id, code, verifier):
    return await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CLAUDE,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
    )


async def refresh(client, client_id, refresh_token):
    return await client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "resource": RESOURCE,
        },
    )


async def signed_in(client) -> dict[str, Any]:
    """Tokens, as Claude holds them after signing in, and its client_id."""
    client_id, verifier, code = await code_for(client)
    tokens = await trade(client, client_id, code, verifier)
    assert tokens.status_code == 200, tokens.text
    return {**tokens.json(), "client_id": client_id}


async def rpc(client, token, method, params=None):
    return await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
    )


@pytest.fixture
async def mcp(client):
    """Calls the MCP server as the signed-in owner: mcp(method, params) is
    the JSON-RPC result."""
    token = (await signed_in(client))["access_token"]

    async def call(method, params=None):
        response = await rpc(client, token, method, params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert "error" not in body, body
        return body["result"]

    return call


@pytest.fixture
def tool(mcp):
    """tool(name, **arguments): the tool's result, or its error message."""

    # Positional-only: a tool may take a `name` of its own (create_category).
    async def call(tool_name, /, **arguments):
        result = await mcp("tools/call", {"name": tool_name, "arguments": arguments})
        if result["isError"]:
            return {"error": result["content"][0]["text"]}
        if "structuredContent" in result:
            content = result["structuredContent"]
            return content.get("result", content)
        return result["content"][0]["text"]

    return call


# Finding the server


async def test_discovery_points_claude_at_this_server(client):
    resource = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert resource["resource"] == RESOURCE
    assert resource["authorization_servers"] == [ISSUER]

    server = (await client.get("/.well-known/oauth-authorization-server")).json()
    # Letter for letter: no trailing slash.
    assert server["issuer"] == ISSUER
    assert server["registration_endpoint"] == f"{ISSUER}/register"
    assert server["code_challenge_methods_supported"] == ["S256"]


async def test_the_server_needs_a_token(client):
    response = await rpc(client, "made-up", "tools/list")
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert f'resource_metadata="{ISSUER}/.well-known/oauth-protected-resource/mcp"' in (
        challenge
    )


async def test_other_paths_still_get_fastapis_404(client):
    response = await client.get("/no-such-page")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


# Signing in


async def test_signing_in_with_google_gives_claude_tokens_that_work(client, mcp):
    tools = await mcp("tools/list")
    names = {tool["name"] for tool in tools["tools"]}
    assert {"create_lab_note", "edit_lab_note_content", "delete_lab_note"} <= names


async def test_the_tokens_are_kept_hashed(client, redis):
    tokens = await signed_in(client)
    keys = [
        key.decode() if isinstance(key, bytes) else key for key in await redis.keys()
    ]
    assert all(tokens["access_token"] not in key for key in keys)
    assert all(tokens["refresh_token"] not in key for key in keys)


@pytest.mark.parametrize(
    "uris",
    [
        ["https://evil.example/callback"],
        [CLAUDE, "https://evil.example/callback"],
        ["https://claude.ai.evil.example/api/mcp/auth_callback"],
        ["http://localhost:3000/elsewhere"],
    ],
)
async def test_codes_can_only_be_sent_to_claude(client, uris):
    response = await register(client, *uris)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


async def test_claude_code_registers_with_its_own_port(client):
    response = await register(client, "http://localhost:51234/callback")
    assert response.status_code == 201


def test_redirect_patterns_stand_for_a_port_only():
    allowed = ("http://localhost:*/callback", CLAUDE)
    assert redirect_allowed("http://localhost:8080/callback", allowed)
    assert redirect_allowed(CLAUDE, allowed)
    assert not redirect_allowed("http://localhost:x/callback", allowed)
    assert not redirect_allowed("http://localhost:1/callback/more", allowed)
    assert not redirect_allowed("http://localhost.evil:1/callback", allowed)


async def test_a_code_works_once(client):
    client_id, verifier, code = await code_for(client)
    assert (await trade(client, client_id, code, verifier)).status_code == 200
    again = await trade(client, client_id, code, verifier)
    assert again.status_code == 400
    assert again.json()["error"] == "invalid_grant"


async def test_a_code_needs_the_verifier_it_was_made_for(client):
    client_id, _, code = await code_for(client)
    wrong, _ = pkce()
    response = await trade(client, client_id, code, wrong)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_another_account_gets_no_code(client, google):
    google.email = "someone@example.com"
    client_id = (await register(client)).json()["client_id"]
    asked = await authorize(client, client_id, pkce()[1])
    back = await through_google(client, await allow(client, asked))
    assert back.status_code == 303
    assert back.headers["location"] == f"{SITE}/admin/login?error=not_allowed"


async def test_tokens_for_another_resource_are_refused(client):
    client_id = (await register(client)).json()["client_id"]
    asked = await authorize(
        client, client_id, pkce()[1], resource="https://elsewhere.example/mcp"
    )
    assert asked.status_code == 302
    query = parse_qs(urlsplit(asked.headers["location"]).query)
    assert asked.headers["location"].startswith(CLAUDE)
    assert query["error"] == ["invalid_target"]


async def test_a_made_up_request_goes_back_to_the_login_page(client):
    response = await client.get(
        "/api/v1/auth/google/login", params={"mcp": "not a request id"}
    )
    assert response.headers["location"] == f"{SITE}/admin/login?error=expired"


async def test_a_request_that_expired_during_sign_in_gets_no_code(client):
    unknown = secrets.token_urlsafe(32)
    back = await through_google(client, f"/api/v1/auth/google/login?mcp={unknown}")
    assert back.headers["location"] == f"{SITE}/admin/login?error=expired"


# The consent page


async def asked_by(client, name="Claude"):
    """A new client's /authorize: the consent page's URL, and the request id."""
    registered = await client.post(
        "/register",
        json={
            "redirect_uris": [CLAUDE],
            "token_endpoint_auth_method": "none",
            "client_name": name,
        },
    )
    asked = await authorize(client, registered.json()["client_id"], pkce()[1])
    assert asked.status_code == 302
    url = asked.headers["location"]
    assert url.startswith(f"{ISSUER}/api/v1/auth/mcp/consent?mcp=")
    return url, parse_qs(urlsplit(url).query)["mcp"][0]


async def test_the_consent_page_names_the_client_and_where_the_code_goes(client):
    url, _ = await asked_by(client, name="<script>alert(1)</script>")
    page = await client.get(url)
    assert page.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert "<script>" not in page.text
    assert CLAUDE in page.text
    assert page.headers["content-security-policy"].startswith("default-src 'none'")
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["cache-control"] == "no-store"


async def test_denying_tells_claude_access_denied(client):
    url, _ = await asked_by(client)
    answered = await consent(client, url, decision="deny")
    assert answered.status_code == 303
    location = answered.headers["location"]
    assert location.startswith(f"{CLAUDE}?")
    query = parse_qs(urlsplit(location).query)
    assert query == {"error": ["access_denied"], "state": ["claude-state"]}


async def test_signing_in_without_consent_gets_no_code(client):
    _, id = await asked_by(client)
    # Straight to Google sign-in with the request's id, past the consent page.
    back = await through_google(client, f"/api/v1/auth/google/login?mcp={id}")
    assert back.headers["location"] == f"{SITE}/admin/login?error=expired"


async def test_a_consent_posted_from_another_site_is_refused(client):
    url, id = await asked_by(client)
    await client.get(url)
    # A form on another site: the SameSite=Strict cookie stays behind, and
    # the page's token is not known there.
    for cookie, token in (
        (None, "guess"),
        (client.cookies["__Host-mcp_consent"], "guess"),
    ):
        client.cookies.clear()
        if cookie:
            client.cookies.set("__Host-mcp_consent", cookie)
        answered = await client.post(
            "/api/v1/auth/mcp/consent",
            data={"mcp": id, "token": token, "decision": "allow"},
        )
        assert answered.headers["location"] == f"{SITE}/admin/login?error=expired"
    back = await through_google(client, f"/api/v1/auth/google/login?mcp={id}")
    assert back.headers["location"] == f"{SITE}/admin/login?error=expired"


@pytest.mark.parametrize("mcp", [None, "not a request id", secrets.token_urlsafe(32)])
async def test_a_consent_page_for_no_request_goes_back_to_the_login_page(client, mcp):
    params = {"mcp": mcp} if mcp else {}
    page = await client.get("/api/v1/auth/mcp/consent", params=params)
    assert page.headers["location"] == f"{SITE}/admin/login?error=expired"


async def test_a_request_is_answered_once(client):
    url, _ = await asked_by(client)
    page = await client.get(url)
    fields = dict(re.findall(r'name="(mcp|token)" value="([^"]*)"', page.text))
    denied = await client.post(
        "/api/v1/auth/mcp/consent", data={**fields, "decision": "deny"}
    )
    assert denied.headers["location"].startswith(CLAUDE)
    # The same form again, with a cookie from a new look at another page.
    await client.get((await asked_by(client))[0])
    fields["token"] = client.cookies["__Host-mcp_consent"]
    allowed = await client.post(
        "/api/v1/auth/mcp/consent", data={**fields, "decision": "allow"}
    )
    assert allowed.headers["location"] == f"{SITE}/admin/login?error=expired"


async def test_the_consent_page_is_off_with_the_server(redis):
    app = get_application()
    app.state.redis = redis
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url=ISSUER) as client:
        page = await client.get(
            "/api/v1/auth/mcp/consent", params={"mcp": secrets.token_urlsafe(32)}
        )
    assert page.headers["location"] == f"{SITE}/admin/login?error=not_configured"


async def test_signing_in_for_claude_starts_no_admin_session(client):
    await code_for(client)
    assert "__Host-admin_session" not in client.cookies


async def test_without_redis_sign_in_is_unavailable(app, client):
    client_id = (await register(client)).json()["client_id"]
    app.state.redis = None
    asked = await authorize(client, client_id, pkce()[1])
    assert asked.status_code == 302
    query = parse_qs(urlsplit(asked.headers["location"]).query)
    assert query["error"] == ["temporarily_unavailable"]


async def test_an_unknown_client_cannot_start_a_sign_in(client):
    asked = await authorize(client, "made-up-client", pkce()[1])
    assert asked.status_code == 400
    assert "location" not in asked.headers


async def test_a_code_for_an_account_removed_meanwhile_is_refused(client, monkeypatch):
    client_id, verifier, code = await code_for(client)
    monkeypatch.setattr(config, "ADMIN_EMAILS", frozenset({"someone@example.com"}))
    response = await trade(client, client_id, code, verifier)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_a_client_deleted_during_sign_in_gets_no_consent_page(
    client, sync_sessions
):
    url, _ = await asked_by(client)
    with sync_sessions() as db:
        db.execute(delete(McpClient))
        db.commit()
    page = await client.get(url)
    assert page.headers["location"] == f"{SITE}/admin/login?error=expired"


async def test_turning_the_server_off_during_sign_in_ends_it(app, client):
    # One request allowed and on its way to Google, one at the consent page.
    login = await allow(client, await authorize(client, *await new_request(client)))
    url, _ = await asked_by(client)
    page = await client.get(url)
    fields = dict(re.findall(r'name="(mcp|token)" value="([^"]*)"', page.text))
    app.state.mcp_oauth = None

    answered = await client.post(
        "/api/v1/auth/mcp/consent", data={**fields, "decision": "allow"}
    )
    assert answered.headers["location"] == f"{SITE}/admin/login?error=not_configured"
    back = await through_google(client, login)
    assert back.headers["location"] == f"{SITE}/admin/login?error=not_configured"


async def new_request(client) -> tuple[str, str]:
    """A registered client's id, and a PKCE challenge for it."""
    client_id = (await register(client)).json()["client_id"]
    return client_id, pkce()[1]


async def test_the_consent_page_fails_closed_when_redis_does(
    client, provider, monkeypatch
):
    url, _ = await asked_by(client)
    page = await client.get(url)
    fields = dict(re.findall(r'name="(mcp|token)" value="([^"]*)"', page.text))

    async def down(*args):
        raise ConnectionError("Redis is down")

    monkeypatch.setattr(provider, "pending", down)
    monkeypatch.setattr(provider, "deny", down)
    shown = await client.get(url)
    assert shown.headers["location"] == f"{SITE}/admin/login?error=unavailable"
    answered = await client.post(
        "/api/v1/auth/mcp/consent", data={**fields, "decision": "deny"}
    )
    assert answered.headers["location"] == f"{SITE}/admin/login?error=unavailable"


async def test_denying_a_request_that_expired_goes_back_to_the_login_page(
    client, redis
):
    url, id = await asked_by(client)
    page = await client.get(url)
    fields = dict(re.findall(r'name="(mcp|token)" value="([^"]*)"', page.text))
    await redis.delete(pending_key(id))
    answered = await client.post(
        "/api/v1/auth/mcp/consent", data={**fields, "decision": "deny"}
    )
    assert answered.headers["location"] == f"{SITE}/admin/login?error=expired"


# Two requests at once: the SDK reads a code or refresh token, then hands it
# over to be traded. Both can read it; only one may trade it.


async def test_of_two_trades_of_one_code_one_wins(client, provider):
    client_id, _, code = await code_for(client)
    registered = await provider.get_client(client_id)
    assert registered is not None
    loaded = await provider.load_authorization_code(registered, code)
    assert loaded is not None
    await provider.exchange_authorization_code(registered, loaded)
    with pytest.raises(TokenError, match="already used"):
        await provider.exchange_authorization_code(registered, loaded)


async def test_of_two_trades_of_one_refresh_token_one_wins(client, provider):
    tokens = await signed_in(client)
    registered = await provider.get_client(tokens["client_id"])
    assert registered is not None
    loaded = await provider.load_refresh_token(registered, tokens["refresh_token"])
    assert loaded is not None
    await provider.exchange_refresh_token(registered, loaded, [])
    with pytest.raises(TokenError, match="already used"):
        await provider.exchange_refresh_token(registered, loaded, [])


async def test_revoking_a_token_twice_does_nothing_more(client, provider):
    tokens = await signed_in(client)
    access = await provider.load_access_token(tokens["access_token"])
    assert access is not None
    await provider.revoke_token(access)
    await provider.revoke_token(access)

    tokens = await signed_in(client)
    registered = await provider.get_client(tokens["client_id"])
    assert registered is not None
    refresh = await provider.load_refresh_token(registered, tokens["refresh_token"])
    assert refresh is not None
    await provider.revoke_token(refresh)
    await provider.revoke_token(refresh)
    assert await provider.load_access_token(tokens["access_token"]) is None


# Tokens


async def test_a_refresh_token_works_once_and_ends_the_old_access_token(client):
    first = await signed_in(client)
    renewed = await refresh(client, first["client_id"], first["refresh_token"])
    assert renewed.status_code == 200, renewed.text
    second = renewed.json()

    assert (await rpc(client, first["access_token"], "tools/list")).status_code == 401
    assert (await rpc(client, second["access_token"], "tools/list")).status_code == 200
    again = await refresh(client, first["client_id"], first["refresh_token"])
    assert again.status_code == 400


async def test_an_account_removed_from_the_list_is_out_at_once(client, monkeypatch):
    tokens = await signed_in(client)
    monkeypatch.setattr(config, "ADMIN_EMAILS", frozenset({"someone@example.com"}))
    assert (await rpc(client, tokens["access_token"], "tools/list")).status_code == 401
    response = await refresh(client, tokens["client_id"], tokens["refresh_token"])
    assert response.status_code == 400


async def test_revoking_either_token_ends_both(client):
    for which in ("access_token", "refresh_token"):
        tokens = await signed_in(client)
        response = await client.post(
            "/revoke",
            # The SDK's form requires client_secret even of a public client
            # (it is typed `str | None` with no default); it is not checked.
            data={
                "token": tokens[which],
                "client_id": tokens["client_id"],
                "client_secret": "",
            },
        )
        assert response.status_code == 200, response.text
        access = await rpc(client, tokens["access_token"], "tools/list")
        assert access.status_code == 401
        renewed = await refresh(client, tokens["client_id"], tokens["refresh_token"])
        assert renewed.status_code == 400


# Lab notes


async def new_note(tool, name, **fields):
    note = await tool(
        "create_lab_note",
        **{
            "title": name.title(),
            "slug": PREFIX + name,
            "excerpt": f"About {name}.",
            "content": f"# {name.title()}\n\nThe body of {name}.",
            "tags": ["Testing"],
            **fields,
        },
    )
    assert "error" not in note, note
    return note


def today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


async def test_a_new_note_is_a_draft_only_the_owner_sees(client, tool):
    note = await new_note(tool, "draft", content="word " * 700)
    assert note["published"] is False
    assert note["read_time"] == "4 min read"
    assert note["date"] == today()

    assert (await client.get(f"/api/v1/lab-notes/{PREFIX}draft")).status_code == 404
    assert (await tool("get_lab_note", id_or_slug=note["id"]))["slug"] == note["slug"]
    by_slug = await tool("get_lab_note", id_or_slug=note["slug"])
    assert by_slug["content"] == "word " * 700


async def test_a_slug_is_lowercase_kebab_case(tool):
    note = await tool(
        "create_lab_note",
        title="Bad",
        slug=f"{PREFIX}Not A Slug",
        excerpt="x",
        content="x",
        tags=["Testing"],
    )
    assert "slug" in note["error"]


async def test_a_taken_slug_is_refused(tool):
    await new_note(tool, "taken")
    again = await tool(
        "create_lab_note",
        title="Again",
        slug=f"{PREFIX}taken",
        excerpt="x",
        content="x",
        tags=["Testing"],
    )
    assert "already exists" in again["error"]


async def test_a_missing_note_is_an_error_claude_can_read(tool):
    missing = await tool("get_lab_note", id_or_slug=f"{PREFIX}missing")
    assert "Lab note not found" in missing["error"]


async def test_an_update_changes_only_the_fields_given(tool):
    note = await new_note(tool, "update")
    updated = await tool(
        "update_lab_note",
        id_or_slug=note["slug"],
        title="New Title",
        content="word " * 400,
    )
    assert updated["title"] == "New Title"
    assert updated["excerpt"] == note["excerpt"]
    assert updated["read_time"] == "2 min read"
    retitled = await tool("update_lab_note", id_or_slug=note["slug"], title="Again")
    assert retitled["read_time"] == updated["read_time"]
    nothing = await tool("update_lab_note", id_or_slug=note["slug"])
    assert "Nothing to change" in nothing["error"]


async def test_an_edit_replaces_one_passage(tool):
    note = await new_note(tool, "edit", content="One cat. Two dogs. Two dogs.")
    edited = await tool(
        "edit_lab_note_content",
        id_or_slug=note["slug"],
        old_text="One cat",
        new_text="Three birds",
    )
    assert edited["content"] == "Three birds. Two dogs. Two dogs."

    twice = await tool(
        "edit_lab_note_content",
        id_or_slug=note["slug"],
        old_text="Two dogs",
        new_text="x",
    )
    assert "appears 2 times" in twice["error"]
    missing = await tool(
        "edit_lab_note_content",
        id_or_slug=note["slug"],
        old_text="fish",
        new_text="x",
    )
    assert "not in the body" in missing["error"]
    empty = await tool(
        "edit_lab_note_content", id_or_slug=note["slug"], old_text="", new_text="x"
    )
    assert "empty" in empty["error"]


async def test_publishing_shows_the_note_and_dates_it_today(client, tool):
    note = await new_note(tool, "publish", date="2020-01-01")
    published = await tool(
        "set_lab_note_published", id_or_slug=note["slug"], published=True
    )
    assert published["published"] is True
    assert published["date"] == today()
    assert (await client.get(f"/api/v1/lab-notes/{PREFIX}publish")).status_code == 200

    hidden = await tool(
        "set_lab_note_published", id_or_slug=note["slug"], published=False
    )
    assert hidden["published"] is False
    assert (await client.get(f"/api/v1/lab-notes/{PREFIX}publish")).status_code == 404


async def test_publishing_can_keep_the_date(tool):
    note = await new_note(tool, "keep-date", date="2020-01-01")
    published = await tool(
        "set_lab_note_published",
        id_or_slug=note["slug"],
        published=True,
        keep_date=True,
    )
    assert published["date"] == "2020-01-01"


async def test_deleting_needs_the_slug_again(tool):
    note = await new_note(tool, "delete")
    refused = await tool(
        "delete_lab_note", id_or_slug=note["id"], confirm_slug="something-else"
    )
    assert "Nothing was deleted" in refused["error"]
    assert (await tool("get_lab_note", id_or_slug=note["slug"]))["id"] == note["id"]

    done = await tool(
        "delete_lab_note", id_or_slug=note["id"], confirm_slug=note["slug"]
    )
    assert done == f"Deleted 'Delete' ({PREFIX}delete)."
    assert "error" in await tool("get_lab_note", id_or_slug=note["slug"])


async def test_the_list_finds_drafts_and_text(tool):
    await new_note(
        tool, "list-draft", content="a rare-word-xyz here", tags=["Draftish"]
    )
    published = await new_note(tool, "list-published", published=True)

    drafts = await tool("list_lab_notes", published=False, search="rare-word-xyz")
    assert [n["slug"] for n in drafts["notes"]] == [f"{PREFIX}list-draft"]
    assert "content" not in drafts["notes"][0]
    live = await tool("list_lab_notes", published=True, tag="Testing")
    assert published["slug"] in [n["slug"] for n in live["notes"]]
    # % is a character to look for, not a wildcard.
    assert (await tool("list_lab_notes", search="%"))["notes"] == []

    tags = {t["tag"]: t["count"] for t in await tool("list_lab_note_tags")}
    assert tags["Draftish"] == 1


async def test_writes_are_logged_with_the_account(tool):
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="INFO")
    try:
        await new_note(tool, "logged")
    finally:
        logger.remove(sink)
    assert any(
        f"MCP create_lab_note by {OWNER}: {PREFIX}logged" in line for line in lines
    )


# The guide, notes as resources, and prompts


async def test_the_writing_guide_is_a_resource(mcp):
    result = await mcp("resources/read", {"uri": "writing://guide"})
    assert result["contents"][0]["text"].startswith("# Writing lab notes")


async def test_a_note_is_a_resource(tool, mcp):
    note = await new_note(tool, "resource")
    result = await mcp("resources/read", {"uri": f"notes://{note['slug']}"})
    assert result["contents"][0]["text"] == note["content"]


async def test_the_draft_prompt_carries_the_guide_and_the_tags(tool, mcp):
    await new_note(tool, "prompt", tags=["PromptTag"])
    result = await mcp(
        "prompts/get",
        {"name": "draft_lab_note", "arguments": {"topic": "GPUs", "material": "8 GB"}},
    )
    text = result["messages"][0]["content"]["text"]
    assert "# Writing lab notes" in text
    assert "PromptTag" in text
    assert "Draft a lab note about: GPUs" in text
    assert "8 GB" in text


async def test_the_review_prompt_carries_the_note(tool, mcp):
    note = await new_note(tool, "review")
    result = await mcp(
        "prompts/get", {"name": "review_lab_note", "arguments": {"slug": note["slug"]}}
    )
    text = result["messages"][0]["content"]["text"]
    assert note["content"] in text
    assert "draft" in text


async def test_a_missing_note_is_neither_a_resource_nor_reviewed(client):
    token = (await signed_in(client))["access_token"]
    read = await rpc(client, token, "resources/read", {"uri": f"notes://{PREFIX}none"})
    assert "error" in read.json()
    review = await rpc(
        client,
        token,
        "prompts/get",
        {"name": "review_lab_note", "arguments": {"slug": f"{PREFIX}none"}},
    )
    assert "error" in review.json()


# Projects and categories


async def new_category(tool, name, **fields):
    category = await tool(
        "create_category", name=PREFIX + name, label=name.title(), **fields
    )
    assert "error" not in category, category
    return category


async def new_project(tool, name, category, **fields):
    project = await tool(
        "create_project",
        **{
            "title": name.title(),
            "slug": PREFIX + name,
            "description": f"About {name}.",
            "category": category,
            "tags": ["Testing"],
            "year": "2026",
            **fields,
        },
    )
    assert "error" not in project, project
    return project


async def test_a_new_project_is_unpublished_and_filed_by_category_name(client, tool):
    await new_category(tool, "ml")
    project = await new_project(tool, "model", f"{PREFIX}ml", featured=True)

    assert project["published"] is False
    assert project["category"]["name"] == f"{PREFIX}ml"
    assert project["featured"] is True
    assert (await client.get(f"/api/v1/projects/{PREFIX}model")).status_code == 404
    by_id = await tool("get_project", id_or_slug=project["id"])
    assert by_id["slug"] == project["slug"]


async def test_a_project_needs_a_category_that_exists(tool):
    missing = await tool(
        "create_project",
        title="X",
        slug=f"{PREFIX}x",
        description="x",
        category=f"{PREFIX}nowhere",
        tags=[],
        year="2026",
    )
    assert "not found" in missing["error"]


async def test_the_list_filters_by_category_and_state(tool):
    await new_category(tool, "a")
    await new_category(tool, "b")
    hidden = await new_project(tool, "hidden", f"{PREFIX}a")
    shown = await new_project(tool, "shown", f"{PREFIX}b", published=True)

    in_a = await tool("list_projects", category=f"{PREFIX}a")
    assert [p["slug"] for p in in_a["projects"]] == [hidden["slug"]]
    live = await tool("list_projects", published=True, category=f"{PREFIX}b")
    assert [p["slug"] for p in live["projects"]] == [shown["slug"]]
    nowhere = await tool("list_projects", category=f"{PREFIX}nowhere")
    assert "not found" in nowhere["error"]


async def test_a_project_update_changes_only_the_fields_given(tool):
    await new_category(tool, "first")
    second = await new_category(tool, "second")
    project = await new_project(tool, "moving", f"{PREFIX}first")

    moved = await tool(
        "update_project",
        id_or_slug=project["slug"],
        category=second["id"],
        metrics="10× less compute · 0.43 ms per image",
    )
    assert moved["category"]["name"] == f"{PREFIX}second"
    assert moved["metrics"] == "10× less compute · 0.43 ms per image"
    assert moved["title"] == project["title"]
    nothing = await tool("update_project", id_or_slug=project["slug"])
    assert "Nothing to change" in nothing["error"]


async def test_publishing_keeps_the_category(client, tool):
    await new_category(tool, "kept")
    project = await new_project(tool, "kept", f"{PREFIX}kept")

    published = await tool(
        "set_project_published", id_or_slug=project["slug"], published=True
    )
    assert published["published"] is True
    assert published["category"]["name"] == f"{PREFIX}kept"
    assert (await client.get(f"/api/v1/projects/{PREFIX}kept")).status_code == 200

    hidden = await tool(
        "set_project_published", id_or_slug=project["slug"], published=False
    )
    assert hidden["published"] is False


async def test_deleting_a_project_needs_the_slug_again(tool):
    await new_category(tool, "gone")
    project = await new_project(tool, "gone", f"{PREFIX}gone")
    refused = await tool(
        "delete_project", id_or_slug=project["id"], confirm_slug="other"
    )
    assert "Nothing was deleted" in refused["error"]
    done = await tool(
        "delete_project", id_or_slug=project["id"], confirm_slug=project["slug"]
    )
    assert done == f"Deleted 'Gone' ({PREFIX}gone)."
    assert "error" in await tool("get_project", id_or_slug=project["slug"])


async def test_categories_are_listed_and_changed(tool):
    created = await new_category(tool, "label", description="Old", order=5)
    listed = {c["name"]: c for c in await tool("list_categories")}
    assert listed[f"{PREFIX}label"]["order"] == 5

    changed = await tool(
        "update_category", name_or_id=f"{PREFIX}label", label="New Label"
    )
    assert changed["label"] == "New Label"
    assert changed["description"] == "Old"
    by_id = await tool("update_category", name_or_id=created["id"], order=1)
    assert by_id["order"] == 1
    nothing = await tool("update_category", name_or_id=f"{PREFIX}label")
    assert "Nothing to change" in nothing["error"]


async def test_all_is_the_works_pages_own_filter(tool):
    made = await tool("create_category", name="all", label="All")
    assert "'all'" in made["error"]
    await new_category(tool, "renamed")
    renamed = await tool("update_category", name_or_id=f"{PREFIX}renamed", name="all")
    assert "'all'" in renamed["error"]


async def test_a_taken_category_name_is_refused(tool):
    await new_category(tool, "twice")
    again = await tool("create_category", name=f"{PREFIX}twice", label="Again")
    assert "already exists" in again["error"]


async def test_a_category_with_projects_is_kept(tool):
    await new_category(tool, "busy")
    await new_project(tool, "inside", f"{PREFIX}busy")

    refused = await tool(
        "delete_category", name_or_id=f"{PREFIX}busy", confirm_name="wrong"
    )
    assert "Nothing was deleted" in refused["error"]
    busy = await tool(
        "delete_category", name_or_id=f"{PREFIX}busy", confirm_name=f"{PREFIX}busy"
    )
    assert "Cannot delete category with 1 project(s)" in busy["error"]

    await new_category(tool, "empty")
    done = await tool(
        "delete_category", name_or_id=f"{PREFIX}empty", confirm_name=f"{PREFIX}empty"
    )
    assert done == f"Deleted the category 'Empty' ({PREFIX}empty)."


# Images


def png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "teal").save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def r2(monkeypatch):
    """R2, as a list of what was stored and deleted."""
    monkeypatch.setattr(storage_service, "public_url", "https://assets.test")
    calls: dict[str, list[Any]] = {"put": [], "delete": []}

    class FakeS3:
        async def put_object(self, **kwargs):
            calls["put"].append(kwargs)

        async def delete_objects(self, **kwargs):
            calls["delete"].append(kwargs)

    @asynccontextmanager
    async def fake_client():
        yield FakeS3()

    monkeypatch.setattr(storage_service, "_client", fake_client)
    return calls


async def upload_token(tool, folder="notes") -> tuple[str, str]:
    """start_image_upload's command, and the token in it."""
    started = await tool("start_image_upload", folder=folder)
    assert "error" not in started, started
    header = next(p for p in shlex.split(started["command"]) if p.startswith("Auth"))
    return started["command"], header.removeprefix("Authorization: Bearer ")


async def send(client, token, data=None, field="file"):
    return await client.post(
        "/api/v1/uploads",
        headers={"Authorization": f"Bearer {token}"},
        files={field: ("photo.png", data or png(), "image/png")},
    )


async def test_claude_code_uploads_a_file_with_a_token(client, tool, r2, redis):
    command, token = await upload_token(tool, folder="covers")
    assert command.endswith(" -F 'file=@PATH' https://test/api/v1/uploads")

    sent = await send(client, token)
    assert sent.status_code == 200, sent.text
    url = sent.json()["data"]["url"]
    assert url.startswith("https://assets.test/covers/") and url.endswith(".png")
    assert r2["put"][0]["ContentType"] == "image/png"
    # Its WebP variants are asked for.
    assert await redis.xlen(STREAM) == 1
    # Once only, and only its hash is kept.
    assert (await send(client, token)).status_code == 401
    assert await redis.get(upload_tokens.key(token)) is None


@pytest.mark.parametrize("header", [None, "Bearer made-up", "Basic abc"])
async def test_an_upload_needs_a_token(client, r2, header):
    headers = {"Authorization": header} if header else {}
    response = await client.post(
        "/api/v1/uploads", headers=headers, files={"file": ("a.png", png())}
    )
    assert response.status_code == 401
    assert r2["put"] == []


async def test_a_token_of_an_account_removed_meanwhile_is_refused(
    client, tool, r2, monkeypatch
):
    _, token = await upload_token(tool)
    monkeypatch.setattr(config, "ADMIN_EMAILS", frozenset({"someone@example.com"}))
    assert (await send(client, token)).status_code == 401


async def test_an_upload_must_be_an_image_sent_as_file(client, tool, r2):
    _, token = await upload_token(tool)
    assert (await send(client, token, data=b"not an image")).status_code == 400
    _, token = await upload_token(tool)
    assert (await send(client, token, field="other")).status_code == 400
    assert r2["put"] == []


async def test_without_redis_there_are_no_uploads(app, client):
    # (Nor MCP sign-in, so no tool to hand out a token.)
    app.state.redis = None
    response = await client.post("/api/v1/uploads", files={"file": ("a.png", png())})
    assert response.status_code == 503


async def test_an_upload_over_the_limit_is_refused(client, tool, r2, monkeypatch):
    _, token = await upload_token(tool)
    monkeypatch.setattr("services.storage.MAX_UPLOAD_BYTES", 10)
    assert (await send(client, token)).status_code == 413
    assert r2["put"] == []


async def test_queuing_variants_never_fails_an_upload(redis):
    class Down:
        async def xadd(self, *args, **kwargs):
            raise ConnectionError("redis is down")

    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    try:
        await queue_variants(None, "notes/20261021/a.png")
        await queue_variants(Down(), "notes/20261021/a.png")  # type: ignore[arg-type]
    finally:
        logger.remove(sink)
    assert any("Could not queue variants for notes/20261021/a.png" in x for x in lines)


def web(monkeypatch, handler):
    """The internet, as `handler`, behind a public address."""

    async def public(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(remote_images, "public_addresses", public)
    monkeypatch.setattr(
        remote_images,
        "client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_claude_ai_imports_an_image_from_the_web(tool, r2, monkeypatch):
    web(monkeypatch, lambda request: httpx.Response(200, content=png()))
    imported = await tool(
        "import_image_from_url", url="https://example.com/a.png", folder="notes"
    )
    assert imported["url"].startswith("https://assets.test/notes/")
    assert imported["markdown"] == f"![]({imported['url']})"
    assert len(r2["put"]) == 1


@pytest.mark.parametrize(
    ("url", "refusal"),
    [
        ("http://example.com/a.png", "Only https://"),
        ("https://example.com:8443/a.png", "port (443)"),
        ("https://user:pw@example.com/a.png", "credentials"),
        ("https://example.com:x/a.png", "not a number"),
        ("file:///etc/passwd", "Only https://"),
    ],
)
async def test_only_plain_https_urls_are_fetched(tool, r2, url, refusal):
    refused = await tool("import_image_from_url", url=url)
    assert refusal in refused["error"]
    assert r2["put"] == []


@pytest.mark.parametrize(
    ("answer", "refusal"),
    [
        (httpx.Response(302, headers={"location": "https://10.0.0.1/"}), "redirects"),
        (httpx.Response(404), "answered 404"),
        (httpx.Response(200, content=b"<html>"), "Not a JPEG"),
    ],
)
async def test_what_comes_back_must_be_an_image(tool, r2, monkeypatch, answer, refusal):
    web(monkeypatch, lambda request: answer)
    refused = await tool("import_image_from_url", url="https://example.com/a.png")
    assert refusal in refused["error"]
    assert r2["put"] == []


async def test_a_fetched_image_stops_at_the_limit(tool, r2, monkeypatch):
    monkeypatch.setattr(remote_images, "MAX_UPLOAD_BYTES", 10)
    web(monkeypatch, lambda request: httpx.Response(200, content=png()))
    refused = await tool("import_image_from_url", url="https://example.com/a.png")
    assert "too large" in refused["error"]


async def test_a_fetch_that_fails_is_an_error_claude_can_read(tool, r2, monkeypatch):
    def unreachable(request):
        raise httpx.ConnectError("no route")

    web(monkeypatch, unreachable)
    refused = await tool("import_image_from_url", url="https://example.com/a.png")
    assert "Could not fetch" in refused["error"]


@pytest.mark.parametrize("host", ["localhost", "10.0.0.1", "169.254.169.254", "::1"])
async def test_only_public_addresses_are_fetched(host):
    with pytest.raises(remote_images.RemoteImageError, match="not on the public"):
        await remote_images.public_addresses(host)


async def test_a_name_that_does_not_resolve_is_refused():
    with pytest.raises(remote_images.RemoteImageError, match="Cannot resolve"):
        await remote_images.public_addresses("does-not-exist.invalid")


async def test_a_public_name_resolves():
    assert await remote_images.public_addresses("1.1.1.1") == ["1.1.1.1"]


async def test_only_the_sites_own_images_are_deleted(tool, r2):
    refused = await tool("delete_image", url="https://elsewhere.example/a.png")
    assert "not in the site's storage" in refused["error"]
    done = await tool("delete_image", url="https://assets.test/notes/20261021/a.png")
    assert done.startswith("Deleted https://assets.test/notes/20261021/a.png")
    keys = [o["Key"] for o in r2["delete"][0]["Delete"]["Objects"]]
    assert keys[0] == "notes/20261021/a.png" and len(keys) == 3


# Read time


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("", "1 min read"),
        ("word " * 875, "5 min read"),
        ("字" * 700, "2 min read"),
        ("word " * 175 + "字" * 350, "2 min read"),
    ],
)
def test_read_time_counts_words_and_cjk_characters(content, expected):
    assert read_time(content) == expected
