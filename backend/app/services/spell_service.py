"""
Spell-check service — Hebrew support via the hunspell C library bindings.

Performance notes
-----------------
The `hunspell` Python package wraps the native libhunspell C library via ctypes.
This is substantially faster than a pure-Python reimplementation for both lookup
and suggestion generation.

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

Hebrew proclitic prefix handling
---------------------------------
Hebrew attaches grammatical particles directly to the front of words with no
space, creating forms like:
  ו   (ve/u)  = and     → ובית   = "and a house"
  ב   (be)    = in/at   → בבית   = "in the house"
  כ   (ke)    = as/like → כבית   = "like a house"
  ל   (le)    = to/for  → לבית   = "to the house"
  מ   (mi)    = from    → מבית   = "from the house"
  ה   (ha)    = the     → הבית   = "the house"
  ש   (she)   = that    → שבית   = "that a house"
  and stacked combos:   ובבית = "and in the house"

The checker tries up to two layers of prefix stripping when a word fails
the direct lookup, so "ובמחשב" → strips "ו" → "במחשב" → strips "ב" →
"מחשב" (computer) ✓ — no false positive.

Suggestions are reconstructed with the prefix so a correction of "ובמשיב"
(misspelled) → ["ובמשיב" (base sug: "משיב")] → ["ובמשיב"], preserving the
grammatical context of the original prefixed word.
"""
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterator, List, Optional, Tuple

from .dictionary_service import DictionaryService

if TYPE_CHECKING:
    from .redis_service import RedisService

logger = logging.getLogger(__name__)

_HE_LETTERS  = "\u05D0-\u05EA\u05F0-\u05F4\uFB1D-\uFB4E"
_HE_MARKS    = "\u0591-\u05C7"
_HE_TOKEN_RE = re.compile(
    # Core: one or more Hebrew letters/marks
    # Optionally followed by (ASCII ' or " or Unicode geresh/gershayim) then more Hebrew chars.
    # This keeps abbreviations like צה"ל and ד'ר as single tokens instead of
    # splitting them at the quote character.
    rf"[{_HE_LETTERS}{_HE_MARKS}]+"
    rf"(?:['\"\u05F3\u05F4][{_HE_LETTERS}{_HE_MARKS}]+)*"
)
_MAQAF = "\u05BE"

# Hebrew proclitic prefixes, ordered longest-first so that multi-letter
# combos (e.g. "וב") are tried before their individual components ("ו", "ב").
# Up to two layers are tried: stripping "וב" from "ובמחשב" leaves "מחשב";
# if one layer isn't enough, the next recursive call strips one more.
_HE_PREFIXES: Tuple[str, ...] = (
    # Two-letter stacked prefixes
    "וב", "וכ", "ול", "ום", "וש", "וה",
    "בה", "כה", "לה", "מה", "שה",
    "כש", "בש", "לש",
    # Single-letter prefixes (most frequent in running text)
    "ה", "ו", "ב", "כ", "ל", "מ", "ש",
)


def _strip_nikud(word: str) -> str:
    """Remove nikud and cantillation marks, leaving only base letters."""
    return re.sub(rf"[{_HE_MARKS}]", "", word)


def _normalize_inner_quotes(word: str) -> str:
    """
    Replace ASCII apostrophe (') and double-quote (") with the proper Hebrew
    geresh (U+05F3) and gershayim (U+05F4) punctuation marks.

    Hebrew abbreviations like ד"ר (doctor) and צה"ל (IDF) are commonly typed
    with ASCII quote characters, but Hunspell dictionaries store them with the
    canonical Unicode punctuation.  Normalising before lookup prevents false
    positives for correctly-spelled abbreviations.
    """
    return word.replace('"', "\u05F4").replace("'", "\u05F3")


def _prefix_bases(word: str) -> Iterator[Tuple[str, str]]:
    """
    Yield (prefix, base) pairs for every single-step prefix stripping of
    *word*.  Only yields pairs where base has at least 2 characters.
    """
    for prefix in _HE_PREFIXES:
        if word.startswith(prefix):
            base = word[len(prefix):]
            if len(base) >= 2:
                yield prefix, base


def _is_correct_with_prefix(word: str, dic, depth: int = 2) -> bool:
    """
    Return True if stripping up to *depth* layers of Hebrew proclitic
    prefixes yields a form that Hunspell accepts.

    Example (depth=2):
      "ובמחשב" → strip "וב" → "מחשב" → spell() → True  ✓
      "ובמחשב" → strip "ו"  → "במחשב" → strip "ב" → "מחשב" → True  ✓
    """
    if depth == 0:
        return False
    for _, base in _prefix_bases(word):
        try:
            if bool(dic.spell(base)):
                return True
        except Exception:
            pass
        if depth > 1 and _is_correct_with_prefix(base, dic, depth - 1):
            return True
    return False


