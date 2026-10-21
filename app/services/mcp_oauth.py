"""
The writing MCP server's authorization server (OAuth 2.1, as the MCP
authorization spec asks), in the shape the SDK takes it:
OAuthAuthorizationServerProvider. The SDK's handlers check each request
(PKCE, the redirect URI, expiry, which resource a token is for); this keeps
the state and decides who gets in.

Signing in is the admin's own Sign in with Google (api/routes/auth.py):

1. The SDK's /authorize calls authorize(), which keeps the request in Redis
   under a random id and sends the browser to the consent page with it.
2. The owner allows the client there (approve()) and signs in with Google.
3. Google sends the browser back to the usual callback. For an account in
   ADMIN_EMAILS it calls complete(), which makes an authorization code and
   sends the browser on to the client's redirect URI with it.
4. The client trades the code, once, for an access token (Redis, an hour)
   and a refresh token (the database, MCP_REFRESH_TOKEN_DAYS), and trades
   the refresh token for new ones from then on.

Why the consent page: anyone may register a client and start /authorize,
and Google lets an owner who is signed in already through with a click.
Without it, a link from someone else could end with their client (their
claude.ai account, say) holding the owner's tokens. The owner sees which
client asks, and where the code will go, before anything is signed.
Codes also only go to redirect URIs in MCP_REDIRECT_URIS, which
register_client() holds every client to.

Tokens are random; only their SHA-256 is kept, as for admin sessions.
Every token names its account, which must still be in ADMIN_EMAILS each
time it is used.
"""

import hashlib
import json
import re
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from redis.asyncio import Redis
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import config
from db.models.mcp_oauth import McpClient, McpRefreshToken
from services import google_oauth

# As long as Google sign-in may take: the request waits for it.
PENDING_TTL_SECONDS = google_oauth.STATE_TTL_SECONDS
CODE_TTL_SECONDS = 5 * 60
# The id authorize() hands the consent page, as made below.
PENDING_ID = re.compile(r"^[A-Za-z0-9_-]{43}$")


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pending_key(id: str) -> str:
    return f"mcp:pending:{id}"


def code_key(code: str) -> str:
    return f"mcp:code:{digest(code)}"


def access_key(token: str) -> str:
    return f"mcp:access:{digest(token)}"


def access_key_for(token_hash: str) -> str:
    return f"mcp:access:{token_hash}"


def redirect_allowed(uri: str, allowed: tuple[str, ...]) -> bool:
    """Whether `uri` is one of `allowed`, where "*" stands for a port."""
    for pattern in allowed:
        if "*" in pattern:
            regex = re.escape(pattern).replace(r"\*", r"[0-9]{1,5}")
            if re.fullmatch(regex, uri):
                return True
        elif uri == pattern:
            return True
    return False


