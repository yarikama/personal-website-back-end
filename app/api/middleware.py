from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from api.dependencies.rate_limit import get_rate_limiter, wait_in_words
from core.config import API_PREFIX
from services.rate_limit import PUBLIC, client_id, retry_after_header

# Admin routes need a valid token already, and the editor calls
# autocomplete on every pause in typing: a public budget would only get in
# the owner's way.
EXEMPT_PREFIXES = (f"{API_PREFIX}/v1/admin/",)


class PublicRateLimitMiddleware:
    """
    Applies the PUBLIC rule to every API request before it reaches a route.

    Add it before CORSMiddleware, so CORS wraps it: a 429 then still
    carries the CORS headers, and the browser lets the page read it
    instead of reporting a network error. Preflights (OPTIONS) are not
    counted; the browser sends them on its own.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] == "OPTIONS"
            or not scope["path"].startswith(f"{API_PREFIX}/")
            or scope["path"].startswith(EXEMPT_PREFIXES)
        ):
            await self.app(scope, receive, send)
            return

        limiter = get_rate_limiter(scope["app"])
        if limiter is not None:
            peer = scope.get("client")
            client = client_id(Headers(scope=scope), peer[0] if peer else None)
            decision = await limiter.hit(PUBLIC, client)
            if not decision.allowed:
                response = JSONResponse(
                    {
                        "detail": "Too many requests. Try again in "
                        f"{wait_in_words(decision.retry_after)}."
                    },
                    status_code=429,
                    headers={"Retry-After": retry_after_header(decision)},
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
