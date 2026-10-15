"""
The site's visitors, for the admin: Vercel Web Analytics, read through its
REST API. The front end sends a page view for each page opened, except
admin pages and any browser that has signed in to the admin, so these are
other people.

Vercel counts production only, splits days at midnight UTC, and keeps a
month on the Hobby plan. Each range is asked at most every ten minutes. Its
query API is slow and uneven (3 to 17 s for the six queries, measured on
2026-10-05), so when it fails the last report for that range is kept.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from loguru import logger

URL = "https://api.vercel.com/v1/query/web-analytics/visits/aggregate"
CACHE_SECONDS = 10 * 60
MAX_DAYS = 30
TOP = 10


class VercelUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class Count:
    # A day (YYYY-MM-DD), a path, a referrer's host, a country code or a
    # device type. Vercel groups whatever is past the top ten as "Others";
    # an empty referrer is a visit with none (typed in, or a bookmark).
    name: str
    pageviews: int
    visitors: int


@dataclass(frozen=True)
class Report:
    since: str
    until: str
    pageviews: int
    visitors: int
    days: tuple[Count, ...]
    pages: tuple[Count, ...]
    referrers: tuple[Count, ...]
    countries: tuple[Count, ...]
    devices: tuple[Count, ...]


@dataclass(frozen=True)
class Vercel:
    token: str = field(repr=False)
    project: str
    team: str


def _count(name: object, row: dict[str, Any]) -> Count:
    return Count(
        name="" if name is None else str(name),
        pageviews=int(row.get("pageviews") or 0),
        visitors=int(row.get("visitors") or 0),
    )


async def _aggregate(
    http: httpx.AsyncClient,
    vercel: Vercel,
    by: str,
    since: datetime,
    until: datetime,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, str | int] = {
        "projectId": vercel.project,
        "by": by,
        # Milliseconds, so the range is exactly what was asked for.
        "since": int(since.timestamp() * 1000),
        "until": int(until.timestamp() * 1000),
    }
    if vercel.team:
        params["slug"] = vercel.team
    if limit:
        params["limit"] = limit
    try:
        response = await http.get(
            URL, params=params, headers={"Authorization": f"Bearer {vercel.token}"}
        )
    except httpx.HTTPError as error:
        raise VercelUnavailableError(repr(error)) from error
    if response.status_code != 200:
        raise VercelUnavailableError(
            f"Vercel answered {response.status_code} for by={by}: {response.text[:200]}"
        )
    try:
        rows = response.json()["data"]
    except (ValueError, KeyError, TypeError) as error:
        raise VercelUnavailableError(f"Unexpected answer for by={by}") from error
    if not isinstance(rows, list):
        raise VercelUnavailableError(f"Unexpected answer for by={by}")
    return [row for row in rows if isinstance(row, dict)]


_cache: dict[tuple[Vercel, int], tuple[float, Report]] = {}


async def report(
    days: int,
    http: httpx.AsyncClient,
    vercel: Vercel,
    now: datetime | None = None,
) -> Report:
    """The last `days` days, today (UTC) included."""
    key = (vercel, days)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]

    until = now or datetime.now(timezone.utc)
    first = until.date() - timedelta(days=days - 1)
    since = datetime(first.year, first.month, first.day, tzinfo=timezone.utc)

    async def ask(by: str, limit: int | None = None) -> list[dict[str, Any]]:
        return await _aggregate(http, vercel, by, since, until, limit)

    try:
        # One row per environment, and only production is counted: the totals.
        totals, daily, pages, referrers, countries, devices = await asyncio.gather(
            ask("environment"),
            ask("day"),
            ask("requestPath", TOP),
            ask("referrerHostname", TOP),
            ask("country", TOP),
            ask("deviceType", TOP),
        )
    except VercelUnavailableError as error:
        if not cached:
            raise
        logger.warning(
            f"Vercel Web Analytics unavailable, keeping the last report: {error}"
        )
        return cached[1]

    # Days without a visit are missing from the answer; they show as zero.
    by_day = {str(row.get("timestamp", ""))[:10]: row for row in daily}
    all_days = [(first + timedelta(days=i)).isoformat() for i in range(days)]

    def top(rows: list[dict[str, Any]], dimension: str) -> tuple[Count, ...]:
        counts = [_count(row.get(dimension), row) for row in rows]
        return tuple(sorted(counts, key=lambda count: count.pageviews, reverse=True))

    result = Report(
        since=first.isoformat(),
        until=until.date().isoformat(),
        pageviews=sum(_count(None, row).pageviews for row in totals),
        visitors=sum(_count(None, row).visitors for row in totals),
        days=tuple(_count(day, by_day.get(day, {})) for day in all_days),
        pages=top(pages, "requestPath"),
        referrers=top(referrers, "referrerHostname"),
        countries=top(countries, "country"),
        devices=top(devices, "deviceType"),
    )
    _cache[key] = (time.monotonic(), result)
    return result
