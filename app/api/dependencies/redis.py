from redis.asyncio import Redis
from starlette.applications import Starlette


def get_redis(app: Starlette) -> Redis | None:
    # Set in the lifespan when REDIS_URL is configured; without it (local
    # development, most tests) there is none, and each caller decides what
    # that means: no sign-in, no conversation history, no image queue.
    return getattr(app.state, "redis", None)
