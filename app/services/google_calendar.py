"""
The owner's Google Calendar on the admin's welcome page, read-only.

Connecting (GET /auth/google/calendar, api/routes/auth.py) runs Google
sign-in with read-only calendar access and keeps the refresh token it
returns, encrypted, in admin_settings under KEY. Reading trades that for an
access token (kept in memory until just before it expires), lists every
calendar in the owner's list, and reads each one's events over the asked
range with recurring events expanded. Each range's report is kept for
CACHE_SECONDS.

The key that encrypts the refresh token is derived from GOOGLE_CLIENT_SECRET,
so the database alone does not give the token away. Rotating that secret
means connecting again; so does Google revoking the token, which it does
after 7 days while the OAuth app is still in Testing.
"""

import asyncio
import base64
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.admin_setting import AdminSetting
from services.google_oauth import TOKEN_URL, Client

KEY = "google_calendar"
API = "https://www.googleapis.com/calendar/v3"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CACHE_SECONDS = 5 * 60
MAX_DAYS = 14
# Renew an access token this long before Google's expiry.
EXPIRY_MARGIN_SECONDS = 60
# Google's most per page; more events than this in a calendar over two
# weeks is not a calendar a person reads.
PAGE_SIZE = 250
TIMEOUT_SECONDS = 15


class NotConnectedError(Exception):
    """No calendar is connected, or Google no longer accepts its token
    (`expired`): the owner connects it again."""

    def __init__(self, expired: bool = False) -> None:
        super().__init__("calendar token no longer accepted" if expired else "")
        self.expired = expired


class CalendarUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class Connection:
    email: str
    refresh_token: str


@dataclass(frozen=True)
class Calendar:
    id: str
    name: str
    color: str


@dataclass(frozen=True)
class Event:
    id: str
    calendar_id: str
    title: str
    # ISO 8601: a date for an all-day event (its end the day after it ends,
    # as Google gives it), else a date and time with its offset.
    start: str
    end: str
    all_day: bool
    location: str
    url: str


@dataclass(frozen=True)
class Report:
    email: str
    calendars: tuple[Calendar, ...]
    events: tuple[Event, ...]
    # Calendars that could not be read this time, by name.
    failed: tuple[str, ...]


def _fernet(client_secret: str) -> Fernet:
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"yarikama admin: google calendar refresh token",
    ).derive(client_secret.encode())
    return Fernet(base64.urlsafe_b64encode(key))


async def save(db: AsyncSession, client: Client, connection: Connection) -> None:
    """Keeps the connection, encrypted; the caller commits."""
    plain = json.dumps(
        {"email": connection.email, "refresh_token": connection.refresh_token}
    )
    value = _fernet(client.client_secret).encrypt(plain.encode()).decode()
    row = await db.get(AdminSetting, KEY)
    if row is None:
        db.add(AdminSetting(key=KEY, value=value))
    else:
        row.value = value
    clear_caches()


async def load(db: AsyncSession, client: Client) -> Connection | None:
    row = await db.get(AdminSetting, KEY)
    if row is None:
        return None
    try:
        plain = _fernet(client.client_secret).decrypt(row.value.encode())
    except InvalidToken:
        # Encrypted under an earlier client secret: connect again.
        logger.warning("The calendar token does not decrypt; connect it again")
        return None
    kept = json.loads(plain)
    return Connection(email=kept["email"], refresh_token=kept["refresh_token"])


async def forget(db: AsyncSession) -> None:
    """Drops the connection; the caller commits."""
    row = await db.get(AdminSetting, KEY)
    if row is not None:
        await db.delete(row)
    clear_caches()


async def revoke(connection: Connection, http: httpx.AsyncClient) -> None:
    """Asks Google to drop the token too. Best effort: forgetting it here is
    what disconnects the calendar."""
    try:
        await http.post(REVOKE_URL, data={"token": connection.refresh_token})
    except httpx.HTTPError as error:
        logger.warning(f"Could not revoke the calendar token: {error!r}")


# By a hash of the refresh token: (monotonic expiry, access token).
_access: dict[str, tuple[float, str]] = {}
# By (refresh token hash, start, days): (monotonic time, report).
_reports: dict[tuple[str, str, int], tuple[float, Report]] = {}


def clear_caches() -> None:
    _access.clear()
    _reports.clear()


def _token_key(connection: Connection) -> str:
    return hashlib.sha256(connection.refresh_token.encode()).hexdigest()


async def access_token(
    connection: Connection, client: Client, http: httpx.AsyncClient
) -> str:
    key = _token_key(connection)
    cached = _access.get(key)
    if cached and time.monotonic() < cached[0]:
        return cached[1]
    try:
        response = await http.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": connection.refresh_token,
                "client_id": client.client_id,
                "client_secret": client.client_secret,
            },
        )
    except httpx.HTTPError as error:
        raise CalendarUnavailableError(f"token endpoint: {error!r}") from error
    if response.status_code == 400 and "invalid_grant" in response.text:
        # Revoked, expired (7 days in Testing), or the password changed.
        raise NotConnectedError(expired=True)
    if response.status_code != 200:
        raise CalendarUnavailableError(
            f"token endpoint answered {response.status_code}: {response.text[:200]}"
        )
    body = response.json()
    token = str(body["access_token"])
    lifetime = float(body.get("expires_in", 3600)) - EXPIRY_MARGIN_SECONDS
    _access[key] = (time.monotonic() + max(lifetime, 0), token)
    return token


