from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from loguru import logger
from prometheus_client import start_http_server
from redis.asyncio import Redis

from core.config import METRICS_PORT, REDIS_URL
from db.session import engine
from services.rate_limit import RateLimiter


def connect_redis(url: str, socket_timeout: float = 0.25) -> Redis:
    # Redis answers in well under a millisecond in the cluster. Short
    # timeouts keep a hung Redis from holding requests: the check fails and
    # the rule's fail-open or fail-closed choice decides instead.
    # decode_responses: everything kept in Redis is text (emails, JSON, job
    # fields), so replies come back as str rather than bytes.
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=socket_timeout,
        socket_connect_timeout=0.25,
        health_check_interval=30,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    if METRICS_PORT:
        start_http_server(METRICS_PORT)
    redis = None
    if str(REDIS_URL):
        redis = connect_redis(str(REDIS_URL))
        app.state.redis = redis
        app.state.rate_limiter = RateLimiter(redis)
    else:
        logger.warning("REDIS_URL is not set: requests are not rate limited")
    try:
        async with AsyncExitStack() as stack:
            # The writing MCP server's (mcp_server/server.py), when it is on:
            # FastAPI does not run the lifespan of an app it routes to.
            mcp = getattr(app.state, "mcp", None)
            if mcp is not None:
                await stack.enter_async_context(mcp.session_manager.run())
            yield
    finally:
        if redis is not None:
            await redis.aclose()
        # Its pooled connections belong to this event loop; close them here
        # rather than leave them to be dropped when the loop is gone.
        await engine.dispose()
