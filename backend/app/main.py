"""
Hebrew Spell-Check API — FastAPI entry point.

Local dev:
  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Docker:
  docker compose up --build

Swagger UI: http://localhost:8000/docs
ReDoc:      http://localhost:8000/redoc
OpenAPI JSON: http://localhost:8000/openapi.json
"""
import logging
import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pythonjsonlogger import jsonlogger

from .config import settings
from .models.schemas import HealthResponse
from .routes import dictionary as dict_router
from .routes import spell as spell_router
from .services.dictionary_service import DictionaryService
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
  toolbar: 'hebrewspellcheck hebrewspellcheck_clear | bold italic',
  hebrewspellcheck_api_url: 'http://localhost:8000',
  extended_valid_elements: 'span[class|data-word|data-suggestions]',
  browser_spellcheck: false,
});
```
        """,
        version="1.0.0",
        contact={"name": "Internal Tools Team"},
        docs_url="/docs",
        redoc_url="/redoc",
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

    # ── CORS ──
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # ── Services (singletons on app.state) ──
    app.state.spell_service = SpellService(
        dict_dir=settings.hunspell_dict_dir,
        language=settings.default_language,
    )
    app.state.dict_service = DictionaryService(
        dict_path=settings.custom_dict_path
    )

    # ── Routers ──
    app.include_router(spell_router.router,  prefix="/spell",      tags=["Spell Check"])
    app.include_router(dict_router.router,   prefix="/dictionary", tags=["Dictionary"])

    # ── Global error handler ──
    @app.exception_handler(Exception)
    async def _unhandled(req: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error: %s", exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

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
        spell_svc: SpellService      = req.app.state.spell_service
        dict_svc:  DictionaryService = req.app.state.dict_service
        return HealthResponse(
            status="ok",
            hunspell_available=spell_svc.is_available(),
            language=settings.default_language,
            custom_dict_words=dict_svc.count(),
        )

    @app.get("/", tags=["System"], include_in_schema=False)
    async def root() -> dict:
        return {"message": "Hebrew Spell-Check API. Swagger UI at /docs"}

    logger.info(
        "Startup complete | spell_engine=%s | lang=%s | cors=%s",
        app.state.spell_service.is_available(),
        settings.default_language,
        settings.cors_origins_list,
    )

    return app


app = create_app()
