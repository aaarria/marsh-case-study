"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import get_settings
from app.utils.logging import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info(
        "Marsh advisory API starting (llm=%s, model=%s%s, research=%s, embeddings=%s)",
        "groq" if settings.uses_groq else ("gemini" if settings.llm_available else "off"),
        settings.groq_model if settings.uses_groq else settings.gemini_model,
        "" if settings.uses_groq or settings.model_free_tier_known else " [not on known free-tier list]",
        "crawler" if settings.research_available else "off",
        settings.embedding_provider,
    )
    try:
        from app.services.bootstrap import ensure_knowledge_base, knowledge_base_ready

        if knowledge_base_ready():
            log.info("Knowledge base ready")
        else:
            log.warning("Knowledge base missing or stale; rebuilding from data/policies in the background")
            ensure_knowledge_base(background=True)
    except Exception as exc:  # pragma: no cover
        log.warning("Bootstrap check failed: %s", exc)
    try:
        from app.services.runs import reconcile_orphaned_runs

        reconcile_orphaned_runs()
    except Exception as exc:  # pragma: no cover
        log.warning("Orphaned-run reconcile failed: %s", exc)
    yield


app = FastAPI(title="Marsh Evidence-First Insurance Advisory API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    # Vercel previews plus any localhost port, so a dev frontend on 3000/3457/… works without editing .env.
    allow_origin_regex=r"https://.*\.vercel\.app|http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal error", "type": type(exc).__name__})


@app.get("/")
def root():
    return {"service": "marsh-advisory-api", "docs": "/docs", "health": "/api/health"}
