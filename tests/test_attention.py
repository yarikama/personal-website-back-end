"""
The Attention app's device sync (/admin/attention/events), against
PostgreSQL like the other database tests. Events are recorded by a device
named "test-device" and deleted afterwards.
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from api.dependencies.auth import get_current_admin, get_todos_admin
from db.dependency import get_db
from db.models.attention import AttentionEvent
from main import get_application

EVENTS = "/api/v1/admin/attention/events"
DEVICE = "test-device"


@pytest.fixture
def client(sync_sessions, db_override):
    def clean():
        with sync_sessions() as db:
            db.execute(
                delete(AttentionEvent).where(AttentionEvent.device.like("test-%"))
            )
            db.commit()

    clean()
    app = get_application()
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_admin] = lambda: "owner@example.com"
    app.dependency_overrides[get_todos_admin] = lambda: "owner@example.com"
    yield TestClient(app)
    clean()


def event(kind="earning", device=DEVICE, **data):
    return {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "at": datetime(2026, 11, 11, 9, 30, tzinfo=timezone.utc).isoformat(),
        "device": device,
        "data": data,
    }


def mine(body):
    return [e for e in body["data"] if e["device"].startswith("test-")]


def test_a_device_reads_what_another_sent(client):
    start = client.get(EVENTS, params={"after": 0, "limit": 1000}).json()
    while start["more"]:
        start = client.get(EVENTS, params={"after": start["next"]}).json()

    phone = event("outside", device="test-iphone", day="2026-11-11")
    sent = client.post(EVENTS, json={"events": [phone]})
    assert sent.status_code == 200, sent.text
    assert sent.json()["data"]["received"] == 1

    body = client.get(EVENTS, params={"after": start["next"]}).json()
    got = mine(body)
    assert [e["id"] for e in got] == [phone["id"]]
    assert got[0]["kind"] == "outside"
    assert got[0]["data"] == {"day": "2026-11-11"}
    assert body["next"] == got[0]["seq"]

    # Nothing new after the last one read.
    again = client.get(EVENTS, params={"after": body["next"]}).json()
    assert mine(again) == []


def test_sending_the_same_event_twice_stores_it_once(client):
    payout = event(companyID="us_google", minutes=30)
    for _ in range(2):
        assert client.post(EVENTS, json={"events": [payout]}).status_code == 200
    seen = []
    page = {"next": 0, "more": True}
    while page["more"]:
        page = client.get(EVENTS, params={"after": page["next"], "limit": 1000}).json()
        seen += [e["id"] for e in mine(page)]
    assert seen.count(payout["id"]) == 1


def test_pages_in_order(client):
    first = client.get(EVENTS, params={"after": 0, "limit": 1000}).json()
    while first["more"]:
        first = client.get(
            EVENTS, params={"after": first["next"], "limit": 1000}
        ).json()
    batch = [event(n=i) for i in range(3)]
    client.post(EVENTS, json={"events": batch})

    page = client.get(EVENTS, params={"after": first["next"], "limit": 2}).json()
    assert page["more"] is True
    rest = client.get(EVENTS, params={"after": page["next"], "limit": 2}).json()
    assert rest["more"] is False
    ids = [e["id"] for e in mine(page) + mine(rest)]
    assert ids == [e["id"] for e in batch]


def test_an_unknown_kind_is_refused(client):
    response = client.post(EVENTS, json={"events": [event("whatever")]})
    assert response.status_code == 422


def test_needs_the_owner(sync_sessions, db_override):
    app = get_application()
    app.dependency_overrides[get_db] = db_override
    response = TestClient(app).get(EVENTS)
    assert response.status_code == 401


def test_sending_nothing_is_fine(client):
    response = client.post(EVENTS, json={"events": []})
    assert response.status_code == 200
    assert response.json()["data"]["received"] == 0
