"""
Admin sign-in.

Sign in with Google (GET /auth/google/login, then Google sends the browser
to GET /auth/google/callback) is the only way in: only accounts in
ADMIN_EMAILS get in, and they get a session cookie
(services/admin_session.py). Errors send the browser back to the site's
login page with ?error=<reason>.
"""

import re
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, Response, status
from fastapi.responses import RedirectResponse
from loguru import logger

from api.dependencies.auth import CurrentAdmin
from core import config
from schemas.auth import AdminResponse
from services import admin_session, google_oauth

router = APIRouter()

# Ties the callback to the browser that started the sign-in: without it, a
# link to the callback with someone else's code would sign a victim in to
# that account. Lax, not Strict: it must come along when Google sends the
# browser back, a navigation from another site.
STATE_COOKIE = "__Host-admin_oauth"
# Only the site's admin pages, so the redirect after sign-in can't be
# pointed anywhere else.
NEXT_PATH = re.compile(r"^/admin(?:/[A-Za-z0-9._~-]+)*/?$")
TOKEN_TIMEOUT_SECONDS = 10


def google_client() -> google_oauth.Client | None:
    secret = str(config.GOOGLE_CLIENT_SECRET)
    if not (config.GOOGLE_CLIENT_ID and secret and config.ADMIN_EMAILS):
        return None
    return google_oauth.Client(
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=secret,
        redirect_uri=config.GOOGLE_REDIRECT_URI,
    )


def to_login_page(reason: str) -> RedirectResponse:
    query = urlencode({"error": reason})
    return RedirectResponse(f"{config.SITE_URL}/admin/login?{query}", status_code=303)


def safe_next(path: str | None) -> str:
    if path and NEXT_PATH.match(path) and ".." not in path:
        return path
    return "/admin"


@router.get("/auth/google/login", include_in_schema=False)
async def google_login(request: Request, next: str | None = None) -> Response:
    client = google_client()
    redis = getattr(request.app.state, "redis", None)
    if client is None or redis is None:
        logger.error(
            "Google sign-in is off: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, "
            "ADMIN_EMAILS or REDIS_URL is unset"
        )
        return to_login_page("not_configured")
    try:
        started = await google_oauth.start(redis, client, safe_next(next))
    except Exception as error:
        logger.warning(f"Google sign-in could not start: {error!r}")
        return to_login_page("unavailable")
    response = RedirectResponse(started.url, status_code=303)
    response.set_cookie(
        STATE_COOKIE,
        started.state,
        max_age=google_oauth.STATE_TTL_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/auth/google/callback", include_in_schema=False)
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    client = google_client()
    redis = getattr(request.app.state, "redis", None)
    if client is None or redis is None:
        return to_login_page("not_configured")
    if error:
        # access_denied: the owner closed Google's page or said no.
        return to_login_page("cancelled")
    expected = request.cookies.get(STATE_COOKIE)
    if not (code and state and expected and secrets.compare_digest(state, expected)):
        return to_login_page("expired")

    try:
        async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT_SECONDS) as http:
            email, next_path = await google_oauth.finish(
                redis, client, config.ADMIN_EMAILS, state, code, http
            )
        token = await admin_session.create(
            redis, email, config.ADMIN_SESSION_HOURS * 3600
        )
    except google_oauth.SignInError as failure:
        logger.warning(f"Google sign-in refused ({failure.reason}): {failure}")
        return to_login_page(failure.reason)
    except Exception as failure:
        logger.warning(f"Google sign-in failed: {failure!r}")
        return to_login_page("unavailable")

    logger.info(f"Admin signed in with Google: {email}")
    response = RedirectResponse(f"{config.SITE_URL}{next_path}", status_code=303)
    response.set_cookie(
        admin_session.COOKIE,
        token,
        max_age=config.ADMIN_SESSION_HOURS * 3600,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(STATE_COOKIE, path="/", secure=True, httponly=True)
    return response


@router.get("/auth/me")
async def me(admin: CurrentAdmin) -> AdminResponse:
    """Who is signed in; 401 if no one. The site asks before admin pages."""
    return AdminResponse(email=admin)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    token = request.cookies.get(admin_session.COOKIE)
    redis = getattr(request.app.state, "redis", None)
    if token and redis is not None:
        try:
            await admin_session.delete(redis, token)
        except Exception as error:
            # The cookie is still cleared below; the session expires on its own.
            logger.warning(f"Admin session could not be deleted: {error!r}")
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        admin_session.COOKIE, path="/", secure=True, httponly=True, samesite="strict"
    )
    return response
