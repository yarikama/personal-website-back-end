"""The owner's Attention app keeps its devices in step through these: each
device sends what it recorded and reads what the others did
(db/models/attention.py). Same token as the to-dos (api/dependencies/auth.py)."""

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from api.dependencies import TodosAdmin
from db.dependency import DB
from db.models.attention import AttentionEvent
from schemas.attention import EventOut, EventsIn

router = APIRouter()


@router.post("/admin/attention/events")
async def send_events(body: EventsIn, _admin: TodosAdmin, db: DB):
    """Store a device's events. One already stored (same id) is left as it is."""
    if body.events:
        rows = [event.model_dump() for event in body.events]
        await db.execute(
            insert(AttentionEvent)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["id"])
        )
        await db.commit()
    return {"data": {"received": len(body.events)}}


@router.get("/admin/attention/events")
async def read_events(
    _admin: TodosAdmin,
    db: DB,
    after: Annotated[int, Query(ge=0, description="The last seq this device has")] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
):
    """Events after `after`, oldest first. `next` is the seq to ask after next
    time; `more` says whether there are more to read now."""
    rows = (
        await db.scalars(
            select(AttentionEvent)
            .where(AttentionEvent.seq > after)
            .order_by(AttentionEvent.seq)
            .limit(limit + 1)
        )
    ).all()
    more = len(rows) > limit
    rows = rows[:limit]
    last = rows[-1].seq if rows else after
    return {
        "data": [EventOut.model_validate(row) for row in rows],
        "next": last,
        "more": more,
    }
