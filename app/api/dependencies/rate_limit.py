import math
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, status

from services.rate_limit import (
    Decision,
    LimiterUnavailableError,
    RateLimiter,
    Rule,
    client_id,
    retry_after_header,
)


def get_rate_limiter(app) -> RateLimiter | None:
    # Set in the lifespan when REDIS_URL is configured; without it (local
    # development, most tests) nothing is limited.
    return getattr(app.state, "rate_limiter", None)


def wait_in_words(seconds: float) -> str:
    if seconds < 90:
        return f"{max(1, math.ceil(seconds))} seconds"
    return f"{math.ceil(seconds / 60)} minutes"


def too_many_requests(decision: Decision, what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many {what}. Try again in {wait_in_words(decision.retry_after)}.",
        headers={"Retry-After": retry_after_header(decision)},
    )


class Limited:
    """What a route learns from its rate limit check."""

    def __init__(self, limiter: RateLimiter | None, rule: Rule, client: str) -> None:
        self._limiter = limiter
        self._rule = rule
        self.client = client

    async def reset(self) -> None:
        if self._limiter is not None:
            await self._limiter.reset(self._rule, self.client)


def rate_limit(
    rule: Rule, what: str, client: str | None = None
) -> Callable[[Request], Awaitable[Limited]]:
    """
    A dependency that counts the request against `rule` and answers 429 once
    the client is over it (`what` names the requests in the message). A
    fixed `client` counts every request against one shared limit.
    """

    async def check(request: Request) -> Limited:
        limiter = get_rate_limiter(request.app)
        who = client or client_id(
            request.headers, request.client and request.client.host
        )
        if limiter is None:
            return Limited(None, rule, who)
        try:
            decision = await limiter.hit(rule, who)
        except LimiterUnavailableError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Temporarily unavailable. Try again shortly.",
                headers={"Retry-After": "30"},
            ) from error
        if not decision.allowed:
            raise too_many_requests(decision, what)
        return Limited(limiter, rule, who)

    return check
