"""The owner's Google Calendar on the admin's welcome page, read-only
(services/google_calendar.py). Connecting it starts at
GET /auth/google/calendar (api/routes/auth.py)."""

from datetime import datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from api.dependencies import CurrentAdmin
from api.routes.auth import google_client
from db.dependency import DB
from services import google_calendar

router = APIRouter()


@router.get("/admin/calendar")
async def calendar_events(
    _admin: CurrentAdmin,
    db: DB,
    start: Annotated[
        datetime,
        Query(description="The start of the owner's day, with its UTC offset"),
    ],
    days: Annotated[int, Query(ge=1, le=google_calendar.MAX_DAYS)] = 7,
):
    """Every calendar in the owner's list and their events over `days` days
    from `start`; or `connected: false` (and whether Google stopped
    accepting the token) when there is none to read."""
    if start.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="start needs its UTC offset",
        )
    client = google_client()
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not set up.",
        )
    connection = await google_calendar.load(db, client)
    if connection is None:
        return {"data": {"connected": False, "expired": False}}
    try:
        async with httpx.AsyncClient(timeout=google_calendar.TIMEOUT_SECONDS) as http:
            report = await google_calendar.report(connection, client, http, start, days)
    except google_calendar.NotConnectedError as error:
        logger.warning("Google no longer accepts the calendar token")
        return {"data": {"connected": False, "expired": error.expired}}
    except google_calendar.CalendarUnavailableError as error:
        logger.warning(f"Google Calendar unavailable: {error}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Google Calendar is unavailable right now.",
        ) from error
    return {"data": google_calendar.as_json(report)}


@router.delete("/admin/calendar", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_calendar(_admin: CurrentAdmin, db: DB) -> None:
    """Forgets the calendar's token and asks Google to revoke it."""
    client = google_client()
    connection = await google_calendar.load(db, client) if client else None
    await google_calendar.forget(db)
    await db.commit()
    if connection is not None:
        async with httpx.AsyncClient(timeout=google_calendar.TIMEOUT_SECONDS) as http:
            await google_calendar.revoke(connection, http)