class WritingOAuth(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """
    `redis` returns the app's Redis (None when it is not configured), as it
    only exists once the app has started. `consent_url` is the consent page,
    which takes the request's id as ?mcp=. `resource` is the MCP server's
    URL, the only resource it issues tokens for.
    """

    def __init__(
        self,
        redis: Callable[[], Redis | None],
        sessions: async_sessionmaker[AsyncSession],
        *,
        consent_url: str,
        resource: str,
    ) -> None:
        self._redis = redis
        self.sessions = sessions
        self.consent_url = consent_url
        self.resource = resource

    def redis(self) -> Redis:
        redis = self._redis()
        if redis is None:
            raise RuntimeError("REDIS_URL is not set: MCP sign-in needs Redis")
        return redis

    # Clients ----------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with self.sessions() as db:
            row = await db.get(McpClient, client_id)
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate(row.info)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(uri) for uri in client_info.redirect_uris or []]
        refused = [u for u in uris if not redirect_allowed(u, config.MCP_REDIRECT_URIS)]
        if not uris or refused:
            raise RegistrationError(
                "invalid_redirect_uri",
                f"redirect URIs not allowed here: {', '.join(refused) or '(none)'}",
            )
        async with self.sessions() as db:
            db.add(
                McpClient(
                    client_id=client_info.client_id,
                    info=client_info.model_dump(mode="json"),
                )
            )
            await db.commit()

    # Signing in -------------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if params.resource not in (None, self.resource):
            raise AuthorizeError("invalid_target", f"only {self.resource} is served")
        id = secrets.token_urlsafe(32)
        request = {
            "client_id": client.client_id,
            "params": params.model_dump(mode="json"),
            "approved": False,
        }
        try:
            await self.redis().set(
                pending_key(id), json.dumps(request), ex=PENDING_TTL_SECONDS
            )
        except Exception as error:
            raise AuthorizeError(
                "temporarily_unavailable", "sign-in is unavailable"
            ) from error
        return f"{self.consent_url}?{urlencode({'mcp': id})}"

    async def pending(
        self, id: str
    ) -> tuple[OAuthClientInformationFull, AuthorizationParams] | None:
        """The client asking to sign in with the request `id`, and what it
        asks; None if the request is unknown or has expired."""
        raw = await self.redis().get(pending_key(id))
        if raw is None:
            return None
        request = json.loads(raw)
        client = await self.get_client(request["client_id"])
        if client is None:
            return None
        return client, AuthorizationParams.model_validate(request["params"])

    async def approve(self, id: str) -> bool:
        """The owner allowed the request on the consent page; False if it is
        unknown or has expired."""
        raw = await self.redis().get(pending_key(id))
        if raw is None:
            return False
        request = {**json.loads(raw), "approved": True}
        await self.redis().set(pending_key(id), json.dumps(request), keepttl=True)
        return True

    async def deny(self, id: str) -> str | None:
        """The owner refused the request: where to tell the client so (its
        redirect URI with error=access_denied), or None if it is unknown."""
        raw = await self.redis().getdel(pending_key(id))
        if raw is None:
            return None
        params = AuthorizationParams.model_validate(json.loads(raw)["params"])
        return construct_redirect_uri(
            str(params.redirect_uri), error="access_denied", state=params.state
        )

    async def complete(self, id: str, email: str) -> str | None:
        """Where to send the browser once `email` has signed in for the
        request `id`: the client's redirect URI with a new code. None if the
        request is unknown, has expired, was completed already, or was never
        allowed on the consent page."""
        raw = await self.redis().getdel(pending_key(id))
        if raw is None:
            return None
        request = json.loads(raw)
        if not request.get("approved"):
            return None
        params = AuthorizationParams.model_validate(request["params"])
        code = secrets.token_urlsafe(32)  # 256 bits; RFC 6749 asks for 128
        kept = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + CODE_TTL_SECONDS,
            client_id=request["client_id"],
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self.resource,
            subject=email,
        )
        # The code itself is not kept, only its hash in the key.
        await self.redis().set(
            code_key(code),
            kept.model_dump_json(exclude={"code"}),
            ex=CODE_TTL_SECONDS,
        )
        return construct_redirect_uri(
            str(params.redirect_uri), code=code, state=params.state
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        raw = await self.redis().get(code_key(authorization_code))
        if raw is None:
            return None
        fields = json.loads(raw)
        code = AuthorizationCode.model_validate({**fields, "code": authorization_code})
        return code if code.client_id == client.client_id else None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Taken, not read: of two requests with the same code, one wins.
        if await self.redis().getdel(code_key(authorization_code.code)) is None:
            raise TokenError("invalid_grant", "authorization code already used")
        email = authorization_code.subject or ""
        if email not in config.ADMIN_EMAILS:
            raise TokenError("invalid_grant", "account is not an admin")
        return await self.issue(
            client.client_id, email, authorization_code.scopes, self.resource
        )

    # Tokens -----------------------------------------------------------------

    async def issue(
        self, client_id: str, email: str, scopes: list[str], resource: str | None
    ) -> OAuthToken:
        """A new access token and refresh token, for `email` through
        `client_id`."""
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        access_seconds = config.MCP_ACCESS_TOKEN_MINUTES * 60
        now = datetime.now(timezone.utc)
        record = {
            "client_id": client_id,
            "scopes": scopes,
            "resource": resource,
            "subject": email,
            "expires_at": int(now.timestamp()) + access_seconds,
            "refresh_hash": digest(refresh),
        }
        await self.redis().set(
            access_key(access), json.dumps(record), ex=access_seconds
        )
        async with self.sessions() as db:
            # Expired refresh tokens go whenever a new one is made.
            await db.execute(
                delete(McpRefreshToken).where(McpRefreshToken.expires_at < now)
            )
            db.add(
                McpRefreshToken(
                    token_hash=digest(refresh),
                    client_id=client_id,
                    email=email,
                    scopes=scopes,
                    resource=resource,
                    access_hash=digest(access),
                    expires_at=now + timedelta(days=config.MCP_REFRESH_TOKEN_DAYS),
                )
            )
            await db.commit()
        return OAuthToken(
            access_token=access,
            expires_in=access_seconds,
            scope=" ".join(scopes) or None,
            refresh_token=refresh,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        async with self.sessions() as db:
            row = await db.get(McpRefreshToken, digest(refresh_token))
        if (
            row is None
            or row.client_id != client.client_id
            or row.expires_at < datetime.now(timezone.utc)
            or row.email not in config.ADMIN_EMAILS
        ):
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row.client_id,
            scopes=row.scopes,
            expires_at=int(row.expires_at.timestamp()),
            resource=row.resource,
            subject=row.email,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """New tokens for old: the refresh token works once (rotation), and
        the access token issued with it ends too."""
        access_hash = await self.forget_refresh(digest(refresh_token.token))
        if access_hash is None:
            raise TokenError("invalid_grant", "refresh token already used")
        await self.redis().delete(access_key_for(access_hash))
        email = refresh_token.subject or ""
        return await self.issue(client.client_id, email, scopes, refresh_token.resource)

    async def forget_refresh(self, token_hash: str) -> str | None:
        """Deletes the refresh token; the hash of the access token issued
        with it, or None if it was gone already."""
        async with self.sessions() as db:
            access_hash = await db.scalar(
                delete(McpRefreshToken)
                .where(McpRefreshToken.token_hash == token_hash)
                .returning(McpRefreshToken.access_hash)
            )
            await db.commit()
        return access_hash

    async def load_access_token(self, token: str) -> AccessToken | None:
        raw = await self.redis().get(access_key(token))
        if raw is None:
            return None
        record = json.loads(raw)
        # Removed from ADMIN_EMAILS since signing in: out at once.
        if record["subject"] not in config.ADMIN_EMAILS:
            return None
        return AccessToken(
            token=token,
            client_id=record["client_id"],
            scopes=record["scopes"],
            expires_at=record["expires_at"],
            resource=record["resource"],
            subject=record["subject"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Ends the token and the one issued with it."""
        if isinstance(token, AccessToken):
            raw = await self.redis().getdel(access_key(token.token))
            if raw is not None:
                await self.forget_refresh(json.loads(raw)["refresh_hash"])
        else:
            access_hash = await self.forget_refresh(digest(token.token))
            if access_hash is not None:
                await self.redis().delete(access_key_for(access_hash))
