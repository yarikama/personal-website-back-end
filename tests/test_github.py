"""The GitHub contribution graph on the admin's welcome page. GitHub is not
called: its page is replaced by a snippet in the same markup."""

import httpx
import pytest
from fastapi.testclient import TestClient

from api.dependencies.auth import get_current_admin
from main import get_application
from services import github

# As GitHub serves it (attributes trimmed), days out of order on purpose.
PAGE = """
<table><tbody><tr>
<td tabindex="0" data-ix="0" style="width: 10px" data-date="2025-10-05"
 id="contribution-day-component-0-1" data-level="2" role="gridcell"
 class="ContributionCalendar-day"></td>
<td tabindex="0" data-ix="0" style="width: 10px" data-date="2025-09-28"
 id="contribution-day-component-0-0" data-level="1" role="gridcell"
 class="ContributionCalendar-day"></td>
<td tabindex="0" data-date="2025-10-12" id="contribution-day-component-0-2"
 data-level="0" class="ContributionCalendar-day"></td>
<td class="ContributionCalendar-label">Mon</td>
</tr></tbody></table>
<tool-tip id="t1" for="contribution-day-component-0-0" class="sr-only">
17 contributions on September 28th.</tool-tip>
<tool-tip id="t2" for="contribution-day-component-0-1" class="sr-only">
1,204 contributions on October 5th.</tool-tip>
<tool-tip id="t3" for="contribution-day-component-0-2" class="sr-only">
No contributions on October 12th.</tool-tip>
"""


@pytest.fixture(autouse=True)
def empty_cache():
    github._cache.clear()
    yield
    github._cache.clear()


def serve(status=200, body=PAGE):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def test_the_calendar_is_read_day_by_day():
    assert github.parse(PAGE) == [
        github.Day("2025-09-28", 1, 17),
        github.Day("2025-10-05", 2, 1204),
        github.Day("2025-10-12", 0, 0),
    ]


def test_markup_it_does_not_know_gives_no_days():
    assert github.parse("<html>something else</html>") == []


@pytest.mark.anyio
async def test_github_is_asked_once_an_hour():
    http, calls = serve()
    first = await github.contributions("someone", http)
    second = await github.contributions("someone", http)

    assert first == second
    assert len(calls) == 1
    assert str(calls[0].url) == "https://github.com/users/someone/contributions"


@pytest.mark.anyio
async def test_an_error_from_github_is_reported():
    http, _ = serve(status=503)
    with pytest.raises(github.GitHubUnavailableError):
        await github.contributions("someone", http)


@pytest.fixture
def client(monkeypatch):
    real = github.contributions

    async def from_snippet(user, http):
        mock, _ = serve()
        return await real(user, mock)

    monkeypatch.setattr(github, "contributions", from_snippet)
    app = get_application()
    app.dependency_overrides[get_current_admin] = lambda: "owner@example.com"
    return TestClient(app)


def test_the_admin_gets_the_days_and_the_total(client):
    response = client.get("/api/v1/admin/github/contributions")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 17 + 1204
    assert data["days"][0] == {"date": "2025-09-28", "level": 1, "count": 17}


def test_only_the_admin_sees_it(client):
    client.app.dependency_overrides.clear()
    assert client.get("/api/v1/admin/github/contributions").status_code == 401
