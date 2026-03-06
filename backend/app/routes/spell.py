"""
POST /spell/check — main spell-check endpoint.

The client sends *plain text* (not raw HTML).
The plugin is responsible for extracting plain text from the editor
and mapping positions back to the DOM after receiving results.

This keeps the backend simple and avoids parsing arbitrary HTML on the server.
"""
import html
import logging
import re

from fastapi import APIRouter, HTTPException, Request

from ..models.schemas import Misspelling, SpellCheckRequest, SpellCheckResponse

router = APIRouter()
logger = logging.getLogger(__name__)

# Very basic HTML tag stripper — used only as a safety net if the client
# accidentally sends HTML; the plugin should send plain text.
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _maybe_strip_html(text: str) -> str:
    """Strip HTML tags and decode HTML entities from text as a safety measure."""
    stripped = _HTML_TAG_RE.sub(" ", text)
    return html.unescape(stripped)


@router.post(
    "/check",
    response_model=SpellCheckResponse,
    summary="Check text for spelling errors",
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
async def check_spelling(request_body: SpellCheckRequest, request: Request):
    spell_service = request.app.state.spell_service
    dict_service = request.app.state.dict_service

    # Enforce max length
    from ..config import settings
    if len(request_body.text) > settings.max_text_length:
        raise HTTPException(
            status_code=413,
            detail=f"Text too long. Maximum {settings.max_text_length} characters.",
        )

    # Normalise language tag
    lang_fs = request_body.language.replace("-", "_")

    # Safety: strip any accidental HTML markup
    plain_text = _maybe_strip_html(request_body.text)

    logger.info(
        "spell/check lang=%s doc=%s text_len=%d",
        lang_fs,
        request_body.documentId or "-",
        len(plain_text),
    )

    raw = spell_service.check_text(
        text=plain_text,
        custom_dict=dict_service,
        max_suggestions=request_body.options.maxSuggestions,
        include_suggestions=request_body.options.includeSuggestions,
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
