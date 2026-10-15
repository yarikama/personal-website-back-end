"""The site's visitors, for the admin, from Vercel Web Analytics."""

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from api.dependencies import CurrentAdmin
from core import config
from services import visitors

router = APIRouter()

# Vercel's query API took up to 17 s for the six queries (2026-10-05).
TIMEOUT_SECONDS = 30


def dump(count: visitors.Count, key: str = "name") -> dict[str, Any]:
    return {key: count.name, "pageviews": count.pageviews, "visitors": count.visitors}


@router.get("/admin/visitors")
async def visitors_report(
    _admin: CurrentAdmin,
    days: Annotated[
        int, Query(ge=1, le=visitors.MAX_DAYS, description="Up to today (UTC)")
    ] = 7,
):
    """Page views and visitors over the last `days` days: the totals, each
    day, and the top pages, referrers, countries and devices."""
    token = str(config.VERCEL_TOKEN)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vercel Web Analytics is not set up (no VERCEL_TOKEN).",
        )
    vercel = visitors.Vercel(token, config.VERCEL_PROJECT, config.VERCEL_TEAM)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
            report = await visitors.report(days, http, vercel)
    except visitors.VercelUnavailableError as error:
        logger.warning(f"Vercel Web Analytics unavailable: {error}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Vercel Web Analytics is unavailable right now.",
        ) from error
    return {
        "data": {
            "since": report.since,
            "until": report.until,
            "pageviews": report.pageviews,
            "visitors": report.visitors,
            "days": [dump(day, "date") for day in report.days],
            "pages": [dump(page) for page in report.pages],
            "referrers": [dump(referrer) for referrer in report.referrers],
            "countries": [dump(country) for country in report.countries],
            "devices": [dump(device) for device in report.devices],
        }
    }
