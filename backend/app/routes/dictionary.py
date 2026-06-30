"""
Organisational dictionary endpoints:
  POST /dictionary/suggest — forward a word + context to the external approval service
"""
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..limiter import limiter
from ..models.schemas import SuggestWordRequest, SuggestWordResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/suggest",
    response_model=SuggestWordResponse,
    summary="Suggest a word for the organisational dictionary",
    description=(
        "Forwards `{word, context}` to the external approval service configured via "
        "`APPROVEIT_URL`. The TinyMCE plugin calls this endpoint when the user clicks "
        "**'הצע למילון'**."
    ),
)
@limiter.limit("200/minute")
async def suggest_word(request: Request, body: SuggestWordRequest) -> SuggestWordResponse:
    if not settings.approveit_url:
        raise HTTPException(status_code=500, detail="APPROVEIT_URL is not configured")

    payload = {"word": body.word}
    if body.context:
        payload["context"] = body.context

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(settings.approveit_url, json=payload)
    except httpx.HTTPError as exc:
        logger.error("Failed to reach approval service: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to reach approval service")

    if not resp.is_success:
        logger.error("Approval service returned %d for word %r", resp.status_code, body.word)
        raise HTTPException(
            status_code=502,
            detail=f"Approval service returned {resp.status_code}",
        )

    return SuggestWordResponse(status="ok")
