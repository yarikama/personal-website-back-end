"""The owner's GitHub contribution graph, for the admin's welcome page."""

import httpx
from fastapi import APIRouter, HTTPException, status
from loguru import logger

from api.dependencies import CurrentAdmin
from core import config
from services import github

router = APIRouter()

TIMEOUT_SECONDS = 10


@router.get("/admin/github/contributions")
async def contributions(_admin: CurrentAdmin):
    """Every day of the last year, oldest first: its count and GitHub's 0-4
    shade."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
            days = await github.contributions(config.GITHUB_USER, http)
    except github.GitHubUnavailableError as error:
        logger.warning(f"GitHub contributions unavailable: {error}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub's contribution graph is unavailable right now.",
        ) from error
    return {
        "data": {
            "user": config.GITHUB_USER,
            "total": sum(day.count for day in days),
            "days": [
                {"date": day.date, "level": day.level, "count": day.count}
                for day in days
            ],
        }
    }
