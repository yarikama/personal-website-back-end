"""
The writing MCP server (mcp_server/) and its sign-in (services/mcp_oauth.py),
over HTTP as Claude uses them: /register, /authorize, Sign in with Google
(Google faked as in test_google_login.py), /token, then JSON-RPC on /mcp.
Notes and clients are rows in PostgreSQL like the other database tests:
every note's slug starts with PREFIX, and the MCP clients (a table only
these tests use) are all deleted afterwards.
"""

import base64
import datetime
import hashlib
import re
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from loguru import logger
from sqlalchemy import delete

from core import config
from db.dependency import get_db
from db.models.lab_notes import LabNote
from db.models.mcp_oauth import McpClient
from main import get_application
from mcp_server import server as mcp_server
from services import google_oauth
from services.content.lab_notes import read_time
from services.mcp_oauth import redirect_allowed

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
            db.execute(delete(McpClient))
            db.commit()

    clean()
    yield
    clean()


@pytest.fixture
async def client(redis, async_sessions, db_override, tables, google):
    app = get_application()
    app.state.redis = redis
    # The public routes, to check what visitors see.
    app.dependency_overrides[get_db] = db_override
    mcp_server.mount(app, sessions=async_sessions)
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

    async def call(name, **arguments):
        result = await mcp("tools/call", {"name": name, "arguments": arguments})
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
