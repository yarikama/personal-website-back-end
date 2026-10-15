"""
A small job queue on Redis Streams with a consumer group.

- enqueue() appends a job (XADD); the stream is capped so it cannot grow
  without bound.
- Workers read new jobs with XREADGROUP and acknowledge each (XACK) only
  after it succeeds: delivery is at least once, so handlers must be
  idempotent.
- A job whose worker failed or died stays pending. After claim_idle_ms any
  worker takes it over (XAUTOCLAIM) and tries again; that wait is also the
  retry delay.
- After max_deliveries attempts it goes to a dead-letter stream with the
  last error, and is acknowledged so nobody retries it again.

Why not a library (arq, RQ, Celery): the point was to see these mechanics.
Design and trade-offs: homelab docs/13-background-jobs.md.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import cast

from loguru import logger
from prometheus_client import Counter, Histogram
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from redis.typing import EncodableT, FieldT

JOBS = Counter("jobs_total", "Jobs handled, by queue and result.", ["queue", "result"])
DURATION = Histogram(
    "job_duration_seconds",
    "Time spent in a job's handler, by queue.",
    ["queue"],
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

Handler = Callable[[dict[str, str]], Awaitable[None]]

# A stream entry as XREADGROUP and XAUTOCLAIM return it: its id and fields,
# as str (core.events.connect_redis decodes replies). redis-py types every
# reply as a union of all commands' shapes.
Entry = tuple[str, dict[str, str]]


def dead_letter_stream(stream: str) -> str:
    return f"{stream}:dead"


async def enqueue(redis: Redis, stream: str, job: dict[str, str]) -> str:
    """Add a job; returns its stream id."""
    fields: dict[FieldT, EncodableT] = {**job}
    job_id = await redis.xadd(stream, fields, maxlen=10_000, approximate=True)
    return cast(str, job_id)


class Worker:
    def __init__(
        self,
        redis: Redis,
        stream: str,
        group: str,
        consumer: str,
        handler: Handler,
        *,
        max_deliveries: int = 5,
        claim_idle_ms: int = 60_000,
        block_ms: int = 5_000,
    ) -> None:
        self.redis = redis
        self.stream = stream
        self.group = group
        self.consumer = consumer
        self.handler = handler
        self.max_deliveries = max_deliveries
        self.claim_idle_ms = claim_idle_ms
        self.block_ms = block_ms
        self.last_error: dict[str, str] = {}

    def start_metrics_at_zero(self) -> None:
        # So increase() sees the first failure (see services.rate_limit).
        for result in ("done", "failed", "dead"):
            JOBS.labels(self.stream, result)

    async def ensure_group(self) -> None:
        # From "0": a new group also sees jobs added before it existed.
        try:
            await self.redis.xgroup_create(
                self.stream, self.group, id="0", mkstream=True
            )
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    async def _claim_stale(self) -> list[Entry]:
        """Jobs another (or an earlier, crashed) worker left unacknowledged.
        A pending job trimmed from the stream meanwhile is not among them:
        since Redis 7, XAUTOCLAIM drops it from the pending list itself."""
        reply = await self.redis.xautoclaim(
            self.stream,
            self.group,
            self.consumer,
            min_idle_time=self.claim_idle_ms,
            start_id="0-0",
            count=10,
        )
        return cast(list[Entry], reply[1])

    async def _deliveries(self, key: str) -> int:
        pending = await self.redis.xpending_range(
            self.stream, self.group, min=key, max=key, count=1
        )
        return int(pending[0]["times_delivered"]) if pending else 1

    async def _handle(self, key: str, job: dict[str, str]) -> None:
        if await self._deliveries(key) > self.max_deliveries:
            await self.redis.xadd(
                dead_letter_stream(self.stream),
                {**job, "job_id": key, "error": self.last_error.pop(key, "unknown")},
                maxlen=1_000,
                approximate=True,
            )
            await self.redis.xack(self.stream, self.group, key)
            JOBS.labels(self.stream, "dead").inc()
            logger.error(f"Job {key} on {self.stream} gave up: {job}")
            return

        started = time.perf_counter()
        try:
            await self.handler(job)
        except Exception as error:  # a job must never take the worker down
            self.last_error[key] = repr(error)[:500]
            JOBS.labels(self.stream, "failed").inc()
            logger.warning(f"Job {key} on {self.stream} failed, will retry: {error!r}")
            return
        finally:
            DURATION.labels(self.stream).observe(time.perf_counter() - started)
        await self.redis.xack(self.stream, self.group, key)
        self.last_error.pop(key, None)
        JOBS.labels(self.stream, "done").inc()

    async def run_once(self) -> int:
        """Handle stale jobs, else wait up to block_ms for a new one."""
        jobs = await self._claim_stale()
        if not jobs:
            reply = await self.redis.xreadgroup(
                self.group,
                self.consumer,
                {self.stream: ">"},
                count=1,
                block=self.block_ms,
            )
            # [[stream, entries]], one stream asked for.
            streams = cast(list[tuple[str, list[Entry]]], reply)
            jobs = streams[0][1] if streams else []
        for job_id, fields in jobs:
            await self._handle(job_id, fields)
        return len(jobs)

    async def run(self, stop: asyncio.Event) -> None:
        """Until stop is set; a job in progress always finishes first."""
        self.start_metrics_at_zero()
        await self.ensure_group()
        while not stop.is_set():
            try:
                await self.run_once()
            # The loop is the worker: it outlives Redis restarts.
            except (RedisError, OSError) as error:  # noqa: PERF203
                logger.warning(f"Queue {self.stream}: Redis unavailable: {error!r}")
                await asyncio.sleep(5)


async def queue_depth(redis: Redis, stream: str, group: str) -> dict[str, int] | None:
    """Jobs not yet read (lag) and read but unacknowledged (pending)."""
    for info in await redis.xinfo_groups(stream):
        if info["name"] == group:
            return {"lag": info.get("lag") or 0, "pending": info["pending"]}
    return None
