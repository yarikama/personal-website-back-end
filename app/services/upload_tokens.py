"""
Single-use upload tokens, for the writing MCP server's images. A model
cannot carry an image's bytes in a tool call, so a tool hands Claude Code a
token instead, and Claude Code sends the file from the owner's machine with
it (POST /api/v1/uploads, in an Authorization header, never in the URL).

A token lasts TTL_SECONDS, works once, and allows one image into one folder
for the account it was made for. Redis keeps only its SHA-256.
"""

import hashlib
import json
import secrets
from typing import TypedDict, cast

from redis.asyncio import Redis

TTL_SECONDS = 10 * 60


class Grant(TypedDict):
    folder: str
    email: str


def key(token: str) -> str:
    return f"upload:token:{hashlib.sha256(token.encode()).hexdigest()}"


async def issue(redis: Redis, folder: str, email: str) -> str:
    token = secrets.token_urlsafe(32)
    grant: Grant = {"folder": folder, "email": email}
    await redis.set(key(token), json.dumps(grant), ex=TTL_SECONDS)
    return token


async def take(redis: Redis, token: str) -> Grant | None:
    """What the token allows, once; None if it is unknown, used or expired."""
    raw = await redis.getdel(key(token))
    return None if raw is None else cast(Grant, json.loads(raw))
