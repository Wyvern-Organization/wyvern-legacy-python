import re
from urllib.parse import urlsplit


CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")
MAX_PUBLIC_URL_LENGTH = 2048
MAX_ATTACHMENTS = 10


def validate_username_handle(value: str) -> str:
    username = str(value or "").strip()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("Username may only contain letters, numbers, underscores, dots, and hyphens")
    return username


def validate_public_url(value: str | None, *, field_name: str = "URL") -> str | None:
    if value is None:
        return None

    url = str(value).strip()
    if not url:
        return None
    if len(url) > MAX_PUBLIC_URL_LENGTH:
        raise ValueError(f"{field_name} is too long")
    if CONTROL_CHAR_RE.search(url) or "\\" in url:
        raise ValueError(f"{field_name} contains unsafe characters")

    if url.startswith("/media/"):
        if "/../" in url or url.endswith("/..") or url.startswith("/media/../"):
            raise ValueError(f"{field_name} contains an unsafe media path")
        return url

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an http(s) URL or a /media/ path")
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not contain embedded credentials")
    return url


def validate_public_url_list(values: list[str] | None, *, field_name: str = "attachments") -> list[str]:
    if values is None:
        return []
    if len(values) > MAX_ATTACHMENTS:
        raise ValueError(f"{field_name} are limited to {MAX_ATTACHMENTS} items")
    normalized: list[str] = []
    for item in values:
        url = validate_public_url(item, field_name=field_name)
        if url:
            normalized.append(url)
    return normalized
