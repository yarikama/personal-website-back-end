"""
Trading Google's authorization code for an ID token (services/google_oauth
exchange_code), against a mocked token endpoint. The sign-in flow around it
is in tests/test_google_login.py, which replaces this trade.
"""

from urllib.parse import parse_qs

import httpx
import pytest
from services.google_oauth import (
    TOKEN_URL,
    Client,
    SignInError,
    check_id_token,
    exchange_code,
)

pytestmark = pytest.mark.anyio

CLIENT = Client("client-id", "client-secret", "https://api.test/callback")


def token_endpoint(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_the_code_and_verifier_are_traded_for_the_id_token():
    sent = {}

    def handler(request):
        sent["url"] = str(request.url)
        sent["form"] = parse_qs(request.content.decode())
        return httpx.Response(200, json={"id_token": "the.id.token"})

    async with token_endpoint(handler) as http:
        assert await exchange_code(CLIENT, "the-code", "the-verifier", http) == (
            "the.id.token"
        )

    assert sent["url"] == TOKEN_URL
    assert sent["form"] == {
        "grant_type": ["authorization_code"],
        "code": ["the-code"],
        "code_verifier": ["the-verifier"],
        "client_id": ["client-id"],
        "client_secret": ["client-secret"],
        "redirect_uri": ["https://api.test/callback"],
    }


@pytest.mark.parametrize(
    ("reply", "says"),
    [
        (httpx.Response(400, text='{"error": "invalid_grant"}'), "invalid_grant"),
        (httpx.Response(200, json={"access_token": "x"}), "no ID token"),
    ],
)
async def test_a_refusal_or_no_id_token_fails_the_sign_in(reply, says):
    async with token_endpoint(lambda request: reply) as http:
        with pytest.raises(SignInError, match=says) as raised:
            await exchange_code(CLIENT, "code", "verifier", http)

    assert raised.value.reason == "failed"


async def test_an_unreachable_token_endpoint_fails_the_sign_in():
    def handler(request):
        raise httpx.ConnectError("no route to host", request=request)

    async with token_endpoint(handler) as http:
        with pytest.raises(SignInError, match="unreachable"):
            await exchange_code(CLIENT, "code", "verifier", http)


def test_an_unreadable_id_token_fails_the_sign_in():
    with pytest.raises(SignInError, match="unreadable ID token") as raised:
        check_id_token("not-a-jwt", "client-id", "nonce")

    assert raised.value.reason == "failed"
