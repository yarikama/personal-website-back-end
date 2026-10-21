from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.config import API_PREFIX
from services.images import MAX_UPLOAD_BYTES

# Uploads are images. Everything else is JSON: a whole note, or the text
# before the cursor that autocomplete reads, fits many times over in 2 MB.
# The admin's uploads, and Claude Code's with an upload token (routes/upload.py).
UPLOAD_PATHS = (f"{API_PREFIX}/v1/admin/upload/", f"{API_PREFIX}/v1/uploads")
MAX_BODY_BYTES = 2 * 1024 * 1024


def limit_for(path: str) -> int:
    return MAX_UPLOAD_BYTES if path.startswith(UPLOAD_PATHS) else MAX_BODY_BYTES


class BodyLimitMiddleware:
    """
    Refuses request bodies over their limit with 413, before a route reads
    them into memory (FastAPI parses a body, and an upload's form, before
    checking the admin token). A declared Content-Length over the limit is
    refused unread; a body without one is cut off as it streams in.

    Add it before CORSMiddleware, so CORS wraps it and the browser can read
    the 413.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = limit_for(scope["path"])
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._refuse(scope, receive, send, limit)
            return

        received = 0
        started = False
        refused = False

        async def counted_receive() -> Message:
            # Over the limit, answer 413 here and tell the route the client
            # left. (An exception would not get out: FastAPI turns any error
            # while reading a body into its own 400.)
            nonlocal received, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit and not started:
                    refused = True
                    await self._refuse(scope, receive, send, limit)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            # Once refused, whatever the route answers is dropped.
            nonlocal started
            if refused:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, counted_receive, guarded_send)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        response = JSONResponse(
            {"detail": f"Request body too large (over {limit // (1024 * 1024)} MB)."},
            status_code=413,
        )
        await response(scope, receive, send)
