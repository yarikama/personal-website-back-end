from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from db.dependency import get_db
from db.models.category import Category
from db.models.lab_notes import LabNote
from db.models.projects import Project
from main import get_application

PREFIX = "test-draft-"


@pytest.fixture
def client(sync_sessions, db_override):
    factory = sync_sessions

    with factory() as db:
        category = Category(name=PREFIX + "cat", label="Test")
        db.add(category)
        db.flush()
        for published in (True, False):
            name = PREFIX + ("live" if published else "draft")
            db.add(
                Project(
                    slug=name,
                    title=name,
                    description=name,
                    tags=[],
                    year="2026",
                    category_id=category.id,
                    featured=False,
                    order=0,
                    published=published,
                )
            )
            db.add(
                LabNote(
                    slug=name,
                    title=name,
                    excerpt=name,
                    content=name,
                    tags=[],
                    read_time="1 min read",
                    date=date(2026, 10, 1),
                    published=published,
                )
            )
        db.commit()

    app = get_application()
    app.dependency_overrides[get_db] = db_override
    yield TestClient(app)

    with factory() as db:
        db.execute(delete(LabNote).where(LabNote.slug.startswith(PREFIX)))
        db.execute(delete(Project).where(Project.slug.startswith(PREFIX)))
        db.execute(delete(Category).where(Category.name.startswith(PREFIX)))
        db.commit()


@pytest.mark.parametrize("kind", ["lab-notes", "projects"])
def test_a_draft_is_not_public_even_by_its_slug(client, kind):
    assert client.get(f"/api/v1/{kind}/{PREFIX}live").status_code == 200
    assert client.get(f"/api/v1/{kind}/{PREFIX}draft").status_code == 404
