"""
Multi-language Spell-Check API — FastAPI entry point.

Local dev:  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
Docker:     docker compose up --build
"""
import logging
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse
from fastapi.templating import Jinja2Templates
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
from .services.mongo_service import MongoService
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

templates = Jinja2Templates(directory="app/templates")


# ─── Language discovery ───────────────────────────────────────────────────────

def _discover_languages(dict_dir: str) -> list[str]:
    """Return language codes for every .aff/.dic pair found in dict_dir."""
    path = Path(dict_dir)
    if not path.exists():
        return []
    found = []
    for aff in path.glob("*.aff"):
        lang = aff.stem  # e.g. "he_IL"
        if (path / f"{lang}.dic").exists():
            found.append(lang)
    return sorted(found)


# ─── App factory ──────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="TinyMCE Multi-Language Spell-Check API",
        summary="Private-network spell-check backend with Hebrew, English, and Arabic support.",
        description="""
## Overview

A production-ready spell-check backend for [TinyMCE v7](https://www.tiny.cloud/).
No cloud services. No external dependencies at runtime.

**Supported languages** (configured via `SPELLCHECK_LANGUAGES`):
- `he-IL` — Hebrew (469k words, hspell 1.4)
- `en-US` / `en-GB` — English
- `ar-SA` / `ar` — Arabic

## Quick start

```bash
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/spell/check \\
     -H 'Content-Type: application/json' \\
     -d '{"text":"שלומ לכולם","language":"he-IL"}'
```

## Admin interfaces

- **Dictionary manager** → [/dictionary](/dictionary)
- **Word approvals** → [/approvals](/approvals)
- **Swagger UI** → [/docs](/docs)
        """,
        version="2.0.0",
        contact={"name": "Internal Tools Team"},
        docs_url="/docs",
        redoc_url="/redoc",
        default_response_class=ORJSONResponse,
        openapi_tags=[
            {"name": "Spell Check", "description": "Submit plain text and get misspelled words with suggestions."},
            {"name": "Dictionary", "description": "Manage the organisational word list and approval queue."},
            {"name": "System", "description": "Health check and service information."},
        ],
    )

    # ── Rate limiting ──
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # ── GZip ──
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # ── CORS ──
    origins = settings.cors_origins_list
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "Authorization"],
        max_age=3600,
    )

    # ── MongoDB (optional) ──
    mongo_svc = MongoService(uri=settings.mongo_uri, db_name=settings.mongo_db)
    app.state.mongo_service = mongo_svc

    # ── Dictionary service ──
    app.state.dict_service = DictionaryService(
        dict_path=settings.custom_dict_path,
        mongo_service=mongo_svc if mongo_svc.is_available() else None,
    )

    # ── Language discovery ──
    all_available = _discover_languages(settings.hunspell_dict_dir)
    enabled = settings.enabled_languages
    if enabled:
        # Filter to only the explicitly requested languages
        norm = {lang.replace("-", "_") for lang in enabled}
        active_langs = [lang for lang in all_available if lang in norm]
        if not active_langs:
            logger.warning(
                "SPELLCHECK_LANGUAGES=%s matched no dictionary files in %s. "
                "Available: %s. Falling back to all.",
                settings.languages,
                settings.hunspell_dict_dir,
                all_available,
            )
            active_langs = all_available
    else:
        active_langs = all_available

    # Store display-friendly language codes for the GUI (he_IL → he-IL)
    app.state.available_languages = [lang.replace("_", "-") for lang in active_langs]
    logger.info("Active languages: %s", active_langs)

    # ── Spell services (one per language) ──
    default_lang_fs = settings.default_language.replace("-", "_")
    spell_services: dict[str, SpellService] = {}

    for lang in active_langs:
        svc = SpellService(dict_dir=settings.hunspell_dict_dir, language=lang)
        if svc.is_available():
            spell_services[lang] = svc
            bcp47 = lang.replace("_", "-")
            spell_services[bcp47] = svc

    # Ensure the default language is reachable
    if default_lang_fs not in spell_services and spell_services:
        first_key = next(iter(spell_services))
        spell_services[default_lang_fs] = spell_services[first_key]

    # ── Keep app.state.spell_service for backwards compat ──
    app.state.spell_service = spell_services.get(
        default_lang_fs, next(iter(spell_services.values())) if spell_services else None
    )
    app.state.spell_services = spell_services

    # ── Routers ──
    app.include_router(spell_router.router, prefix="/spell", tags=["Spell Check"])
    app.include_router(dict_router.router, prefix="/dictionary", tags=["Dictionary"])

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
    )
    async def health(req: Request) -> HealthResponse:
        spell_svc = req.app.state.spell_service
        dict_svc = req.app.state.dict_service
        mongo = req.app.state.mongo_service
        return HealthResponse(
            status="ok",
            hunspell_available=spell_svc.is_available() if spell_svc else False,
            language=settings.default_language,
            custom_dict_words=dict_svc.count(),
            storage_backend="mongodb" if mongo.is_available() else "json",
            pending_approvals=mongo.pending_count() if mongo.is_available() else 0,
        )

    # ── Approvals GUI ──
    @app.get("/approvals", include_in_schema=False)
    async def approvals_page(req: Request):
        mongo = req.app.state.mongo_service
        pending_count = mongo.pending_count() if mongo.is_available() else 0
        languages = getattr(req.app.state, "available_languages", [])
        return templates.TemplateResponse(
            "approvals.html",
            {
                "request": req,
                "pending_count": pending_count,
                "languages": languages,
                "mongo_available": mongo.is_available(),
            },
        )

    # ── Documentation root ──
    @app.get("/", include_in_schema=False)
    async def root(req: Request):
        accept = req.headers.get("accept", "application/json")
        if "text/html" in accept:
            mongo = req.app.state.mongo_service
            languages = getattr(req.app.state, "available_languages", [])
            return templates.TemplateResponse(
                "index.html",
                {
                    "request": req,
                    "languages": languages,
                    "mongo_available": mongo.is_available(),
                    "storage_backend": "MongoDB" if mongo.is_available() else "JSON file",
                    "version": "2.0.0",
                },
            )
        return {"message": "TinyMCE Spell-Check API — docs at /docs, admin at /dictionary and /approvals"}

    logger.info(
        "Startup complete | languages=%s | mongo=%s | cors=%s",
        active_langs,
        mongo_svc.is_available(),
        settings.cors_origins_list,
    )

    return app


app = create_app()
