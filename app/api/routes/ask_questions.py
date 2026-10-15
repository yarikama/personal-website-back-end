"""The chat's questions in the admin area: what visitors asked, the owner's
ratings of the answers (services/ask_log.py stores them), and how many
visitors asked since each admin last looked."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from api.crud import get_or_404
from api.dependencies import CurrentAdmin
from core.paginator import count, paginate
from db.dependency import DB
from db.models.admin_seen import AdminSeen
from db.models.ask import AskQuestion
from fastapi import APIRouter, Query
from schemas.ask_questions import (
    AskQuestionRating,
    AskQuestionResponse,
    NewQuestions,
    SeenQuestions,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

PAGE = "questions"


async def last_seen(db: AsyncSession, admin: str) -> datetime | None:
    row = await db.get(AdminSeen, (admin, PAGE))
    return row.seen_at if row else None


@router.get("/admin/ask/questions/new")
async def new_questions(admin: CurrentAdmin, db: DB):
    """How many visitors asked since this admin last opened Questions."""
    since = await last_seen(db, admin)
    query = select(AskQuestion).where(AskQuestion.admin.is_(False))
    if since is not None:
        query = query.where(AskQuestion.created_at > since)
    return {"data": NewQuestions(count=await count(db, query), since=since)}


@router.post("/admin/ask/questions/seen")
async def mark_questions_seen(admin: CurrentAdmin, db: DB):
    """Opening Questions: what came in after the time returned is new."""
    row = await db.get(AdminSeen, (admin, PAGE))
    previous = row.seen_at if row else None
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(AdminSeen(email=admin, page=PAGE, seen_at=now))
    else:
        row.seen_at = now
    await db.commit()
    return {"data": SeenQuestions(previous=previous)}


@router.get("/admin/ask/questions")
async def list_questions(
    _admin: CurrentAdmin,
    db: DB,
    who: Literal["visitors", "admin", "all"] = Query("visitors"),
    uncited: bool = Query(False, description="Only answers that cite nothing"),
    passage: bool = Query(False, description="Only questions about a passage"),
    failed: bool = Query(False, description="Only answers cut off or broken off"),
    rating: Literal["good", "bad", "none"] | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Newest first."""
    query = select(AskQuestion)
    if who != "all":
        query = query.where(AskQuestion.admin.is_(who == "admin"))
    if uncited:
        query = query.where(func.jsonb_array_length(AskQuestion.citations) == 0)
    if passage:
        query = query.where(AskQuestion.quote.isnot(None))
    if failed:
        query = query.where(
            (AskQuestion.status != "answered") | AskQuestion.truncated.is_(True)
        )
    if rating == "none":
        query = query.where(AskQuestion.rating.is_(None))
    elif rating:
        query = query.where(AskQuestion.rating == rating)

    query = query.order_by(AskQuestion.created_at.desc(), AskQuestion.id)
    return await paginate(db, query, AskQuestionResponse, offset, limit)


@router.patch("/admin/ask/questions/{id}")
async def rate_question(
    id: UUID,
    body: AskQuestionRating,
    _admin: CurrentAdmin,
    db: DB,
):
    row = await get_or_404(db, AskQuestion, id, "Question")
    row.rating = body.rating
    await db.commit()
    await db.refresh(row)
    return {"data": AskQuestionResponse.model_validate(row)}
