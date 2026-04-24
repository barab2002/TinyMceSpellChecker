"""
Pydantic schemas for API request / response validation.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
import re


# ─── Spell-check request ──────────────────────────────────────────────────────

class SpellCheckOptions(BaseModel):
    includeSuggestions: bool = Field(
        default=True,
        description="Return spelling suggestions for each misspelled word.",
    )
    maxSuggestions: int = Field(
        default=5, ge=1, le=20,
        description="Maximum number of suggestions to return per misspelled word (1–20).",
    )

    model_config = {
        "json_schema_extra": {
            "example": {"includeSuggestions": True, "maxSuggestions": 5}
        }
    }


class SpellCheckRequest(BaseModel):
    text: str = Field(
        ...,
        description="Plain text to spell-check. Do NOT send raw HTML.",
        min_length=1,
        openapi_examples={
            "basic_hebrew": {
                "summary": "Hebrew text with intentional errors",
                "value": "שלומ לכולם. אנחנו עוסקים בפיתוח תוכנה ובניהול אורחנוזציה גדולה.",
            },
            "clean_hebrew": {
                "summary": "Correct Hebrew text (no errors expected)",
                "value": "שלום לכולם. אנחנו עוסקים בפיתוח תוכנה.",
            },
            "with_org_words": {
                "summary": "Text containing org-dictionary words",
                "value": "המערכת שלנו מבוססת על Salesforce ו-ZoomInfo לניהול לקוחות.",
            },
        },
    )
    language: str = Field(
        default="he-IL",
        description="BCP-47 language tag. Supported: he-IL, en-US, en-GB, ar-SA.",
    )
    documentId: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Optional document identifier for logging/correlation.",
    )
    options: SpellCheckOptions = Field(
        default_factory=SpellCheckOptions,
        description="Spell-check options.",
    )

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text must not be empty")
        return v

    @field_validator("language")
    @classmethod
    def language_allowed(cls, v: str) -> str:
        allowed = {
            "he-IL", "he_IL",
            "en-US", "en_US", "en-GB", "en_GB",
            "ar-SA", "ar_SA", "ar",
        }
        if v not in allowed:
            raise ValueError(
                f"Unsupported language: {v!r}. Supported: {sorted(allowed)}"
            )
        return v

    model_config = {
        "json_schema_extra": {
            "example": {
                "text": "שלומ לכולם. אנחנו עוסקים בניהול אורחנוזציה גדולה.",
                "language": "he-IL",
                "options": {"includeSuggestions": True, "maxSuggestions": 5},
            }
        }
    }


# ─── Spell-check response ─────────────────────────────────────────────────────

class Misspelling(BaseModel):
    word: str = Field(..., description="The misspelled word as it appeared in the input text.")
    start: int = Field(..., description="Start character offset in the submitted plain text.")
    end: int = Field(..., description="Exclusive end character offset.")
    suggestions: List[str] = Field(..., description="Ordered list of spelling suggestions (best first).")
    source: str = Field(default="hunspell", description="The spell-check engine that flagged this word.")

    model_config = {
        "json_schema_extra": {
            "example": {
                "word": "שלומ",
                "start": 0,
                "end": 4,
                "suggestions": ["שלום", "שלמו", "לשמו"],
                "source": "hunspell",
            }
        }
    }


class SpellCheckResponse(BaseModel):
    language: str = Field(..., description="Language tag echoed from the request.")
    misspellings: List[Misspelling] = Field(..., description="List of misspelled words with offsets.")
    total: int = Field(..., description="Total number of misspellings found.", ge=0)

    model_config = {
        "json_schema_extra": {
            "example": {
                "language": "he-IL",
                "misspellings": [
                    {"word": "שלומ", "start": 0, "end": 4, "suggestions": ["שלום", "שלמו"], "source": "hunspell"},
                ],
                "total": 1,
            }
        }
    }


# ─── Dictionary schemas ───────────────────────────────────────────────────────

class DictionaryWord(BaseModel):
    word: str = Field(
        ..., min_length=1, max_length=200,
        description="The word to add or remove from the organisational dictionary.",
        examples=["Salesforce", "ZoomInfo", "MyProduct"],
    )
    language: str = Field(
        default="all",
        description="Language scope. 'all' means accepted for every language.",
        examples=["all", "he-IL", "en-US"],
    )

    @field_validator("word")
    @classmethod
    def no_control_chars(cls, v: str) -> str:
        v = v.strip()
        if re.search(r"[\x00-\x1f\x7f]", v):
            raise ValueError("Word contains invalid control characters")
        return v

    model_config = {
        "json_schema_extra": {"example": {"word": "Salesforce", "language": "all"}}
    }


class DictionaryWordEntry(BaseModel):
    """A single entry returned in the paginated dictionary list."""
    word: str
    language: str
    added_at: str = ""


class DictionaryResponse(BaseModel):
    words: List[str] = Field(..., description="All words in the organisational dictionary.")
    count: int = Field(..., description="Total number of words.", ge=0)

    model_config = {
        "json_schema_extra": {
            "example": {
                "words": ["Base44", "Clari", "HubSpot", "Salesforce", "ZoomInfo"],
                "count": 5,
            }
        }
    }


class DictionaryPageResponse(BaseModel):
    """Paginated dictionary response for the admin GUI."""
    words: List[DictionaryWordEntry]
    total: int
    page: int
    page_size: int


class DictionaryImportResponse(BaseModel):
    added: int = Field(..., description="Number of new words added.", ge=0)
    skipped: int = Field(..., description="Words already present (no-ops).", ge=0)
    errors: List[dict] = Field(default_factory=list, description="Words that failed validation.")
    total_words: int = Field(..., description="Total words in dictionary after import.", ge=0)

    model_config = {
        "json_schema_extra": {
            "example": {"added": 12, "skipped": 3, "errors": [], "total_words": 25}
        }
    }


# ─── Pending / Approval schemas ───────────────────────────────────────────────

class SuggestWordRequest(BaseModel):
    """Sent by the TinyMCE plugin when a user clicks 'הוסף למילון'."""
    word: str = Field(..., min_length=1, max_length=200, description="Word to suggest for approval.")
    language: str = Field(default="all", description="Language the word was found in.")
    context: Optional[str] = Field(
        default=None, max_length=500,
        description="Optional surrounding text for the reviewer's context.",
    )

    @field_validator("word")
    @classmethod
    def no_control_chars(cls, v: str) -> str:
        v = v.strip()
        if re.search(r"[\x00-\x1f\x7f]", v):
            raise ValueError("Word contains invalid control characters")
        return v

    model_config = {
        "json_schema_extra": {
            "example": {"word": "MyProduct", "language": "he-IL", "context": None}
        }
    }


class SuggestWordResponse(BaseModel):
    status: str = Field(
        ...,
        description="'queued' when using MongoDB, 'added' when using JSON fallback.",
    )
    word: str
    id: Optional[str] = Field(default=None, description="MongoDB id of the pending entry.")

    model_config = {
        "json_schema_extra": {
            "example": {"status": "queued", "word": "MyProduct", "id": "664abc123"}
        }
    }


class PendingWordEntry(BaseModel):
    id: str
    word: str
    language: str
    submitted_at: str
    status: str
    context: Optional[str] = None
    reviewed_at: Optional[str] = None


class PendingListResponse(BaseModel):
    words: List[PendingWordEntry]
    total: int
    status_filter: str


# ─── Health ───────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = Field(..., description="'ok' when the service is healthy.")
    hunspell_available: bool
    language: str
    custom_dict_words: int
    storage_backend: str = Field(default="json", description="'mongodb' or 'json'")
    pending_approvals: int = Field(default=0, description="Words awaiting admin approval.")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "ok",
                "hunspell_available": True,
                "language": "he_IL",
                "custom_dict_words": 18,
                "storage_backend": "mongodb",
                "pending_approvals": 3,
            }
        }
    }
