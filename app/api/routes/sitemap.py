"""The site's sitemap.xml (services/sitemap.py), proxied by the front end."""

from fastapi import APIRouter, Response

from core.config import CANONICAL_SITE_URL
from db.dependency import DB
from services import sitemap

router = APIRouter()


@router.get("/sitemap.xml", include_in_schema=False)
async def get_sitemap(db: DB) -> Response:
    return Response(
        sitemap.xml(CANONICAL_SITE_URL, await sitemap.urls(db)),
        media_type="application/xml",
        # Search engines ask rarely; an hour at the edge spares the database.
        headers={"Cache-Control": "public, max-age=3600"},
    )
