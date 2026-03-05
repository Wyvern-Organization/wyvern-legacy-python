from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.config import get_settings
from app.models import User
from app.schemas.upload import UploadOut
from app.services.rate_limiter import rate_limiter
from app.services.storage import upload_file_to_storage
from app.utils.dependencies import get_current_user
from app.utils.responses import success_response


router = APIRouter(prefix="/uploads", tags=["uploads"])
settings = get_settings()


async def _get_upload_size(file: UploadFile) -> int:
    size = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
    await file.seek(0)
    return size


@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict:
    await rate_limiter.check(
        key_prefix="uploads",
        actor_id=current_user.id,
        limit=settings.rate_limit_upload_count,
        window_seconds=settings.rate_limit_upload_window_seconds,
    )

    size = await _get_upload_size(file)
    if not current_user.is_paid and size > settings.free_upload_limit_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Free-tier uploads are limited to {settings.free_upload_limit_bytes} bytes",
        )

    url = await upload_file_to_storage(file, current_user.id)

    payload = UploadOut(
        url=url,
        filename=file.filename or "upload.bin",
        content_type=file.content_type or "application/octet-stream",
        size=size,
    )
    return success_response(payload.model_dump())
