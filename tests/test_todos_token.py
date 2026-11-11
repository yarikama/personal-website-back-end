# The sign-in fixtures come from test_mcp_writing.py; pytest passes them to
# the tests by name, which ruff reads as redefinitions.
# ruff: noqa: F811
"""
The to-dos with a bearer token, as the owner's Attention iPhone app uses
them: it signs in through the writing MCP server's OAuth (Sign in with
Google faked as in test_mcp_writing.py) with scope "todos" and the redirect
URI attention://oauth/callback, then calls /api/v1/admin/todos. To-dos are
on days in 2001 and deleted afterwards.
"""

import json
from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import delete

from core import config
from db.models.todo import AdminTodo
from services.mcp_oauth import access_key
from tests.test_mcp_writing import (  # noqa: F401
    RESOURCE,
    app,
    client,
    consent,
    google,
    pkce,
    provider,
    settings,
    signed_in,
    tables,
    through_google,
)

pytestmark = pytest.mark.anyio

APP = "attention://oauth/callback"
TODOS = "/api/v1/admin/todos"
DAY = date(2001, 6, 1)


@pytest.fixture(autouse=True)
def todos_of_2001(sync_sessions):
    def clean():
        with sync_sessions() as db:
            db.execute(
                delete(AdminTodo).where(
                    AdminTodo.day.between(date(2001, 1, 1), date(2001, 12, 31))
                )
            )
            db.commit()

    clean()
    yield
    clean()


async def register_app(client, scope="todos"):
    return await client.post(
        "/register",
        json={
            "redirect_uris": [APP],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "Attention",
            "scope": scope,
        },
    )


async def app_tokens(client) -> dict[str, str]:
    """Tokens, as the app holds them after signing in with scope todos."""
    registered = await register_app(client)
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]
    verifier, challenge = pkce()
    asked = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": APP,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "app-state",
            "scope": "todos",
            "resource": RESOURCE,
        },
    )
    assert asked.status_code == 302, asked.text
    answered = await consent(client, asked.headers["location"])
    assert answered.status_code == 303, answered.text
    back = await through_google(client, answered.headers["location"])
    assert back.status_code == 303, back.text
    location = back.headers["location"]
    assert location.startswith(f"{APP}?")
    code = parse_qs(urlsplit(location).query)["code"][0]
    tokens = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": APP,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
    )
    assert tokens.status_code == 200, tokens.text
    return tokens.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_the_app_may_register_its_callback(client):
    assert (await register_app(client)).status_code == 201


async def test_a_todos_token_reads_and_writes_the_todos(client):
    token = (await app_tokens(client))["access_token"]
    headers = bearer(token)  # no Origin: an app, not a page of the site

    listed = await client.get(TODOS, params={"day": DAY.isoformat()}, headers=headers)
    assert listed.status_code == 200, listed.text

    added = await client.post(
        TODOS, json={"text": "Mock interview", "day": DAY.isoformat()}, headers=headers
    )
    assert added.status_code == 201, added.text
    item = added.json()["data"]

    ticked = await client.patch(
        f"{TODOS}/{item['id']}",
        json={"done": True, "day": DAY.isoformat()},
        headers=headers,
    )
    assert ticked.status_code == 200, ticked.text
    assert ticked.json()["data"]["doneOn"] == DAY.isoformat()


async def test_claudes_tokens_for_the_mcp_server_do_not_work_here(client):
    token = (await signed_in(client))["access_token"]
    response = await client.get(
        TODOS, params={"day": DAY.isoformat()}, headers=bearer(token)
    )
    assert response.status_code == 401


async def test_a_client_registered_without_the_scope_cannot_ask_for_it(client):
    registered = await register_app(client, scope="")
    client_id = registered.json()["client_id"]
    _, challenge = pkce()
    asked = await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": APP,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "app-state",
            "scope": "todos",
            "resource": RESOURCE,
        },
    )
    # Sent back to the app with an error, never to the consent page.
    assert asked.status_code == 302
    assert parse_qs(urlsplit(asked.headers["location"]).query)["error"] == [
        "invalid_scope"
    ]


@pytest.mark.parametrize(
    "header", [None, "Bearer made-up", "Bearer ", "Basic dXNlcjpwYXNz"]
)
async def test_no_token_or_a_made_up_one_is_refused(client, header):
    headers = {"Authorization": header} if header else {}
    response = await client.get(TODOS, params={"day": DAY.isoformat()}, headers=headers)
    assert response.status_code == 401


async def test_an_expired_token_is_refused(client, redis):
    token = (await app_tokens(client))["access_token"]
    record = json.loads(await redis.get(access_key(token)))
    record["expires_at"] = 1
    await redis.set(access_key(token), json.dumps(record))
    response = await client.get(
        TODOS, params={"day": DAY.isoformat()}, headers=bearer(token)
    )
    assert response.status_code == 401


async def test_a_token_of_an_account_removed_meanwhile_is_refused(client, monkeypatch):
    token = (await app_tokens(client))["access_token"]
    monkeypatch.setattr(config, "ADMIN_EMAILS", frozenset())
    response = await client.get(
        TODOS, params={"day": DAY.isoformat()}, headers=bearer(token)
    )
    assert response.status_code == 401


async def test_a_todos_token_opens_nothing_else(client):
    token = (await app_tokens(client))["access_token"]
    # The admin's own account page needs the session cookie.
    me = await client.get("/api/v1/auth/me", headers=bearer(token))
    assert me.status_code == 401
