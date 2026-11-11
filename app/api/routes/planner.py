"""What Claude's morning plan works from (services/planner.py): the owner's
long-term goals, which they write, and the notes Claude keeps on them,
which they can read and correct."""

from fastapi import APIRouter

from api.dependencies import CurrentAdmin
from db.dependency import DB
from schemas.planner import PlannerResponse, PlannerUpdate
from services import planner

router = APIRouter()


async def current(db: DB) -> PlannerResponse:
    goals = await planner.setting(db, planner.GOALS)
    notes = await planner.setting(db, planner.NOTES)
    return PlannerResponse(
        goals=goals.text,
        goals_updated_at=goals.updated_at,
        notes=notes.text,
        notes_updated_at=notes.updated_at,
    )


@router.get("/admin/planner")
async def get_planner(_admin: CurrentAdmin, db: DB):
    return {"data": await current(db)}


@router.put("/admin/planner")
async def set_planner(body: PlannerUpdate, _admin: CurrentAdmin, db: DB):
    if body.goals is not None:
        await planner.put(db, planner.GOALS, body.goals)
    if body.notes is not None:
        await planner.put(db, planner.NOTES, body.notes)
    await db.commit()
    # A fresh read: updated_at is set by the database.
    db.expire_all()
    return {"data": await current(db)}
