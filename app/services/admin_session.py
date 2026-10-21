"""
Admin sessions, kept in Redis and carried by an HttpOnly cookie.

The cookie holds a random token that page scripts cannot read, so a script
injected into the site cannot carry the session away. Redis keeps only the
token's SHA-256, so its contents alone do not let anyone sign in, and
deleting the key ends the session at once (signing out). Redis keeps no
data across restarts: a restart signs the owner out.
"""

import hashlib
import secrets
from typing import cast

from redis.asyncio import Redis

COOKIE = "__Host-admin_session"


def key(token: str) -> str:
    digest = hashlib.sha256(token.encode()).hexdigest()
    return f"admin:session:{digest}"


async def create(redis: Redis, email: str, ttl_seconds: int) -> str:
    token = secrets.token_urlsafe(32)
    await redis.set(key(token), email, ex=ttl_seconds)
    return token


async def lookup(redis: Redis, token: str) -> str | None:
    """The signed-in account's email, or None if the session is unknown or
    expired. Redis errors propagate: the caller decides how to fail."""
    # str: core.events.connect_redis decodes replies, which redis-py's
    # types cannot tell.
    return cast(str | None, await redis.get(key(token)))


async def delete(redis: Redis, token: str) -> None:
    await redis.delete(key(token))
