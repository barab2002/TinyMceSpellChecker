"""
Organisational dictionary endpoints:
  GET  /dictionary           — list all custom words
  GET  /dictionary/export    — download as CSV file
  POST /dictionary/import    — bulk-import from CSV or text file (one word per line)
  POST /dictionary/add       — add a word (idempotent)
  POST /dictionary/remove    — remove a word
"""
import csv
import io
import logging

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from ..limiter import limiter
from ..models.schemas import DictionaryImportResponse, DictionaryResponse, DictionaryWord

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
@limiter.limit("200/minute")
async def list_dictionary(request: Request) -> DictionaryResponse:
    svc   = request.app.state.dict_service
    words = svc.list_words()
    return DictionaryResponse(words=words, count=len(words))


@router.get(
    "/export",
    summary="Export dictionary as a CSV file",
    description=(
        "Downloads the entire organisational dictionary as a UTF-8 CSV file with a single "
        "`word` column. Import this file back with `POST /dictionary/import`."
    ),
    response_class=StreamingResponse,
)
@limiter.limit("30/minute")
async def export_dictionary(request: Request) -> StreamingResponse:
    svc   = request.app.state.dict_service
    words = svc.list_words()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["word"])          # header
    for word in words:
        writer.writerow([word])

    content = buf.getvalue()
    buf.close()

    return StreamingResponse(
        io.StringIO(content),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=org_dictionary.csv"},
    )


@router.post(
    "/import",
    response_model=DictionaryImportResponse,
    summary="Import words from a CSV or plain-text file",
    description="""
Upload a **CSV** (with a `word` header column) or a **plain text** file
(one word per line) to bulk-add words to the organisational dictionary.

- Duplicate words are silently skipped (counted in `skipped`).
- Words that fail validation are reported in `errors` but do not abort the import.
- BOM (UTF-8 with BOM from Excel) is handled automatically.
""",
)
@limiter.limit("10/minute")
async def import_dictionary(
    request: Request,
    file: UploadFile = File(..., description="CSV or text file — UTF-8 or UTF-8-BOM"),
) -> DictionaryImportResponse:
    svc = request.app.state.dict_service

    raw = await file.read()
    # Handle UTF-8 BOM (common when exported from Excel)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")

    lines = text.splitlines()
    added   = 0
    skipped = 0
    errors  = []

    # Detect CSV vs plain text by checking for a comma or a "word" header
    if lines and ("," in lines[0] or lines[0].strip().lower() == "word"):
        reader = csv.reader(lines)
        rows = list(reader)
        # Skip header row if it says "word"
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
    return DictionaryImportResponse(
        added=added,
        skipped=skipped,
        errors=errors,
        total_words=svc.count(),
    )


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
@limiter.limit("200/minute")
async def add_word(request: Request, body: DictionaryWord) -> DictionaryResponse:
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
@limiter.limit("200/minute")
async def remove_word(request: Request, body: DictionaryWord) -> DictionaryResponse:
    svc     = request.app.state.dict_service
    removed = svc.remove(body.word)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"Word not found in dictionary: {body.word!r}",
        )
    words = svc.list_words()
    return DictionaryResponse(words=words, count=len(words))
