"""
Organisational dictionary endpoints:
  GET  /dictionary        — list all custom words
  POST /dictionary/add    — add a word (idempotent)
  POST /dictionary/remove — remove a word
"""
import logging

from fastapi import APIRouter, HTTPException, Request

from ..models.schemas import DictionaryResponse, DictionaryWord

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "",
    response_model=DictionaryResponse,
    summary="List all custom dictionary words",
    description=(
        "Returns every word in the organisational dictionary sorted alphabetically. "
        "These words are **always accepted** during spell-check regardless of Hunspell's verdict."
    ),
)
async def list_dictionary(request: Request) -> DictionaryResponse:
    svc   = request.app.state.dict_service
    words = svc.list_words()
    return DictionaryResponse(words=words, count=len(words))


@router.post(
    "/add",
    response_model=DictionaryResponse,
    summary="Add a word to the custom dictionary",
    description=(
        "Adds a word to the organisational dictionary. "
        "Idempotent — if the word already exists the request succeeds silently. "
        "The TinyMCE plugin calls this endpoint when the user clicks **'הוסף למילון'**."
    ),
)
async def add_word(body: DictionaryWord, request: Request) -> DictionaryResponse:
    svc = request.app.state.dict_service
    try:
        svc.add(body.word)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    words = svc.list_words()
    return DictionaryResponse(words=words, count=len(words))


@router.post(
    "/remove",
    response_model=DictionaryResponse,
    summary="Remove a word from the custom dictionary",
    description="Removes a word from the organisational dictionary. Returns 404 if not found.",
)
async def remove_word(body: DictionaryWord, request: Request) -> DictionaryResponse:
    svc     = request.app.state.dict_service
    removed = svc.remove(body.word)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"Word not found in dictionary: {body.word!r}",
        )
    words = svc.list_words()
    return DictionaryResponse(words=words, count=len(words))
