"""
The image queue: which jobs exist, what one does, and how missed ones are
found again. The worker process (worker.py) runs them.
"""

import asyncio
from typing import Protocol, cast

from loguru import logger
from redis.asyncio import Redis
from services.images import WIDTHS, is_original, make_variants, variant_key
from services.jobs import Entry, dead_letter_stream, decode, enqueue

STREAM = "jobs:images"
GROUP = "image-workers"


class Storage(Protocol):
    async def read(self, key: str) -> bytes: ...
    async def write(self, key: str, data: bytes, content_type: str) -> None: ...
    async def list_keys(self) -> list[str]: ...


async def enqueue_variants(redis: Redis, key: str) -> None:
    if is_original(key):
        await enqueue(redis, STREAM, {"key": key})


async def make_and_store_variants(storage: Storage, key: str) -> None:
    """Idempotent: running it twice writes the same variants again."""
    data = await storage.read(key)
    # Decoding and encoding take a CPU for a second or so: off the event
    # loop, so the worker keeps answering Redis and metrics meanwhile.
    variants = await asyncio.to_thread(make_variants, data)
    for width, body in variants.items():
        await storage.write(variant_key(key, width), body, "image/webp")
    logger.info(
        f"Image {key}: {len(data):,} bytes -> "
        + ", ".join(f"w{w} {len(b):,}" for w, b in variants.items())
    )


def missing_variants(keys: list[str]) -> list[str]:
    present = set(keys)
    return [
        key
        for key in keys
        if is_original(key)
        and not all(variant_key(key, width) in present for width in WIDTHS)
    ]


async def given_up(redis: Redis) -> set[str]:
    """Keys whose jobs are in the dead-letter stream."""
    entries = cast(list[Entry], await redis.xrange(dead_letter_stream(STREAM)))
    return {decode(fields).get("key", "") for _, fields in entries}


async def reconcile(storage: Storage, redis: Redis) -> int:
    """
    Queue every original whose variants are missing: images uploaded before
    the worker existed, and jobs lost because Redis keeps no data across a
    restart. Queuing one twice is harmless.

    Images whose job already gave up (a corrupt file, say) are skipped: an
    hourly retry would only fail the same way and dead-letter them again.
    Clearing the dead-letter stream lets the next reconcile try them anew.
    """
    skipped = await given_up(redis)
    missing = [
        key for key in missing_variants(await storage.list_keys()) if key not in skipped
    ]
    for key in missing:
        await enqueue_variants(redis, key)
    if missing:
        logger.info(f"Reconcile: queued {len(missing)} image(s) without variants")
    return len(missing)
