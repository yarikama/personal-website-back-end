"""
Admin sign-in.

Sign in with Google (GET /auth/google/login, then Google sends the browser
to GET /auth/google/callback) is the only way in: only accounts in
ADMIN_EMAILS get in, and they get a session cookie
(services/admin_session.py). Errors send the browser back to the site's
login page with ?error=<reason>.

The same sign-in lets Claude into the writing MCP server
(services/mcp_oauth.py): its /authorize sends the browser to the consent
page here with ?mcp=<request id>; once the owner allows the client, Google
sign-in carries the id along, and the callback sends the browser back to
the MCP client with an authorization code instead of starting a session.
"""

import re
import secrets
from datetime import datetime, timezone
from html import escape
from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

from api.dependencies.auth import CurrentAdmin
from core import config
from schemas.auth import AdminResponse
from services import admin_session, google_oauth, mcp_oauth

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
# Ties the consent page's answer to the page this API served: a form posted
# from another site comes without it (SameSite=Strict), however it is made.
CONSENT_COOKIE = "__Host-mcp_consent"
# The consent page's own policy, in place of the API's: no script, its
# inline style, and a form whose answer leads on to Google sign-in.
CONSENT_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; "
    "form-action 'self' https://accounts.google.com; frame-ancestors 'none'"
)


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
async def google_login(
    request: Request, next: str | None = None, mcp: str | None = None
) -> Response:
    client = google_client()
    redis = getattr(request.app.state, "redis", None)
    if client is None or redis is None:
        logger.error(
            "Google sign-in is off: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, "
            "ADMIN_EMAILS or REDIS_URL is unset"
        )
        return to_login_page("not_configured")
    if mcp is not None and not mcp_oauth.PENDING_ID.match(mcp):
        return to_login_page("expired")
    try:
        started = await google_oauth.start(redis, client, safe_next(next), mcp)
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
            signed_in = await google_oauth.finish(
                redis, client, config.ADMIN_EMAILS, state, code, http
            )
        email = signed_in.email
        if signed_in.mcp is not None:
            return await to_mcp_client(request, signed_in.mcp, email)
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
    response = RedirectResponse(f"{config.SITE_URL}{signed_in.next}", status_code=303)
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


async def to_mcp_client(request: Request, id: str, email: str) -> Response:
    """Sends the browser back to the MCP client that asked to sign in, with
    an authorization code. No session cookie: Claude gets tokens instead."""
    provider = mcp_provider(request)
    if provider is None:
        return to_login_page("not_configured")
    url = await provider.complete(id, email)
    if url is None:
        return to_login_page("expired")
    logger.info(f"Admin signed in to the writing MCP server with Google: {email}")
    response = RedirectResponse(url, status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/", secure=True, httponly=True)
    return response


def consent_page(
    client: OAuthClientInformationFull,
    params: AuthorizationParams,
    id: str,
    token: str,
) -> str:
    """The consent page. Everything the client chose (its name) is escaped."""
    name = escape(client.client_name or "An unnamed client")
    goes_to = escape(str(params.redirect_uri))
    issued = client.client_id_issued_at
    registered = (
        datetime.fromtimestamp(issued, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if issued
        else "at an unknown time"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect to yarikama.com</title>
<style>
body {{ font: 16px/1.5 system-ui, sans-serif; max-width: 32rem;
  margin: 3rem auto; padding: 0 1rem; color: #1c1c1e; }}
code {{ word-break: break-all; }}
button {{ font: inherit; padding: .5rem 1.25rem; margin-right: .5rem; }}
</style>
</head>
<body>
<h1>Connect {name}?</h1>
<p>It will read, write, publish and delete the lab notes on yarikama.com,
as you.</p>
<p>It registered {registered}, and the sign-in goes back to
<code>{goes_to}</code>.</p>
<p><strong>Only allow it if you have just asked Claude to connect.</strong>
If someone sent you this link, deny it.</p>
<form method="post">
<input type="hidden" name="mcp" value="{escape(id)}">
<input type="hidden" name="token" value="{escape(token)}">
<button name="decision" value="allow">Allow and sign in with Google</button>
<button name="decision" value="deny">Deny</button>
</form>
</body>
</html>
"""


def mcp_provider(request: Request) -> mcp_oauth.WritingOAuth | None:
    return getattr(request.app.state, "mcp_oauth", None)


@router.get("/auth/mcp/consent", include_in_schema=False)
async def mcp_consent(request: Request, mcp: str | None = None) -> Response:
    """Asks the owner whether the MCP client may sign in."""
    provider = mcp_provider(request)
    if provider is None:
        return to_login_page("not_configured")
    if not (mcp and mcp_oauth.PENDING_ID.match(mcp)):
        return to_login_page("expired")
    try:
        asked = await provider.pending(mcp)
    except Exception as error:
        logger.warning(f"MCP consent unavailable: {error!r}")
        return to_login_page("unavailable")
    if asked is None:
        return to_login_page("expired")
    token = secrets.token_urlsafe(32)
    response = HTMLResponse(
        consent_page(*asked, mcp, token),
        headers={"Content-Security-Policy": CONSENT_CSP, "Cache-Control": "no-store"},
    )
    response.set_cookie(
        CONSENT_COOKIE,
        token,
        max_age=mcp_oauth.PENDING_TTL_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response


@router.post("/auth/mcp/consent", include_in_schema=False)
async def mcp_consent_answer(
    request: Request,
    mcp: Annotated[str, Form()],
    token: Annotated[str, Form()],
    decision: Annotated[str, Form()],
) -> Response:
    """The owner's answer: on to Google sign-in, or back to the client with
    access_denied."""
    provider = mcp_provider(request)
    if provider is None:
        return to_login_page("not_configured")
    expected = request.cookies.get(CONSENT_COOKIE)
    if not (
        expected
        and secrets.compare_digest(token, expected)
        and mcp_oauth.PENDING_ID.match(mcp)
    ):
        return to_login_page("expired")
    try:
        if decision == "allow":
            allowed = await provider.approve(mcp)
            target = (
                f"{config.API_PREFIX}/v1/auth/google/login?{urlencode({'mcp': mcp})}"
                if allowed
                else None
            )
        else:
            target = await provider.deny(mcp)
    except Exception as error:
        logger.warning(f"MCP consent unavailable: {error!r}")
        return to_login_page("unavailable")
    if target is None:
        return to_login_page("expired")
    answer = "allowed" if decision == "allow" else "denied"
    logger.info(f"MCP sign-in request {answer} on the consent page")
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(
        CONSENT_COOKIE, path="/", secure=True, httponly=True, samesite="strict"
    )
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