async def _get(
    http: httpx.AsyncClient, token: str, path: str, params: dict[str, Any]
) -> dict[str, Any]:
    try:
        response = await http.get(
            f"{API}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError as error:
        raise CalendarUnavailableError(f"{path}: {error!r}") from error
    if response.status_code == 401:
        # The access token was revoked early: the next read asks for another.
        _access.clear()
    if response.status_code != 200:
        raise CalendarUnavailableError(
            f"{path} answered {response.status_code}: {response.text[:200]}"
        )
    return response.json()


def _event(calendar: Calendar, item: dict[str, Any], free_busy: bool) -> Event | None:
    if item.get("status") == "cancelled":
        return None
    start, end = item.get("start") or {}, item.get("end") or {}
    all_day = "date" in start
    title = item.get("summary") or ("Busy" if free_busy else "(No title)")
    return Event(
        id=str(item.get("id", "")),
        calendar_id=calendar.id,
        title=str(title),
        start=str(start.get("date") or start.get("dateTime") or ""),
        end=str(end.get("date") or end.get("dateTime") or ""),
        all_day=all_day,
        location=str(item.get("location") or ""),
        url=str(item.get("htmlLink") or ""),
    )


def _sort_key(event: Event, since: datetime) -> datetime:
    """All-day events first in their day, then by time."""
    if event.all_day:
        day = date.fromisoformat(event.start)
        return datetime(day.year, day.month, day.day, tzinfo=since.tzinfo)
    return datetime.fromisoformat(event.start)


async def report(
    connection: Connection,
    client: Client,
    http: httpx.AsyncClient,
    since: datetime,
    days: int,
) -> Report:
    """Events from `since` (the start of the owner's day, with its offset)
    over `days` days, in every calendar in the owner's list."""
    key = (_token_key(connection), since.isoformat(), days)
    cached = _reports.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]

    token = await access_token(connection, client, http)
    listed = await _get(
        http,
        token,
        "/users/me/calendarList",
        {
            "maxResults": PAGE_SIZE,
            "fields": "items(id,summary,summaryOverride,backgroundColor,"
            "accessRole,primary)",
        },
    )
    items = listed.get("items") or []
    # The primary calendar first, then as Google lists them.
    items.sort(key=lambda item: not item.get("primary"))
    calendars = [
        Calendar(
            id=str(item["id"]),
            name=str(item.get("summaryOverride") or item.get("summary") or item["id"]),
            color=str(item.get("backgroundColor") or ""),
        )
        for item in items
        if item.get("id")
    ]
    roles = {
        str(item["id"]): item.get("accessRole") for item in items if item.get("id")
    }

    until = since + timedelta(days=days)

    async def events_of(calendar: Calendar) -> list[dict[str, Any]]:
        body = await _get(
            http,
            token,
            f"/calendars/{quote(calendar.id, safe='')}/events",
            {
                "timeMin": since.isoformat(),
                "timeMax": until.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": PAGE_SIZE,
                "fields": "items(id,status,summary,start,end,location,htmlLink)",
            },
        )
        return body.get("items") or []

    results = await asyncio.gather(
        *(events_of(calendar) for calendar in calendars), return_exceptions=True
    )
    events: list[Event] = []
    failed: list[str] = []
    for calendar, got in zip(calendars, results, strict=True):
        if isinstance(got, BaseException):
            # One calendar that can't be read (a removed subscription, say)
            # leaves the rest shown.
            logger.warning(f"Calendar {calendar.name!r} unreadable: {got!r}")
            failed.append(calendar.name)
            continue
        free_busy = roles.get(calendar.id) == "freeBusyReader"
        events.extend(
            event
            for item in got
            if (event := _event(calendar, item, free_busy)) is not None
        )
    if calendars and len(failed) == len(calendars):
        raise CalendarUnavailableError("no calendar could be read")

    events.sort(key=lambda event: _sort_key(event, since))
    result = Report(
        email=connection.email,
        calendars=tuple(calendars),
        events=tuple(events),
        failed=tuple(failed),
    )
    _reports[key] = (time.monotonic(), result)
    return result


def as_json(report: Report) -> dict[str, Any]:
    return {
        "connected": True,
        "email": report.email,
        "calendars": [
            {"id": c.id, "name": c.name, "color": c.color} for c in report.calendars
        ],
        "events": [
            {
                "id": e.id,
                "calendar_id": e.calendar_id,
                "title": e.title,
                "start": e.start,
                "end": e.end,
                "all_day": e.all_day,
                "location": e.location,
                "url": e.url,
            }
            for e in report.events
        ],
        "failed": list(report.failed),
    }
