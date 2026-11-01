"""The site's visitors, for the admin, from Vercel Web Analytics."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from api.dependencies import CurrentAdmin
from services import visitors

router = APIRouter()


@router.get("/admin/visitors")
async def visitors_report(
    _admin: CurrentAdmin,
    days: Annotated[
        int, Query(ge=1, le=visitors.MAX_DAYS, description="Up to today (UTC)")
    ] = 7,
):
    """Page views and visitors over the last `days` days: the totals, each
    day, and the top pages, referrers, countries and devices."""
    try:
        report = await visitors.from_settings(days)
    except visitors.NotSetUpError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    except visitors.VercelUnavailableError as error:
        logger.warning(f"Vercel Web Analytics unavailable: {error}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Vercel Web Analytics is unavailable right now.",
        ) from error
    return {"data": visitors.as_json(report)}
