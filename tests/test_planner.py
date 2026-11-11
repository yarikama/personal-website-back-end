"""
Claude's morning plan (services/planner.py) and what it works from
(/admin/planner), against PostgreSQL like the other database tests. Every
day used is in 2001, and those rows and the planner's settings are deleted
afterwards.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from api.dependencies.auth import get_current_admin
from db.dependency import get_db
from db.models.admin_setting import AdminSetting
from db.models.todo import AdminTodo
from main import get_application
from schemas.todos import Bucket, PlanItem
from services import planner

MON, TUE = date(2001, 1, 1), date(2001, 1, 2)
KEYS = (planner.GOALS, planner.NOTES)


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
            db.execute(delete(AdminSetting).where(AdminSetting.key.in_(KEYS)))
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


def item(text, bucket: Bucket = "today", reason="Due Friday", **extra):
    return PlanItem(text=text, bucket=bucket, reason=reason, **extra)


async def redraw(async_sessions, day, *items, notes=None):
    async with async_sessions() as db:
        return await planner.redraw(db, day, list(items), notes)


def listed(client, day):
    response = client.get("/api/v1/admin/todos", params={"day": day.isoformat()})
    assert response.status_code == 200
    return response.json()["data"]


# The plan


@pytest.mark.anyio
async def test_the_plan_is_listed_with_its_buckets_and_reasons(client, async_sessions):
    await redraw(
        async_sessions,
        MON,
        item("Finish COMP 552 HW4", reason="Due Friday 23:59, not started"),
        item(
            "Reply to the Acme recruiter", bucket="week", href="https://mail.google.com"
        ),
    )

    planned = [todo for todo in listed(client, MON) if todo["source"] == "claude"]

    assert [(t["text"], t["bucket"]) for t in planned] == [
        ("Finish COMP 552 HW4", "today"),
        ("Reply to the Acme recruiter", "week"),
    ]
    assert planned[0]["reason"] == "Due Friday 23:59, not started"
    assert planned[1]["href"] == "https://mail.google.com"


def test_the_owners_and_the_daily_items_say_whose_they_are(client):
    client.post("/api/v1/admin/todos", json={"text": "Call home", "day": "2001-01-01"})

    sources = {todo["text"]: todo["source"] for todo in listed(client, MON)}

    assert sources == {"Solve a problem on NeetCode": "daily", "Call home": "owner"}
    assert all(todo["bucket"] is None for todo in listed(client, MON))


@pytest.mark.anyio
async def test_a_kept_item_keeps_its_row_and_the_rest_are_redrawn(
    client, async_sessions
):
    [kept, dropped] = await redraw(
        async_sessions, MON, item("Finish HW4"), item("Read the PPO paper")
    )

    redrawn = await redraw(
        async_sessions,
        TUE,
        item("Prepare for the Acme onsite"),
        item("Finish HW4", reason="Due tomorrow"),
    )

    planned = [todo for todo in listed(client, TUE) if todo["source"] == "claude"]
    assert {todo["text"] for todo in planned} == {
        "Finish HW4",
        "Prepare for the Acme onsite",
    }
    assert redrawn[1].id == kept.id
    # Still for the day it was first for, so it shows as carried over.
    assert (redrawn[1].day, redrawn[1].reason) == (MON, "Due tomorrow")
    assert dropped.id not in {todo["id"] for todo in planned}


@pytest.mark.anyio
async def test_the_owners_items_and_what_was_done_are_left_alone(
    client, async_sessions, factory
):
    mine = client.post(
        "/api/v1/admin/todos", json={"text": "Call home", "day": "2001-01-01"}
    ).json()["data"]
    [done] = await redraw(async_sessions, MON, item("Finish HW3"))
    client.patch(
        f"/api/v1/admin/todos/{done.id}", json={"done": True, "day": "2001-01-01"}
    )

    await redraw(async_sessions, MON)

    with factory() as db:
        rows = {row.text: row for row in db.scalars(select(AdminTodo))}
    assert not rows["Call home"].removed
    assert (rows["Finish HW3"].removed, rows["Finish HW3"].done_on) == (False, MON)
    assert mine["id"] in {todo["id"] for todo in listed(client, MON)}


@pytest.mark.anyio
async def test_the_same_plan_twice_changes_nothing(client, async_sessions):
    first = await redraw(async_sessions, MON, item("Finish HW4"), item("Read"))
    second = await redraw(async_sessions, MON, item("Finish HW4"), item("Read"))

    assert [row.id for row in first] == [row.id for row in second]
    assert len(listed(client, MON)) == 3  # and NeetCode


@pytest.mark.anyio
async def test_the_plan_keeps_its_notes_until_given_new_ones(async_sessions):
    await redraw(async_sessions, MON, item("Finish HW4"), notes="Interviewing")
    await redraw(async_sessions, TUE, item("Finish HW4"))

    async with async_sessions() as db:
        context = await planner.context(db, TUE)
    assert context.notes.text == "Interviewing"


@pytest.mark.anyio
async def test_the_context_has_what_is_open_and_what_was_done_lately(
    client, async_sessions
):
    [done, _] = await redraw(
        async_sessions, MON, item("Finish HW3"), item("Finish HW4")
    )
    client.patch(
        f"/api/v1/admin/todos/{done.id}", json={"done": True, "day": "2001-01-01"}
    )

    async with async_sessions() as db:
        context = await planner.context(db, TUE)

    assert {row.text for row in context.open} == {
        "Finish HW4",
        "Solve a problem on NeetCode",
    }
    assert [row.text for row in context.done] == ["Finish HW3"]


@pytest.mark.parametrize(
    "plan",
    [
        {"text": "Finish HW4", "bucket": "someday", "reason": "Due Friday"},
        {"text": "Finish HW4", "bucket": "today", "reason": ""},
        {"text": "  ", "bucket": "today", "reason": "Due Friday"},
        {
            "text": "Open",
            "bucket": "today",
            "reason": "x",
            "href": "javascript:alert(1)",
        },
    ],
)
def test_a_plan_item_needs_a_bucket_a_reason_and_a_safe_link(plan):
    with pytest.raises(ValueError):
        PlanItem.model_validate(plan)


@pytest.mark.anyio
async def test_plan_items_are_not_offered_as_the_owners_past_items(
    client, async_sessions
):
    await redraw(async_sessions, MON, item("Finish HW4"))
    client.post("/api/v1/admin/todos", json={"text": "Call home", "day": "2001-01-01"})

    history = client.get("/api/v1/admin/todos/history").json()["data"]

    assert "Call home" in history
    assert "Finish HW4" not in history


# What it works from


def test_there_are_no_goals_or_notes_at_first(client):
    assert client.get("/api/v1/admin/planner").json()["data"] == {
        "goals": "",
        "goalsUpdatedAt": None,
        "notes": "",
        "notesUpdatedAt": None,
    }


def test_the_owner_writes_goals_and_corrects_the_notes(client):
    client.put("/api/v1/admin/planner", json={"goals": "  - A new-grad offer  "})
    client.put("/api/v1/admin/planner", json={"notes": "Interviewing at Acme"})

    planner_ = client.get("/api/v1/admin/planner").json()["data"]

    assert planner_["goals"] == "- A new-grad offer"
    assert planner_["notes"] == "Interviewing at Acme"
    assert planner_["goalsUpdatedAt"] is not None
    assert planner_["notesUpdatedAt"] is not None


def test_what_is_not_given_is_kept(client):
    client.put("/api/v1/admin/planner", json={"goals": "Graduate", "notes": "Busy"})
    changed = client.put("/api/v1/admin/planner", json={"notes": ""}).json()["data"]

    assert (changed["goals"], changed["notes"]) == ("Graduate", "")


def test_goals_are_a_page_at_most(client):
    response = client.put("/api/v1/admin/planner", json={"goals": "x" * 4001})
    assert response.status_code == 422


def test_only_the_admin_has_the_planner(client):
    client.app.dependency_overrides.pop(get_current_admin)
    assert client.get("/api/v1/admin/planner").status_code == 401
    assert client.put("/api/v1/admin/planner", json={"goals": "x"}).status_code == 401
