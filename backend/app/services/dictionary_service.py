"""
Custom (organizational) dictionary service.

Stores approved words in a JSON file:
  { "words": ["Salesforce", "ZoomInfo", ...] }

Thread-safety note: this MVP uses a simple in-memory set with file persistence.
For concurrent environments use a file lock (filelock package) or move to SQLite.
"""
import json
import logging
import re
from pathlib import Path
from typing import List, Set

logger = logging.getLogger(__name__)

# Words are stored in their original case but matched case-insensitively
_VALID_WORD_RE = re.compile(r"^[\w\u0590-\u05FF\uFB1D-\uFB4E .'\-]+$", re.UNICODE)


class DictionaryService:
    def __init__(self, dict_path: str) -> None:
        self._path = Path(dict_path)
        self._words: Set[str] = set()
        self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load words from JSON file, creating it if absent."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._words = set(raw.get("words", []))
                logger.info("Custom dictionary loaded: %d words", len(self._words))
            except Exception as exc:
                logger.error("Failed to load custom dictionary: %s", exc)
                self._words = set()
        else:
            self._save()  # create empty file

    def _save(self) -> None:
        data = {"words": sorted(self._words, key=str.lower)}
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _normalise(self, word: str) -> str:
        return word.strip().lower()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def contains(self, word: str) -> bool:
        """Return True if the word is in the custom dictionary (case-insensitive)."""
        return self._normalise(word) in {self._normalise(w) for w in self._words}

    def add(self, word: str) -> bool:
        """
        Add a word to the dictionary.
        Returns True if added, False if already present or invalid.
        Raises ValueError for invalid input.
        """
        word = word.strip()
        if not word:
            raise ValueError("Word must not be empty")
        if len(word) > 200:
            raise ValueError("Word too long (max 200 characters)")
        if not _VALID_WORD_RE.match(word):
            raise ValueError(f"Word contains disallowed characters: {word!r}")
        if self.contains(word):
            return False  # already present
        self._words.add(word)
        self._save()
        logger.info("Added to custom dictionary: %r", word)
        return True

    def remove(self, word: str) -> bool:
        """Remove a word from the dictionary. Returns True if it was present."""
        word = word.strip()
        # Find exact match case-insensitively
        target = next(
            (w for w in self._words if w.lower() == word.lower()), None
        )
        if target is None:
            return False
        self._words.discard(target)
        self._save()
        logger.info("Removed from custom dictionary: %r", word)
        return True

    def list_words(self) -> List[str]:
        return sorted(self._words, key=str.lower)

    def count(self) -> int:
        return len(self._words)
