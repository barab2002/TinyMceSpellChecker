"""
Entry point for the Hebrew Spell-Check API.

Start locally:
  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Or via Docker Compose (see docker-compose.yml).
"""
import logging
import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pythonjsonlogger import jsonlogger

from .config import settings
from .models.schemas import HealthResponse
from .routes import dictionary as dict_router
from .routes import spell as spell_router
from .services.dictionary_service import DictionaryService
from .services.spell_service import SpellService


# ---------------------------------------------------------------------------
# Logging setup — structured JSON for production, plain for dev
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    fmt = jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    handler.setFormatter(fmt)
    root.handlers = [handler]


_setup_logging()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    application = FastAPI(
        title="Hebrew Spell-Check API",
        description=(
            "Internal spell-check service using Hunspell with Hebrew dictionary support. "
            "Designed for private/enterprise networks."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # --- CORS ---
    origins = settings.cors_origins_list
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # --- Services (singletons attached to app state) ---
    application.state.spell_service = SpellService(
        dict_dir=settings.hunspell_dict_dir,
        language=settings.default_language,
    )
    application.state.dict_service = DictionaryService(
        dict_path=settings.custom_dict_path
    )

    # --- Routers ---
    application.include_router(spell_router.router, prefix="/spell", tags=["Spell Check"])
    application.include_router(dict_router.router, prefix="/dictionary", tags=["Dictionary"])

    # --- Global exception handler ---
    @application.exception_handler(Exception)
    async def unhandled_exception(req: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    # --- Health endpoint ---
    @application.get("/health", response_model=HealthResponse, tags=["System"])
    async def health(req: Request):
        spell_svc: SpellService = req.app.state.spell_service
        dict_svc: DictionaryService = req.app.state.dict_service
        return HealthResponse(
            status="ok",
            hunspell_available=spell_svc.is_available(),
            language=settings.default_language,
            custom_dict_words=dict_svc.count(),
        )

    @application.get("/", tags=["System"])
    async def root():
        return {"message": "Hebrew Spell-Check API is running. See /docs for usage."}

    logger.info(
        "App created | hunspell=%s | lang=%s | cors=%s",
        application.state.spell_service.is_available(),
        settings.default_language,
        settings.cors_origins_list,
    )

    return application


app = create_app()
