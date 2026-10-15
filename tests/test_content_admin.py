"""
The admin's projects, categories and lab notes (/admin/projects,
/admin/categories, /admin/lab-notes), and what the public routes show of
them, against PostgreSQL like the other database tests. Every row has a
name or slug starting with PREFIX and is deleted afterwards.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from api.dependencies.auth import get_current_admin
from db.dependency import get_db
from db.models.category import Category
from db.models.lab_notes import LabNote
from db.models.projects import Project
from main import get_application

PREFIX = "test-admin-"


@pytest.fixture
def client(sync_sessions, db_override):
    factory = sync_sessions

    def clean():
        with factory() as db:
            for model, column in (
                (LabNote, LabNote.slug),
                (Project, Project.slug),
                (Category, Category.name),
            ):
                db.execute(delete(model).where(column.startswith(PREFIX)))
            db.commit()

    clean()
    app = get_application()
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_admin] = lambda: "owner@example.com"
    yield TestClient(app)
    clean()


def category(client, name, **fields):
    response = client.post(
        "/api/v1/admin/categories",
        json={"name": PREFIX + name, "label": name.title(), **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def project(client, slug, category_id, **fields):
    response = client.post(
        "/api/v1/admin/projects",
        json={
            "slug": PREFIX + slug,
            "title": slug.title(),
            "description": f"About {slug}",
            "tags": ["python"],
            "year": "2026",
            "categoryId": category_id,
            "featured": False,
            "order": 0,
            "published": True,
            **fields,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def lab_note(client, slug, **fields):
    response = client.post(
        "/api/v1/admin/lab-notes",
        json={
            "slug": PREFIX + slug,
            "title": slug.title(),
            "excerpt": f"About {slug}",
            "content": f"# {slug}",
            "tags": [PREFIX + "tag"],
            "readTime": "1 min read",
            "date": "2026-10-01",
            "published": True,
            **fields,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


# Projects


def test_a_new_project_comes_back_with_its_category(client):
    ml = category(client, "ml")

    created = project(client, "papit", ml["id"])

    assert created["category"]["name"] == PREFIX + "ml"
    assert created["createdAt"] is not None
    assert created["updatedAt"] is not None


def test_a_project_slug_is_taken_once(client):
    ml = category(client, "ml")
    project(client, "papit", ml["id"])

    again = client.post(
        "/api/v1/admin/projects",
        json={**project_fields(ml["id"]), "slug": PREFIX + "papit"},
    )

    assert again.status_code == 409
    assert again.json()["detail"] == f"Project with slug '{PREFIX}papit' already exists"


def test_a_project_needs_a_category_that_exists(client):
    response = client.post(
        "/api/v1/admin/projects",
        json=project_fields("00000000-0000-0000-0000-000000000001"),
    )

    assert response.status_code == 404


def test_an_updated_project_shows_its_new_values(client):
    ml, web = category(client, "ml"), category(client, "web")
    created = project(client, "papit", ml["id"])

    response = client.put(
        f"/api/v1/admin/projects/{created['id']}",
        json={"title": "PAPIT", "categoryId": web["id"]},
    )

    assert response.status_code == 200, response.text
    updated = response.json()["data"]
    assert updated["title"] == "PAPIT"
    # The new category, not the one loaded before the change.
    assert updated["category"]["name"] == PREFIX + "web"
    # Set by the database on UPDATE, so read back after it.
    assert updated["updatedAt"] > created["updatedAt"]


def test_an_update_cannot_take_another_projects_slug(client):
    ml = category(client, "ml")
    project(client, "first", ml["id"])
    second = project(client, "second", ml["id"])

    response = client.put(
        f"/api/v1/admin/projects/{second['id']}", json={"slug": PREFIX + "first"}
    )

    assert response.status_code == 409


def test_the_admin_sees_drafts_and_counts_them(client):
    ml = category(client, "ml")
    project(client, "live", ml["id"])
    draft = project(client, "draft", ml["id"], published=False)

    page = client.get(
        "/api/v1/admin/projects", params={"category_id": ml["id"], "limit": 1}
    ).json()
    everything = client.get(
        "/api/v1/admin/projects", params={"category_id": ml["id"]}
    ).json()["data"]

    assert page["pagination"]["total"] == 2
    assert page["pagination"]["hasMore"] is True
    [listed_draft] = [p for p in everything if p["id"] == draft["id"]]
    assert listed_draft["category"]["id"] == ml["id"]
    # The public routes do not show the draft.
    assert client.get(f"/api/v1/projects/{PREFIX}draft").status_code == 404
    assert client.get(f"/api/v1/projects/{PREFIX}live").status_code == 200


def test_a_projects_order_places_it_and_it_can_be_deleted(client):
    ml = category(client, "ml")
    a, b = project(client, "a", ml["id"]), project(client, "b", ml["id"])

    for item, order in ((a, 2), (b, 1)):
        client.put(f"/api/v1/admin/projects/{item['id']}", json={"order": order})
    listed = client.get("/api/v1/projects", params={"category_id": ml["id"]})

    assert [p["slug"] for p in listed.json()["data"]] == [PREFIX + "b", PREFIX + "a"]
    assert client.delete(f"/api/v1/admin/projects/{a['id']}").status_code == 204
    assert client.delete(f"/api/v1/admin/projects/{a['id']}").status_code == 404


# Categories


def test_a_category_name_is_taken_once(client):
    category(client, "ml")
    web = category(client, "web")

    twice = client.post(
        "/api/v1/admin/categories", json={"name": PREFIX + "ml", "label": "ML"}
    )
    renamed = client.put(
        f"/api/v1/admin/categories/{web['id']}", json={"name": PREFIX + "ml"}
    )

    assert twice.status_code == 409
    assert renamed.status_code == 409


def test_a_category_may_keep_its_own_name(client):
    ml = category(client, "ml")

    response = client.put(
        f"/api/v1/admin/categories/{ml['id']}",
        json={"name": PREFIX + "ml", "label": "Machine learning"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["label"] == "Machine learning"


def test_a_category_is_updated(client):
    ml = category(client, "ml")

    response = client.put(
        f"/api/v1/admin/categories/{ml['id']}", json={"label": "ML/AI"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["label"] == "ML/AI"
    assert response.json()["data"]["updated_at"] > ml["updated_at"]


def test_a_category_with_projects_is_not_deleted(client):
    ml, empty = category(client, "ml"), category(client, "empty")
    project(client, "papit", ml["id"])

    refused = client.delete(f"/api/v1/admin/categories/{ml['id']}")

    assert refused.status_code == 409
    assert "1 project(s)" in refused.json()["detail"]
    assert client.delete(f"/api/v1/admin/categories/{empty['id']}").status_code == 204
    public = client.get("/api/v1/categories").json()["data"]
    assert PREFIX + "empty" not in [c["name"] for c in public]


def test_public_categories_count_published_projects(client):
    ml, web = category(client, "ml"), category(client, "web")
    project(client, "live", ml["id"])
    project(client, "live-too", ml["id"])
    project(client, "draft", web["id"], published=False)

    data = client.get("/api/v1/categories").json()["data"]

    everything, counts = data[0], {c["name"]: c["count"] for c in data[1:]}
    assert (everything["name"], everything["id"]) == (
        "all",
        "00000000-0000-0000-0000-000000000000",
    )
    assert (counts[PREFIX + "ml"], counts[PREFIX + "web"]) == (2, 0)
    # Every published project, these and the other tests' alike.
    assert everything["count"] == sum(counts.values())


def test_with_no_category_there_is_no_all_either(client):
    class NoCategories:
        async def scalars(self, query):
            return iter([])

    client.app.dependency_overrides[get_db] = NoCategories

    assert client.get("/api/v1/categories").json() == {"data": []}


def test_categories_are_listed_by_their_order(client):
    category(client, "a", order=9)
    b = category(client, "b", order=8)

    client.put(f"/api/v1/admin/categories/{b['id']}", json={"order": 10})
    listed = client.get("/api/v1/admin/categories").json()["data"]

    mine = [c["name"] for c in listed if c["name"].startswith(PREFIX)]
    assert mine == [PREFIX + "a", PREFIX + "b"]


# Lab notes


def test_an_updated_lab_note_shows_its_new_values(client):
    created = lab_note(client, "vllm")

    response = client.put(
        f"/api/v1/admin/lab-notes/{created['id']}", json={"title": "vLLM"}
    )

    assert response.status_code == 200, response.text
    updated = response.json()["data"]
    assert updated["title"] == "vLLM"
    assert updated["updatedAt"] > created["updatedAt"]


def test_a_lab_note_slug_is_taken_once(client):
    lab_note(client, "first")
    second = lab_note(client, "second")

    again = client.post(
        "/api/v1/admin/lab-notes",
        json={**lab_note_fields(), "slug": PREFIX + "first"},
    )
    renamed = client.put(
        f"/api/v1/admin/lab-notes/{second['id']}", json={"slug": PREFIX + "first"}
    )

    assert again.status_code == 409
    assert renamed.status_code == 409


def test_published_lab_notes_and_their_tags(client):
    lab_note(client, "live", tags=[PREFIX + "tag", PREFIX + "other"])
    lab_note(client, "live-too")
    draft = lab_note(client, "draft", published=False)

    tags = client.get("/api/v1/lab-notes/tags").json()["data"]
    listed = client.get("/api/v1/lab-notes", params={"tag": PREFIX + "tag"}).json()
    admin = client.get("/api/v1/admin/lab-notes", params={"tag": PREFIX + "tag"})

    counts = {t["tag"]: t["count"] for t in tags}
    assert (counts[PREFIX + "tag"], counts[PREFIX + "other"]) == (2, 1)
    assert listed["pagination"]["total"] == 2
    assert admin.json()["pagination"]["total"] == 3
    assert client.get(f"/api/v1/lab-notes/{PREFIX}draft").status_code == 404
    assert client.get(f"/api/v1/admin/lab-notes/{draft['id']}").status_code == 200


def test_a_lab_note_is_deleted(client):
    note = lab_note(client, "gone")

    assert client.delete(f"/api/v1/admin/lab-notes/{note['id']}").status_code == 204
    assert client.delete(f"/api/v1/admin/lab-notes/{note['id']}").status_code == 404
    assert client.get(f"/api/v1/lab-notes/{PREFIX}gone").status_code == 404


def project_fields(category_id):
    return {
        "slug": PREFIX + "x",
        "title": "X",
        "description": "X",
        "tags": [],
        "year": "2026",
        "categoryId": category_id,
        "featured": False,
        "order": 0,
        "published": True,
    }


def lab_note_fields():
    return {
        "slug": PREFIX + "x",
        "title": "X",
        "excerpt": "X",
        "content": "X",
        "tags": [],
        "readTime": "1 min read",
        "date": "2026-10-01",
    }
