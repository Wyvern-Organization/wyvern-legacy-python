from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import admin, auth, channels, dms, messages, servers, uploads, users
from app.services.pubsub import start_pubsub_listener, stop_pubsub_listener
from app.services.redis_client import close_redis, init_redis
from app.utils.responses import error_response, success_response
from app.websocket.handlers import websocket_endpoint


settings = get_settings()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_FILE = PROJECT_ROOT / "index.html"
ADMIN_FILE = PROJECT_ROOT / "admin.html"
LANDING_FILE = PROJECT_ROOT.parent / "landing" / "index.html"
CHANGELOG_FILE = PROJECT_ROOT / "changelog.md"
ROOT_LOGO_FILE = PROJECT_ROOT.parent / "wyvern_logo_transparent.png"
ROOT_FULL_LOGO_FILE = PROJECT_ROOT.parent / "wyvern_logo.png"
STATIC_DIR = Path(__file__).resolve().parent / "static"
MEDIA_DIR = settings.resolve_media_dir(PROJECT_ROOT)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_redis()
    await start_pubsub_listener()
    yield
    await stop_pubsub_listener()
    await close_redis()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount(settings.media_url_prefix, StaticFiles(directory=MEDIA_DIR), name="media")

app.include_router(auth.router, prefix=settings.api_v1_prefix)
app.include_router(users.router, prefix=settings.api_v1_prefix)
app.include_router(servers.router, prefix=settings.api_v1_prefix)
app.include_router(channels.router, prefix=settings.api_v1_prefix)
app.include_router(messages.router, prefix=settings.api_v1_prefix)
app.include_router(uploads.router, prefix=settings.api_v1_prefix)
app.include_router(dms.router, prefix=settings.api_v1_prefix)
app.include_router(admin.router, prefix=settings.api_v1_prefix)


@app.websocket("/ws")
async def websocket_route(websocket: WebSocket) -> None:
    await websocket_endpoint(websocket)


@app.get("/health")
async def health_check() -> dict:
    return success_response({"status": "ok"})


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="index.html not found")
    return FileResponse(INDEX_FILE)


@app.get("/invite/{code}", include_in_schema=False)
async def serve_invite_index(code: str) -> FileResponse:
    _ = code
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="index.html not found")
    return FileResponse(INDEX_FILE)


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


@app.get("/changelog.md", include_in_schema=False)
async def serve_changelog() -> FileResponse:
    if not CHANGELOG_FILE.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="changelog.md not found")
    return FileResponse(CHANGELOG_FILE)


@app.get("/wyvern_logo_transparent.png", include_in_schema=False)
async def serve_root_logo() -> FileResponse:
    if not ROOT_LOGO_FILE.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="wyvern_logo_transparent.png not found")
    return FileResponse(ROOT_LOGO_FILE)


@app.get("/wyvern_logo.png", include_in_schema=False)
async def serve_root_full_logo() -> FileResponse:
    if not ROOT_FULL_LOGO_FILE.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="wyvern_logo.png not found")
    return FileResponse(ROOT_FULL_LOGO_FILE)


def _get_proxy_request_headers(request: Request) -> dict[str, str]:
    excluded = HOP_BY_HOP_HEADERS | {"host", "content-length"}
    return {key: value for key, value in request.headers.items() if key.lower() not in excluded}


def _get_proxy_response_headers(response: httpx.Response) -> list[tuple[str, str]]:
    excluded = HOP_BY_HOP_HEADERS | {"content-length"}
    return [(key, value) for key, value in response.headers.multi_items() if key.lower() not in excluded]


@app.api_route("/mirror", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"], include_in_schema=False)
@app.api_route("/mirror/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"], include_in_schema=False)
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
    target_url = settings.mirror_target_url
    if normalized_path:
        target_url = f"{target_url}/{normalized_path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

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
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=error_response(
                code="MIRROR_UPSTREAM_UNREACHABLE",
                message="Failed to reach mirror upstream",
                details=str(exc),
            ),
        )

    proxied_response = Response(
        content=upstream.content,
        status_code=upstream.status_code,
    )
    for key, value in _get_proxy_response_headers(upstream):
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
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_response(code="INTERNAL_ERROR", message="Unexpected server error", details=str(exc)),
    )

# Run
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)