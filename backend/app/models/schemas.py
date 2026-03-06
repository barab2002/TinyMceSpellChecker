"""
Pydantic schemas for API request / response validation.
All fields carry openapi_examples so the Swagger UI (/docs) shows
realistic, runnable examples for every endpoint.
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
        description=(
            "Plain text to spell-check.  "
            "Do NOT send raw HTML — the TinyMCE plugin strips HTML before sending."
        ),
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
                "summary": "Text containing org-dictionary words (should not be flagged)",
                "value": "המערכת שלנו מבוססת על Salesforce ו-ZoomInfo לניהול לקוחות.",
            },
        },
    )
    language: str = Field(
        default="he-IL",
        description="BCP-47 language tag.  Currently supported: he-IL (Hebrew).",
        openapi_examples={
            "hebrew": {"summary": "Hebrew (Israel)", "value": "he-IL"},
        },
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
        # Whitelist prevents path-traversal attacks on dictionary file names
        allowed = {"he-IL", "he_IL", "en-US", "en_US", "en-GB", "en_GB"}
        if v not in allowed:
            raise ValueError(
                f"Unsupported language: {v!r}. "
                f"Supported: {sorted(allowed)}"
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
    word: str = Field(
        ...,
        description="The misspelled word as it appeared in the input text.",
        examples=["שלומ"],
    )
    start: int = Field(
        ...,
        description="Start character offset in the plain text that was submitted.",
        examples=[0],
    )
    end: int = Field(
        ...,
        description="Exclusive end character offset.",
        examples=[4],
    )
    suggestions: List[str] = Field(
        ...,
        description="Ordered list of spelling suggestions (best first).",
        examples=[["שלום", "שלמו"]],
    )
    source: str = Field(
        default="hunspell",
        description="The spell-check engine that flagged this word.",
        examples=["hunspell"],
    )

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
    misspellings: List[Misspelling] = Field(
        ...,
        description=(
            "List of misspelled words with their offsets in the submitted plain text. "
            "Empty list means no errors found."
        ),
    )
    total: int = Field(..., description="Total number of misspellings found.", ge=0)

    model_config = {
        "json_schema_extra": {
            "example": {
                "language": "he-IL",
                "misspellings": [
                    {
                        "word": "שלומ",
                        "start": 0,
                        "end": 4,
                        "suggestions": ["שלום", "שלמו"],
                        "source": "hunspell",
                    },
                    {
                        "word": "אורחנוזציה",
                        "start": 33,
                        "end": 43,
                        "suggestions": [],
                        "source": "hunspell",
                    },
                ],
                "total": 2,
            }
        }
    }


# ─── Dictionary schemas ───────────────────────────────────────────────────────

class DictionaryWord(BaseModel):
    word: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="The word to add or remove from the organisational dictionary.",
        examples=["Salesforce", "ZoomInfo", "MyProduct"],
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
            "example": {"word": "Salesforce"}
        }
    }


class DictionaryResponse(BaseModel):
    words: List[str] = Field(
        ...,
        description="All words currently in the organisational dictionary, sorted alphabetically.",
    )
    count: int = Field(..., description="Total number of words in the dictionary.", ge=0)

    model_config = {
        "json_schema_extra": {
            "example": {
                "words": ["Base44", "Clari", "HubSpot", "Salesforce", "ZoomInfo"],
                "count": 5,
            }
        }
    }


# ─── Health ───────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = Field(..., description="'ok' when the service is healthy.", examples=["ok"])
    hunspell_available: bool = Field(
        ...,
        description="True when the spylls dictionary is loaded and ready.",
    )
    language: str = Field(
        ...,
        description="Active spell-check language (Hunspell dictionary key).",
        examples=["he_IL"],
    )
    custom_dict_words: int = Field(
        ...,
        description="Number of words in the organisational dictionary.",
        ge=0,
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "ok",
                "hunspell_available": True,
                "language": "he_IL",
                "custom_dict_words": 18,
            }
        }
    }
