import io
from contextlib import asynccontextmanager

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from PIL import Image
from starlette.datastructures import Headers

from api import body_limit
from api.dependencies.auth import get_current_admin
from main import get_application
from services import images
from services.storage import (
    R2StorageService,
    UploadRefusedError,
    UploadTooLargeError,
)

client = TestClient(get_application())


def image_bytes(fmt: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), "teal").save(out, format=fmt)
    return out.getvalue()


# ── request size ────────────────────────────────────────────────────────────


def test_a_declared_body_over_the_limit_is_refused_unread():
    response = client.post(
        "/api/v1/contact",
        content=b"{}",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(body_limit.MAX_BODY_BYTES + 1),
        },
    )
    assert response.status_code == 413


def test_a_streamed_body_is_cut_off_at_the_limit():
    def chunks():
        for _ in range(3):
            yield b"x" * (body_limit.MAX_BODY_BYTES // 2 + 1)

    response = client.post("/api/v1/ask", content=chunks())
    assert response.status_code == 413


def test_uploads_may_be_larger_than_other_bodies():
    size = body_limit.MAX_BODY_BYTES * 2
    assert size <= images.MAX_UPLOAD_BYTES
    response = client.post(
        "/api/v1/admin/upload/image",
        content=b"x" * size,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code != 413  # refused for having no token instead
    assert body_limit.limit_for("/api/v1/admin/upload/image") == images.MAX_UPLOAD_BYTES


# ── what an upload is ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fmt, expected",
    [
        ("PNG", ("png", "image/png")),
        ("JPEG", ("jpg", "image/jpeg")),
        ("GIF", ("gif", "image/gif")),
        ("WEBP", ("webp", "image/webp")),
    ],
)
def test_the_bytes_decide_what_an_upload_is(fmt, expected):
    assert images.identify_upload(image_bytes(fmt)) == expected


@pytest.mark.parametrize(
    "data",
    [
        b"<html><script>alert(1)</script></html>",
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        image_bytes("PNG")[:40],  # truncated
        image_bytes("BMP"),  # an image, but not one the site serves
    ],
)
def test_anything_else_is_not_an_image(data):
    with pytest.raises(images.NotAnImageError):
        images.identify_upload(data)


# ── storing an upload ───────────────────────────────────────────────────────


@pytest.fixture
def storage(monkeypatch):
    service = R2StorageService()
    service.public_url = "https://assets.test"
    stored = []

    class FakeS3:
        async def put_object(self, **kwargs):
            stored.append(kwargs)

    @asynccontextmanager
    async def fake_client():
        yield FakeS3()

    monkeypatch.setattr(service, "_client", fake_client)
    return service, stored


def upload(data: bytes, filename: str, content_type: str) -> UploadFile:
    return UploadFile(
        io.BytesIO(data),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


@pytest.mark.anyio
async def test_a_misnamed_image_is_stored_as_what_it_is(storage):
    service, stored = storage
    url = await service.upload_image(
        upload(image_bytes("PNG"), "evil.html", "text/html"), "notes"
    )

    assert url.startswith("https://assets.test/notes/") and url.endswith(".png")
    assert stored[0]["ContentType"] == "image/png"
    assert stored[0]["Key"].endswith(".png")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "data, folder",
    [
        (b"not an image", "notes"),
        (image_bytes("PNG"), "../backups"),
        (image_bytes("PNG"), "anything"),
    ],
)
async def test_non_images_and_unknown_folders_are_refused(storage, data, folder):
    service, stored = storage
    with pytest.raises(UploadRefusedError):
        await service.upload_image(upload(data, "x.png", "image/png"), folder)
    assert stored == []


@pytest.mark.anyio
async def test_an_image_over_the_limit_is_refused(storage, monkeypatch):
    service, stored = storage
    monkeypatch.setattr("services.storage.MAX_UPLOAD_BYTES", 10)
    with pytest.raises(UploadTooLargeError):
        await service.upload_image(
            upload(image_bytes("PNG"), "x.png", "image/png"), "notes"
        )
    assert stored == []


@pytest.mark.parametrize(
    "data, folder, status, detail",
    [
        (b"not an image", "notes", 400, "Not a JPEG, PNG, GIF or WebP image."),
        (image_bytes("PNG"), "anything", 400, "Unknown folder: anything"),
        (image_bytes("PNG") * 2, "notes", 413, "Image too large (over 20 MB)."),
    ],
)
def test_the_route_answers_a_refused_upload_with_its_status(
    monkeypatch, data, folder, status, detail
):
    # Under the request body limit, over the stored image's.
    monkeypatch.setattr("services.storage.MAX_UPLOAD_BYTES", len(image_bytes("PNG")))
    app = get_application()
    app.dependency_overrides[get_current_admin] = lambda: "owner@example.com"

    response = TestClient(app).post(
        f"/api/v1/admin/upload/image?folder={folder}",
        files={"file": ("x.png", data, "image/png")},
    )

    assert response.status_code == status
    assert response.json() == {"detail": detail}
