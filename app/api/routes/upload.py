from typing import Annotated

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile
from loguru import logger
from starlette.datastructures import UploadFile as FormFile

from api.dependencies import CurrentAdmin
from api.dependencies.redis import get_redis
from core import config
from schemas.upload import DeleteResponse, UploadData, UploadResponse
from services import upload_tokens
from services.image_jobs import queue_variants
from services.storage import (
    UploadRefusedError,
    UploadTooLargeError,
    storage_service,
)

router = APIRouter()


@router.post("/admin/upload/image", response_model=UploadResponse)
async def upload_image(
    request: Request,
    _admin: CurrentAdmin,
    file: Annotated[UploadFile, File()],
    folder: str = "images",
):
    """Upload an image to R2 storage. Returns the public URL."""
    try:
        url = await storage_service.upload_image(file, folder)
    except UploadTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except UploadRefusedError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    # WebP variants are made in the background (worker.py).
    await queue_variants(get_redis(request.app), storage_service.key_for(url))
    return UploadResponse(data=UploadData(url=url, filename=file.filename))


@router.post("/uploads", response_model=UploadResponse)
async def upload_with_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
):
    """
    An image from Claude Code, sent as the multipart field `file` with a
    token from the writing MCP server's start_image_upload tool
    (services/upload_tokens.py) in `Authorization: Bearer <token>`. The
    token picks the folder.

    The form is read only once the token checks out: with a File parameter,
    FastAPI would read up to 20 MB from anyone before the check.
    """
    redis = get_redis(request.app)
    if redis is None:
        raise HTTPException(status_code=503, detail="Uploads are unavailable.")
    scheme, _, token = (authorization or "").partition(" ")
    grant = (
        await upload_tokens.take(redis, token)
        if scheme.lower() == "bearer" and token
        else None
    )
    # Removed from ADMIN_EMAILS since the token was made: refused.
    if grant is None or grant["email"] not in config.ADMIN_EMAILS:
        raise HTTPException(
            status_code=401,
            detail="Unknown, used or expired upload token. Ask for a new one.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Starlette's UploadFile: what a form holds (FastAPI's is a subclass).
    file = (await request.form()).get("file")
    if not isinstance(file, FormFile):
        raise HTTPException(status_code=400, detail="Send the image as `file`.")
    try:
        url = await storage_service.upload_image(file, grant["folder"])
    except UploadTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except UploadRefusedError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    await queue_variants(redis, storage_service.key_for(url))
    logger.info(f"MCP image upload by {grant['email']}: {url}")
    return UploadResponse(data=UploadData(url=url, filename=file.filename))


@router.delete("/admin/upload/image", response_model=DeleteResponse)
async def delete_image(
    _admin: CurrentAdmin,
    url: str,
):
    """Delete an image from R2 storage by its URL."""
    await storage_service.delete_image(url)
    return DeleteResponse(message="Image deleted successfully")
