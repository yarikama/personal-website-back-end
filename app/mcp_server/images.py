"""
Images for the notes and the projects' covers, kept in R2 like the admin's
uploads (services/storage.py), with the same checks and WebP variants.

A model cannot put a file's bytes in a tool call, so there are two ways in:
Claude Code sends a file from the owner's machine with an upload token
(start_image_upload), and claude.ai, which has no files to send, has one
fetched from a URL (import_image_from_url).
"""

import shlex
from collections.abc import Callable
from typing import Annotated, Any, Literal

from loguru import logger
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field
from redis.asyncio import Redis

from mcp_server.common import OUTWARD, WRITE, who
from services import remote_images, upload_tokens
from services.image_jobs import queue_variants
from services.storage import UploadRefusedError, storage_service

Folder = Annotated[
    Literal["notes", "covers", "images"],
    Field(description="notes: in a note's body; covers: a project's cover"),
]
# Fetches from the internet: the one tool here that leaves the site.
FETCH = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)


def register(
    server: MCPServer, redis: Callable[[], Redis | None], upload_url: str
) -> None:
    """`redis` returns the app's Redis, which only exists once it has
    started; `upload_url` is where Claude Code sends files
    (POST /api/v1/uploads)."""

    def need_redis() -> Redis:
        found = redis()
        if found is None:
            raise ToolError("Uploads are unavailable: the server has no Redis.")
        return found

    @server.tool(annotations=WRITE)
    async def start_image_upload(folder: Folder = "notes") -> dict[str, Any]:
        """
        For Claude Code: a single-use, 10-minute token to send one image file
        from this machine. Run `command` with the file's path in place of
        PATH; the answer's data.url is the image's URL. Embed it in a note as
        ![what it shows](url), or set it as a project's cover_image.
        """
        token = await upload_tokens.issue(need_redis(), folder, who())
        header = shlex.quote(f"Authorization: Bearer {token}")
        command = (
            f"curl -sS --fail-with-body -H {header} -F 'file=@PATH' "
            f"{shlex.quote(upload_url)}"
        )
        logger.info(f"MCP start_image_upload by {who()}: {folder}")
        return {
            "command": command,
            "expires_in_seconds": upload_tokens.TTL_SECONDS,
            "folder": folder,
        }

    @server.tool(annotations=FETCH)
    async def import_image_from_url(
        url: Annotated[
            str, Field(description="The image's own https:// URL (no redirects)")
        ],
        folder: Folder = "notes",
    ) -> dict[str, Any]:
        """
        Copies an image from the web into the site's storage: for claude.ai,
        which cannot send a file. JPEG, PNG, GIF or WebP, up to 20 MB, from
        a public https URL. Returns its new URL.
        """
        try:
            data = await remote_images.fetch(url)
            stored = await storage_service.upload_bytes(data, folder)
        except UploadRefusedError as error:
            raise ToolError(str(error)) from error
        await queue_variants(redis(), storage_service.key_for(stored))
        logger.info(f"MCP import_image_from_url by {who()}: {url} -> {stored}")
        return {"url": stored, "markdown": f"![]({stored})"}

    @server.tool(annotations=OUTWARD)
    async def delete_image(
        url: Annotated[str, Field(description="An image URL in the site's storage")],
    ) -> str:
        """Deletes an image and its WebP variants for good. Only when the
        owner asks, and only once nothing shows it any more."""
        if not url.startswith(f"{storage_service.public_url}/"):
            raise ToolError("That URL is not in the site's storage.")
        await storage_service.delete_image(url)
        logger.info(f"MCP delete_image by {who()}: {url}")
        return f"Deleted {url} and its variants."
