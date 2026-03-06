"""
Spell-check service — Hebrew support via spylls (pure Python Hunspell).

Why spylls?
  - Pure Python: `pip install spylls` — no libhunspell-dev system dependency.
  - Reads standard .aff / .dic files identically to the C Hunspell library.
  - Works inside Docker AND in local dev without any apt-get step.
  - Verified working with the he_IL (hspell 1.4) dictionary bundled in this repo.

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
from typing import List, Tuple

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
        self._init(dict_dir, language)

    def _init(self, dict_dir: str, language: str) -> None:
        lang_fs = language.replace("-", "_")
        # spylls.Dictionary.from_files() expects path WITHOUT extension
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
        """Return True if correctly spelled. Fails open if engine unavailable."""
        if not self._dic:
            return True
        clean = _strip_nikud(word)
        if not clean:
            return True
        try:
            return bool(self._dic.lookup(clean))
        except Exception:
            return True

    def get_suggestions(self, word: str, max_n: int = 5) -> List[str]:
        """Return up to max_n spelling suggestions (strings, never bytes)."""
        if not self._dic:
            return []
        clean = _strip_nikud(word)
        try:
            return list(self._dic.suggest(clean))[:max_n]
        except Exception as exc:
            logger.warning("suggest(%r) failed: %s", word, exc)
            return []

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
        """
        tokens = self.tokenize(text)
        misspellings: List[dict] = []
        cache: dict = {}  # stripped_word -> (is_correct, [suggestions])

        for word, start, end in tokens:
            clean = _strip_nikud(word)

            # Organisational dictionary has highest priority
            if custom_dict.contains(clean):
                continue

            if clean not in cache:
                is_correct  = self.check_word(word)
                suggestions = (
                    [] if is_correct
                    else (self.get_suggestions(word, max_suggestions) if include_suggestions else [])
                )
                cache[clean] = (is_correct, suggestions)

            is_correct, suggestions = cache[clean]

            if not is_correct:
                misspellings.append(
                    {"word": word, "start": start, "end": end,
                     "suggestions": suggestions, "source": "hunspell"}
                )

        return misspellings
