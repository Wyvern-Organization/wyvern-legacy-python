from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.schemas.auth import LegalAcceptanceRequest
from app.services.legal import (
    apply_current_legal_acceptance,
    legal_metadata,
    load_legal_document,
    render_legal_page_html,
    user_requires_legal_reacceptance,
    validate_legal_acceptance_payload,
)
from app.utils.dependencies import get_current_user
from app.utils.responses import success_response


api_router = APIRouter(prefix="/legal", tags=["legal"])
public_router = APIRouter(tags=["legal-public"])


def _request_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _serialize_legal_acceptance(user: User) -> dict[str, object]:
    return {
        "accepted_terms_version": user.accepted_terms_version,
        "accepted_privacy_version": user.accepted_privacy_version,
        "legal_accepted_at": user.legal_accepted_at.isoformat() if user.legal_accepted_at else None,
        "legal_reaccept_required": user_requires_legal_reacceptance(user),
    }


@api_router.get("/current")
async def current_legal(request: Request) -> dict:
    return success_response(legal_metadata(_request_base_url(request)))


@api_router.post("/accept")
async def accept_current_legal(
    payload: LegalAcceptanceRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    try:
        validate_legal_acceptance_payload(
            accepted_legal=payload.accepted_legal,
            terms_version=payload.terms_version,
            privacy_version=payload.privacy_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    apply_current_legal_acceptance(current_user)
    await db.commit()
    return success_response(
        {
            **_serialize_legal_acceptance(current_user),
            "legal": legal_metadata(_request_base_url(request)),
            "message": "Legal acceptance updated.",
        }
    )


@public_router.get("/legal/{slug}", include_in_schema=False)
async def legal_document_page(slug: str, request: Request):
    try:
        document = load_legal_document(slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Legal document not found") from exc
    return HTMLResponse(render_legal_page_html(document, base_url=_request_base_url(request)))
