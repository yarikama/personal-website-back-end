"""
The site's sitemap.xml, for search engines: its fixed pages, then every
published note, project and learning page, each with when it last changed.
The front end (Vercel) serves it at /sitemap.xml by proxying here.
"""

from dataclasses import dataclass
from datetime import date, datetime
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.lab_notes import LabNote
from db.models.learnings import LearningNode
from db.models.projects import Project

# The site's own pages, which no row lists.
PAGES = ("/", "/works", "/notes", "/learnings", "/talks")


@dataclass(frozen=True)
class Url:
    path: str
    lastmod: date | None = None


def learning_path(node: LearningNode) -> str | None:
    """Where a published node has a page of its own on the site: a learning
    (its ref, or /learnings/<slug>), or a field or topic with an overview.
    External links and bare concepts have none."""
    if node.kind == "learning":
        ref = node.ref or f"/learnings/{node.slug}"
        return ref if ref.startswith("/learnings/") else None
    if node.kind == "concept" and (node.overview or "").strip():
        return f"/learnings/{node.slug}"
    return None


def day(moment: datetime | date | None) -> date | None:
    if isinstance(moment, datetime):
        return moment.date()
    return moment


async def urls(db: AsyncSession) -> list[Url]:
    found = [Url(path) for path in PAGES]
    notes = await db.scalars(
        select(LabNote).where(LabNote.published.is_(True)).order_by(LabNote.date.desc())
    )
    found += [Url(f"/notes/{n.slug}", day(n.updated_at) or n.date) for n in notes]
    projects = await db.scalars(
        select(Project).where(Project.published.is_(True)).order_by(Project.slug)
    )
    found += [Url(f"/works/{p.slug}", day(p.updated_at)) for p in projects]
    nodes = await db.scalars(
        select(LearningNode)
        .where(LearningNode.published.is_(True))
        .order_by(LearningNode.slug)
    )
    seen = {url.path for url in found}
    for node in nodes:
        path = learning_path(node)
        if path and path not in seen:
            seen.add(path)
            found.append(Url(path, day(node.updated_at)))
    return found


def xml(site: str, found: list[Url]) -> str:
    site = site.rstrip("/")
    entries = []
    for url in found:
        lastmod = f"<lastmod>{url.lastmod.isoformat()}</lastmod>" if url.lastmod else ""
        entries.append(f"<url><loc>{escape(site + url.path)}</loc>{lastmod}</url>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )
