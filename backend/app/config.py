"""Application settings.

All secrets come from environment variables / .env. Paths default to the repo root so the
backend can run from `backend/` locally and from `/app` in Docker.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "data" / "policies").exists() or (parent / ".env.example").exists():
            return parent
    return here.parents[2]


REPO_ROOT = _find_repo_root()

# Text models whose input/output are "Free of charge" on the Gemini API Free Tier, per Google's
# official pricing page (https://ai.google.dev/gemini-api/docs/pricing, checked 2026-09-24).
# Google recommends `gemini-3.5-flash-lite` or `gemini-3.8-flash` for new projects.
# This list is informational: the app never changes the configured model, it only warns when
# GEMINI_MODEL is not a model known to be free-tier eligible.
FREE_TIER_TEXT_MODELS: frozenset[str] = frozenset(
    {
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
    }
)
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM. Gemini remains the default. When GROQ_API_KEY is set, Groq is used instead.
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default=DEFAULT_GEMINI_MODEL, alias="GEMINI_MODEL")
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    groq_model: str = Field(default="openai/gpt-oss-20b", alias="GROQ_MODEL")
    # Gemini 3.x models think by default; "low" keeps latency and free-tier token use down.
    # Allowed: minimal | low | medium | high | default (leave the model default).
    gemini_thinking_level: str = Field(default="low", alias="GEMINI_THINKING_LEVEL")
    gemini_embedding_model: str = Field(default="gemini-embedding-001", alias="GEMINI_EMBEDDING_MODEL")

    # Research: a native crawler (app/research/search.py) queries public search engines' HTML result
    # pages and reads the result pages over plain HTTP. No search API, no key, no AI call; Gemini only
    # writes the profile from the fetched text. OFF by default; enabling it is an explicit choice.
    web_research_enabled: bool = Field(default=False, alias="WEB_RESEARCH_ENABLED")

    # Retrieval. `local` (fastembed ONNX, offline, no quota) is the default; `gemini` uses
    # GEMINI_EMBEDDING_MODEL and consumes free-tier quota on every query.
    embedding_provider: str = Field(default="local", alias="EMBEDDING_PROVIDER")  # local | gemini
    reranker_enabled: bool = Field(default=True, alias="RERANKER_ENABLED")
    reranker_model: str = Field(default="Xenova/ms-marco-MiniLM-L-6-v2", alias="RERANKER_MODEL")

    # Paths
    policies_dir: str = Field(default="data/policies", alias="POLICIES_DIR")
    storage_dir: str = Field(default="storage", alias="STORAGE_DIR")
    outputs_dir: str = Field(default="outputs", alias="OUTPUTS_DIR")

    # API
    cors_origins: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")
    # Where this API is reachable from a reader's machine; used only to build the brochure-page links
    # written into exported decks (…/api/policies/{id}/document#page=N).
    public_api_url: str = Field(default="http://localhost:8000", alias="PUBLIC_API_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    # Ceiling on analyses executing at once; each one makes a burst of Gemini calls against one free-tier key.
    max_concurrent_runs: int = Field(default=3, ge=1, le=32, alias="MAX_CONCURRENT_RUNS")

    # ---- derived paths ----
    def _abs(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (REPO_ROOT / path)

    @property
    def policies_path(self) -> Path:
        return self._abs(self.policies_dir)

    @property
    def storage_path(self) -> Path:
        return self._abs(self.storage_dir)

    @property
    def outputs_path(self) -> Path:
        return self._abs(self.outputs_dir)

    @property
    def vector_index_path(self) -> Path:
        return self.storage_path / "vector_index"

    @property
    def bm25_index_path(self) -> Path:
        return self.storage_path / "bm25_index"

    @property
    def metadata_path(self) -> Path:
        return self.storage_path / "metadata"

    @property
    def sqlite_path(self) -> Path:
        return self.storage_path / "sqlite" / "marsh.db"

    @property
    def cache_path(self) -> Path:
        return self.storage_path / "cache"

    @property
    def presentations_path(self) -> Path:
        return self.outputs_path / "presentations"

    @property
    def audit_reports_path(self) -> Path:
        return self.outputs_path / "audit_reports"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_available(self) -> bool:
        return bool((self.groq_api_key or "").strip() or self.gemini_api_key)

    @property
    def uses_groq(self) -> bool:
        return bool((self.groq_api_key or "").strip())

    @property
    def research_available(self) -> bool:
        return self.web_research_enabled

    @property
    def model_free_tier_known(self) -> bool:
        """True when GEMINI_MODEL is on Google's published free-tier list (see FREE_TIER_TEXT_MODELS)."""
        return self.gemini_model.strip().removeprefix("models/") in FREE_TIER_TEXT_MODELS

    def ensure_dirs(self) -> None:
        for p in (
            self.vector_index_path,
            self.bm25_index_path,
            self.metadata_path,
            self.sqlite_path.parent,
            self.cache_path,
            self.presentations_path,
            self.audit_reports_path,
        ):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
