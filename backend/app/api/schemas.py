"""Request / response schemas for the API (input validation lives here)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.client import ClientIntake


class AnalyzeRequest(ClientIntake):
    pass


class AnalyzeResponse(BaseModel):
    run_id: str
    status: str


class SlideIn(BaseModel):
    title: str = Field(max_length=200)
    subtitle: str | None = Field(default=None, max_length=300)
    bullets: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    footnote: str | None = None
    layout: str = "bullets"


class AuditPreviewRequest(BaseModel):
    slides: list[SlideIn] = Field(min_length=1, max_length=6)


class RewriteRequest(BaseModel):
    """Inline AI edit of one bullet. The bullet is sent as currently shown (it may be an unsaved draft)."""

    slide_number: int = Field(ge=1, le=6)
    instruction: str = Field(min_length=2, max_length=500)
    text: str = Field(min_length=1, max_length=600)
    kind: str = Field(default="policy", max_length=32)
    source_chunk_ids: list[str] = Field(default_factory=list, max_length=12)
    source_urls: list[str] = Field(default_factory=list, max_length=12)


class AnswerRequest(BaseModel):
    """Answer to the question a run is paused on. `action` is validated against the question's options server-side."""

    action: str = Field(min_length=1, max_length=64)
    # review question
    slides: list[SlideIn] | None = Field(default=None, max_length=6)
    feedback: str | None = Field(default=None, max_length=2000)
    note: str | None = Field(default=None, max_length=1000)
    reviewer: str | None = Field(default=None, max_length=120)
    # context question
    industry: str | None = Field(default=None, max_length=120)
    geography: str | None = Field(default=None, max_length=120)
    employee_count: int | None = Field(default=None, ge=1, le=5_000_000)
    advisor_notes: str | None = Field(default=None, max_length=2000)
    client_priorities: list[str] | None = Field(default=None, max_length=12)
