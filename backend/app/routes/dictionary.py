"""
Organisational dictionary endpoints:
  GET    /dictionary         — search/list words in the organisational dictionary (paginated)
  DELETE /dictionary/{word}  — remove a word from the organisational dictionary
  POST   /dictionary/suggest — forward a word + context to the external approval service
  POST   /dictionary/approve — callback used by the external approval service to add an
                                approved word to the organisational dictionary
"""
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from ..config import settings
from ..limiter import limiter
from ..models.schemas import (
    ApproveWordRequest,
    ApproveWordResponse,
    DictionaryResponse,
    RemoveWordResponse,
    SuggestWordRequest,
    SuggestWordResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "",
    response_model=DictionaryResponse,
    summary="Search/list words in the organisational dictionary",
)
@limiter.limit("200/minute")
async def list_words(
    request: Request,
    q: Optional[str] = Query(default=None, description="Case-insensitive substring filter."),
    limit: int = Query(default=50, ge=1, le=200, description="Page size."),
    offset: int = Query(default=0, ge=0, description="Number of matching words to skip."),
) -> DictionaryResponse:
    dict_service = request.app.state.dict_service
    words, total = dict_service.search(q, limit, offset)
    return DictionaryResponse(words=words, count=total, limit=limit, offset=offset)


@router.delete(
    "/{word}",
    response_model=RemoveWordResponse,
    summary="Remove a word from the organisational dictionary",
)
@limiter.limit("200/minute")
async def remove_word(request: Request, word: str) -> RemoveWordResponse:
    dict_service = request.app.state.dict_service
    try:
        removed = dict_service.remove(word)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return RemoveWordResponse(removed=removed)


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
