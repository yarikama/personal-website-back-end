"""
The site's sitemap.xml (/api/v1/sitemap.xml), against PostgreSQL like the
other database tests. Every row's slug or name starts with PREFIX, and they
are deleted afterwards.
"""

from datetime import date
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from db.dependency import get_db
from db.models.category import Category
from db.models.lab_notes import LabNote
from db.models.learnings import LearningNode
from db.models.projects import Project
from main import get_application
from services import sitemap

PREFIX = "test-sitemap-"
SITE = "https://www.yarikama.com"
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def note(slug, published=True):
    return LabNote(
        slug=PREFIX + slug,
        title=slug,
        excerpt=slug,
        content=slug,
        tags=[],
        read_time="1 min read",
        date=date(2026, 10, 1),
        published=published,
    )


def node(slug, kind, published=True, **extra):
    return LearningNode(
        slug=PREFIX + slug,
        kind=kind,
        title=slug,
        summary=slug,
        published=published,
        **extra,
    )


@pytest.fixture
def client(sync_sessions, db_override):
    factory = sync_sessions

    def clean():
        with factory() as db:
            db.execute(delete(LabNote).where(LabNote.slug.startswith(PREFIX)))
            db.execute(delete(Project).where(Project.slug.startswith(PREFIX)))
            db.execute(delete(Category).where(Category.name.startswith(PREFIX)))
            db.execute(delete(LearningNode).where(LearningNode.slug.startswith(PREFIX)))
            db.commit()

    clean()
    with factory() as db:
        category = Category(name=PREFIX + "cat", label="Test")
        db.add(category)
        db.flush()
        for slug, published in (("live", True), ("draft", False)):
            db.add(note(slug, published))
            db.add(
                Project(
                    slug=PREFIX + slug,
                    title=slug,
                    description=slug,
                    tags=[],
                    year="2026",
                    category_id=category.id,
                    featured=False,
                    order=0,
                    published=published,
                )
            )
        db.add_all(
            [
                node("page", "learning"),
                node("draft-page", "learning", published=False),
                node("field", "concept", overview="What the field is about."),
                node("bare", "concept"),
                node("paper", "external", ref="https://arxiv.org/abs/1707.06347"),
            ]
        )
        db.commit()

    app = get_application()
    app.dependency_overrides[get_db] = db_override
    yield TestClient(app)
    clean()


def locations(client) -> list[str]:
    response = client.get("/api/v1/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    root = ElementTree.fromstring(response.content)
    return [loc.text or "" for loc in root.findall("s:url/s:loc", NS)]


def test_the_sitemap_starts_with_the_sites_own_pages(client):
    assert locations(client)[:5] == [
        f"{SITE}/",
        f"{SITE}/works",
        f"{SITE}/notes",
        f"{SITE}/learnings",
        f"{SITE}/talks",
    ]


def test_published_notes_projects_and_learning_pages_are_listed(client):
    mine = [loc for loc in locations(client) if PREFIX in loc]

    assert sorted(mine) == sorted(
        [
            f"{SITE}/notes/{PREFIX}live",
            f"{SITE}/works/{PREFIX}live",
            f"{SITE}/learnings/{PREFIX}page",
            f"{SITE}/learnings/{PREFIX}field",
        ]
    )


def test_drafts_bare_concepts_and_external_links_are_left_out(client):
    listed = set(locations(client))

    for left_out in (
        f"{SITE}/notes/{PREFIX}draft",
        f"{SITE}/works/{PREFIX}draft",
        f"{SITE}/learnings/{PREFIX}draft-page",
        f"{SITE}/learnings/{PREFIX}bare",
        f"{SITE}/learnings/{PREFIX}paper",
    ):
        assert left_out not in listed
    assert not any("arxiv.org" in loc for loc in listed)


def test_each_row_says_when_it_last_changed(client):
    response = client.get("/api/v1/sitemap.xml")
    root = ElementTree.fromstring(response.content)
    for url in root.findall("s:url", NS):
        loc = url.findtext("s:loc", namespaces=NS) or ""
        if PREFIX in loc:
            assert url.findtext("s:lastmod", namespaces=NS)


def test_the_sitemap_is_cached_for_an_hour(client):
    response = client.get("/api/v1/sitemap.xml")
    assert response.headers["cache-control"] == "public, max-age=3600"


@pytest.mark.parametrize(
    ("kind", "ref", "overview", "path"),
    [
        ("learning", None, None, "/learnings/x"),
        ("learning", "/learnings/elsewhere", None, "/learnings/elsewhere"),
        ("learning", "https://example.com", None, None),
        ("concept", None, "  ", None),
        ("concept", None, "An overview.", "/learnings/x"),
        ("external", "https://example.com", None, None),
    ],
)
def test_where_a_node_has_a_page(kind, ref, overview, path):
    found = LearningNode(
        slug="x", kind=kind, title="x", summary="x", ref=ref, overview=overview
    )
    assert sitemap.learning_path(found) == path


def test_addresses_are_escaped():
    out = sitemap.xml(SITE, [sitemap.Url("/notes/a&b")])
    assert "<loc>https://www.yarikama.com/notes/a&amp;b</loc>" in out
