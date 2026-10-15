"""
The owner's GitHub contribution graph, for the admin's welcome page: the
public calendar at github.com/users/<user>/contributions (the one on the
profile page), so no token is needed. Fetched at most once an hour.

It is GitHub's page markup, not an API: each day is a <td> with data-date
and data-level (0-4), and a <tool-tip> for it says "N contributions on
...". If that changes, parsing finds no days and the admin page shows
nothing rather than breaking.
"""

import re
import time
from dataclasses import dataclass

import httpx

URL = "https://github.com/users/{user}/contributions"
CACHE_SECONDS = 60 * 60
# GitHub answers scripts without a browser-like User-Agent differently.
HEADERS = {"User-Agent": "Mozilla/5.0 (personal website admin; contribution graph)"}

_TD = re.compile(r"<td\b([^>]*\bContributionCalendar-day\b[^>]*)>", re.S)
_ATTR = re.compile(r'\b(data-date|data-level|id)="([^"]*)"')
_TIP = re.compile(r'<tool-tip\b[^>]*\bfor="([^"]+)"[^>]*>([^<]*)</tool-tip>', re.S)
_COUNT = re.compile(r"^\s*(\d[\d,]*)\s+contribution")


class GitHubUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class Day:
    date: str
    level: int
    count: int


def parse(html: str) -> list[Day]:
    """The days in the calendar, oldest first."""
    counts: dict[str, int] = {}
    for target, text in _TIP.findall(html):
        match = _COUNT.match(text)
        counts[target] = int(match.group(1).replace(",", "")) if match else 0
    days = []
    for attrs in _TD.findall(html):
        values = dict(_ATTR.findall(attrs))
        if "data-date" not in values:
            continue
        level = int(values.get("data-level", "0") or 0)
        days.append(
            Day(
                date=values["data-date"],
                level=max(0, min(level, 4)),
                count=counts.get(values.get("id", ""), 0),
            )
        )
    return sorted(days, key=lambda day: day.date)


_cache: dict[str, tuple[float, list[Day]]] = {}


async def contributions(user: str, http: httpx.AsyncClient) -> list[Day]:
    cached = _cache.get(user)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        response = await http.get(URL.format(user=user), headers=HEADERS)
    except httpx.HTTPError as error:
        raise GitHubUnavailableError(repr(error)) from error
    if response.status_code != 200:
        raise GitHubUnavailableError(f"GitHub answered {response.status_code}")
    days = parse(response.text)
    _cache[user] = (time.monotonic(), days)
    return days
