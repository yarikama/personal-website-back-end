"""The site's visitors, for the admin. Vercel is not called: its answers are
replaced by rows in the same shape, one set per `by`."""

from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Secret

from api.dependencies.auth import get_current_admin
from core import config
from main import get_application
from services import visitors

NOW = datetime(2026, 10, 5, 21, 30, tzinfo=timezone.utc)
VERCEL = visitors.Vercel("token-1", "portfolio", "yarikamas-projects")

# As Vercel answers, trimmed to the fields read; out of order on purpose.
ROWS = {
    "environment": [{"environment": "production", "pageviews": 42, "visitors": 17}],
    "day": [
        {"timestamp": "2026-10-05T00:00:00.000Z", "pageviews": 30, "visitors": 12},
        {"timestamp": "2026-10-03T00:00:00.000Z", "pageviews": 12, "visitors": 6},
    ],
    "requestPath": [
        {"requestPath": "/notes", "pageviews": 8, "visitors": 5},
        {"requestPath": "/", "pageviews": 25, "visitors": 15},
        {"requestPath": "Others", "pageviews": 9, "visitors": 4},
    ],
    "referrerHostname": [
        {"referrerHostname": None, "pageviews": 20, "visitors": 9},
        {"referrerHostname": "www.linkedin.com", "pageviews": 22, "visitors": 8},
    ],
    "country": [{"country": "US", "pageviews": 30, "visitors": 11}],
    "deviceType": [{"deviceType": "mobile", "pageviews": 12, "visitors": 7}],
}


@pytest.fixture(autouse=True)
def empty_cache():
    visitors._cache.clear()
    yield
    visitors._cache.clear()


def serve(status=200, rows=ROWS):
    calls = []

    def handler(request):
        calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"code": "forbidden"}})
        return httpx.Response(200, json={"data": rows[request.url.params["by"]]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


@pytest.mark.anyio
async def test_the_report_has_totals_days_and_top_lists():
    http, _ = serve()
    report = await visitors.report(7, http, VERCEL, now=NOW)

    assert (report.since, report.until) == ("2026-09-29", "2026-10-05")
    assert (report.pageviews, report.visitors) == (42, 17)
    # Every day of the range, the ones without visits as zero.
    assert [day.name for day in report.days] == [
        "2026-09-29",
        "2026-09-30",
        "2026-10-01",
        "2026-10-02",
        "2026-10-03",
        "2026-10-04",
        "2026-10-05",
    ]
    assert report.days[4] == visitors.Count("2026-10-03", 12, 6)
    assert report.days[0] == visitors.Count("2026-09-29", 0, 0)
    # Most viewed first; a visit with no referrer has an empty name.
    assert [page.name for page in report.pages] == ["/", "Others", "/notes"]
    assert report.referrers == (
        visitors.Count("www.linkedin.com", 22, 8),
        visitors.Count("", 20, 9),
    )


@pytest.mark.anyio
async def test_vercel_is_asked_for_the_range_and_once_in_ten_minutes():
    http, calls = serve()
    first = await visitors.report(7, http, VERCEL, now=NOW)
    second = await visitors.report(7, http, VERCEL, now=NOW)

    assert first == second
    assert len(calls) == 6
    params = calls[0].url.params
    assert calls[0].url.path == "/v1/query/web-analytics/visits/aggregate"
    assert calls[0].headers["Authorization"] == "Bearer token-1"
    assert params["projectId"] == "portfolio"
    assert params["slug"] == "yarikamas-projects"
    # Milliseconds: midnight UTC six days back, to now.
    since = datetime(2026, 9, 29, tzinfo=timezone.utc)
    assert params["since"] == str(int(since.timestamp() * 1000))
    assert params["until"] == str(int(NOW.timestamp() * 1000))
    assert sorted(call.url.params["by"] for call in calls) == sorted(ROWS)


@pytest.mark.anyio
async def test_an_error_from_vercel_is_reported():
    http, _ = serve(status=403)
    with pytest.raises(visitors.VercelUnavailableError):
        await visitors.report(7, http, VERCEL, now=NOW)


@pytest.mark.anyio
async def test_when_vercel_fails_the_last_report_is_kept():
    http, _ = serve()
    first = await visitors.report(7, http, VERCEL, now=NOW)
    # Ten minutes on, Vercel fails.
    key = (VERCEL, 7)
    visitors._cache[key] = (visitors._cache[key][0] - visitors.CACHE_SECONDS - 1, first)
    failing, calls = serve(status=503)

    assert await visitors.report(7, failing, VERCEL, now=NOW) == first
    assert len(calls) == 6


def test_the_token_is_not_in_the_repr():
    assert "token-1" not in repr(VERCEL)


@pytest.fixture
def client(monkeypatch):
    real = visitors.report

    async def from_rows(days, http, vercel, now=None):
        mock, _ = serve()
        return await real(days, mock, vercel, now=NOW)

    monkeypatch.setattr(visitors, "report", from_rows)
    monkeypatch.setattr(config, "VERCEL_TOKEN", Secret("token-1"))
    app = get_application()
    app.dependency_overrides[get_current_admin] = lambda: "owner@example.com"
    return TestClient(app)


def test_the_admin_gets_the_report(client):
    response = client.get("/api/v1/admin/visitors?days=7")

    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["pageviews"], data["visitors"]) == (42, 17)
    assert data["days"][-1] == {"date": "2026-10-05", "pageviews": 30, "visitors": 12}
    assert data["countries"] == [{"name": "US", "pageviews": 30, "visitors": 11}]


def test_without_a_token_it_is_off(client, monkeypatch):
    monkeypatch.setattr(config, "VERCEL_TOKEN", Secret(""))
    assert client.get("/api/v1/admin/visitors").status_code == 503


def test_the_range_is_one_to_thirty_days(client):
    assert client.get("/api/v1/admin/visitors?days=0").status_code == 422
    assert client.get("/api/v1/admin/visitors?days=31").status_code == 422


def test_only_the_admin_sees_it(client):
    client.app.dependency_overrides.clear()
    assert client.get("/api/v1/admin/visitors").status_code == 401
