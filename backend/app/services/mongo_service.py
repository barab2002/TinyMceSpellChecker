"""
MongoDB service — handles both the approved organisational dictionary
and the pending-approval word queue.

Falls back gracefully when MongoDB is unavailable or not configured:
the DictionaryService will use its JSON file instead.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MongoService:
    def __init__(self, uri: str, db_name: str) -> None:
        self._db = None
        self._available = False

        if not uri:
            logger.info("MongoDB URI not set — dictionary will use JSON file storage")
            return

        try:
            from pymongo import MongoClient, ASCENDING  # type: ignore[import-untyped]

            self._client = MongoClient(uri, serverSelectionTimeoutMS=5000)
            self._client.admin.command("ping")
            self._db = self._client[db_name]

            # Approved words collection
            aw = self._db.approved_words
            aw.create_index([("word", ASCENDING)], unique=True)
            aw.create_index([("language", ASCENDING)])

            # Pending words collection
            pw = self._db.pending_words
            pw.create_index([("word", ASCENDING)])
            pw.create_index([("status", ASCENDING)])
            pw.create_index([("language", ASCENDING)])
            pw.create_index([("submitted_at", ASCENDING)])

            self._available = True
            logger.info("MongoDB connected: db=%s", db_name)

        except Exception as exc:
            logger.warning(
                "MongoDB unavailable — dictionary will use JSON file storage: %s", exc
            )

    # ── Public ────────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        return self._available

    # ── Approved words ────────────────────────────────────────────────────────

    def get_all_words(self) -> List[str]:
        if not self._available:
            return []
        docs = self._db.approved_words.find({}, {"word": 1, "_id": 0})
        return [d["word"] for d in docs]

    def get_words_paginated(
        self,
        language: Optional[str] = None,
        q: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[Dict], int]:
        if not self._available:
            return [], 0

        filt: dict = {}
        if language and language != "all":
            filt["language"] = language
        if q:
            filt["word"] = {"$regex": q, "$options": "i"}

        total = self._db.approved_words.count_documents(filt)
        docs = (
            self._db.approved_words.find(filt)
            .sort("word", 1)
            .skip(skip)
            .limit(limit)
        )
        words = [
            {
                "word": d["word"],
                "language": d.get("language", "all"),
                "added_at": d.get("added_at", "").isoformat()
                if isinstance(d.get("added_at"), datetime)
                else "",
            }
            for d in docs
        ]
        return words, total

    def search_words(
        self, q: str, language: Optional[str] = None, limit: int = 10
    ) -> List[str]:
        if not self._available or not q:
            return []

        filt: dict = {"word": {"$regex": q, "$options": "i"}}
        if language and language != "all":
            filt["language"] = language

        docs = self._db.approved_words.find(filt, {"word": 1, "_id": 0}).limit(limit)
        return [d["word"] for d in docs]

    def add_word(self, word: str, language: str = "all") -> bool:
        if not self._available:
            return False
        try:
            self._db.approved_words.update_one(
                {"word": word},
                {
                    "$setOnInsert": {
                        "word": word,
                        "language": language,
                        "added_at": _now(),
                        "source": "api",
                    }
                },
                upsert=True,
            )
            return True
        except Exception as exc:
            logger.error("MongoDB add_word failed: %s", exc)
            return False

    def remove_word(self, word: str) -> bool:
        if not self._available:
            return False
        result = self._db.approved_words.delete_one({"word": word})
        return result.deleted_count > 0

    def word_exists(self, word: str) -> bool:
        if not self._available:
            return False
        return self._db.approved_words.count_documents({"word": word}) > 0

    def migrate_json_words(self, words: List[str]) -> int:
        """Seed MongoDB from an existing JSON dictionary (idempotent)."""
        if not self._available:
            return 0
        migrated = 0
        for word in words:
            try:
                self._db.approved_words.update_one(
                    {"word": word},
                    {
                        "$setOnInsert": {
                            "word": word,
                            "language": "all",
                            "added_at": _now(),
                            "source": "migration",
                        }
                    },
                    upsert=True,
                )
                migrated += 1
            except Exception:
                pass
        return migrated

    # ── Pending words ─────────────────────────────────────────────────────────

    def add_pending(
        self,
        word: str,
        language: str = "all",
        context: Optional[str] = None,
    ) -> Optional[str]:
        """
        Add a word to the pending-approval queue.
        Returns the new document _id as a string, or None on failure.
        If an identical pending entry already exists, returns its id.
        """
        if not self._available:
            return None
        existing = self._db.pending_words.find_one(
            {"word": word, "status": "pending"}
        )
        if existing:
            return str(existing["_id"])
        result = self._db.pending_words.insert_one(
            {
                "word": word,
                "language": language,
                "submitted_at": _now(),
                "status": "pending",
                "reviewed_at": None,
                "context": context,
            }
        )
        return str(result.inserted_id)

    def get_pending(
        self,
        status: str = "pending",
        language: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict]:
        if not self._available:
            return []

        filt: dict = {"status": status}
        if language and language != "all":
            filt["language"] = language

        docs = (
            self._db.pending_words.find(filt)
            .sort("submitted_at", -1)
            .skip(skip)
            .limit(limit)
        )
        result = []
        for d in docs:
            result.append(
                {
                    "id": str(d["_id"]),
                    "word": d["word"],
                    "language": d.get("language", "all"),
                    "submitted_at": d["submitted_at"].isoformat()
                    if isinstance(d.get("submitted_at"), datetime)
                    else "",
                    "status": d["status"],
                    "context": d.get("context"),
                    "reviewed_at": d["reviewed_at"].isoformat()
                    if isinstance(d.get("reviewed_at"), datetime)
                    else None,
                }
            )
        return result

    def pending_count(self, status: str = "pending") -> int:
        if not self._available:
            return 0
        return self._db.pending_words.count_documents({"status": status})

    def approve_pending(self, word_id: str) -> Optional[Dict]:
        """
        Mark a pending word as approved, add it to approved_words, and return
        the word document so the caller can update the in-memory dictionary.
        Returns None if the id is not found or already reviewed.
        """
        if not self._available:
            return None
        from bson import ObjectId  # type: ignore[import-untyped]

        try:
            doc = self._db.pending_words.find_one_and_update(
                {"_id": ObjectId(word_id), "status": "pending"},
                {"$set": {"status": "approved", "reviewed_at": _now()}},
            )
        except Exception:
            return None

        if not doc:
            return None

        word = doc["word"]
        language = doc.get("language", "all")
        self.add_word(word, language)
        return {"word": word, "language": language}

    def dismiss_pending(self, word_id: str) -> bool:
        """Mark a pending word as dismissed. Returns True on success."""
        if not self._available:
            return False
        from bson import ObjectId  # type: ignore[import-untyped]

        try:
            result = self._db.pending_words.update_one(
                {"_id": ObjectId(word_id), "status": "pending"},
                {"$set": {"status": "dismissed", "reviewed_at": _now()}},
            )
            return result.modified_count > 0
        except Exception:
            return False
