"""
The contact form's messages, in the owner's inbox (/admin/contact). Against
PostgreSQL, like the other database tests; only messages made here are
deleted afterwards.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from api.dependencies.auth import get_current_admin
from db.dependency import get_db
from db.models.contact import ContactMessage
from main import get_application

PREFIX = "test-contact: "


@pytest.fixture
def factory(sync_sessions):
    factory = sync_sessions
    yield factory
    with factory() as db:
        db.execute(
            delete(ContactMessage).where(ContactMessage.subject.startswith(PREFIX))
        )
        db.commit()


@pytest.fixture
def client(factory, db_override):
    app = get_application()
    app.dependency_overrides[get_db] = db_override
    # Signed in (tests/test_google_login.py covers how).
    app.dependency_overrides[get_current_admin] = lambda: "owner@example.com"
    return TestClient(app)


def add(factory, subject, minutes_ago=0, **fields):
    with factory() as db:
        message = ContactMessage(
            name="Visitor",
            email="visitor@example.com",
            subject=PREFIX + subject,
            message="Hello there, just saying hi.",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
            **fields,
        )
        db.add(message)
        db.commit()
        return str(message.id)


def subjects(client, **params):
    response = client.get("/api/v1/admin/contact", params={"limit": 100, **params})
    assert response.status_code == 200
    return [
        m["subject"].removeprefix(PREFIX)
        for m in response.json()["data"]
        if m["subject"].startswith(PREFIX)
    ]


def test_messages_are_listed_newest_first(client, factory):
    add(factory, "older", minutes_ago=10)
    add(factory, "newer", minutes_ago=1)

    assert subjects(client) == ["newer", "older"]


def test_unread_ones_can_be_counted(client, factory):
    add(factory, "unread one")
    add(factory, "unread two")
    add(factory, "seen", read=True)

    assert subjects(client, read=False) == ["unread two", "unread one"]
    assert subjects(client, read=True) == ["seen"]
    page = client.get("/api/v1/admin/contact", params={"read": False, "limit": 1})
    assert page.json()["pagination"]["total"] >= 2


def test_a_message_is_marked_read_then_replied(client, factory):
    id = add(factory, "to answer")

    read = client.patch(f"/api/v1/admin/contact/{id}", json={"read": True})
    replied = client.patch(f"/api/v1/admin/contact/{id}", json={"replied": True})

    assert read.json()["data"]["read"] is True
    assert replied.json()["data"]["read"] is True
    assert replied.json()["data"]["replied"] is True


def test_a_message_can_be_deleted(client, factory):
    id = add(factory, "spam")

    assert client.delete(f"/api/v1/admin/contact/{id}").status_code == 204
    assert client.delete(f"/api/v1/admin/contact/{id}").status_code == 404
    assert "spam" not in subjects(client)


def test_only_the_admin_reads_them(client, factory):
    id = add(factory, "private")
    client.app.dependency_overrides.pop(get_current_admin)

    assert client.get("/api/v1/admin/contact").status_code == 401
    assert (
        client.patch(f"/api/v1/admin/contact/{id}", json={"read": True}).status_code
        == 401
    )
    assert client.delete(f"/api/v1/admin/contact/{id}").status_code == 401


def test_the_old_public_path_no_longer_lists_them(client):
    assert client.get("/api/v1/contact").status_code == 405


# The form


def send(client, **fields):
    return client.post(
        "/api/v1/contact",
        json={
            "name": "V",
            "email": "visitor@example.com",
            "subject": PREFIX + "Hi",
            "message": "Yo",
            **fields,
        },
    )


def test_short_and_long_messages_are_both_taken(client):
    short = send(client)
    long = send(
        client,
        name="N" * 500,
        subject=PREFIX + "S" * 1000,
        message="M" * 50_000,
    )

    assert (short.status_code, long.status_code) == (201, 201)
    stored = client.get("/api/v1/admin/contact", params={"limit": 100}).json()["data"]
    assert any(len(m["message"]) == 50_000 and len(m["name"]) == 500 for m in stored)


@pytest.mark.parametrize("field", ["name", "subject", "message"])
def test_a_blank_field_is_refused(client, field):
    assert send(client, **{field: "   "}).status_code == 422
