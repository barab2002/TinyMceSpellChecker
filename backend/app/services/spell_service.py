"""
Spell-check service — Hebrew support via spylls (pure Python Hunspell).

Performance notes
-----------------
spylls.suggest() is the bottleneck: pure Python, ~50–200 ms per unknown word.
Two caches are maintained for the lifetime of the process:

  _lookup_cache  – word → bool  (is it spelled correctly?)
  _suggest_cache – (word, max_n) → [suggestions]

These are populated on first encounter and reused across all subsequent requests,
giving near-zero latency for repeated words (which is the common case).

Hebrew tokenisation notes
-------------------------
Hebrew text block: U+05D0–U+05EA (letters) plus:
  - Nikud (vowel points)   U+05B0–U+05C7
  - Cantillation marks     U+0591–U+05AF
  - Maqaf (hyphen)         U+05BE  — treated as word separator

Strategy:
  1. Regex-find runs of Hebrew characters (letters + optional nikud).
  2. Strip nikud before Hunspell — the dictionary uses unpointed forms.
  3. Skip tokens shorter than 2 base letters.
  4. Split on maqaf (U+05BE); check each part independently.
  5. Cache per-word results to avoid redundant calls on repeated words.
"""
import logging
import re
from pathlib import Path
from typing import Dict, List, Tuple

from .dictionary_service import DictionaryService

logger = logging.getLogger(__name__)

_HE_LETTERS  = "\u05D0-\u05EA\u05F0-\u05F4\uFB1D-\uFB4E"
_HE_MARKS    = "\u0591-\u05C7"
_HE_TOKEN_RE = re.compile(
    rf"[{_HE_LETTERS}{_HE_MARKS}]*[{_HE_LETTERS}][{_HE_LETTERS}{_HE_MARKS}]*"
)
_MAQAF = "\u05BE"


def _strip_nikud(word: str) -> str:
    """Remove nikud and cantillation marks, leaving only base letters."""
    return re.sub(rf"[{_HE_MARKS}]", "", word)


class SpellService:
    def __init__(self, dict_dir: str, language: str = "he_IL") -> None:
        self.language = language
        self._dic = None
        # Cross-request caches — populated on first encounter, reused forever
        self._lookup_cache: Dict[str, bool] = {}
        self._suggest_cache: Dict[Tuple[str, int], List[str]] = {}
        self._init(dict_dir, language)

    def _init(self, dict_dir: str, language: str) -> None:
        lang_fs = language.replace("-", "_")
        base = Path(dict_dir) / lang_fs

        if not Path(str(base) + ".aff").exists() or not Path(str(base) + ".dic").exists():
            logger.error(
                "Dictionary files not found: %s.aff / %s.dic", base, base
            )
            return

        try:
            from spylls.hunspell import Dictionary
            self._dic = Dictionary.from_files(str(base))
            logger.info("spylls dictionary loaded: language=%s", lang_fs)
        except Exception as exc:
            logger.error("Failed to load spylls dictionary: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return self._dic is not None

    def check_word(self, word: str) -> bool:
        """Return True if correctly spelled. Uses cross-request cache."""
        if not self._dic:
            return True
        clean = _strip_nikud(word)
        if not clean:
            return True

        if clean in self._lookup_cache:
            return self._lookup_cache[clean]

        try:
            result = bool(self._dic.lookup(clean))
        except Exception:
            result = True  # fail open

        self._lookup_cache[clean] = result
        return result

    def get_suggestions(self, word: str, max_n: int = 5) -> List[str]:
        """Return up to max_n spelling suggestions. Results are cached."""
        if not self._dic:
            return []
        clean = _strip_nikud(word)
        cache_key = (clean, max_n)

        if cache_key in self._suggest_cache:
            return self._suggest_cache[cache_key]

        try:
            suggestions = list(self._dic.suggest(clean))[:max_n]
        except Exception as exc:
            logger.warning("suggest(%r) failed: %s", word, exc)
            suggestions = []

        self._suggest_cache[cache_key] = suggestions
        return suggestions

    def cache_stats(self) -> dict:
        return {
            "lookup_cached_words": len(self._lookup_cache),
            "suggest_cached_entries": len(self._suggest_cache),
        }

    # ------------------------------------------------------------------
    # Tokenisation
    # ------------------------------------------------------------------

    def tokenize(self, text: str) -> List[Tuple[str, int, int]]:
        """
        Extract Hebrew word tokens from plain text.
        Returns [(token_str, start_offset, end_offset), ...].
        Maqaf-joined words (e.g. בית-ספר) are split; each part is yielded
        separately with its adjusted offset.
        """
        tokens: List[Tuple[str, int, int]] = []

        for m in _HE_TOKEN_RE.finditer(text):
            raw_word  = m.group()
            raw_start = m.start()

            if _MAQAF in raw_word:
                cursor = raw_start
                for part in raw_word.split(_MAQAF):
                    part_end = cursor + len(part)
                    if len(_strip_nikud(part)) >= 2:
                        tokens.append((part, cursor, part_end))
                    cursor = part_end + 1  # +1 for the maqaf character itself
            else:
                if len(_strip_nikud(raw_word)) >= 2:
                    tokens.append((raw_word, raw_start, m.end()))

        return tokens

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def check_text(
        self,
        text: str,
        custom_dict: DictionaryService,
        max_suggestions: int = 5,
        include_suggestions: bool = True,
    ) -> List[dict]:
        """
        Check plain text for Hebrew misspellings.

        Returns list of dicts: { word, start, end, suggestions, source }
        where start/end are character offsets in the supplied plain text.

        Uses the cross-request instance caches for lookup and suggestions.
        """
        tokens = self.tokenize(text)
        misspellings: List[dict] = []
        # Per-request dedup: avoid re-running suggest() for same clean word twice
        seen_clean: set = set()

        for word, start, end in tokens:
            clean = _strip_nikud(word)

            # Organisational dictionary has highest priority
            if custom_dict.contains(clean):
                continue

            is_correct = self.check_word(word)

            if not is_correct:
                # Compute suggestions only once per unique clean word per request
                if clean not in seen_clean:
                    suggestions = (
                        self.get_suggestions(word, max_suggestions)
                        if include_suggestions else []
                    )
                    seen_clean.add(clean)
                else:
                    cache_key = (_strip_nikud(word), max_suggestions)
                    suggestions = self._suggest_cache.get(cache_key, [])

                misspellings.append(
                    {"word": word, "start": start, "end": end,
                     "suggestions": suggestions, "source": "hunspell"}
                )

        return misspellings
