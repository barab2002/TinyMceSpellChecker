"""
Custom (organizational) dictionary service.

Words are stored durably in MongoDB (one document per word):
  { "word": "Salesforce", "word_lower": "salesforce" }

A unique index on `word_lower` enforces case-insensitive de-duplication.

To keep spell-checking fast, all words are also held in an in-memory set
(`_words`) which serves every read (`contains`, `list_words`, `count`).
`contains()` is called once per token during a spell-check, so it must not
hit the database. MongoDB is written to only on add/remove.

The in-memory cache is kept in sync with MongoDB by `refresh()`, which a
background task in the app calls on a configurable interval. This picks up
changes made by other worker processes or external clients. Readers are
lock-free: writers build a new set and atomically swap it in (copy-on-write),
so a `contains()` call iterating the cache never sees it mutate underneath it.
"""
import json
import logging
import re
import threading
from pathlib import Path
from typing import List, Optional, Set

from pymongo import MongoClient
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)

# Words are stored in their original case but matched case-insensitively
_VALID_WORD_RE = re.compile(r"^[\w֐-׿יִ-פֿ .'\-]+$", re.UNICODE)

# Normalize keyboard ASCII quotes to their Hebrew Unicode equivalents so
# that words like ב"ר (ASCII ") are stored identically
# to ב״ר (gershayim U+05F4), matching what spell_service.py
# already does before Hunspell lookups.
_QUOTE_NORM = str.maketrans({'"': '״', "'": '׳'})


def _normalize_quotes(word: str) -> str:
    return word.translate(_QUOTE_NORM)


class DictionaryService:
    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        collection_name: str,
        seed_json_path: Optional[str] = None,
    ) -> None:
        self._words: Set[str] = set()
        self._words_lower: Set[str] = set()
        self._lock = threading.Lock()
        self._collection = None
        try:
            client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            # Force a connection check so startup fails fast / logs clearly.
            client.admin.command("ping")
            self._collection = client[db_name][collection_name]
            self._collection.create_index("word_lower", unique=True)
            if seed_json_path:
                self._maybe_seed(seed_json_path)
            self._load()
        except PyMongoError as exc:
            # Fail soft: spell-check still runs against Hunspell with no custom words.
            logger.error("MongoDB unavailable, custom dictionary disabled: %s", exc)
            self._collection = None
            self._words = set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_seed(self, seed_json_path: str) -> None:
        """One-time migration: seed Mongo from the legacy JSON file if empty."""
        if self._collection.estimated_document_count() > 0:
            return
        path = Path(seed_json_path)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            words = [w.strip() for w in raw.get("words", []) if w and w.strip()]
        except Exception as exc:
            logger.error("Failed to read seed file %s: %s", seed_json_path, exc)
            return
        docs = {}
        for w in words:
            docs[w.lower()] = {"word": w, "word_lower": w.lower()}
        if docs:
            self._collection.insert_many(list(docs.values()))
            logger.info("Seeded custom dictionary from JSON: %d words", len(docs))

    def _publish(self, words: Set[str]) -> None:
        """Atomically swap the in-memory cache to a new word set (copy-on-write)."""
        lower = {w.lower() for w in words}
        with self._lock:
            self._words = words
            self._words_lower = lower

    def _load(self) -> None:
        """Load all words from Mongo into the in-memory read cache."""
        words = {doc["word"] for doc in self._collection.find({}, {"word": 1})}
        self._publish(words)
        logger.info("Custom dictionary loaded: %d words", len(words))

    def refresh(self) -> None:
        """Reload the in-memory cache from MongoDB.

        Called periodically by the app's background refresh task so each worker
        process picks up words added/removed elsewhere. Safe to call often;
        on a DB error the existing cache is kept.
        """
        if self._collection is None:
            return
        try:
            words = {doc["word"] for doc in self._collection.find({}, {"word": 1})}
        except PyMongoError as exc:
            logger.error("Dictionary refresh failed, keeping current cache: %s", exc)
            return
        before = len(self._words)
        self._publish(words)
        if len(words) != before:
            logger.info(
                "Custom dictionary refreshed: %d -> %d words", before, len(words)
            )

    def _normalise(self, word: str) -> str:
        return _normalize_quotes(word.strip()).lower()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def contains(self, word: str) -> bool:
        """Return True if the word is in the custom dictionary (case-insensitive)."""
        # Lock-free read: grab the current lowercased view by reference.
        return self._normalise(word) in self._words_lower

    def add(self, word: str) -> bool:
        """
        Add a word to the dictionary.
        Returns True if added, False if already present.
        Raises ValueError for invalid input.
        """
        word = _normalize_quotes(word.strip())
        if not word:
            raise ValueError("Word must not be empty")
        if len(word) > 200:
            raise ValueError("Word too long (max 200 characters)")
        if not _VALID_WORD_RE.match(word):
            raise ValueError(f"Word contains disallowed characters: {word!r}")
        if self._collection is None:
            raise RuntimeError("Custom dictionary store is unavailable")
        result = self._collection.update_one(
            {"word_lower": word.lower()},
            {"$setOnInsert": {"word": word, "word_lower": word.lower()}},
            upsert=True,
        )
        if result.upserted_id is None:
            return False  # already present
        self._publish(self._words | {word})
        logger.info("Added to custom dictionary: %r", word)
        return True

    def remove(self, word: str) -> bool:
        """Remove a word from the dictionary. Returns True if it was present."""
        word = _normalize_quotes(word.strip())
        if self._collection is None:
            raise RuntimeError("Custom dictionary store is unavailable")
        result = self._collection.delete_one({"word_lower": word.lower()})
        if result.deleted_count == 0:
            return False
        lower = word.lower()
        self._publish({w for w in self._words if w.lower() != lower})
        logger.info("Removed from custom dictionary: %r", word)
        return True

    def list_words(self) -> List[str]:
        return sorted(self._words, key=str.lower)

    def search(
        self, query: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> tuple[List[str], int]:
        """Case-insensitive substring search over the in-memory cache, paginated.

        Returns (page_of_words, total_matching_words).
        """
        words = sorted(self._words, key=str.lower)
        if query:
            q = query.strip().lower()
            words = [w for w in words if q in w.lower()]
        return words[offset : offset + limit], len(words)

    def count(self) -> int:
        return len(self._words)
