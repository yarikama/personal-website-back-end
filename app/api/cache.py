import hashlib

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.config import API_PREFIX

# Published content only: the same for every visitor, and nothing here reads
# credentials. Everything else (admin, contact) is never cached.
CACHEABLE_PREFIXES = tuple(
    f"{API_PREFIX}/v1/{name}" for name in ("projects", "lab-notes", "categories")
)

# Browsers revalidate every time (cheap: a 304 from Cloudflare's edge).
# Cloudflare keeps a copy for 60 s, so an edit shows up within a minute, and
# may serve the old copy for 10 more minutes while it fetches the new one.
CACHE_CONTROL = "public, max-age=0, s-maxage=60, stale-while-revalidate=600"


def etag_for(body: bytes) -> str:
    return '"' + hashlib.blake2b(body, digest_size=16).hexdigest() + '"'


def matches(if_none_match: str, etag: str) -> bool:
    candidates = {tag.strip().removeprefix("W/") for tag in if_none_match.split(",")}
    return etag in candidates or "*" in candidates


class PublicCacheMiddleware:
    """
    Makes published content cacheable: Cache-Control for Cloudflare and
    browsers, an ETag, and 304 Not Modified when the client already has it.

    Add it last, outside CORSMiddleware. CORS answers each allowed origin
    with its own Access-Control-Allow-Origin and "Vary: Origin", and
    Cloudflare ignores Vary: a copy cached for yarikama.com would then be
    refused on www.yarikama.com. These responses carry no credentials, so
    they are rewritten to "*", which is right for every origin.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "GET"
            or not scope["path"].startswith(CACHEABLE_PREFIXES)
        ):
            await self.app(scope, receive, send)
            return

        start: Message = {}
        body = bytearray()

        async def buffer(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))

        await self.app(scope, receive, buffer)

        headers = MutableHeaders(raw=list(start["headers"]))
        status = start["status"]
        if status == 200:
            etag = etag_for(bytes(body))
            headers["ETag"] = etag
            headers["Cache-Control"] = CACHE_CONTROL
            if "access-control-allow-origin" in headers:
                headers["Access-Control-Allow-Origin"] = "*"
                del headers["Access-Control-Allow-Credentials"]
                del headers["Vary"]
            if_none_match = Headers(scope=scope).get("if-none-match")
            if if_none_match and matches(if_none_match, etag):
                status = 304
                body = bytearray()
                del headers["Content-Length"]
                del headers["Content-Type"]

        await send(
            {"type": "http.response.start", "status": status, "headers": headers.raw}
        )
        await send({"type": "http.response.body", "body": bytes(body)})
