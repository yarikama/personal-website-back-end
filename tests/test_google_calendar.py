"""
The owner's Google Calendar on the admin (services/google_calendar.py,
api/routes/calendar.py and the calendar half of api/routes/auth.py).
Google is not called: its token endpoint and Calendar API are an
httpx.MockTransport, and the code-for-token trade is replaced. The saved
connection is a row in admin_settings, removed afterwards.
"""

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from api.routes import auth
from core import config
from db.dependency import get_db
from db.models.admin_setting import AdminSetting
from main import get_application
from services import google_calendar, google_oauth
from services.google_calendar import Connection, NotConnectedError
from services.google_oauth import Client

pytestmark = pytest.mark.anyio

CLIENT_ID = "test-client.apps.googleusercontent.com"
SECRET = "test-client-secret"
OWNER = "owner@example.com"
SITE = "https://site.example"
CLIENT = Client(CLIENT_ID, SECRET, "https://api.example/callback")
CONNECTION = Connection(OWNER, "the-refresh-token")
CDT = timezone(timedelta(hours=-5))
MONDAY = datetime(2026, 11, 16, tzinfo=CDT)


@pytest.fixture(autouse=True)
def google(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", SECRET)
    monkeypatch.setattr(config, "ADMIN_EMAILS", frozenset({OWNER}))
    monkeypatch.setattr(config, "SITE_URL", SITE)
    google_calendar.clear_caches()
    yield
    google_calendar.clear_caches()


@pytest.fixture
def setting(sync_sessions):
    """The saved connection's row, deleted before and after."""

    def clean():
        with sync_sessions() as db:
            db.execute(
                delete(AdminSetting).where(AdminSetting.key == google_calendar.KEY)
            )
            db.commit()

    clean()
    yield sync_sessions
    clean()


class FakeGoogle:
    """Google's token endpoint and Calendar API, from canned answers."""

    def __init__(self) -> None:
        self.calendars: list[dict[str, Any]] = [
            {
                "id": "henry@example.com",
                "summary": "Henry",
                "primary": True,
                "backgroundColor": "#7fa8a3",
                "accessRole": "owner",
            },
            {
                "id": "rice#class@group.calendar.google.com",
                "summary": "Rice classes",
                "backgroundColor": "#d9b066",
                "accessRole": "reader",
            },
            {
                "id": "friend@example.com",
                "summary": "Friend",
                "accessRole": "freeBusyReader",
            },
        ]
        self.events: dict[str, Any] = {
            "henry@example.com": [
                {
                    "id": "a",
                    "summary": "Office hours",
                    "location": "Duncan Hall",
                    "start": {"dateTime": "2026-11-16T15:00:00-06:00"},
                    "end": {"dateTime": "2026-11-16T16:00:00-06:00"},
                    "htmlLink": "https://calendar.google.com/event?eid=a",
                },
                {
                    "id": "gone",
                    "status": "cancelled",
                    "start": {"dateTime": "2026-11-16T09:00:00-06:00"},
                    "end": {"dateTime": "2026-11-16T10:00:00-06:00"},
                },
            ],
            "rice#class@group.calendar.google.com": [
                {
                    "id": "b",
                    "summary": "COMP 646",
                    "start": {"dateTime": "2026-11-16T10:50:00-06:00"},
                    "end": {"dateTime": "2026-11-16T12:05:00-06:00"},
                },
                {
                    "id": "c",
                    "summary": "Thanksgiving break",
                    "start": {"date": "2026-11-16"},
                    "end": {"date": "2026-11-17"},
                },
            ],
            "friend@example.com": [
                {
                    "id": "d",
                    "start": {"dateTime": "2026-11-17T12:00:00-06:00"},
                    "end": {"dateTime": "2026-11-17T13:00:00-06:00"},
                },
            ],
        }
        self.token_status = 200
        self.token_calls = 0
        self.list_calls = 0
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == google_oauth.TOKEN_URL:
            self.token_calls += 1
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["refresh_token"]
            assert form["refresh_token"] == ["the-refresh-token"]
            if self.token_status != 200:
                return httpx.Response(
                    self.token_status, json={"error": "invalid_grant"}
                )
            return httpx.Response(
                200, json={"access_token": "access-1", "expires_in": 3599}
            )
        assert request.headers["Authorization"] == "Bearer access-1"
        path = request.url.path.removeprefix("/calendar/v3")
        if path == "/users/me/calendarList":
            self.list_calls += 1
            return httpx.Response(200, json={"items": self.calendars})
        _, _, rest = path.partition("/calendars/")
        calendar_id = rest.removesuffix("/events")
        items = self.events.get(calendar_id)
        if items is None:
            return httpx.Response(404, json={"error": {"message": "Not Found"}})
        return httpx.Response(200, json={"items": items})


@pytest.fixture
def fake():
    return FakeGoogle()


def http_for(fake: FakeGoogle) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))


