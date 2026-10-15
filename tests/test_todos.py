"""
The admin's to-dos (/admin/todos), against PostgreSQL like the other
database tests. Every day used is in 2001, and those rows are deleted
afterwards.
"""

import asyncio
from datetime import date

import pytest
from api.dependencies.auth import get_current_admin
from db.dependency import get_db
from db.models.todo import AdminTodo
from fastapi.testclient import TestClient
from main import get_application
from services import todos
from sqlalchemy import delete, func, select

MON, TUE, WED = date(2001, 1, 1), date(2001, 1, 2), date(2001, 1, 3)


@pytest.fixture
def factory(sync_sessions):
    factory = sync_sessions

    def clean():
        with factory() as db:
            db.execute(
                delete(AdminTodo).where(
                    AdminTodo.day.between(date(2001, 1, 1), date(2001, 12, 31))
                )
            )
            db.commit()

    clean()
    yield factory
    clean()


@pytest.fixture
def client(factory, db_override):
    app = get_application()
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_admin] = lambda: "owner@example.com"
    return TestClient(app)


def today(client, day):
    response = client.get("/api/v1/admin/todos", params={"day": day.isoformat()})
    assert response.status_code == 200
    return response.json()["data"]


def add(client, text, day, **extra):
    response = client.post(
        "/api/v1/admin/todos", json={"text": text, "day": day.isoformat(), **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def tick(client, todo, day, done=True):
    return client.patch(
        f"/api/v1/admin/todos/{todo['id']}", json={"done": done, "day": day.isoformat()}
    )


def test_every_day_starts_with_neetcode(client):
    [item] = today(client, MON)
    assert (item["dailyKey"], item["href"]) == ("neetcode", "https://neetcode.io")
    # Asked again, not added twice.
    assert len(today(client, MON)) == 1


def test_what_is_not_done_carries_over(client):
    monday = today(client, MON)[0]
    add(client, "Read the vLLM release notes", MON)

    tuesday = today(client, TUE)

    # Monday's two, then Tuesday's own NeetCode.
    assert [(t["text"], t["day"]) for t in tuesday] == [
        ("Solve a problem on NeetCode", "2001-01-01"),
        ("Read the vLLM release notes", "2001-01-01"),
        ("Solve a problem on NeetCode", "2001-01-02"),
    ]
    assert monday["id"] == tuesday[0]["id"]


def test_done_stays_for_the_day_then_goes(client):
    item = add(client, "Reply to the recruiter", MON)
    done = tick(client, item, MON).json()["data"]

    assert done["doneOn"] == "2001-01-01"
    assert item["id"] in [t["id"] for t in today(client, MON)]
    assert item["id"] not in [t["id"] for t in today(client, TUE)]


def test_done_late_counts_on_the_day_it_was_done(client):
    item = add(client, "Fix the flaky test", MON)
    tick(client, item, TUE)

    assert item["id"] in [t["id"] for t in today(client, TUE)]
    assert item["id"] not in [t["id"] for t in today(client, WED)]


def test_unticking_brings_it_back(client):
    item = add(client, "Back up the sealed-secrets key", MON)
    tick(client, item, MON)
    tick(client, item, MON, done=False)

    assert item["id"] in [t["id"] for t in today(client, TUE)]


def test_a_removed_daily_item_is_not_added_again_that_day(client):
    [neetcode] = today(client, MON)

    assert client.delete(f"/api/v1/admin/todos/{neetcode['id']}").status_code == 204
    assert today(client, MON) == []
    assert client.delete(f"/api/v1/admin/todos/{neetcode['id']}").status_code == 404
    # The next day brings its own.
    assert [t["day"] for t in today(client, TUE)] == ["2001-01-02"]


@pytest.mark.parametrize(
    "href", ["javascript:alert(1)", "//evil.example", "data:text/html,hi"]
)
def test_links_are_web_addresses_or_site_paths(client, href):
    response = client.post(
        "/api/v1/admin/todos",
        json={"text": "x", "day": MON.isoformat(), "href": href},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("href", "kept"),
    [
        ("/notes/svd", "/notes/svd"),
        ("  https://neetcode.io  ", "https://neetcode.io"),
        ("http://localhost:3000/admin", "http://localhost:3000/admin"),
        ("   ", None),
    ],
)
def test_a_link_is_kept_trimmed_and_a_blank_one_dropped(client, href, kept):
    assert add(client, "Read it", MON, href=href)["href"] == kept


@pytest.mark.anyio
async def test_the_first_requests_of_a_day_add_its_daily_item_once(
    factory, async_sessions
):
    # Each finds no NeetCode for the day and adds one; the unique index lets
    # one in, and the others roll back and list the one that got in.
    async def first_look():
        async with async_sessions() as db:
            return [t.daily_key for t in await todos.for_day(db, WED)]

    lists = await asyncio.gather(*(first_look() for _ in range(5)))

    assert lists == [["neetcode"]] * 5
    with factory() as db:
        assert db.scalar(select(func.count()).where(AdminTodo.day == WED)) == 1


def test_a_blank_item_is_refused(client):
    response = client.post(
        "/api/v1/admin/todos", json={"text": "   ", "day": MON.isoformat()}
    )
    assert response.status_code == 422


def test_only_the_admin_has_them(client):
    client.app.dependency_overrides.pop(get_current_admin)
    assert (
        client.get("/api/v1/admin/todos", params={"day": "2001-01-01"}).status_code
        == 401
    )


def test_past_items_are_offered_once_most_recent_first(client):
    add(client, "Review the CSP reports", MON)
    add(client, "Email the Rice career office", MON)
    add(client, "Review the CSP reports", TUE)
    removed = add(client, "A typo", TUE)
    client.delete(f"/api/v1/admin/todos/{removed['id']}")
    today(client, TUE)  # adds the daily NeetCode

    history = client.get("/api/v1/admin/todos/history").json()["data"]
    mine = [
        text
        for text in history
        if text in {"Review the CSP reports", "Email the Rice career office", "A typo"}
    ]

    assert mine == ["Review the CSP reports", "Email the Rice career office"]
    assert "Solve a problem on NeetCode" not in history
