"""
The image worker process (worker.py): reconciling with R2 on start, working
the queue, and stopping on SIGTERM only once the job in progress is done.
R2 is a dict; Redis is the test's (fakeredis, or CI's real one).
"""

import asyncio
import functools
import io
import os
import signal

import pytest
from PIL import Image
from prometheus_client import REGISTRY

import worker
from services.image_jobs import STREAM
from services.images import WIDTHS, variant_key
from services.jobs import Worker

pytestmark = pytest.mark.anyio

ORIGINAL = "covers/20260101/worker.png"


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (2000, 1000), (20, 120, 200)).save(out, "PNG")
    return out.getvalue()


class FakeStorage:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.written = asyncio.Event()

    async def read(self, key):
        return self.objects[key]

    async def write(self, key, data, content_type):
        self.objects[key] = data
        self.written.set()

    async def holds(self, keys, seconds=5.0):
        """Until every one of `keys` has been written."""

        async def each_write():
            while not all(key in self.objects for key in keys):
                self.written.clear()
                await self.written.wait()

        await asyncio.wait_for(each_write(), seconds)

    async def list_keys(self):
        return list(self.objects)


class WaitsLikeRedis:
    """
    fakeredis answers a blocking XREADGROUP at once, without giving up the
    event loop, so the worker's loop would never let anything else run. A
    real Redis waits on its socket: this waits as long when nothing came.
    """

    def __init__(self, redis):
        self._redis = redis

    def __getattr__(self, name):
        return getattr(self._redis, name)

    async def xreadgroup(self, *args, block=None, **kwargs):
        reply = await self._redis.xreadgroup(*args, block=block, **kwargs)
        if not reply and block:
            await asyncio.sleep(block / 1000)
        return reply


@pytest.fixture
def storage(monkeypatch, redis):
    """The worker as main() runs it, on this Redis and a fake R2 holding one
    original without variants. Blocking reads are short, so stopping takes
    milliseconds rather than the 5 s of production."""
    fake = FakeStorage({ORIGINAL: png()})
    client = WaitsLikeRedis(redis)
    monkeypatch.setattr(worker, "REDIS_URL", "redis://worker.test")
    monkeypatch.setattr(worker, "METRICS_PORT", 0)
    monkeypatch.setattr(worker, "connect_redis", lambda url, socket_timeout: client)
    monkeypatch.setattr(worker, "storage_service", fake)
    monkeypatch.setattr(worker, "Worker", functools.partial(Worker, block_ms=10))
    return fake


async def stopped_by_sigterm(task):
    # main() has installed its handler by now: it is past the queue's start.
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, 5)


async def test_on_start_it_backfills_what_r2_is_missing(storage):
    variants = [variant_key(ORIGINAL, width) for width in WIDTHS]

    task = asyncio.create_task(worker.main())
    try:
        await storage.holds(variants)
    finally:
        await stopped_by_sigterm(task)

    assert task.done() and task.exception() is None
    assert REGISTRY.get_sample_value("jobs_dead_letters", {"queue": STREAM}) == 0


async def test_sigterm_lets_the_job_in_progress_finish(storage, monkeypatch):
    started, finished = asyncio.Event(), []

    async def slow(storage, key):
        started.set()
        await asyncio.sleep(0.2)
        finished.append(key)

    monkeypatch.setattr(worker, "make_and_store_variants", slow)
    task = asyncio.create_task(worker.main())
    await asyncio.wait_for(started.wait(), 5)

    await stopped_by_sigterm(task)

    assert finished == [ORIGINAL]


async def test_without_redis_there_is_nothing_to_run(monkeypatch):
    monkeypatch.setattr(worker, "REDIS_URL", "")

    with pytest.raises(SystemExit, match="REDIS_URL is not set"):
        await worker.main()


async def test_a_periodic_task_that_fails_runs_again():
    stop, runs = asyncio.Event(), []

    async def flaky():
        runs.append(1)
        if len(runs) == 1:
            raise RuntimeError("R2 unreachable")
        stop.set()

    await asyncio.wait_for(worker.every(0.01, stop, flaky), 5)

    assert len(runs) == 2
