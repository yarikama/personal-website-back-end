import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from api.dependencies.auth import get_current_admin
from core import config
from db.dependency import get_db
from db.models.admin_seen import AdminSeen
from db.models.ask import AskQuestion
from fastapi.testclient import TestClient
from main import get_application
from services import ask_log
from sqlalchemy import delete, select

PREFIX = "test-ask-log: "
ADMIN = "owner@example.com"


@pytest.fixture
def factory(sync_sessions, monkeypatch, async_sessions):
    factory = sync_sessions
    monkeypatch.setattr(ask_log, "SessionLocal", async_sessions)
    yield factory
    with factory() as db:
        db.execute(delete(AskQuestion).where(AskQuestion.question.startswith(PREFIX)))
        db.execute(delete(AdminSeen).where(AdminSeen.email == ADMIN))
        db.commit()


def entry(question, **change):
    fields = dict(
        question=PREFIX + question,
        quote=None,
        page=None,
        answer="An answer.",
        citations=[{"id": "P1", "kind": "project", "title": "PAPIT", "url": None}],
        status="answered",
        truncated=False,
        output_tokens=3,
        duration_ms=1200,
        admin=False,
    )
    return ask_log.Entry(**{**fields, **change})


def record(entry):
    """ask_log.record from these sync tests, each in an event loop of its own
    (the sessions use NullPool)."""
    asyncio.run(ask_log.record(entry))


@pytest.fixture
def client(factory, db_override):
    app = get_application()
    app.dependency_overrides[get_db] = db_override
    # Signed in (tests/test_google_login.py covers how).
    app.dependency_overrides[get_current_admin] = lambda: ADMIN
    return TestClient(app)


def questions(client, **params):
    response = client.get("/api/v1/admin/ask/questions", params=params)
    assert response.status_code == 200
    return [
        q["question"].removeprefix(PREFIX)
        for q in response.json()["data"]
        if q["question"].startswith(PREFIX)
    ]


def test_a_question_is_stored_and_old_ones_are_forgotten(factory):
    now = datetime.now(timezone.utc)
    days = config.ASK_QUESTION_RETENTION_DAYS
    record(entry("old", created_at=now - timedelta(days=days, minutes=1)))
    record(entry("kept", created_at=now - timedelta(days=days - 1)))
    record(entry("new", created_at=now))

    with factory() as db:
        rows = db.scalars(
            select(AskQuestion).where(AskQuestion.question.startswith(PREFIX))
        )
        assert sorted(r.question.removeprefix(PREFIX) for r in rows) == ["kept", "new"]


def test_a_database_problem_does_not_raise(monkeypatch):
    def broken():
        raise RuntimeError("database down")

    monkeypatch.setattr(ask_log, "SessionLocal", broken)
    record(entry("lost"))  # logged, not raised


def test_the_admin_lists_questions_newest_first_with_filters(client):
    now = datetime.now(timezone.utc)
    rows = [
        entry("visitor", created_at=now - timedelta(minutes=5)),
        entry("owner", admin=True, created_at=now - timedelta(minutes=4)),
        entry("uncited", citations=[], created_at=now - timedelta(minutes=3)),
        entry(
            "passage",
            quote="A passage.",
            page="/notes/x",
            created_at=now - timedelta(minutes=2),
        ),
        entry("cut", truncated=True, created_at=now - timedelta(minutes=1)),
        entry("broke", status="error", citations=[], created_at=now),
    ]
    for row in rows:
        record(row)

    assert questions(client) == ["broke", "cut", "passage", "uncited", "visitor"]
    assert questions(client, who="admin") == ["owner"]
    assert len(questions(client, who="all")) == 6
    assert questions(client, uncited=True) == ["broke", "uncited"]
    assert questions(client, passage=True) == ["passage"]
    assert questions(client, failed=True) == ["broke", "cut"]


def test_the_admin_rates_an_answer(client):
    record(entry("rate me"))
    [row] = [
        q
        for q in client.get("/api/v1/admin/ask/questions").json()["data"]
        if q["question"] == PREFIX + "rate me"
    ]
    assert row["rating"] is None and row["durationMs"] == 1200

    url = f"/api/v1/admin/ask/questions/{row['id']}"
    assert client.patch(url, json={"rating": "bad"}).json()["data"]["rating"] == "bad"
    assert questions(client, rating="bad") == ["rate me"]
    assert client.patch(url, json={"rating": None}).json()["data"]["rating"] is None
    assert questions(client, rating="none") == ["rate me"]
    assert client.patch(url, json={"rating": "meh"}).status_code == 422


def test_only_the_admin_reads_them(client):
    client.app.dependency_overrides.pop(get_current_admin)
    assert client.get("/api/v1/admin/ask/questions").status_code == 401
    missing = "/api/v1/admin/ask/questions/00000000-0000-0000-0000-000000000000"
    assert client.patch(missing, json={"rating": "good"}).status_code == 401


def new_since_last_visit(client):
    response = client.get("/api/v1/admin/ask/questions/new")
    assert response.status_code == 200
    return response.json()["data"]


def test_visitors_questions_are_counted_since_the_last_visit(client, factory):
    with factory() as db:
        db.execute(delete(AdminSeen).where(AdminSeen.email == ADMIN))
        db.commit()
    assert new_since_last_visit(client)["since"] is None

    first = client.post("/api/v1/admin/ask/questions/seen").json()["data"]
    assert first["previous"] is None
    assert new_since_last_visit(client)["count"] == 0

    record(entry("while away"))
    record(entry("mine", admin=True))  # the admin's own: not news
    news = new_since_last_visit(client)
    assert news["count"] == 1
    assert news["since"] is not None

    again = client.post("/api/v1/admin/ask/questions/seen").json()["data"]
    assert again["previous"] == news["since"]
    assert new_since_last_visit(client)["count"] == 0


def test_each_admin_has_their_own_last_visit(client, factory):
    client.post("/api/v1/admin/ask/questions/seen")
    record(entry("for both"))
    client.app.dependency_overrides[get_current_admin] = lambda: "other@example.com"
    try:
        other = new_since_last_visit(client)
        assert other["since"] is None
        assert other["count"] >= 1
    finally:
        client.app.dependency_overrides[get_current_admin] = lambda: ADMIN
    assert new_since_last_visit(client)["count"] == 1
