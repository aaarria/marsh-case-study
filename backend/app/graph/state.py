"""LangGraph state for one advisory run. Values are JSON-serialisable dicts (checkpoint-safe)."""
from __future__ import annotations

from typing import Any, TypedDict


class AdvisoryState(TypedDict, total=False):
    run_id: str
    intake: dict[str, Any]
    policy_ids: list[str]
    # client intelligence
    profile: dict[str, Any]
    research_again: bool  # advisor added context after the profile came back empty
    exposures: list[dict[str, Any]]
    # policy analysis
    features: list[str]
    matrix: dict[str, Any]
    scenarios: list[dict[str, Any]]
    outcomes: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    fits: list[dict[str, Any]]
    recommendation: dict[str, Any]
    recommendation_confirmed: bool  # close call resolved (by the advisor or by the score)
    evidence_pack: dict[str, Any]
    # pitch & audit
    pitch: dict[str, Any]
    pitch_warnings: list[str]
    audit: dict[str, Any]
    audit_history: list[dict[str, Any]]
    regenerate_feedback: str | None
    # human review & outputs
    review: dict[str, Any]
    status: str  # running | awaiting_review | approved | rejected | failed
    outputs: dict[str, str]
    error: str | None
    warnings: list[str]
