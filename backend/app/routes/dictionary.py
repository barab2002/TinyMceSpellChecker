"""
Organisational dictionary endpoints.

  GET  /dictionary                       — list approved words (JSON) or admin GUI (browser)
  GET  /dictionary/words                 — paginated word list for admin GUI AJAX
  GET  /dictionary/search                — autocomplete search
  GET  /dictionary/export                — download as CSV
  POST /dictionary/import                — bulk-import from CSV / text
  POST /dictionary/add                   — add a word directly (admin, bypasses approval)
  POST /dictionary/remove                — remove a word
  POST /dictionary/suggest               — suggest a word for approval (TinyMCE plugin)
  GET  /dictionary/pending               — list pending / approved / dismissed words
  POST /dictionary/pending/{id}/approve  — approve a pending word
  POST /dictionary/pending/{id}/dismiss  — dismiss a pending word
"""
import csv
import io
import logging

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates

from ..limiter import limiter
from ..models.schemas import (
    DictionaryImportResponse,
    DictionaryPageResponse,
    DictionaryResponse,
    DictionaryWord,
    DictionaryWordEntry,
    PendingListResponse,
    PendingWordEntry,
    SuggestWordRequest,
    SuggestWordResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="app/templates")


# ─── GUI page (content-negotiated) ───────────────────────────────────────────

@router.get("", include_in_schema=False)
@limiter.limit("200/minute")
async def dictionary_root(request: Request):
    accept = request.headers.get("accept", "application/json")
    if "text/html" in accept and "application/json" not in accept.split(",")[0]:
        svc = request.app.state.dict_service
        languages = getattr(request.app.state, "available_languages", [])
        return templates.TemplateResponse(
            "dictionary.html",
            {
                "request": request,
                "languages": languages,
                "using_mongo": svc.using_mongo(),
                "word_count": svc.count(),
            },
        )
    svc = request.app.state.dict_service
    words = svc.list_words()
    return DictionaryResponse(words=words, count=len(words))


# ─── Paginated word list (for GUI AJAX) ──────────────────────────────────────

@router.get(
    "/words",
    response_model=DictionaryPageResponse,
    summary="Paginated word list for the admin GUI",
    tags=["Dictionary"],
)
@limiter.limit("200/minute")
async def list_words_paginated(
    request: Request,
    language: str = Query(default="all"),
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> DictionaryPageResponse:
    mongo = getattr(request.app.state, "mongo_service", None)
    svc = request.app.state.dict_service

    if mongo and mongo.is_available():
        skip = (page - 1) * page_size
        raw_words, total = mongo.get_words_paginated(
            language=language if language != "all" else None,
            q=q or None,
            skip=skip,
            limit=page_size,
        )
        entries = [
            DictionaryWordEntry(word=w["word"], language=w["language"], added_at=w.get("added_at", ""))
            for w in raw_words
        ]
    else:
        all_words = svc.list_words()
        if q:
            all_words = [w for w in all_words if q.lower() in w.lower()]
        total = len(all_words)
        start = (page - 1) * page_size
        entries = [
            DictionaryWordEntry(word=w, language="all", added_at="")
            for w in all_words[start : start + page_size]
        ]

    return DictionaryPageResponse(words=entries, total=total, page=page, page_size=page_size)


# ─── Autocomplete search ─────────────────────────────────────────────────────

@router.get("/search", summary="Autocomplete search for dictionary words", tags=["Dictionary"])
@limiter.limit("300/minute")
async def search_words(
    request: Request,
    q: str = Query(..., min_length=1),
    language: str = Query(default="all"),
    limit: int = Query(default=10, ge=1, le=50),
):
    mongo = getattr(request.app.state, "mongo_service", None)
    svc = request.app.state.dict_service

    if mongo and mongo.is_available():
        results = mongo.search_words(q=q, language=language if language != "all" else None, limit=limit)
    else:
        all_words = svc.list_words()
        results = [w for w in all_words if q.lower() in w.lower()][:limit]

    return {"results": results, "query": q}


# ─── Export ──────────────────────────────────────────────────────────────────

@router.get("/export", summary="Export dictionary as a CSV file", tags=["Dictionary"], response_class=StreamingResponse)
@limiter.limit("30/minute")
async def export_dictionary(request: Request) -> StreamingResponse:
    svc = request.app.state.dict_service
    words = svc.list_words()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["word"])
    for word in words:
        writer.writerow([word])
    content = buf.getvalue()
    buf.close()
    return StreamingResponse(
        io.StringIO(content),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=org_dictionary.csv"},
    )


# ─── Import ──────────────────────────────────────────────────────────────────

@router.post(
    "/import",
    response_model=DictionaryImportResponse,
    summary="Import words from a CSV or plain-text file",
    description="Upload CSV (with 'word' header) or plain text (one word per line). BOM-safe.",
    tags=["Dictionary"],
)
@limiter.limit("10/minute")
async def import_dictionary(
    request: Request,
    file: UploadFile = File(...),
) -> DictionaryImportResponse:
    svc = request.app.state.dict_service
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")

    lines = text.splitlines()
    added = skipped = 0
    errors = []

    if lines and ("," in lines[0] or lines[0].strip().lower() == "word"):
        reader = csv.reader(lines)
        rows = list(reader)
        start = 1 if rows and rows[0] and rows[0][0].strip().lower() == "word" else 0
        candidates = [row[0].strip() for row in rows[start:] if row and row[0].strip()]
    else:
        candidates = [line.strip() for line in lines if line.strip()]

    for word in candidates:
        try:
            result = svc.add(word)
            if result:
                added += 1
            else:
                skipped += 1
        except ValueError as exc:
            errors.append({"word": word, "error": str(exc)})

    logger.info("Dictionary import: added=%d skipped=%d errors=%d", added, skipped, len(errors))
    return DictionaryImportResponse(added=added, skipped=skipped, errors=errors, total_words=svc.count())


