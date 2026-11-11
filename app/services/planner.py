"""
Claude's morning plan of the admin's to-dos. A scheduled Claude reads the
owner's goals, the notes it keeps on the owner, the open to-dos and what
was done lately (with their mail and calendar, which it reads itself), and
lays out the day's items: due today, to move on this week, can wait.

Each morning redraws Claude's own items and leaves the owner's and the
daily ones alone. An item it keeps (the same text) stays the same row, so
an app that holds its id (Attention) can still tick it; one it drops is
removed, and what was done stays done.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.admin_setting import AdminSetting
from db.models.todo import AdminTodo
from schemas.todos import PlanItem
from services import todos

GOAL = "goal"
GOALS = "long_term_goals"
NOTES = "planner_notes"
# How far back what was done is shown, for what has moved lately.
DONE_DAYS = 7


@dataclass(frozen=True)
class Setting:
    text: str
    updated_at: datetime | None


async def setting(db: AsyncSession, key: str) -> Setting:
    row = await db.get(AdminSetting, key)
    return Setting(row.value, row.updated_at) if row else Setting("", None)


async def put(db: AsyncSession, key: str, text: str) -> None:
    """Sets `key` to `text`; the caller commits."""
    row = await db.get(AdminSetting, key)
    if row is None:
        db.add(AdminSetting(key=key, value=text))
    else:
        row.value = text


@dataclass(frozen=True)
class Context:
    goal: Setting
    goals: Setting
    notes: Setting
    open: list[AdminTodo]
    done: list[AdminTodo]


async def context(db: AsyncSession, day: date) -> Context:
    """What the plan for `day` starts from."""
    open = [row for row in await todos.for_day(db, day) if row.done_on is None]
    done = await db.scalars(
        select(AdminTodo)
        .where(
            AdminTodo.removed.is_(False),
            AdminTodo.done_on.between(day - timedelta(days=DONE_DAYS), day),
        )
        .order_by(AdminTodo.done_on.desc(), AdminTodo.created_at)
    )
    return Context(
        goal=await setting(db, GOAL),
        goals=await setting(db, GOALS),
        notes=await setting(db, NOTES),
        open=open,
        done=list(done),
    )


async def redraw(
    db: AsyncSession, day: date, items: list[PlanItem], notes: str | None = None
) -> list[AdminTodo]:
    """
    Claude's open items become `items`, in that order: one with the text
    of an open item keeps its row (and the day it was first for), with the
    new bucket, reason and link; the rest are added for `day`; open items
    not among them are removed. `notes`, when given, replaces the notes.
    """
    open = await db.scalars(
        select(AdminTodo).where(
            AdminTodo.source == "claude",
            AdminTodo.removed.is_(False),
            AdminTodo.done_on.is_(None),
            AdminTodo.day <= day,
        )
    )
    by_text = {row.text: row for row in open}
    rows = []
    for item in items:
        row = by_text.pop(item.text, None)
        if row is None:
            row = AdminTodo(text=item.text, day=day, source="claude")
            db.add(row)
        row.bucket, row.reason, row.href = item.bucket, item.reason, item.href
        rows.append(row)
    for row in by_text.values():
        row.removed = True
    if notes is not None:
        await put(db, NOTES, notes)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return rows
