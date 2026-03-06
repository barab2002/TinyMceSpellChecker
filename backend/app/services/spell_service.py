"""
Spell-check service wrapping Hunspell via pyhunspell.

Hebrew tokenisation notes
-------------------------
Hebrew text uses Unicode block U+05D0–U+05EA (letters) plus:
  - Nikud (vowel points)   U+05B0–U+05C7
  - Cantillation marks     U+0591–U+05AF
  - Punctuation            U+05BE maqaf (hyphen), U+05F3/U+05F4 geresh/gershayim

Strategy:
  1. Find runs of Hebrew characters (letters + optional nikud).
  2. Strip nikud before passing to Hunspell — it rarely handles pointed text.
  3. Skip single-character tokens and tokens shorter than 2 base letters.
  4. Maqaf-joined words (e.g. בית-ספר) are checked as separate parts.
  5. Cache per-word results to avoid redundant Hunspell calls on repeated words.

Limitations:
  - This does not do morphological analysis; compound forms may produce false
    positives.  The custom dictionary is the recommended mitigation.
  - Mixed Hebrew-English words are currently skipped.
"""
import logging
import re
from typing import List, Optional, Tuple

from .dictionary_service import DictionaryService

logger = logging.getLogger(__name__)

# Hebrew base letters
_HE_LETTERS = "\u05D0-\u05EA\u05F0-\u05F4\uFB1D-\uFB4E"
# Nikud + cantillation (marks only, no letters)
_HE_MARKS = "\u0591-\u05C7"
# Full Hebrew token: letters + marks, at least one letter
_HE_TOKEN_RE = re.compile(
    rf"[{_HE_LETTERS}{_HE_MARKS}]*[{_HE_LETTERS}][{_HE_LETTERS}{_HE_MARKS}]*"
)
# Maqaf (Hebrew hyphen U+05BE)
_MAQAF = "\u05BE"


def _strip_nikud(word: str) -> str:
    """Remove nikud and cantillation marks, leaving only base letters."""
    return re.sub(rf"[{_HE_MARKS}]", "", word)


def _split_maqaf(word: str) -> List[str]:
    """Split a word on maqaf, returning each part."""
    return word.split(_MAQAF)


class SpellService:
    def __init__(self, dict_dir: str, language: str = "he_IL") -> None:
        self.language = language
        self._hunspell = None
        self._init_hunspell(dict_dir, language)

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_hunspell(self, dict_dir: str, language: str) -> None:
        import os
        from pathlib import Path

        # Support both he-IL and he_IL
        lang_fs = language.replace("-", "_")
        base = Path(dict_dir)

        aff = base / f"{lang_fs}.aff"
        dic = base / f"{lang_fs}.dic"

        if not aff.exists() or not dic.exists():
            logger.error(
                "Hunspell dictionary files not found: %s / %s", aff, dic
            )
            return

        try:
            import hunspell as _hunspell_mod  # pyhunspell

            self._hunspell = _hunspell_mod.HunSpell(str(dic), str(aff))
            logger.info("Hunspell initialised: language=%s", lang_fs)
        except ImportError:
            logger.error(
                "pyhunspell is not installed. "
                "Run: pip install pyhunspell (requires libhunspell-dev)"
            )
        except Exception as exc:
            logger.error("Hunspell initialisation failed: %s", exc)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return self._hunspell is not None

    def check_word(self, word: str) -> bool:
        """Return True if the word is correctly spelled (or Hunspell unavailable)."""
        if not self._hunspell:
            return True  # fail-open: don't block if engine is down
        clean = _strip_nikud(word)
        if not clean:
            return True
        try:
            return bool(self._hunspell.spell(clean))
        except Exception:
            return True

    def get_suggestions(self, word: str, max_n: int = 5) -> List[str]:
        """Return up to max_n spelling suggestions."""
        if not self._hunspell:
            return []
        clean = _strip_nikud(word)
        try:
            raw = self._hunspell.suggest(clean)
            # pyhunspell may return bytes on some builds
            result: List[str] = []
            for s in raw[:max_n]:
                result.append(s.decode("utf-8") if isinstance(s, bytes) else s)
            return result
        except Exception as exc:
            logger.warning("suggest(%r) failed: %s", word, exc)
            return []

    # ------------------------------------------------------------------
    # Tokenisation
    # ------------------------------------------------------------------

    def tokenize(self, text: str) -> List[Tuple[str, int, int]]:
        """
        Extract Hebrew word tokens from *plain* text.
        Returns list of (token_string, start_offset, end_offset).
        Tokens spanning maqaf are expanded into their sub-parts with adjusted offsets.
        """
        tokens: List[Tuple[str, int, int]] = []
        for m in _HE_TOKEN_RE.finditer(text):
            raw_word = m.group()
            raw_start = m.start()

            if _MAQAF in raw_word:
                # Split on maqaf and yield each part with its sub-offset
                cursor = raw_start
                for part in raw_word.split(_MAQAF):
                    part_end = cursor + len(part)
                    clean = _strip_nikud(part)
                    if len(clean) >= 2:
                        tokens.append((part, cursor, part_end))
                    cursor = part_end + 1  # +1 for the maqaf itself
            else:
                clean = _strip_nikud(raw_word)
                if len(clean) >= 2:
                    tokens.append((raw_word, raw_start, m.end()))

        return tokens

    # ------------------------------------------------------------------
    # Main check method
    # ------------------------------------------------------------------

    def check_text(
        self,
        text: str,
        custom_dict: DictionaryService,
        max_suggestions: int = 5,
        include_suggestions: bool = True,
    ) -> List[dict]:
        """
        Check *plain* text for Hebrew misspellings.

        Returns a list of dicts compatible with the Misspelling schema:
          { word, start, end, suggestions, source }
        """
        tokens = self.tokenize(text)
        misspellings: List[dict] = []
        word_cache: dict = {}  # clean_word -> (is_correct, suggestions)

        for word, start, end in tokens:
            clean = _strip_nikud(word)

            # 1. Custom / organisational dictionary takes highest priority
            if custom_dict.contains(clean):
                continue

            # 2. Cached result
            if clean in word_cache:
                is_correct, suggestions = word_cache[clean]
            else:
                is_correct = self.check_word(word)
                suggestions = (
                    [] if is_correct
                    else self.get_suggestions(word, max_suggestions)
                ) if include_suggestions else []
                word_cache[clean] = (is_correct, suggestions)

            if not is_correct:
                misspellings.append(
                    {
                        "word": word,
                        "start": start,
                        "end": end,
                        "suggestions": suggestions,
                        "source": "hunspell",
                    }
                )

        return misspellings