# ─── Add (direct — admin) ────────────────────────────────────────────────────

@router.post(
    "/add",
    response_model=DictionaryResponse,
    summary="Add a word directly to the approved dictionary (admin)",
    description=(
        "Adds a word immediately — bypasses the approval queue. "
        "The TinyMCE plugin should use POST /dictionary/suggest instead."
    ),
    tags=["Dictionary"],
)
@limiter.limit("200/minute")
async def add_word(request: Request, body: DictionaryWord) -> DictionaryResponse:
    svc = request.app.state.dict_service
    try:
        svc.add(body.word, body.language)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    words = svc.list_words()
    return DictionaryResponse(words=words, count=len(words))


# ─── Remove ──────────────────────────────────────────────────────────────────

@router.post("/remove", response_model=DictionaryResponse, summary="Remove a word from the approved dictionary", tags=["Dictionary"])
@limiter.limit("200/minute")
async def remove_word(request: Request, body: DictionaryWord) -> DictionaryResponse:
    svc = request.app.state.dict_service
    removed = svc.remove(body.word)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Word not found: {body.word!r}")
    words = svc.list_words()
    return DictionaryResponse(words=words, count=len(words))


# ─── Suggest (plugin → approval queue) ──────────────────────────────────────

@router.post(
    "/suggest",
    response_model=SuggestWordResponse,
    summary="Suggest a word for approval (called by the TinyMCE plugin)",
    description=(
        "When a user clicks **'הוסף למילון'** in the editor, the plugin calls this endpoint. "
        "The word is placed in a pending-approval queue (visible at `/approvals`). "
        "An admin can then approve or dismiss it from the GUI. "
        "If MongoDB is not configured, the word is added directly — backwards-compatible fallback."
    ),
    tags=["Dictionary"],
)
@limiter.limit("60/minute")
async def suggest_word(request: Request, body: SuggestWordRequest) -> SuggestWordResponse:
    mongo = getattr(request.app.state, "mongo_service", None)
    svc = request.app.state.dict_service

    if mongo and mongo.is_available():
        word_id = mongo.add_pending(word=body.word, language=body.language, context=body.context)
        logger.info("Word queued for approval: %r (lang=%s)", body.word, body.language)
        return SuggestWordResponse(status="queued", word=body.word, id=word_id)
    else:
        try:
            svc.add(body.word, body.language)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        logger.info("Word added directly (no MongoDB): %r", body.word)
        return SuggestWordResponse(status="added", word=body.word, id=None)


# ─── Pending list ────────────────────────────────────────────────────────────

@router.get("/pending", response_model=PendingListResponse, summary="List pending / approved / dismissed suggestions", tags=["Dictionary"])
@limiter.limit("200/minute")
async def list_pending(
    request: Request,
    status: str = Query(default="pending"),
    language: str = Query(default="all"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> PendingListResponse:
    mongo = getattr(request.app.state, "mongo_service", None)
    if not mongo or not mongo.is_available():
        return PendingListResponse(words=[], total=0, status_filter=status)

    skip = (page - 1) * page_size
    raw = mongo.get_pending(status=status, language=language if language != "all" else None, skip=skip, limit=page_size)
    total = mongo.pending_count(status)
    entries = [PendingWordEntry(**w) for w in raw]
    return PendingListResponse(words=entries, total=total, status_filter=status)


# ─── Approve ─────────────────────────────────────────────────────────────────

@router.post("/pending/{word_id}/approve", summary="Approve a pending word suggestion", tags=["Dictionary"])
@limiter.limit("200/minute")
async def approve_pending(request: Request, word_id: str):
    mongo = getattr(request.app.state, "mongo_service", None)
    if not mongo or not mongo.is_available():
        raise HTTPException(status_code=503, detail="MongoDB is not configured")

    approved = mongo.approve_pending(word_id)
    if not approved:
        raise HTTPException(status_code=404, detail="Pending entry not found or already reviewed")

    svc = request.app.state.dict_service
    svc.add_word_to_memory(approved["word"])
    logger.info("Approved word: %r", approved["word"])
    return {"status": "approved", "word": approved["word"]}


# ─── Dismiss ─────────────────────────────────────────────────────────────────

@router.post("/pending/{word_id}/dismiss", summary="Dismiss a pending word suggestion", tags=["Dictionary"])
@limiter.limit("200/minute")
async def dismiss_pending(request: Request, word_id: str):
    mongo = getattr(request.app.state, "mongo_service", None)
    if not mongo or not mongo.is_available():
        raise HTTPException(status_code=503, detail="MongoDB is not configured")

    ok = mongo.dismiss_pending(word_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Pending entry not found or already reviewed")

    logger.info("Dismissed pending word id=%s", word_id)
    return {"status": "dismissed"}
