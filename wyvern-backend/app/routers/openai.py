from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import get_settings
from app.services.api_tokens import get_current_api_token


router = APIRouter(prefix="/openai/v1", tags=["openai"], include_in_schema=False)
settings = get_settings()

DISABLED_GATEWAY_MESSAGE = "Wyvern's built-in AI gateway has moved to Wyv. Use the Wyv host with /openai/v1 instead."


def _openai_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
    }


def _disabled_gateway_response() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_410_GONE,
        content={"success": False, "data": None, "error": {"code": "GATEWAY_MOVED", "message": DISABLED_GATEWAY_MESSAGE, "details": None}},
        headers=_openai_headers(),
    )


@router.get("/models")
async def list_models(_: Any = Depends(get_current_api_token)) -> dict[str, Any]:
    return _disabled_gateway_response()


@router.post("/chat/completions")
async def create_chat_completion(
    payload: dict[str, Any],
    _: Any = Depends(get_current_api_token),
):
    return _disabled_gateway_response()


@router.post("/responses")
async def create_response(
    payload: dict[str, Any],
    _: Any = Depends(get_current_api_token),
):
    return _disabled_gateway_response()
