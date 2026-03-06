"""
Pydantic schemas for API request/response validation.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
import re


class SpellCheckOptions(BaseModel):
    includeSuggestions: bool = True
    maxSuggestions: int = Field(default=5, ge=1, le=20)


class SpellCheckRequest(BaseModel):
    text: str = Field(..., description="Plain text or HTML content to spell-check")
    language: str = Field(default="he-IL", description="BCP-47 language tag, e.g. he-IL")
    documentId: Optional[str] = Field(default=None, max_length=128)
    options: SpellCheckOptions = SpellCheckOptions()

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text must not be empty")
        return v

    @field_validator("language")
    @classmethod
    def language_allowed(cls, v: str) -> str:
        # Only accept known language tags to prevent path traversal
        allowed = {"he-IL", "he_IL", "en-US", "en_US", "en-GB", "en_GB"}
        if v not in allowed:
            raise ValueError(f"Unsupported language: {v}")
        return v


class Misspelling(BaseModel):
    word: str
    start: int           # character offset in the plain-text that was sent
    end: int             # exclusive end offset
    suggestions: List[str]
    source: str = "hunspell"


class SpellCheckResponse(BaseModel):
    language: str
    misspellings: List[Misspelling]
    total: int


# --- Dictionary schemas ---

class DictionaryWord(BaseModel):
    word: str = Field(..., min_length=1, max_length=200)

    @field_validator("word")
    @classmethod
    def no_control_chars(cls, v: str) -> str:
        v = v.strip()
        if re.search(r"[\x00-\x1f\x7f]", v):
            raise ValueError("Word contains invalid control characters")
        return v


class DictionaryResponse(BaseModel):
    words: List[str]
    count: int


class HealthResponse(BaseModel):
    status: str
    hunspell_available: bool
    language: str
    custom_dict_words: int
