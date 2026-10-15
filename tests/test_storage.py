"""
R2 through the S3 API (services/storage.py), against a fake S3 client that
records each call: which bucket and keys, and what is sent. Uploads
themselves are in tests/test_upload_limits.py.
"""

from contextlib import asynccontextmanager

import pytest
from core import config
from services import storage
from services.images import WIDTHS, variant_key
from services.storage import IMMUTABLE, TIMEOUTS, R2StorageService

pytestmark = pytest.mark.anyio

PUBLIC = "https://assets.test"


class FakeBody:
    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self):
        return self.data


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages
        self.asked = None

    def paginate(self, **kwargs):
        self.asked = kwargs

        async def pages():
            for page in self.pages:
                yield page

        return pages()


class FakeS3:
    def __init__(self, objects=None, pages=None):
        self.objects = dict(objects or {})
        self.calls = []
        self.paginator = FakePaginator(pages or [])

    async def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))

    async def delete_objects(self, **kwargs):
        self.calls.append(("delete_objects", kwargs))

    async def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        return {"Body": FakeBody(self.objects[kwargs["Key"]])}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self.paginator


def service_on(s3, monkeypatch):
    service = R2StorageService()
    service.bucket_name = "bucket"
    service.public_url = PUBLIC

    @asynccontextmanager
    async def client():
        yield s3

    monkeypatch.setattr(service, "_client", client)
    return service


async def test_the_client_is_r2s_s3_endpoint_with_short_timeouts(monkeypatch):
    opened = {}

    class FakeSession:
        def client(self, service, **kwargs):
            opened.update(service=service, **kwargs)

            @asynccontextmanager
            async def s3():
                yield "the s3 client"

            return s3()

    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(storage.aioboto3, "Session", FakeSession)

    async with R2StorageService()._client() as s3:
        assert s3 == "the s3 client"

    assert opened == {
        "service": "s3",
        "endpoint_url": "https://acct.r2.cloudflarestorage.com",
        "aws_access_key_id": "key-id",
        "aws_secret_access_key": "secret",
        "region_name": "auto",
        "config": TIMEOUTS,
    }


async def test_deleting_an_original_deletes_its_variants_too(monkeypatch):
    s3 = FakeS3()
    service = service_on(s3, monkeypatch)
    key = "covers/20260101/a.png"

    assert await service.delete_image(f"{PUBLIC}/{key}") is True

    [(name, sent)] = s3.calls
    assert name == "delete_objects"
    assert sent["Bucket"] == "bucket"
    assert sent["Delete"] == {
        "Objects": [{"Key": key}] + [{"Key": variant_key(key, w)} for w in WIDTHS],
        "Quiet": True,
    }


async def test_deleting_an_image_without_variants_deletes_only_it(monkeypatch):
    s3 = FakeS3()
    service = service_on(s3, monkeypatch)

    await service.delete_image(f"{PUBLIC}/notes/20260101/a.gif")

    [(_, sent)] = s3.calls
    assert sent["Delete"]["Objects"] == [{"Key": "notes/20260101/a.gif"}]


async def test_an_object_is_read_back_whole(monkeypatch):
    s3 = FakeS3({"covers/a.png": b"bytes of a"})
    service = service_on(s3, monkeypatch)

    assert await service.read("covers/a.png") == b"bytes of a"
    assert s3.calls == [("get_object", {"Bucket": "bucket", "Key": "covers/a.png"})]


async def test_variants_are_written_to_be_cached_for_good(monkeypatch):
    s3 = FakeS3()
    service = service_on(s3, monkeypatch)

    await service.write("covers/a.w640.webp", b"webp", "image/webp")

    assert s3.calls == [
        (
            "put_object",
            {
                "Bucket": "bucket",
                "Key": "covers/a.w640.webp",
                "Body": b"webp",
                "ContentType": "image/webp",
                "CacheControl": IMMUTABLE,
            },
        )
    ]


async def test_every_page_of_keys_is_listed(monkeypatch):
    pages = [
        {"Contents": [{"Key": "covers/a.png"}, {"Key": "covers/b.png"}]},
        {"Contents": [{"Key": "notes/c.png"}]},
        {},  # an empty bucket's page has no Contents
    ]
    s3 = FakeS3(pages=pages)
    service = service_on(s3, monkeypatch)

    assert await service.list_keys() == ["covers/a.png", "covers/b.png", "notes/c.png"]
    assert s3.paginator.asked == {"Bucket": "bucket"}


def test_a_public_url_maps_back_to_its_key():
    service = R2StorageService()
    service.public_url = PUBLIC

    assert service.key_for(f"{PUBLIC}/covers/a.png") == "covers/a.png"
