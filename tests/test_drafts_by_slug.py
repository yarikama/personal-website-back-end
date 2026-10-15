import os
from datetime import date

import pytest
from db.dependency import get_async_db
from db.models.category import Category
from db.models.lab_notes import LabNote
from db.models.projects import Project
from db.session import Base
from fastapi.testclient import TestClient
from main import get_application
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

PREFIX = "test-draft-"


@pytest.fixture
def client(async_db):
    database_url = os.getenv(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/test_db"
    )
    engine = create_engine(database_url)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

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
    app.dependency_overrides[get_async_db] = async_db
    yield TestClient(app)

    with factory() as db:
        db.query(LabNote).filter(LabNote.slug.startswith(PREFIX)).delete(
            synchronize_session=False
        )
        db.query(Project).filter(Project.slug.startswith(PREFIX)).delete(
            synchronize_session=False
        )
        db.query(Category).filter(Category.name.startswith(PREFIX)).delete(
            synchronize_session=False
        )
        db.commit()


@pytest.mark.parametrize("kind", ["lab-notes", "projects"])
def test_a_draft_is_not_public_even_by_its_slug(client, kind):
    assert client.get(f"/api/v1/{kind}/{PREFIX}live").status_code == 200
    assert client.get(f"/api/v1/{kind}/{PREFIX}draft").status_code == 404
