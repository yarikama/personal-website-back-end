"""
The admin's to-dos (/admin/todos), against PostgreSQL like the other
database tests. Every day used is in 2001, and those rows are deleted
afterwards.
"""

import os
from datetime import date

import pytest
from api.dependencies.auth import get_current_admin
from db.dependency import get_async_db
from db.models.todo import AdminTodo
from db.session import Base
from fastapi.testclient import TestClient
from main import get_application
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

MON, TUE, WED = date(2001, 1, 1), date(2001, 1, 2), date(2001, 1, 3)


@pytest.fixture
def factory():
    database_url = os.getenv(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/test_db"
    )
    engine = create_engine(database_url)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def clean():
        with factory() as db:
            db.query(AdminTodo).filter(
                AdminTodo.day.between(date(2001, 1, 1), date(2001, 12, 31))
            ).delete(synchronize_session=False)
            db.commit()

    clean()
    yield factory
    clean()


@pytest.fixture
def client(factory, async_db):
    app = get_application()
    app.dependency_overrides[get_async_db] = async_db
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
