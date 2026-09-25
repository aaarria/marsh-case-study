"""Policy fit arena, gap analysis and scoring models."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.policy import CoverageStatus, SourceRef


class Scenario(BaseModel):
    scenario_id: str
    exposure_id: str
    title: str
    description: str
    feature_keys: list[str]
    weight: float = 1.0


class ScenarioOutcome(BaseModel):
    scenario_id: str
    policy_id: str
    status: CoverageStatus
    rationale: str
    conditions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    value: float | None = None  # numeric contribution used in scoring


class PolicyGap(BaseModel):
    policy_id: str
    exposure_id: str | None
    gap_type: str  # GAP | EXCLUSION | LIMITATION | CONDITION | UNCERTAINTY
    detail: str
    severity: str = "MEDIUM"  # LOW | MEDIUM | HIGH
    sources: list[SourceRef] = Field(default_factory=list)


class FitComponent(BaseModel):
    name: str
    value: float
    weight: float
    contribution: float
    explanation: str


class PolicyFitResult(BaseModel):
    policy_id: str
    score: float  # 0-100 decision-support metric
    components: list[FitComponent]
    explanation: list[str]
    evaluated_scenarios: int
    unknown_scenarios: int
    confidence: str  # HIGH | MEDIUM | LOW based on evidence completeness
    close_call_with: list[str] = Field(default_factory=list)


class Recommendation(BaseModel):
    recommended_policy_id: str
    policy_name: str
    fit_score: float
    rationale: list[str]
    runner_up_policy_id: str | None = None
    caveats: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
