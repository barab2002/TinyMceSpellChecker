"""
Organisational dictionary endpoints:
  GET  /dictionary         — list all words in the organisational dictionary
  POST /dictionary/suggest — forward a word + context to the external approval service
  POST /dictionary/approve — callback used by the external approval service to add an
                              approved word to the organisational dictionary
"""
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..limiter import limiter
from ..models.schemas import (
    ApproveWordRequest,
    ApproveWordResponse,
    DictionaryResponse,
    SuggestWordRequest,
    SuggestWordResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "",
    response_model=DictionaryResponse,
    summary="List all words in the organisational dictionary",
)
@limiter.limit("200/minute")
async def list_words(request: Request) -> DictionaryResponse:
    dict_service = request.app.state.dict_service
    words = dict_service.list_words()
    return DictionaryResponse(words=words, count=len(words))


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


@router.post(
    "/approve",
    response_model=ApproveWordResponse,
    summary="Add an approved word to the organisational dictionary",
    description=(
        "Callback for the external approval service: once a reviewer approves a word "
        "suggested via `/dictionary/suggest`, the approval service calls this endpoint "
        "to add it to the organisational dictionary."
    ),
)
@limiter.limit("200/minute")
async def approve_word(request: Request, body: ApproveWordRequest) -> ApproveWordResponse:
    dict_service = request.app.state.dict_service
    try:
        added = dict_service.add(body.word)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return ApproveWordResponse(added=added)
