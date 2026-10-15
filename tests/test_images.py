import io

import pytest
from PIL import Image, UnidentifiedImageError

from main import get_application
from services import image_jobs
from services.image_jobs import (
    GROUP,
    STREAM,
    make_and_store_variants,
    missing_variants,
    reconcile,
)
from services.images import WIDTHS, is_original, make_variants, variant_key
from services.jobs import Worker, dead_letter_stream


def png(width, height, mode="RGB", exif_orientation=None):
    image = Image.new(mode, (width, height), (200, 30, 30, 128)[: len(mode)])
    out = io.BytesIO()
    kwargs = {}
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kwargs["exif"] = exif
    image.save(out, "PNG", **kwargs)
    return out.getvalue()


def size(data):
    with Image.open(io.BytesIO(data)) as image:
        return image.format, image.size, image.mode


def test_variant_names_sit_next_to_the_original():
    assert variant_key("covers/20260205/230ac736.png", 640) == (
        "covers/20260205/230ac736.w640.webp"
    )


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("covers/a.png", True),
        ("notes/a.JPEG", True),
        ("notes/a.webp", True),
        ("notes/a.gif", False),  # may be animated
        ("notes/a.w640.webp", False),  # a variant
    ],
)
def test_which_keys_get_variants(key, expected):
    assert is_original(key) is expected


def test_large_images_are_scaled_down_to_each_width():
    variants = make_variants(png(3200, 1800))

    assert sorted(variants) == list(WIDTHS)
    assert size(variants[640]) == ("WEBP", (640, 360), "RGB")
    assert size(variants[1600]) == ("WEBP", (1600, 900), "RGB")


def test_small_images_are_never_upscaled():
    variants = make_variants(png(800, 600))

    assert size(variants[640])[1] == (640, 480)
    assert size(variants[1600])[1] == (800, 600)


def test_transparency_is_kept():
    assert size(make_variants(png(100, 100, "RGBA"))[640])[2] == "RGBA"


def test_phone_rotation_is_applied():
    # EXIF orientation 6: the camera was turned; the picture is portrait.
    variants = make_variants(png(400, 300, exif_orientation=6))

    assert size(variants[640])[1] == (300, 400)


def test_not_an_image_fails_the_job():
    with pytest.raises(UnidentifiedImageError):
        make_variants(b"<html>not an image</html>")


class FakeStorage:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.types = {}

    async def read(self, key):
        return self.objects[key]

    async def write(self, key, data, content_type):
        self.objects[key] = data
        self.types[key] = content_type

    async def list_keys(self):
        return list(self.objects)


@pytest.mark.anyio
async def test_a_job_writes_every_variant():
    storage = FakeStorage({"covers/a.png": png(2000, 1000)})

    await make_and_store_variants(storage, "covers/a.png")

    assert storage.types == {
        "covers/a.w640.webp": "image/webp",
        "covers/a.w1600.webp": "image/webp",
    }


def test_reconcile_finds_originals_without_all_variants():
    keys = [
        "covers/done.png",
        "covers/done.w640.webp",
        "covers/done.w1600.webp",
        "covers/half.jpg",
        "covers/half.w640.webp",
        "notes/new.png",
        "notes/anim.gif",
    ]

    assert missing_variants(keys) == ["covers/half.jpg", "notes/new.png"]


@pytest.mark.anyio
async def test_reconcile_then_the_worker_backfills_old_images(redis):
    storage = FakeStorage({"covers/old.png": png(1000, 500), "notes/x.gif": b"GIF"})

    assert await reconcile(storage, redis) == 1

    async def handle(job):
        await make_and_store_variants(storage, job["key"])

    worker = Worker(redis, STREAM, GROUP, "w1", handle, block_ms=10)
    await worker.ensure_group()
    await worker.run_once()

    assert missing_variants(await storage.list_keys()) == []
    assert await reconcile(storage, redis) == 0


@pytest.mark.anyio
async def test_uploading_an_image_queues_its_variants(redis, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from api.dependencies.auth import get_current_admin
    from services.storage import storage_service

    async def fake_upload(file, folder):
        return f"{storage_service.public_url}/covers/20260930/abcd1234.png"

    monkeypatch.setattr(storage_service, "upload_image", fake_upload)
    app = get_application()
    app.dependency_overrides[get_current_admin] = lambda: "owner"
    app.state.redis = redis

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/admin/upload/image",
            files={"file": ("cover.png", png(10, 10), "image/png")},
        )

    assert response.status_code == 200
    [(_, job)] = await redis.xrange(image_jobs.STREAM)
    assert job == {"key": "covers/20260930/abcd1234.png"}


@pytest.mark.anyio
async def test_reconcile_skips_images_whose_job_gave_up(redis):
    storage = FakeStorage(
        {"test/broken.png": b"\x89PNG truncated", "covers/new.png": png(10, 10)}
    )
    await redis.xadd(
        dead_letter_stream(STREAM), {"key": "test/broken.png", "error": "broken"}
    )

    assert await reconcile(storage, redis) == 1
    [(_, job)] = await redis.xrange(STREAM)
    assert job == {"key": "covers/new.png"}

    # Clearing the dead letters lets the next reconcile try again.
    await redis.delete(dead_letter_stream(STREAM))
    assert await reconcile(storage, redis) == 2
