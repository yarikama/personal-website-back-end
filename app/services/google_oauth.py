"""
Sign in with Google: the OAuth 2.0 authorization code flow with PKCE, run
by the API so the site loads no script from Google.

1. start() makes a random state, PKCE verifier and nonce and keeps them in
   Redis for STATE_TTL_SECONDS; the browser is sent to Google with the
   state, the verifier's hash and the nonce.
2. Google sends the browser back with a code and the state. finish() takes
   the state's values (once only), trades the code and the verifier for an
   ID token, and checks it.

The ID token comes straight from Google's token endpoint over TLS, so its
signature is not checked again (OpenID Connect Core 3.1.3.7, step 6); its
issuer, audience, expiry and nonce are.
"""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from redis.asyncio import Redis

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
STATE_TTL_SECONDS = 10 * 60
# Leeway for the clocks of this server and Google's.
CLOCK_SKEW_SECONDS = 60


class SignInError(Exception):
    """Sign-in did not succeed. `reason` is a short code for the site's
    login page; the message is for the log."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Client:
    client_id: str
    client_secret: str
    redirect_uri: str


@dataclass(frozen=True)
class Started:
    state: str
    url: str


@dataclass(frozen=True)
class SignedIn:
    """Who signed in, and what for: the admin page to return to, or the MCP
    sign-in request to complete."""

    email: str
    next: str
    mcp: str | None


def state_key(state: str) -> str:
    return f"admin:oauth:{state}"


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def start(
    redis: Redis, client: Client, next_path: str, mcp: str | None = None
) -> Started:
    """`mcp`: the MCP sign-in request this is for (services/mcp_oauth.py),
    if any; finish() hands it back."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)  # 86 characters; PKCE allows 43-128
    nonce = secrets.token_urlsafe(16)
    kept = {"verifier": verifier, "nonce": nonce, "next": next_path, "mcp": mcp}
    await redis.set(state_key(state), json.dumps(kept), ex=STATE_TTL_SECONDS)
    query = urlencode(
        {
            "client_id": client.client_id,
            "redirect_uri": client.redirect_uri,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge(verifier),
            "code_challenge_method": "S256",
            # Always ask which account, so signing in with the wrong one is
            # easy to undo.
            "prompt": "select_account",
        }
    )
    return Started(state=state, url=f"{AUTHORIZE_URL}?{query}")


async def take_state(redis: Redis, state: str) -> dict[str, Any]:
    """The values start() kept for this state, removed so they work once."""
    raw = await redis.getdel(state_key(state))
    if raw is None:
        raise SignInError("expired", "unknown or expired state")
    return json.loads(raw)


def check_id_token(id_token: str, client_id: str, nonce: str) -> dict[str, Any]:
    """The ID token's claims, if Google issued it to this client for this
    sign-in and it has not expired."""
    try:
        claims = jwt.decode(id_token, options={"verify_signature": False})
    except jwt.PyJWTError as error:
        raise SignInError("failed", f"unreadable ID token: {error}") from error
    now = time.time()
    if claims.get("iss") not in ISSUERS:
        raise SignInError("failed", f"ID token from {claims.get('iss')!r}")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if client_id not in audiences:
        raise SignInError("failed", "ID token issued to another client")
    if not isinstance(claims.get("exp"), int | float) or (
        claims["exp"] + CLOCK_SKEW_SECONDS < now
    ):
        raise SignInError("failed", "ID token expired")
    if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise SignInError("failed", "ID token nonce does not match")
    return claims


def allowed_email(claims: dict[str, Any], allowed: frozenset[str]) -> str:
    """The account's email, if Google verified it and it may sign in."""
    email = str(claims.get("email", "")).strip().lower()
    if not email or claims.get("email_verified") is not True:
        raise SignInError("not_allowed", "account has no verified email")
    if email not in allowed:
        raise SignInError("not_allowed", "account is not an admin")
    return email


async def exchange_code(
    client: Client, code: str, verifier: str, http: httpx.AsyncClient
) -> str:
    """Trade the authorization code for an ID token."""
    try:
        response = await http.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "client_id": client.client_id,
                "client_secret": client.client_secret,
                "redirect_uri": client.redirect_uri,
            },
        )
    except httpx.HTTPError as error:
        raise SignInError("failed", f"token endpoint unreachable: {error!r}") from error
    if response.status_code != 200:
        # Google's error body names the problem (e.g. invalid_grant) and
        # holds no secret.
        raise SignInError(
            "failed",
            f"token endpoint answered {response.status_code}: {response.text[:200]}",
        )
    id_token = response.json().get("id_token")
    if not id_token:
        raise SignInError("failed", "token endpoint returned no ID token")
    return id_token


async def finish(
    redis: Redis,
    client: Client,
    allowed: frozenset[str],
    state: str,
    code: str,
    http: httpx.AsyncClient,
) -> SignedIn:
    kept = await take_state(redis, state)
    id_token = await exchange_code(client, code, kept["verifier"], http)
    claims = check_id_token(id_token, client.client_id, kept["nonce"])
    return SignedIn(allowed_email(claims, allowed), kept["next"], kept.get("mcp"))
