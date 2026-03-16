"""
POST /spell/check — main spell-check endpoint.

The client sends *plain text* (not raw HTML).
The plugin is responsible for extracting plain text from the editor
and mapping positions back to the DOM after receiving results.

The CPU-bound spylls work is offloaded to a thread-pool executor so the
async event loop stays free to handle other requests concurrently.
"""
import asyncio
import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import ORJSONResponse

from ..limiter import limiter
from ..models.schemas import Misspelling, SpellCheckRequest, SpellCheckResponse

router = APIRouter()
logger = logging.getLogger(__name__)

# Shared thread pool — keeps threads alive across requests (avoids spawn overhead)
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="spellcheck")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _maybe_strip_html(text: str) -> str:
    stripped = _HTML_TAG_RE.sub(" ", text)
    return html.unescape(stripped)


@router.post(
    "/check",
    response_model=SpellCheckResponse,
    summary="Check text for spelling errors (rate-limited: 120/min per IP)",
    description="""
Submit **plain text** (not raw HTML) and receive a list of misspelled Hebrew words
with character offsets and spelling suggestions.

### How offsets work

`start` and `end` are character offsets in the `text` you submitted.

```
text = "שלומ לכולם"
              ↑    ↑
         start=0  end=4  →  word="שלומ"
```

The TinyMCE plugin uses these offsets to locate and highlight the exact text nodes
in the editor without ever touching HTML markup.

### Custom dictionary priority

Words in the organisational dictionary (`POST /dictionary/add`) are **always accepted**,
even if Hunspell flags them. Add product names, acronyms, and customer names there.
""",
)
@limiter.limit("120/minute")
async def check_spelling(request: Request, request_body: SpellCheckRequest):
    # Select the spell service for the requested language.
    # Falls back to the default (Hebrew) service if the language is not loaded.
    lang_key = request_body.language.replace("-", "_")
    spell_service = request.app.state.spell_services.get(
        lang_key, request.app.state.spell_service
    )
    dict_service = request.app.state.dict_service

    from ..config import settings
    if len(request_body.text) > settings.max_text_length:
        raise HTTPException(
            status_code=413,
            detail=f"Text too long. Maximum {settings.max_text_length} characters.",
        )

    plain_text = _maybe_strip_html(request_body.text)

    logger.info(
        "spell/check lang=%s text_len=%d cache=%s",
        request_body.language,
        len(plain_text),
        spell_service.cache_stats(),
    )

    # Run CPU-bound spylls work in a thread pool so the event loop stays free
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(
        _EXECUTOR,
        lambda: spell_service.check_text(
            text=plain_text,
            custom_dict=dict_service,
            max_suggestions=request_body.options.maxSuggestions,
            include_suggestions=request_body.options.includeSuggestions,
        ),
    )

    misspellings = [
        Misspelling(
            word=m["word"],
            start=m["start"],
            end=m["end"],
            suggestions=m["suggestions"],
            source=m["source"],
        )
        for m in raw
    ]

    return SpellCheckResponse(
        language=request_body.language,
        misspellings=misspellings,
        total=len(misspellings),
    )
