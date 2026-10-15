from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from loguru import logger

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
    redis = getattr(request.app.state, "redis", None)
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
