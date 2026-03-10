"""
Hebrew Spell-Check API — FastAPI entry point.

Local dev:
  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Docker:
  docker compose up --build

Swagger UI: http://localhost:8000/docs
"""
import logging
import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse
from pythonjsonlogger import jsonlogger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from .config import settings
from .limiter import limiter
from .models.schemas import HealthResponse
from .routes import dictionary as dict_router
from .routes import spell as spell_router
from .services.dictionary_service import DictionaryService
from .services.redis_service import RedisService
from .services.spell_service import SpellService


# ─── Logging ──────────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        jsonlogger.JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    root.handlers = [handler]


_setup_logging()
logger = logging.getLogger(__name__)


# ─── App factory ──────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="Hebrew Spell-Check API",
        summary="Internal Hebrew spell-check service for TinyMCE v7.",
        description="""
## Overview

A **private-network** spell-check backend using [spylls](https://github.com/zverok/spylls)
(pure-Python Hunspell) and a bundled `he_IL` Hebrew dictionary.

No cloud services. No external dependencies at runtime.

## Quick test

```bash
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/spell/check \\
     -H 'Content-Type: application/json' \\
     -d '{"text":"שלומ לכולם","language":"he-IL"}'
```

## TinyMCE integration

```js
tinymce.init({
  external_plugins: { hebrewspellcheck: '/plugin/hebrewspellcheck/plugin.js' },
  toolbar: 'hebrewspellcheck hebrewspellcheck_clear hebrewspellcheck_toggle_auto hebrewspellcheck_dictionary | bold italic',
  hebrewspellcheck_api_url: 'http://localhost:8000',
  extended_valid_elements: 'span[class|data-word|data-suggestions]',
  browser_spellcheck: false,
});
```
        """,
        version="1.1.0",
        contact={"name": "Internal Tools Team"},
        docs_url="/docs",
        redoc_url="/redoc",
        # Use orjson for all JSON responses — 3-5× faster serialisation
        default_response_class=ORJSONResponse,
        openapi_tags=[
            {
                "name": "Spell Check",
                "description": "Submit plain text and receive a list of misspelled Hebrew words with suggestions.",
            },
            {
                "name": "Dictionary",
                "description": (
                    "Manage the organisational word list. "
                    "Words added here are always accepted — even if Hunspell flags them."
                ),
            },
            {
                "name": "System",
                "description": "Health check and service information.",
            },
        ],
    )

    # ── Rate limiting ──
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # ── GZip compression (helps large responses) ──
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # ── CORS ──
    # NOTE: allow_credentials=True is incompatible with allow_origins=["*"].
    # Browsers (and Swagger "Try it out") reject credentialed wildcard CORS.
    # We use allow_credentials=False which is correct for a public JSON API.
    origins = settings.cors_origins_list
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "Authorization"],
        max_age=3600,
    )

    # ── Redis (connect + pre-load dictionary) ──
    redis_svc = RedisService()
    redis_svc.connect(settings.redis_url)
    if redis_svc.is_available():
        dic_file = (
            f"{settings.hunspell_dict_dir}/{settings.default_language.replace('-', '_')}.dic"
        )
        redis_svc.load_dictionary(dic_file, settings.default_language.replace("-", "_"))
    app.state.redis_service = redis_svc

    # ── Services (singletons on app.state) ──
    app.state.spell_service = SpellService(
        dict_dir=settings.hunspell_dict_dir,
        language=settings.default_language,
        redis_svc=redis_svc,
    )
    app.state.dict_service = DictionaryService(
        dict_path=settings.custom_dict_path
    )

    # ── Routers ──
    app.include_router(spell_router.router,  prefix="/spell",      tags=["Spell Check"])
    app.include_router(dict_router.router,   prefix="/dictionary", tags=["Dictionary"])

    # ── Global error handler ──
    @app.exception_handler(Exception)
    async def _unhandled(req: Request, exc: Exception) -> ORJSONResponse:
        logger.exception("Unhandled error: %s", exc)
        return ORJSONResponse(status_code=500, content={"detail": "Internal server error"})

    # ── Health ──
    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["System"],
        summary="Service health check",
        description=(
            "Returns `status: ok` when the service is running.  "
            "`hunspell_available: true` confirms the Hebrew dictionary is loaded."
        ),
    )
    async def health(req: Request) -> HealthResponse:
        spell_svc:  SpellService      = req.app.state.spell_service
        dict_svc:   DictionaryService = req.app.state.dict_service
        redis_svc:  RedisService      = req.app.state.redis_service
        lang_key = settings.default_language.replace("-", "_")
        return HealthResponse(
            status="ok",
            hunspell_available=spell_svc.is_available(),
            language=settings.default_language,
            custom_dict_words=dict_svc.count(),
            redis_available=redis_svc.is_available(),
            redis_dict_words=redis_svc.dict_size(lang_key),
        )

    @app.get("/", tags=["System"], include_in_schema=False)
    async def root() -> dict:
        return {"message": "Hebrew Spell-Check API — Swagger UI at /docs"}

    logger.info(
        "Startup complete | spell_engine=%s | lang=%s | redis=%s | cors=%s",
        app.state.spell_service.is_available(),
        settings.default_language,
        app.state.redis_service.is_available(),
        settings.cors_origins_list,
    )

    return app


app = create_app()
