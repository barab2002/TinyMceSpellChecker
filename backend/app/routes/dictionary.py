"""
Dictionary management endpoints:
  GET  /dictionary          — list all custom words
  POST /dictionary/add      — add a word
  POST /dictionary/remove   — remove a word
"""
import logging

from fastapi import APIRouter, HTTPException, Request

from ..models.schemas import DictionaryResponse, DictionaryWord

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("", response_model=DictionaryResponse)
async def list_dictionary(request: Request):
    """Return all words currently in the organisational dictionary."""
    svc = request.app.state.dict_service
    words = svc.list_words()
    return DictionaryResponse(words=words, count=len(words))


@router.post("/add", response_model=DictionaryResponse)
async def add_word(body: DictionaryWord, request: Request):
    """Add a word to the organisational dictionary."""
    svc = request.app.state.dict_service
    try:
        added = svc.add(body.word)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not added:
        logger.debug("Word already in dictionary: %r", body.word)

    words = svc.list_words()
    return DictionaryResponse(words=words, count=len(words))


@router.post("/remove", response_model=DictionaryResponse)
async def remove_word(body: DictionaryWord, request: Request):
    """Remove a word from the organisational dictionary."""
    svc = request.app.state.dict_service
    removed = svc.remove(body.word)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"Word not found in dictionary: {body.word!r}",
        )
    words = svc.list_words()
    return DictionaryResponse(words=words, count=len(words))