# Keeping the token


async def test_the_token_is_kept_encrypted_and_reads_back(setting, async_sessions):
    async with async_sessions() as db:
        await google_calendar.save(db, CLIENT, CONNECTION)
        await db.commit()
    with setting() as db:
        stored = db.scalars(
            select(AdminSetting.value).where(AdminSetting.key == google_calendar.KEY)
        ).one()
    assert "the-refresh-token" not in stored
    assert OWNER not in stored

    async with async_sessions() as db:
        assert await google_calendar.load(db, CLIENT) == CONNECTION
        # Under another client secret it does not decrypt: connect again.
        other = Client(CLIENT_ID, "a-rotated-secret", CLIENT.redirect_uri)
        assert await google_calendar.load(db, other) is None
        await google_calendar.forget(db)
        await db.commit()
        assert await google_calendar.load(db, CLIENT) is None


# Reading


async def test_every_calendar_is_read_and_events_come_in_order(fake):
    async with http_for(fake) as http:
        report = await google_calendar.report(CONNECTION, CLIENT, http, MONDAY, 7)

    assert [c.name for c in report.calendars] == ["Henry", "Rice classes", "Friend"]
    assert [(e.title, e.calendar_id) for e in report.events] == [
        ("Thanksgiving break", "rice#class@group.calendar.google.com"),
        ("COMP 646", "rice#class@group.calendar.google.com"),
        ("Office hours", "henry@example.com"),
        # A calendar shared as free/busy only has no titles.
        ("Busy", "friend@example.com"),
    ]
    holiday = report.events[0]
    assert holiday.all_day and holiday.start == "2026-11-16"
    assert report.events[2].location == "Duncan Hall"
    assert report.failed == ()

    events_call = next(r for r in fake.requests if "rice" in r.url.path)
    # The id is one path segment, its # escaped.
    assert "/calendars/rice%23class%40group.calendar.google.com/events" in str(
        events_call.url
    )
    params = events_call.url.params
    assert params["singleEvents"] == "true"
    assert params["timeMin"] == "2026-11-16T00:00:00-05:00"
    assert params["timeMax"] == "2026-11-23T00:00:00-05:00"


async def test_a_calendar_that_cannot_be_read_leaves_the_rest(fake):
    fake.calendars.append({"id": "removed@example.com", "summary": "Old club"})
    async with http_for(fake) as http:
        report = await google_calendar.report(CONNECTION, CLIENT, http, MONDAY, 7)
    assert report.failed == ("Old club",)
    assert len(report.events) == 4


async def test_no_readable_calendar_is_unavailable(fake):
    fake.events = {}
    async with http_for(fake) as http:
        with pytest.raises(google_calendar.CalendarUnavailableError):
            await google_calendar.report(CONNECTION, CLIENT, http, MONDAY, 7)


async def test_the_access_token_and_the_report_are_reused(fake):
    async with http_for(fake) as http:
        await google_calendar.report(CONNECTION, CLIENT, http, MONDAY, 7)
        await google_calendar.report(CONNECTION, CLIENT, http, MONDAY, 7)
        assert fake.list_calls == 1
        await google_calendar.report(CONNECTION, CLIENT, http, MONDAY, 3)
    assert fake.list_calls == 2
    assert fake.token_calls == 1


async def test_a_token_google_no_longer_accepts_means_connect_again(fake):
    fake.token_status = 400
    async with http_for(fake) as http:
        with pytest.raises(NotConnectedError) as raised:
            await google_calendar.report(CONNECTION, CLIENT, http, MONDAY, 7)
    assert raised.value.expired


# Connecting


def id_token(**claims):
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": int(time.time()) + 300,
        "email": OWNER,
        "email_verified": True,
    }
    payload.update(claims)
    return jwt.encode(payload, "google-signs-these-not-us-0123456789", "HS256")


@pytest.fixture
def app(redis, db_override, setting):
    application = get_application()
    application.state.redis = redis
    application.dependency_overrides[get_db] = db_override
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        yield client


@pytest.fixture
def token_endpoint(monkeypatch):
    """Answers the code-for-token trade with an ID token for the nonce the
    sign-in kept, a refresh token and the scopes in `granted`."""
    answer: dict[str, Any] = {
        "scope": f"openid email {google_oauth.CALENDAR_SCOPE}",
        "refresh_token": "the-refresh-token",
    }
    take_state = google_oauth.take_state
    nonce = {}

    async def remember_nonce(redis_, state):
        kept = await take_state(redis_, state)
        nonce["value"] = kept["nonce"]
        return kept

    async def exchange(client, code, verifier, http):
        return {"id_token": id_token(nonce=nonce["value"]), **answer}

    monkeypatch.setattr(google_oauth, "take_state", remember_nonce)
    monkeypatch.setattr(google_oauth, "exchange_tokens", exchange)
    return answer


