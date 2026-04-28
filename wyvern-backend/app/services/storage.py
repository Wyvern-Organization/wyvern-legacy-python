from pathlib import Path
from uuid import uuid4

import aiofiles
from fastapi import HTTPException, UploadFile, status

from app.config import get_settings


ALLOWED_MIME_PREFIXES = (
    "image/",
    "video/",
    "audio/",
    "text/plain",
    "text/markdown",
    "application/pdf",
    "application/json",
    "application/zip",
)

BLOCKED_MIME_TYPES = {
    "image/svg+xml",
    "text/html",
    "application/xhtml+xml",
    "text/xml",
    "application/xml",
    "text/javascript",
    "application/javascript",
}

BLOCKED_EXTENSIONS = {
    ".svg",
    ".html",
    ".htm",
    ".xhtml",
    ".xml",
    ".js",
    ".mjs",
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


async def virus_scan_hook(file_obj: UploadFile) -> bool:
    filename = (file_obj.filename or "").lower()
    if any(filename.endswith(ext) for ext in BLOCKED_EXTENSIONS):
        return False

    content_type = (file_obj.content_type or "").lower()
    if content_type in BLOCKED_MIME_TYPES:
        return False

    await file_obj.seek(0)
    head = await file_obj.read(4096)
    await file_obj.seek(0)
    sniff = head.lower()
    if b"<script" in sniff or b"<!doctype html" in sniff or b"<svg" in sniff:
        return False
    return True


def _validate_mime_type(content_type: str | None) -> None:
    if not content_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing MIME type")

    lowered = content_type.lower()
    if lowered in BLOCKED_MIME_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported MIME type")

    if not any(
        lowered.startswith(prefix) if prefix.endswith("/") else lowered == prefix
        for prefix in ALLOWED_MIME_PREFIXES
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported MIME type")


async def upload_file_to_storage(file: UploadFile, user_id: str) -> str:
    settings = get_settings()
    _validate_mime_type(file.content_type)

    if not await virus_scan_hook(file):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File failed virus scan")

    safe_name = Path(file.filename or "upload.bin").name.replace("/", "_").replace("\\", "_")
    relative_key = Path(str(user_id)) / f"{uuid4()}-{safe_name}"

    media_dir = settings.resolve_media_dir(PROJECT_ROOT)
    target_path = media_dir / relative_key
    target_path.parent.mkdir(parents=True, exist_ok=True)

    await file.seek(0)
    async with aiofiles.open(target_path, "wb") as output:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            await output.write(chunk)

    await file.seek(0)
    return f"{settings.media_url_prefix}/{relative_key.as_posix()}"
