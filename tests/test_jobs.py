import asyncio

import pytest
from prometheus_client import REGISTRY
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from services import jobs
from services.jobs import Worker, dead_letter_stream, enqueue, queue_depth

pytestmark = pytest.mark.anyio

STREAM = "jobs:test"
GROUP = "testers"


def worker(redis, handler, consumer="w1", **kwargs):
    kwargs.setdefault("claim_idle_ms", 60_000)
    return Worker(redis, STREAM, GROUP, consumer, handler, block_ms=10, **kwargs)


async def pending(redis) -> int:
    depth = await queue_depth(redis, STREAM, GROUP)
    assert depth is not None, "the group exists"
    return depth["pending"]


async def test_a_job_is_handled_once_and_acknowledged(redis):
    seen = []

    async def handle(job):
        seen.append(job)

    w = worker(redis, handle)
    await w.ensure_group()
    await enqueue(redis, STREAM, {"key": "covers/a.png"})

    assert await w.run_once() == 1
    assert await w.run_once() == 0
    assert seen == [{"key": "covers/a.png"}]
    assert await queue_depth(redis, STREAM, GROUP) == {"lag": 0, "pending": 0}


async def test_jobs_added_before_the_group_existed_are_not_lost(redis):
    seen = []

    async def handle(job):
        seen.append(job["key"])

    await enqueue(redis, STREAM, {"key": "early"})
    w = worker(redis, handle)
    await w.ensure_group()
    await w.ensure_group()  # a second worker starting is fine

    await w.run_once()

    assert seen == ["early"]


async def test_a_failed_job_stays_pending_and_is_retried(redis):
    attempts = []

    async def flaky(job):
        attempts.append(job["key"])
        if len(attempts) == 1:
            raise RuntimeError("R2 hiccup")

    w = worker(redis, flaky, claim_idle_ms=0)
    await w.ensure_group()
    await enqueue(redis, STREAM, {"key": "a"})

    await w.run_once()
    assert await pending(redis) == 1
    await w.run_once()

    assert attempts == ["a", "a"]
    assert await pending(redis) == 0


async def test_another_worker_takes_over_a_crashed_workers_job(redis):
    seen = []

    async def handle(job):
        seen.append(job["key"])

    crashed = worker(redis, handle, consumer="crashed")
    await crashed.ensure_group()
    await enqueue(redis, STREAM, {"key": "a"})
    # It read the job and died before acknowledging it.
    await redis.xreadgroup(GROUP, "crashed", {STREAM: ">"}, count=1)

    rescuer = worker(redis, handle, consumer="rescuer", claim_idle_ms=0)
    await rescuer.run_once()

    assert seen == ["a"]
    assert await pending(redis) == 0


async def test_a_job_that_keeps_failing_goes_to_the_dead_letter_stream(redis):
    async def broken(job):
        raise ValueError("cannot identify image file")

    w = worker(redis, broken, claim_idle_ms=0, max_deliveries=3)
    await w.ensure_group()
    await enqueue(redis, STREAM, {"key": "corrupt.png"})

    for _ in range(4):
        await w.run_once()

    [(_, dead)] = await redis.xrange(dead_letter_stream(STREAM))
    assert dead[b"key"] == b"corrupt.png"
    assert b"cannot identify image file" in dead[b"error"]
    assert await pending(redis) == 0
    assert await w.run_once() == 0


async def test_run_works_the_queue_until_stopped(redis):
    stop, seen = asyncio.Event(), []

    async def handle(job):
        seen.append(job)
        stop.set()

    await enqueue(redis, STREAM, {"key": "covers/a.png"})

    await asyncio.wait_for(worker(redis, handle).run(stop), 5)

    assert seen == [{"key": "covers/a.png"}]
    # Counted from zero, so the first failure shows up in increase().
    sample = {"queue": STREAM, "result": "failed"}
    assert REGISTRY.get_sample_value("jobs_total", sample) is not None


async def test_run_waits_out_a_redis_outage(redis, monkeypatch):
    stop, waits = asyncio.Event(), []
    w = worker(redis, None)
    replies = iter([RedisConnectionError("Redis restarting"), 0])

    async def run_once():
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        stop.set()
        return reply

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(w, "run_once", run_once)
    monkeypatch.setattr(jobs.asyncio, "sleep", sleep)

    await w.run(stop)

    assert waits == [5]


async def test_an_error_creating_the_group_other_than_it_existing_is_raised(redis):
    await redis.set(STREAM, "not a stream")

    with pytest.raises(ResponseError, match="WRONGTYPE"):
        await worker(redis, None).ensure_group()


async def test_a_job_trimmed_from_the_stream_while_pending_is_dropped(redis):
    async def broken(job):
        raise ValueError("fails, stays pending")

    w = worker(redis, broken, claim_idle_ms=0)
    await w.ensure_group()
    job_id = await enqueue(redis, STREAM, {"key": "covers/a.png"})
    await w.run_once()  # delivered, failed: pending
    await redis.xdel(STREAM, job_id)

    await w.run_once()

    assert await pending(redis) == 0


async def test_there_is_no_depth_for_a_group_that_does_not_exist(redis):
    await worker(redis, None).ensure_group()

    assert await queue_depth(redis, STREAM, "nobody") is None
