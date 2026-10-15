from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.config import API_PREFIX

# On every response. HSTS: browsers that have seen api.yarikama.com over
# HTTPS refuse plain HTTP to it for two years (Cloudflare already redirects;
# this closes the first-request gap after that). nosniff: a response is
# never read as anything but its declared type. DENY: nothing frames it.
HEADERS = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

# The API returns JSON and event streams, never a page: nothing in a
# response may load or run anything. Not on /docs, whose Swagger UI loads
# scripts from a CDN.
API_CSP = "default-src 'none'; frame-ancestors 'none'"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        api = scope["path"].startswith(f"{API_PREFIX}/")

        async def add_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS.items():
                    headers.setdefault(name, value)
                if api:
                    headers.setdefault("Content-Security-Policy", API_CSP)
            await send(message)

        await self.app(scope, receive, add_headers)
