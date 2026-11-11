"""
The morning plan of the admin's to-dos (services/planner.py), for a
scheduled Claude: what it starts from, and the day's items it lays out.
It reads the owner's mail and calendar through their own connectors.
"""

from datetime import date
from typing import Annotated, Any

from loguru import logger
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models.todo import AdminTodo
from mcp_server.common import READ, today, who
from schemas.planner import Page
from schemas.todos import PlanItem
from services import planner

# Redrawing with the same items again changes nothing.
REDRAW = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
MAX_ITEMS = 12

Day = Annotated[
    date,
    Field(
        description="The owner's today, in their time zone (America/Chicago), "
        "e.g. '2026-11-11'"
    ),
]


def near_today(day: date) -> None:
    # The owner's today is UTC's, or a day either side of it.
    if abs((day - today()).days) > 1:
        raise ToolError(f"{day} is not today in any time zone; give the owner's today.")


def plain(row: AdminTodo) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "text": row.text,
        "source": row.source,
        "bucket": row.bucket,
        "reason": row.reason,
        "href": row.href,
        "day": row.day.isoformat(),
        "done_on": row.done_on.isoformat() if row.done_on else None,
    }


def register(server: MCPServer, sessions: async_sessionmaker[AsyncSession]) -> None:
    @server.tool(annotations=READ)
    async def get_planner_context(day: Day) -> dict[str, Any]:
        """
        What the morning plan starts from: the owner's current goal (a few
        words) and long-term goals (Markdown), the notes you keep on them
        between mornings, every open to-do (the owner's, the daily ones and
        yours, each with the day it was first for) and what was done in the
        last week. Read their mail and calendar too before planning.
        """
        near_today(day)
        async with sessions() as db:
            found = await planner.context(db, day)
            return {
                "goal": found.goal.text,
                "long_term_goals": found.goals.text,
                "notes": found.notes.text,
                "notes_updated_at": (
                    found.notes.updated_at.isoformat()
                    if found.notes.updated_at
                    else None
                ),
                "open": [plain(row) for row in found.open],
                "done_lately": [plain(row) for row in found.done],
            }

    @server.tool(annotations=REDRAW)
    async def set_todays_plan(
        day: Day,
        items: Annotated[
            list[PlanItem],
            Field(
                max_length=MAX_ITEMS,
                description="The day's items, most pressing first: the whole "
                "plan, since your open items not in it are removed. Keep an "
                "item's text word for word to keep it (the owner's apps hold "
                "it by id). Leave out what the owner or the daily items "
                "already cover.",
            ),
        ],
        notes: Annotated[
            Page | None,
            Field(
                description="Your notes on the owner, replacing the last: what "
                "they are working towards and what is coming up, in a few "
                "lines of Markdown, for the next morning. Omit to keep them."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """
        Lays out the day's to-dos on the admin's welcome page: due today
        ("today"), to move on this week ("week"), or can wait ("later"),
        each with why. Only your own items change; the owner's and the
        daily ones stay as they are.
        """
        near_today(day)
        texts = [item.text for item in items]
        if len(set(texts)) < len(texts):
            raise ToolError("Two items have the same text; merge them.")
        async with sessions() as db:
            rows = await planner.redraw(db, day, items, notes)
        logger.info(f"MCP set_todays_plan by {who()}: {day}, {len(rows)} items")
        return {"items": [plain(row) for row in rows]}
