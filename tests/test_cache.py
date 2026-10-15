from typing import cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from api.cache import CACHE_CONTROL, PublicCacheMiddleware, etag_for, matches
from main import get_application

ORIGIN = {"Origin": "https://www.yarikama.com"}


def app_with_routes():
    """The production middleware order around stand-in routes (no database)."""
    app = FastAPI()

    @app.get("/api/v1/projects")
    def projects():
        return {"data": [{"title": "PAPIT"}]}

    @app.get("/api/v1/lab-notes/{slug}")
    def missing(slug: str):
        from fastapi import HTTPException

        raise HTTPException(404)

    @app.get("/api/v1/admin/projects")
    def admin():
        return {"data": []}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://yarikama.com", "https://www.yarikama.com"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(PublicCacheMiddleware)
    return TestClient(app)


def test_published_content_is_cacheable_for_every_origin():
    response = app_with_routes().get("/api/v1/projects", headers=ORIGIN)

    assert response.status_code == 200
    assert response.headers["cache-control"] == CACHE_CONTROL
    assert response.headers["etag"] == etag_for(response.content)
    # One cached copy must work for yarikama.com and www.yarikama.com alike.
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers
    assert "vary" not in response.headers


def test_a_client_with_the_current_copy_gets_304():
    client = app_with_routes()
    etag = client.get("/api/v1/projects").headers["etag"]

    response = client.get("/api/v1/projects", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag


def test_a_stale_copy_gets_the_new_content():
    response = app_with_routes().get(
        "/api/v1/projects", headers={"If-None-Match": '"old"'}
    )

    assert response.status_code == 200
    assert response.json() == {"data": [{"title": "PAPIT"}]}


def test_errors_and_admin_routes_are_not_cached():
    client = app_with_routes()

    missing = client.get("/api/v1/lab-notes/nope", headers=ORIGIN)
    admin = client.get("/api/v1/admin/projects", headers=ORIGIN)

    assert missing.status_code == 404
    for response in (missing, admin):
        assert "cache-control" not in response.headers
        assert "etag" not in response.headers
    assert admin.headers["access-control-allow-origin"] == "https://www.yarikama.com"


def test_if_none_match_parsing():
    assert matches('"a", W/"b"', '"b"')
    assert matches("*", '"b"')
    assert not matches('"a"', '"b"')


def test_the_app_puts_the_cache_outside_cors():
    names = [cast(type, m.cls).__name__ for m in get_application().user_middleware]

    # user_middleware lists the outermost first. The security headers go on
    # everything, cached copies included.
    assert names[:3] == [
        "SecurityHeadersMiddleware",
        "PublicCacheMiddleware",
        "CORSMiddleware",
    ]
