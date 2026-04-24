"""
Custom (organizational) dictionary service.

Storage backends (selected at startup):
  1. MongoDB  — when SPELLCHECK_MONGO_URI is set and the connection succeeds.
  2. JSON file — fallback; stores { "words": [...] } at SPELLCHECK_CUSTOM_DICT_PATH.

In both cases an in-memory set is maintained for O(1) lookups during spell-check.
MongoDB writes are synchronous (pymongo); the latency is acceptable for infrequent
admin operations, and the spell-check hot path only touches the in-memory set.
"""
import json
import logging
import re
from pathlib import Path
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

_VALID_WORD_RE = re.compile(r"^[\w֐-׿יִ-פֿ .'\-]+$", re.UNICODE)


class DictionaryService:
    def __init__(self, dict_path: str, mongo_service=None) -> None:
        self._path = Path(dict_path)
        self._words: Set[str] = set()
        self._mongo = mongo_service

        if mongo_service and mongo_service.is_available():
            self._load_from_mongo()
        else:
            self._load_from_json()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_from_mongo(self) -> None:
        words = self._mongo.get_all_words()
        self._words = set(words)
        logger.info("Custom dictionary loaded from MongoDB: %d words", len(self._words))

        # Seed MongoDB from the local JSON file if it exists and MongoDB is empty
        if len(self._words) == 0 and self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                json_words = raw.get("words", [])
                if json_words:
                    migrated = self._mongo.migrate_json_words(json_words)
                    self._words = set(json_words)
                    logger.info(
                        "Migrated %d words from JSON file to MongoDB", migrated
                    )
            except Exception as exc:
                logger.error("JSON→MongoDB migration failed: %s", exc)

    def _load_from_json(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._words = set(raw.get("words", []))
                logger.info("Custom dictionary loaded from JSON: %d words", len(self._words))
            except Exception as exc:
                logger.error("Failed to load custom dictionary: %s", exc)
                self._words = set()
        else:
            self._save_json()

    def _save_json(self) -> None:
        data = {"words": sorted(self._words, key=str.lower)}
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, word: str) -> str:
        word = word.strip()
        if not word:
            raise ValueError("Word must not be empty")
        if len(word) > 200:
            raise ValueError("Word too long (max 200 characters)")
        if not _VALID_WORD_RE.match(word):
            raise ValueError(f"Word contains disallowed characters: {word!r}")
        return word

    def _normalise(self, word: str) -> str:
        return word.strip().lower()

    # ------------------------------------------------------------------
    # Public API (sync — safe to call from thread-pool executor)
    # ------------------------------------------------------------------

    def contains(self, word: str) -> bool:
        return self._normalise(word) in {self._normalise(w) for w in self._words}

    def add(self, word: str, language: str = "all") -> bool:
        """
        Add word to approved dictionary.
        Returns True if added, False if already present.
        Raises ValueError for invalid input.
        """
        word = self._validate(word)
        if self.contains(word):
            return False

        self._words.add(word)

        if self._mongo and self._mongo.is_available():
            self._mongo.add_word(word, language)
        else:
            self._save_json()

        logger.info("Added to dictionary: %r (lang=%s)", word, language)
        return True

    def remove(self, word: str) -> bool:
        word = word.strip()
        target = next(
            (w for w in self._words if w.lower() == word.lower()), None
        )
        if target is None:
            return False

        self._words.discard(target)

        if self._mongo and self._mongo.is_available():
            self._mongo.remove_word(target)
        else:
            self._save_json()

        logger.info("Removed from dictionary: %r", target)
        return True

    def add_word_to_memory(self, word: str) -> None:
        """Add a word directly to the in-memory set (called after approval)."""
        self._words.add(word)

    def list_words(self) -> List[str]:
        return sorted(self._words, key=str.lower)

    def count(self) -> int:
        return len(self._words)

    def using_mongo(self) -> bool:
        return bool(self._mongo and self._mongo.is_available())
