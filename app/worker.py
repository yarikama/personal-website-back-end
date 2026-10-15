"""
Background worker: makes WebP variants of uploaded images.

    python -m worker            (PYTHONPATH=app, as in the image)

Runs the image queue until SIGTERM, letting a job in progress finish.
Reconciles with R2 on start and every hour, and serves Prometheus metrics
on METRICS_PORT. Design: homelab docs/13-background-jobs.md.
"""

import asyncio
import contextlib
import signal
import socket

from loguru import logger
from prometheus_client import Gauge, start_http_server
from redis.exceptions import RedisError

from core.config import METRICS_PORT, REDIS_URL
from core.events import connect_redis
from services.image_jobs import GROUP, STREAM, make_and_store_variants, reconcile
from services.jobs import Worker, dead_letter_stream, queue_depth
from services.storage import storage_service

RECONCILE_EVERY_SECONDS = 3600
DEPTH_EVERY_SECONDS = 15

QUEUE_LAG = Gauge("jobs_queue_lag", "Jobs added but not yet read.", ["queue"])
QUEUE_PENDING = Gauge(
    "jobs_queue_pending", "Jobs read but not yet acknowledged.", ["queue"]
)
DEAD_LETTERS = Gauge("jobs_dead_letters", "Jobs that gave up.", ["queue"])


async def every(seconds: float, stop: asyncio.Event, action) -> None:
    while not stop.is_set():
        try:
            await action()
        except Exception as error:  # keep the loop alive; the next run retries
            logger.warning(f"{action.__name__} failed: {error!r}")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=seconds)


async def main() -> None:
    if not str(REDIS_URL):
        raise SystemExit("REDIS_URL is not set; the worker has no queue to read")
    if METRICS_PORT:
        start_http_server(METRICS_PORT)
    # Blocking reads wait up to 5 s, longer than the API's 250 ms timeout.
    redis = connect_redis(str(REDIS_URL), socket_timeout=10)

    async def handle(job: dict[str, str]) -> None:
        await make_and_store_variants(storage_service, job["key"])

    worker = Worker(redis, STREAM, GROUP, socket.gethostname(), handle)
    await worker.ensure_group()

    async def reconcile_with_r2() -> None:
        await reconcile(storage_service, redis)

    async def record_depth() -> None:
        try:
            depth = await queue_depth(redis, STREAM, GROUP)
            dead = await redis.xlen(dead_letter_stream(STREAM))
        except RedisError:
            return
        if depth:
            QUEUE_LAG.labels(STREAM).set(depth["lag"])
            QUEUE_PENDING.labels(STREAM).set(depth["pending"])
        DEAD_LETTERS.labels(STREAM).set(dead)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)

    logger.info(f"Image worker {socket.gethostname()} started")
    background = [
        asyncio.create_task(every(RECONCILE_EVERY_SECONDS, stop, reconcile_with_r2)),
        asyncio.create_task(every(DEPTH_EVERY_SECONDS, stop, record_depth)),
    ]
    # Only a job in progress is worth waiting for on shutdown; a reconcile
    # cut short simply runs again at the next start.
    await worker.run(stop)
    for task in background:
        task.cancel()
    await asyncio.gather(*background, return_exceptions=True)
    await redis.aclose()
    logger.info("Image worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
