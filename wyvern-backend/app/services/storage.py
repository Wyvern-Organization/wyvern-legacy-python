from dataclasses import dataclass
from pathlib import Path
import re
from uuid import uuid4

import aiofiles
import filetype
from fastapi import HTTPException, UploadFile, status

from app.config import get_settings


ALLOWED_MIME_PREFIXES = (
    "image/",
    "video/",
    "audio/",
)

ALLOWED_EXACT_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "application/pdf",
    "application/json",
    "application/zip",
    "application/x-zip-compressed",
}

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

TRUSTED_EXTENSIONS_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/json": ".json",
    "text/plain": ".txt",
    "text/markdown": ".md",
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SAFE_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
ACTIVE_TEXT_MARKERS = (
    b"<script",
    b"<!doctype html",
    b"<html",
    b"<svg",
    b"<?xml",
)


@dataclass(frozen=True)
class UploadInspection:
    mime_type: str
    extension: str


def _normalize_mime_type(content_type: str | None) -> str:
    if not content_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing MIME type")
    return content_type.split(";", 1)[0].strip().lower()


def _is_allowed_mime_type(mime_type: str) -> bool:
    if mime_type in BLOCKED_MIME_TYPES:
        return False
    return mime_type in ALLOWED_EXACT_MIME_TYPES or any(mime_type.startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES)


def _reject(detail: str) -> None:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _sniff_active_content(head: bytes) -> bool:
    lowered = head[:8192].lower().lstrip()
    return any(marker in lowered for marker in ACTIVE_TEXT_MARKERS)


def _looks_like_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _validate_text_payload(head: bytes) -> None:
    if not _looks_like_text(head):
        _reject("MIME type does not match file content")
    if _sniff_active_content(head):
        _reject("Active document uploads are not supported")


def _trusted_extension_for(mime_type: str, detected_extension: str | None = None) -> str:
    if detected_extension:
        extension = f".{detected_extension.lower().lstrip('.')}"
        if extension not in BLOCKED_EXTENSIONS:
            return extension
    extension = TRUSTED_EXTENSIONS_BY_MIME.get(mime_type)
    if not extension:
        _reject("Unsupported MIME type")
    return extension


async def inspect_upload(file_obj: UploadFile) -> UploadInspection:
    filename = file_obj.filename or ""
    if Path(filename).suffix.lower() in BLOCKED_EXTENSIONS:
        _reject("Unsupported file extension")

    claimed_mime = _normalize_mime_type(file_obj.content_type)
    if not _is_allowed_mime_type(claimed_mime):
        _reject("Unsupported MIME type")

    await file_obj.seek(0)
    head = await file_obj.read(8192)
    await file_obj.seek(0)

    if _sniff_active_content(head):
        _reject("Active document uploads are not supported")

    detected = filetype.guess(head)
    detected_mime = detected.mime.lower() if detected and detected.mime else None
    detected_extension = detected.extension if detected else None

    if detected_mime in BLOCKED_MIME_TYPES:
        _reject("Unsupported MIME type")

    if detected_mime:
        normalized_detected = "application/zip" if detected_mime == "application/x-zip-compressed" else detected_mime
        normalized_claimed = "application/zip" if claimed_mime == "application/x-zip-compressed" else claimed_mime
        if normalized_detected != normalized_claimed:
            _reject("MIME type does not match file content")
        return UploadInspection(mime_type=claimed_mime, extension=_trusted_extension_for(claimed_mime, detected_extension))

    if claimed_mime in {"text/plain", "text/markdown", "application/json"}:
        _validate_text_payload(head)
        return UploadInspection(mime_type=claimed_mime, extension=_trusted_extension_for(claimed_mime))

    _reject("MIME type does not match file content")


async def virus_scan_hook(file_obj: UploadFile) -> bool:
    try:
        await inspect_upload(file_obj)
    except HTTPException:
        return False
    return True


def _safe_user_segment(user_id: str) -> str:
    segment = str(user_id).strip()
    if not SAFE_USER_ID_RE.fullmatch(segment):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid upload owner")
    return segment


async def upload_file_to_storage(file: UploadFile, user_id: str) -> str:
    settings = get_settings()
    inspection = await inspect_upload(file)

    relative_key = Path(_safe_user_segment(user_id)) / f"{uuid4()}{inspection.extension}"

    media_dir = settings.resolve_media_dir(PROJECT_ROOT).resolve()
    target_path = (media_dir / relative_key).resolve()
    if not target_path.is_relative_to(media_dir):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid upload path")
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
