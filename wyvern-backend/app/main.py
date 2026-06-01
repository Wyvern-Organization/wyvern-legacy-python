from contextlib import asynccontextmanager
import logging
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, HTTPException, Request, WebSocket, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import engine
from app.routers import admin, auth, channels, dms, messages, runtime, servers, sync, uploads, users, webhooks, workspaces
from app.services.pubsub import start_pubsub_listener, stop_pubsub_listener
from app.services.redis_client import close_redis, init_redis
from app.services.recommendations import start_recommendation_worker, stop_recommendation_worker
from app.services.sync_bridge import start_sync_bridge_worker, stop_sync_bridge_worker
from app.utils.responses import error_response, success_response
from app.websocket.handlers import websocket_endpoint


settings = get_settings()
logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_FILE = PROJECT_ROOT / "index.html"
ADMIN_FILE = PROJECT_ROOT / "admin.html"
LANDING_FILE = PROJECT_ROOT.parent / "landing" / "index.html"
CHANGELOG_FILE = PROJECT_ROOT / "changelog.md"
ROOT_LOGO_FILE = PROJECT_ROOT / "wyvern_logo_transparent.png"
ROOT_FULL_LOGO_FILE = PROJECT_ROOT / "wyvern_logo.png"
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_LOGO_FILE = STATIC_DIR / "wyvern-logo.png"
MEDIA_DIR = settings.resolve_media_dir(PROJECT_ROOT)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
ALEMBIC_INI_FILE = PROJECT_ROOT / "alembic.ini"
ALEMBIC_SCRIPT_DIR = PROJECT_ROOT / "alembic"
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
INDEXING_MESSAGE = "Indexing..."
INDEXING_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Indexing...</title>
  <style>
    html, body { height: 100%; margin: 0; background: #0b0f17; color: #f8fafc; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { display: grid; place-items: center; }
  </style>
</head>
<body>Indexing...</body>
</html>"""
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _get_expected_migration_heads() -> set[str]:
    alembic_config = AlembicConfig(str(ALEMBIC_INI_FILE))
    alembic_config.set_main_option("script_location", str(ALEMBIC_SCRIPT_DIR))
    script = ScriptDirectory.from_config(alembic_config)
    return set(script.get_heads())


async def ensure_database_schema_current() -> None:
    expected_heads = _get_expected_migration_heads()

    async with engine.connect() as connection:
        current_heads = await connection.run_sync(
            lambda sync_connection: set(MigrationContext.configure(sync_connection).get_current_heads())
        )

    if current_heads != expected_heads:
        expected = ", ".join(sorted(expected_heads)) or "<none>"
        current = ", ".join(sorted(current_heads)) or "<none>"
        raise RuntimeError(
            "Database schema is out of date. "
            f"Expected Alembic head(s): {expected}. Current database revision(s): {current}. "
            "Run `alembic upgrade head` before starting the app."
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.indexing:
        logger.warning("Starting in indexing mode; database schema and background workers are paused")
        yield
        return

    await ensure_database_schema_current()
    await init_redis()
    await start_pubsub_listener()
    await start_sync_bridge_worker()
    await start_recommendation_worker()
    yield
    await stop_recommendation_worker()
    await stop_sync_bridge_worker()
    await stop_pubsub_listener()
    await close_redis()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

cors_origins = settings.get_cors_origins()
allow_all_origins = "*" in cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all_origins else cors_origins,
    allow_credentials=not allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount(settings.media_url_prefix, StaticFiles(directory=MEDIA_DIR), name="media")


def _is_api_path(path: str) -> bool:
    return path.startswith(settings.api_v1_prefix) or path.startswith(EDGE_API_V1_PREFIX) or path.startswith("/internal/sync")


def _is_allowed_indexing_asset(path: str) -> bool:
    return (
        path.startswith("/static/")
        or path.startswith(f"{settings.media_url_prefix}/")
        or path in {"/health", "/favicon.ico"}
    )


@app.middleware("http")
async def indexing_mode_middleware(request: Request, call_next):
    if not settings.indexing:
        return await call_next(request)

    path = request.url.path
    if _is_allowed_indexing_asset(path):
        return await call_next(request)

    if _is_api_path(path):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=error_response(code="INDEXING_MODE", message=INDEXING_MESSAGE),
        )

    if request.method in {"GET", "HEAD"}:
        return HTMLResponse(INDEXING_HTML, status_code=status.HTTP_200_OK)

    if request.method in WRITE_METHODS:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=error_response(code="INDEXING_MODE", message=INDEXING_MESSAGE),
        )

    return await call_next(request)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), payment=(), usb=()"
    response.headers.setdefault(
        "Content-Security-Policy",
        (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: https:; "
            "media-src 'self' https: blob:; "
            "connect-src 'self' https: wss:; "
            "frame-src 'self' https:; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        ),
    )
    media_prefix = f"{settings.media_url_prefix}/"
    if request.url.path == settings.media_url_prefix or request.url.path.startswith(media_prefix):
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response

EDGE_API_V1_PREFIX = f"/edge{settings.api_v1_prefix}"
BROWSER_API_ROUTERS = (
    auth.router,
    runtime.router,
    users.router,
    servers.router,
    channels.router,
    messages.router,
    uploads.router,
    dms.router,
    webhooks.router,
    workspaces.router,
    admin.router,
)

for router in BROWSER_API_ROUTERS:
    app.include_router(router, prefix=settings.api_v1_prefix)
    app.include_router(router, prefix=EDGE_API_V1_PREFIX)
app.include_router(sync.router)


@app.websocket("/ws")
async def websocket_route(websocket: WebSocket) -> None:
    await websocket_endpoint(websocket)


@app.websocket("/edge/ws")
async def edge_websocket_route(websocket: WebSocket) -> None:
    await websocket_endpoint(websocket)


@app.get("/health")
async def health_check() -> JSONResponse:
    if settings.indexing:
        payload = {"ok": True, "mode": "indexing", "message": INDEXING_MESSAGE}
    else:
        payload = success_response({"status": "ok"})
    return JSONResponse(
        payload,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
        },
    )


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="index.html not found")
    return FileResponse(INDEX_FILE, headers={"Cache-Control": "no-store"})


@app.get("/invite/{code}", include_in_schema=False)
async def serve_invite_index(code: str) -> FileResponse:
    _ = code
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="index.html not found")
    return FileResponse(INDEX_FILE, headers={"Cache-Control": "no-store"})


@app.get("/edge", include_in_schema=False)
@app.get("/edge/", include_in_schema=False)
@app.get("/edge/{path:path}", include_in_schema=False)
async def serve_edge_index(path: str = "") -> FileResponse:
    _ = path
    if not settings.edge_mode_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Edge Mode is not enabled")
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="index.html not found")
    return FileResponse(INDEX_FILE, headers={"Cache-Control": "no-store"})


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
async def serve_admin() -> FileResponse:
    if not ADMIN_FILE.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="admin.html not found")
    return FileResponse(ADMIN_FILE)


@app.get("/landing", include_in_schema=False)
@app.get("/landing/", include_in_schema=False)
async def serve_landing() -> FileResponse:
    if not LANDING_FILE.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="landing/index.html not found")
    return FileResponse(LANDING_FILE)


@app.get("/landing/mirror", include_in_schema=False)
@app.get("/landing/mirror/", include_in_schema=False)
@app.get("/landing/mirror/{path:path}", include_in_schema=False)
async def serve_landing_mirror(path: str = "") -> FileResponse:
    _ = path
    if not LANDING_FILE.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="landing/index.html not found")
    return FileResponse(LANDING_FILE)


@app.get("/changelog.md", include_in_schema=False)
async def serve_changelog() -> FileResponse:
    if not CHANGELOG_FILE.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="changelog.md not found")
    return FileResponse(CHANGELOG_FILE)


@app.get("/wyvern_logo_transparent.png", include_in_schema=False)
async def serve_root_logo() -> FileResponse:
    for logo_file in (ROOT_LOGO_FILE, STATIC_LOGO_FILE):
        if logo_file.exists():
            return FileResponse(logo_file)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="wyvern_logo_transparent.png not found")


@app.get("/wyvern_logo.png", include_in_schema=False)
async def serve_root_full_logo() -> FileResponse:
    for logo_file in (ROOT_FULL_LOGO_FILE, STATIC_LOGO_FILE):
        if logo_file.exists():
            return FileResponse(logo_file)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="wyvern_logo.png not found")


def _get_proxy_request_headers(request: Request) -> dict[str, str]:
    excluded = HOP_BY_HOP_HEADERS | {"host", "content-length", "authorization", "cookie", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"}
    return {key: value for key, value in request.headers.items() if key.lower() not in excluded}


def _build_mirror_target_url(base_url: str, path: str = "", query: str = "") -> str:
    parsed = urlsplit(base_url)
    base_path = parsed.path.rstrip("/")
    normalized_path = path.lstrip("/")

    if normalized_path:
        target_path = f"{base_path}/{normalized_path}" if base_path else f"/{normalized_path}"
    else:
        target_path = base_path or "/"

    return urlunsplit((parsed.scheme, parsed.netloc, target_path, query, ""))


def _rewrite_location_header(value: str, base_url: str, current_target_url: str) -> str:
    parsed_base = urlsplit(base_url)
    resolved_value = urljoin(current_target_url, value)
    parsed_value = urlsplit(resolved_value)

    same_origin = parsed_value.scheme == parsed_base.scheme and parsed_value.netloc == parsed_base.netloc
    if not same_origin:
        return value

    location_path = parsed_value.path or "/"
    base_path = parsed_base.path.rstrip("/")
    if base_path and location_path.startswith(f"{base_path}/"):
        location_path = location_path[len(base_path):]
    elif base_path and location_path == base_path:
        location_path = "/"

    location_path = location_path if location_path.startswith("/") else f"/{location_path}"
    return urlunsplit(("", "", f"/mirror{location_path}", parsed_value.query, parsed_value.fragment))


def _get_proxy_response_headers(response: httpx.Response, base_url: str, current_target_url: str) -> list[tuple[str, str]]:
    excluded = HOP_BY_HOP_HEADERS | {"content-length"}
    headers: list[tuple[str, str]] = []
    for key, value in response.headers.multi_items():
        lowered = key.lower()
        if lowered in excluded:
            continue
        if lowered == "location":
            value = _rewrite_location_header(value, base_url, current_target_url)
        headers.append((key, value))
    return headers


@app.api_route("/mirror", methods=["GET", "OPTIONS", "HEAD"], include_in_schema=False)
@app.api_route("/mirror/", methods=["GET", "OPTIONS", "HEAD"], include_in_schema=False)
@app.api_route("/mirror/{path:path}", methods=["GET", "OPTIONS", "HEAD"], include_in_schema=False)
async def mirror_request(request: Request, path: str = "") -> Response:
    if not settings.mirror_target_url:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=error_response(
                code="MIRROR_DISABLED",
                message="Mirror target is not configured. Set MIRROR_TARGET_URL in your environment.",
            ),
        )

    normalized_path = path.lstrip("/")
    target_url = _build_mirror_target_url(settings.mirror_target_url, normalized_path, request.url.query)

    request_body = await request.body()
    request_headers = _get_proxy_request_headers(request)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=False) as client:
            upstream = await client.request(
                method=request.method,
                url=target_url,
                headers=request_headers,
                content=request_body,
            )
    except httpx.RequestError as exc:
        logger.warning("Mirror upstream request failed for %s: %s", target_url, exc)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=error_response(
                code="MIRROR_UPSTREAM_UNREACHABLE",
                message="Failed to reach mirror upstream",
            ),
        )

    content_type = upstream.headers.get("content-type", "").lower()
    if request.method in {"GET", "HEAD"} and "text/html" in content_type:
        logger.warning("Blocked proxied HTML document from mirror target %s", target_url)
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=error_response(
                code="MIRROR_HTML_BLOCKED",
                message="HTML document proxying is disabled through the mirror endpoint",
            ),
        )

    proxied_response = Response(
        content=upstream.content,
        status_code=upstream.status_code,
    )
    for key, value in _get_proxy_response_headers(upstream, settings.mirror_target_url, target_url):
        proxied_response.headers.append(key, value)
    return proxied_response


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(code="HTTP_ERROR", message=str(exc.detail)),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error_response(code="VALIDATION_ERROR", message="Invalid request", details=exc.errors()),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled application exception", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_response(code="INTERNAL_ERROR", message="Unexpected server error"),
    )

# Run
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
