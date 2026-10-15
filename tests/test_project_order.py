from datetime import datetime

import pytest
from db.dependency import get_db
from db.models.category import Category
from db.models.projects import Project
from fastapi.testclient import TestClient
from main import get_application
from sqlalchemy import delete

PREFIX = "test-order-"


@pytest.fixture
def session_factory(sync_sessions):
    factory = sync_sessions
    yield factory
    with factory() as db:
        db.execute(delete(Project).where(Project.slug.startswith(PREFIX)))
        db.execute(delete(Category).where(Category.name.startswith(PREFIX)))
        db.commit()


def test_public_projects_follow_order_field(session_factory, db_override):
    # Inserted in an order that matches neither `order` nor creation time, so the
    # test fails if the endpoint falls back to insertion or physical row order.
    rows = [
        ("c", 2, datetime(2026, 1, 3)),
        ("a", 0, datetime(2026, 1, 1)),
        ("b", 1, datetime(2026, 1, 2)),
        ("d", 1, datetime(2026, 1, 4)),  # ties with b on order; newer comes first
    ]
    with session_factory() as db:
        category = Category(name=PREFIX + "cat", label="Test")
        db.add(category)
        db.flush()
        for name, order, created in rows:
            db.add(
                Project(
                    slug=PREFIX + name,
                    title=name,
                    description=name,
                    tags=[],
                    year="2026",
                    category_id=category.id,
                    featured=False,
                    order=order,
                    published=True,
                    created_at=created,
                )
            )
        db.commit()

    app = get_application()
    app.dependency_overrides[get_db] = db_override
    response = TestClient(app).get("/api/v1/projects")

    assert response.status_code == 200
    slugs = [p["slug"] for p in response.json()["data"] if p["slug"].startswith(PREFIX)]
    assert slugs == [PREFIX + s for s in ["a", "d", "b", "c"]]
