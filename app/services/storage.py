import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aioboto3
from aiobotocore.config import AioConfig
from fastapi import UploadFile

from core import config
from services.images import (
    MAX_UPLOAD_BYTES,
    WIDTHS,
    NotAnImageError,
    identify_upload,
    is_original,
    variant_key,
)

# botocore waits 60 s to connect by default: far too long for a request.
TIMEOUTS = AioConfig(connect_timeout=5, read_timeout=30, retries={"max_attempts": 3})

# Keys are unique per upload and never rewritten, so browsers and Cloudflare
# may keep variants for good.
IMMUTABLE = "public, max-age=31536000, immutable"

# The admin's upload folders: note images, project covers, and the default.
UPLOAD_FOLDERS = frozenset({"images", "notes", "covers"})


class UploadRefusedError(ValueError):
    """An upload that is not stored: an unknown folder, or not an image.
    The message is for the admin."""


class UploadTooLargeError(UploadRefusedError):
    """An upload over MAX_UPLOAD_BYTES."""


class R2StorageService:
    def __init__(self):
        self.endpoint_url = f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
        self.bucket_name = config.R2_BUCKET_NAME
        self.public_url = config.R2_PUBLIC_URL

    @asynccontextmanager
    async def _client(self):
        session = aioboto3.Session()
        async with session.client(  # type: ignore[attr-defined]
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=config.R2_ACCESS_KEY_ID,
            aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=TIMEOUTS,
        ) as s3:
            yield s3

    def key_for(self, url: str) -> str:
        return url.removeprefix(f"{self.public_url}/")

    async def upload_image(self, file: UploadFile, folder: str = "images") -> str:
        """
        Upload an image to R2 and return its public URL. The bytes decide
        what it is (services/images.identify_upload); the file name and the
        type the browser sent are ignored.
        """
        if folder not in UPLOAD_FOLDERS:
            raise UploadRefusedError(f"Unknown folder: {folder[:40]}")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise UploadTooLargeError("Image too large (over 20 MB).")
        try:
            ext, content_type = identify_upload(data)
        except NotAnImageError as error:
            raise UploadRefusedError("Not a JPEG, PNG, GIF or WebP image.") from error

        # The UTC date, so a key does not depend on the machine's time zone.
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        unique_id = uuid.uuid4().hex[:8]
        key = f"{folder}/{timestamp}/{unique_id}.{ext}"

        async with self._client() as s3:
            await s3.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=data,
                ContentType=content_type,
            )

        return f"{self.public_url}/{key}"

    async def delete_image(self, url: str) -> bool:
        """Delete an image, and its WebP variants, by its public URL."""
        key = self.key_for(url)
        keys = [key]
        if is_original(key):
            keys += [variant_key(key, width) for width in WIDTHS]
        async with self._client() as s3:
            # Deleting a key that does not exist is not an error in S3.
            await s3.delete_objects(
                Bucket=self.bucket_name,
                Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True},
            )
        return True

    async def read(self, key: str) -> bytes:
        async with self._client() as s3:
            response = await s3.get_object(Bucket=self.bucket_name, Key=key)
            async with response["Body"] as body:
                return await body.read()

    async def write(self, key: str, data: bytes, content_type: str) -> None:
        async with self._client() as s3:
            await s3.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=data,
                ContentType=content_type,
                CacheControl=IMMUTABLE,
            )

    async def list_keys(self) -> list[str]:
        keys = []
        async with self._client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self.bucket_name):
                keys += [item["Key"] for item in page.get("Contents", [])]
        return keys


storage_service = R2StorageService()
