import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from loguru import logger

from api.dependencies.redis import get_redis
from core import config
from services import admin_session

# Requests that only read. Any other method sent with the session cookie
# must come from one of the site's own pages (CORS_ORIGINS): the cookie is
# SameSite=Strict already, and this also holds if a browser ignores that.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def session_admin(request: Request) -> str | None:
    """
    The account signed in with the session cookie, if it still may.
    Raises 503 if Redis cannot be reached: the session can't be checked.
    """
    token = request.cookies.get(admin_session.COOKIE)
    redis = get_redis(request.app)
    if not token or redis is None:
        return None
    try:
        email = await admin_session.lookup(redis, token)
    except Exception as error:
        logger.warning(f"Admin session unavailable: {error!r}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Temporarily unavailable. Try again shortly.",
            headers={"Retry-After": "30"},
        ) from error
    # Removed from ADMIN_EMAILS since signing in: out at once.
    if email is None or email not in config.ADMIN_EMAILS:
        return None
    return email


async def current_admin(request: Request) -> str | None:
    """The admin making the request, or None for a visitor."""
    email = await session_admin(request)
    if email is None:
        return None
    if (
        request.method not in SAFE_METHODS
        and request.headers.get("Origin") not in config.CORS_ORIGINS
    ):
        return None
    return email


async def get_current_admin(request: Request) -> str:
    admin = await current_admin(request)
    if admin is None:
        raise unauthorized("Not signed in")
    return admin


CurrentAdmin = Annotated[str, Depends(get_current_admin)]


# The to-dos may also be managed with a bearer token from the writing MCP
# server's sign-in (services/mcp_oauth.py), so the owner's iPhone app can use
# them. Only a token granted this scope works here: Claude's tokens for the
# MCP server are not. A bearer token is never sent by a browser on its own,
# so the Origin check the session cookie needs does not apply.
TODOS_SCOPE = "todos"


def bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def token_admin(request: Request) -> str | None:
    """The admin a to-dos token belongs to, if the request carries one that
    is valid, unexpired and granted TODOS_SCOPE."""
    token = bearer_token(request)
    provider = getattr(request.app.state, "mcp_oauth", None)
    if token is None or provider is None:
        return None
    try:
        access = await provider.load_access_token(token)
    except Exception as error:
        logger.warning(f"To-dos token could not be checked: {error!r}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Temporarily unavailable. Try again shortly.",
            headers={"Retry-After": "30"},
        ) from error
    if access is None or TODOS_SCOPE not in access.scopes:
        return None
    if access.expires_at is not None and access.expires_at <= time.time():
        return None
    return access.subject


async def get_todos_admin(request: Request) -> str:
    """The admin, signed in on the site or holding a to-dos token."""
    admin = await current_admin(request) or await token_admin(request)
    if admin is None:
        raise unauthorized("Not signed in")
    return admin


TodosAdmin = Annotated[str, Depends(get_todos_admin)]