async def test_connecting_needs_the_admin_signed_in(client):
    response = await client.get("/api/v1/auth/google/calendar")
    assert response.status_code == 401


async def connect(client, sign_in):
    headers = await sign_in(OWNER)
    response = await client.get("/api/v1/auth/google/calendar", headers=headers)
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return response, query


async def test_connecting_asks_google_for_read_only_calendar_access(client, sign_in):
    response, query = await connect(client, sign_in)
    assert response.headers["location"].startswith(google_oauth.AUTHORIZE_URL)
    assert query["scope"] == [f"openid email {google_oauth.CALENDAR_SCOPE}"]
    assert google_oauth.CALENDAR_SCOPE.endswith("/calendar.readonly")
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent select_account"]
    assert response.headers["set-cookie"].startswith(f"{auth.STATE_COOKIE}=")


async def test_the_callback_keeps_the_token_and_returns_to_the_admin(
    client, sign_in, token_endpoint, async_sessions
):
    _, query = await connect(client, sign_in)
    response = await client.get(
        "/api/v1/auth/google/callback",
        params={"code": "the-code", "state": query["state"][0]},
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"{SITE}/admin?calendar=connected"
    # No new session: the owner was signed in already.
    assert "admin_session" not in response.headers.get("set-cookie", "")
    async with async_sessions() as db:
        assert await google_calendar.load(db, CLIENT) == CONNECTION


async def test_unticking_calendar_access_keeps_nothing(
    client, sign_in, token_endpoint, async_sessions
):
    token_endpoint["scope"] = "openid email"
    _, query = await connect(client, sign_in)
    response = await client.get(
        "/api/v1/auth/google/callback",
        params={"code": "the-code", "state": query["state"][0]},
    )
    assert response.headers["location"] == f"{SITE}/admin?calendar=denied"
    async with async_sessions() as db:
        assert await google_calendar.load(db, CLIENT) is None


# The admin's routes


async def test_with_no_calendar_connected_the_page_says_so(client, sign_in):
    headers = await sign_in(OWNER)
    response = await client.get(
        "/api/v1/admin/calendar",
        params={"start": MONDAY.isoformat(), "days": 7},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"data": {"connected": False, "expired": False}}


async def test_the_page_reads_the_connected_calendar(
    client, sign_in, async_sessions, monkeypatch
):
    async with async_sessions() as db:
        await google_calendar.save(db, CLIENT, CONNECTION)
        await db.commit()
    asked = {}

    async def report(connection, client_, http, since, days):
        asked.update(connection=connection, since=since, days=days)
        return google_calendar.Report(OWNER, (), (), ())

    monkeypatch.setattr(google_calendar, "report", report)
    headers = await sign_in(OWNER)
    response = await client.get(
        "/api/v1/admin/calendar",
        params={"start": MONDAY.isoformat(), "days": 3},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"] == {
        "connected": True,
        "email": OWNER,
        "calendars": [],
        "events": [],
        "failed": [],
    }
    assert asked == {"connection": CONNECTION, "since": MONDAY, "days": 3}


async def test_the_start_of_the_day_needs_its_offset(client, sign_in):
    headers = await sign_in(OWNER)
    response = await client.get(
        "/api/v1/admin/calendar",
        params={"start": "2026-11-16T00:00:00", "days": 7},
        headers=headers,
    )
    assert response.status_code == 422


async def test_disconnecting_forgets_and_revokes_the_token(
    client, sign_in, async_sessions, monkeypatch
):
    async with async_sessions() as db:
        await google_calendar.save(db, CLIENT, CONNECTION)
        await db.commit()
    revoked = []

    async def revoke(connection, http):
        revoked.append(connection)

    monkeypatch.setattr(google_calendar, "revoke", revoke)
    headers = await sign_in(OWNER)
    response = await client.delete("/api/v1/admin/calendar", headers=headers)
    assert response.status_code == 204
    assert revoked == [CONNECTION]
    async with async_sessions() as db:
        assert await google_calendar.load(db, CLIENT) is None


async def test_the_report_as_json_names_each_event_s_calendar():
    event = google_calendar.Event(
        "a",
        "cal",
        "Office hours",
        "2026-11-16T15:00:00-06:00",
        "2026-11-16T16:00:00-06:00",
        False,
        "",
        "",
    )
    report = google_calendar.Report(
        OWNER, (google_calendar.Calendar("cal", "Henry", "#7fa8a3"),), (event,), ()
    )
    data = google_calendar.as_json(report)
    assert data["events"][0]["calendar_id"] == data["calendars"][0]["id"] == "cal"
    assert json.dumps(data)
