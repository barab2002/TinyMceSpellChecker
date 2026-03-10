"""
Redis integration for the Hebrew spell-check service.

On startup the service parses the Hunspell .dic file and bulk-loads every
base word form into a Redis SET.  Subsequent `check_word()` calls can then
use a single SISMEMBER command — an O(1) network round-trip — instead of
invoking the hunspell C library for words that are already known to be correct.

Key design
----------
  spell:dict:{lang}    Redis SET  — all base-form words from the .dic file
  spell:loaded:{lang}  Redis key  — sentinel "1" once the SET is populated

If Redis is unreachable the service logs a warning and returns is_available()
== False; the caller (SpellService) falls back to pure hunspell mode without
any change in correctness.
"""
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DICT_KEY    = "spell:dict:{lang}"
_LOADED_KEY  = "spell:loaded:{lang}"
_BATCH_SIZE  = 5_000


class RedisService:
    def __init__(self) -> None:
        self._client = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, url: str) -> None:
        """Connect to Redis.  Silently degrades if the server is unreachable."""
        if not url:
            logger.info("Redis URL not configured — running hunspell-only mode")
            return
        try:
            import redis  # type: ignore[import-untyped]
            client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=3)
            client.ping()
            self._client = client
            logger.info("Redis connected: %s", url)
        except Exception as exc:
            logger.warning(
                "Redis unavailable (%s) — falling back to hunspell-only caching", exc
            )
            self._client = None

    def is_available(self) -> bool:
        return self._client is not None

    # ------------------------------------------------------------------
    # Dictionary loading
    # ------------------------------------------------------------------

    def load_dictionary(self, dic_path: str, language: str) -> int:
        """
        Parse a Hunspell .dic file and SADD every base word form into
        ``spell:dict:{language}``.

        The SET is only populated once: if the sentinel key
        ``spell:loaded:{language}`` already exists the method returns 0
        immediately (idempotent across restarts).

        Returns the number of words loaded, or 0 if already loaded / error.
        """
        if not self._client:
            return 0

        dict_key   = _DICT_KEY.format(lang=language)
        loaded_key = _LOADED_KEY.format(lang=language)

        if self._client.exists(loaded_key):
            count = self._client.scard(dict_key)
            logger.info(
                "Redis dictionary already loaded: lang=%s words=%d", language, count
            )
            return 0

        path = Path(dic_path)
        if not path.exists():
            logger.error("Dictionary .dic file not found: %s", dic_path)
            return 0

        logger.info("Loading Hebrew dictionary into Redis: %s → %s", dic_path, dict_key)
        loaded = 0
        pipe = self._client.pipeline(transaction=False)

        with path.open(encoding="utf-8", errors="replace") as fh:
            # First line of a Hunspell .dic is the word count — skip it
            fh.readline()

            for line in fh:
                word = line.strip()
                if not word:
                    continue
                # Strip Hunspell affix flags: "מחשב/ABCD" → "מחשב"
                slash = word.find("/")
                if slash != -1:
                    word = word[:slash]
                if word:
                    pipe.sadd(dict_key, word)
                    loaded += 1
                    if loaded % _BATCH_SIZE == 0:
                        pipe.execute()
                        pipe = self._client.pipeline(transaction=False)

        # Flush any remaining words in the last partial batch
        if loaded % _BATCH_SIZE != 0:
            pipe.execute()

        # Mark as fully loaded so subsequent startups skip the work
        self._client.set(loaded_key, "1")

        logger.info(
            "Hebrew dictionary loaded into Redis: lang=%s words=%d", language, loaded
        )
        return loaded

    # ------------------------------------------------------------------
    # Word lookup
    # ------------------------------------------------------------------

    def is_word(self, word: str, language: str) -> bool:
        """
        Return True if *word* is in the Redis dictionary SET.

        Only checks the exact form — prefix-stripped variants are handled
        at the SpellService level.  Returns False on any Redis error so the
        caller can fall through to hunspell.
        """
        if not self._client:
            return False
        dict_key = _DICT_KEY.format(lang=language)
        try:
            return bool(self._client.sismember(dict_key, word))
        except Exception as exc:
            logger.warning("Redis SISMEMBER failed: %s", exc)
            return False

    def dict_size(self, language: str) -> int:
        """Return the number of words stored in the Redis SET."""
        if not self._client:
            return 0
        try:
            return self._client.scard(_DICT_KEY.format(lang=language))
        except Exception:
            return 0
