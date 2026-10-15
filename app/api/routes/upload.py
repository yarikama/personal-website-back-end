from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from loguru import logger
from redis.exceptions import RedisError

from api.dependencies import CurrentAdmin
from schemas.upload import DeleteResponse, UploadData, UploadResponse
from services.image_jobs import enqueue_variants
from services.storage import storage_service

router = APIRouter()


@router.post("/admin/upload/image", response_model=UploadResponse)
async def upload_image(
    request: Request,
    _admin: CurrentAdmin,
    file: Annotated[UploadFile, File()],
    folder: str = "images",
):
    """Upload an image to R2 storage. Returns the public URL."""
    url = await storage_service.upload_image(file, folder)
    # WebP variants are made in the background (worker.py). If queuing
    # fails, the worker's hourly reconcile finds the image anyway.
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await enqueue_variants(redis, storage_service.key_for(url))
        except (RedisError, OSError) as error:
            logger.warning(f"Could not queue variants for {url}: {error!r}")
    return UploadResponse(data=UploadData(url=url, filename=file.filename))


@router.delete("/admin/upload/image", response_model=DeleteResponse)
async def delete_image(
    _admin: CurrentAdmin,
    url: str,
):
    """Delete an image from R2 storage by its URL."""
    await storage_service.delete_image(url)
    return DeleteResponse(message="Image deleted successfully")