def _org_dict_match(clean: str, custom_dict: "DictionaryService") -> bool:
    """
    Return True if *clean* (or any prefix-stripped form of it) is in the
    organisational dictionary.  This prevents "לSalesforce" from being
    flagged when "Salesforce" is in the custom dictionary.
    """
    if custom_dict.contains(clean):
        return True
    for _, base in _prefix_bases(clean):
        if custom_dict.contains(base):
            return True
    return False


class SpellService:
    def __init__(
        self,
        dict_dir: str,
        language: str = "he_IL",
        redis_svc: "Optional[RedisService]" = None,
    ) -> None:
        self.language = language
        self._dic = None
        self._redis = redis_svc
        # Cross-request caches — populated on first encounter, reused forever
        self._lookup_cache: Dict[str, bool] = {}
        self._suggest_cache: Dict[Tuple[str, int], List[str]] = {}
        self._init(dict_dir, language)

    def _init(self, dict_dir: str, language: str) -> None:
        lang_fs = language.replace("-", "_")
        base = Path(dict_dir) / lang_fs
        dic_path = str(base) + ".dic"
        aff_path = str(base) + ".aff"

        if not Path(dic_path).exists() or not Path(aff_path).exists():
            logger.error(
                "Dictionary files not found: %s.aff / %s.dic", base, base
            )
            return

        try:
            import hunspell  # type: ignore[import-untyped]
            self._dic = hunspell.HunSpell(dic_path, aff_path)
            logger.info("hunspell dictionary loaded: language=%s", lang_fs)
        except Exception as exc:
            logger.error("Failed to load hunspell dictionary: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return self._dic is not None

    def check_word(self, word: str) -> bool:
        """
        Return True if correctly spelled.

        Tries the word as-is first, then strips up to two layers of Hebrew
        proclitic prefixes (ה, ו, ב, כ, ל, מ, ש and two-letter combos)
        before concluding a word is misspelled.  Results are cached.
        """
        if not self._dic:
            return True
        clean = _strip_nikud(word)
        if not clean:
            return True

        # Normalise ASCII ' / " to Hebrew geresh/gershayim so that abbreviations
        # like צה"ל typed with a regular keyboard quote look up correctly in the
        # Hunspell dictionary (which uses the Unicode punctuation marks).
        if "'" in clean or '"' in clean:
            clean = _normalize_inner_quotes(clean)

        if clean in self._lookup_cache:
            return self._lookup_cache[clean]

        # Fast path: Redis dictionary SET covers all base forms from he_IL.dic
        if self._redis and self._redis.is_word(clean, self.language):
            self._lookup_cache[clean] = True
            return True

        try:
            result = bool(self._dic.spell(clean))
        except Exception:
            result = True  # fail open

        # Prefix fallback: "ובמחשב" should not be flagged
        if not result:
            result = _is_correct_with_prefix(clean, self._dic, depth=2)

        self._lookup_cache[clean] = result
        return result

    def get_suggestions(self, word: str, max_n: int = 5) -> List[str]:
        """
        Return up to max_n spelling suggestions, reconstructing the
        original proclitic prefix when suggesting for a prefix-stripped base.

        Example: "ובמשהיב" (misspelled) → strips "וב", suggests ["משיב"],
        reconstructs → ["ובמשיב"].  Results are cached.
        """
        if not self._dic:
            return []
        clean = _strip_nikud(word)
        cache_key = (clean, max_n)

        if cache_key in self._suggest_cache:
            return self._suggest_cache[cache_key]

        suggestions: List[str] = []
        try:
            suggestions = self._dic.suggest(clean)[:max_n]
        except Exception as exc:
            logger.warning("suggest(%r) failed: %s", word, exc)

        # No direct suggestions → try suggestions for the prefix-stripped base
        # and prepend the prefix back so the correction preserves the sentence
        # context (e.g. "ו" + "מחשוב" instead of bare "מחשוב").
        if not suggestions:
            for prefix, base in _prefix_bases(clean):
                try:
                    base_sugs = self._dic.suggest(base)[:max_n]
                except Exception:
                    base_sugs = []
                if base_sugs:
                    suggestions = [prefix + s for s in base_sugs][:max_n]
                    break

        self._suggest_cache[cache_key] = suggestions
        return suggestions

    def cache_stats(self) -> dict:
        stats: dict = {
            "lookup_cached_words": len(self._lookup_cache),
            "suggest_cached_entries": len(self._suggest_cache),
            "redis_available": self._redis is not None and self._redis.is_available(),
        }
        if self._redis and self._redis.is_available():
            stats["redis_dict_size"] = self._redis.dict_size(self.language)
        return stats

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

        Organisational-dictionary matching is also prefix-aware: "לSalesforce"
        is not flagged if "Salesforce" is in the custom dictionary.
        """
        tokens = self.tokenize(text)
        misspellings: List[dict] = []
        # Per-request dedup: avoid re-running suggest() for same clean word twice
        seen_clean: set = set()

        for word, start, end in tokens:
            clean = _strip_nikud(word)

            # Organisational dictionary has highest priority (prefix-aware)
            if _org_dict_match(clean, custom_dict):
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
