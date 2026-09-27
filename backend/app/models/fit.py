"""Policy fit arena, gap analysis and scoring models."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.models.policy import CoverageStatus, SourceRef


class RequirementClass(str, Enum):
    MUST_HAVE = "MUST_HAVE"
    PREFERENCE = "PREFERENCE"
    BASELINE = "BASELINE"
    EXPOSURE = "EXPOSURE"  # hypothesis. Weight stays 0 unless exposure_pool is configured.


class CoverageExpectation(str, Enum):
    """Whether the client needs the benefit in the base policy, as an add-on, or either way."""

    BASE = "BASE"
    OPTIONAL_ADD_ON = "OPTIONAL_ADD_ON"
    EITHER = "EITHER"


class CriterionType(str, Enum):
    COVERAGE = "coverage"
    LIMIT = "limit"
    SUBLIMIT = "sublimit"
    WAITING_PERIOD = "waiting_period"
    DEDUCTIBLE = "deductible"
    COPAYMENT = "copayment"
    ELIGIBILITY = "eligibility"
    EXCLUSION = "exclusion"
    GEOGRAPHIC = "geographic"
    ADD_ON = "add_on"
    CONDITION = "condition"
    WELLNESS = "wellness"


class DecisionState(str, Enum):
    ELIGIBLE = "eligible"
    NOT_ELIGIBLE = "not_eligible"
    CLOSE_DECISION = "close_decision"
    INCOMPLETE = "incomplete"
    INCOMPLETE_COMPARISON = "incomplete_comparison"
    ADVISOR_OVERRIDE = "advisor_override"


class ClientRequirement(BaseModel):
    requirement_id: str
    description: str
    feature: str
    type: CriterionType
    priority_weight: float
    weight: float = 0.0
    requirement_class: RequirementClass
    hard_constraint: bool = False
    target_value: float | None = None
    target_unit: str | None = None
    tolerance: float | None = None
    source: str
    confidence: float = 1.0
    client_asked: bool = False
    assumption: bool = False
    accept_add_on: bool = False
    coverage_expectation: CoverageExpectation = CoverageExpectation.EITHER
    weight_valid: bool = True
    priority_status: str = "VALID"  # VALID | REVIEW_REQUIRED. Invalid priorities stay on the requirement.


class CriterionScore(BaseModel):
    requirement_id: str
    feature: str
    description: str
    requirement_class: str
    type: str
    weight: float
    criterion_score: float | None = None
    contribution: float | None = None
    status: str
    evidence: str | None = None
    source_page: int | None = None
    source_section: str | None = None
    source_chunk_id: str | None = None
    must_have_gap: bool = False
    explicit_exclusion: bool = False
    unresolved: bool = False
    comparison_incomplete: bool = False
    coverage_expectation: str = ""
    condition_materiality: str | None = None
    note: str = ""


class Scenario(BaseModel):
    scenario_id: str
    exposure_id: str
    title: str
    description: str
    feature_keys: list[str]
    weight: float = 1.0
    client_asked: bool = False  # advisor priority: silence counts. A generic baseline item does not, unless several brochures document it.


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
    score: float  # 0-100 fit on evidenced requirements only
    components: list[FitComponent]
    explanation: list[str]
    evaluated_scenarios: int
    unknown_scenarios: int
    confidence: str  # HIGH | MEDIUM | LOW from evidence completeness, not retrieval rank
    close_call_with: list[str] = Field(default_factory=list)
    evidence_completeness: float = 0.0
    eligible: bool = True
    decision_state: str = DecisionState.ELIGIBLE.value
    must_have_gaps: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    explicit_exclusions: list[str] = Field(default_factory=list)
    contributions: list[CriterionScore] = Field(default_factory=list)
    comparison_incomplete: list[str] = Field(default_factory=list)
    unresolved_must_haves: list[str] = Field(default_factory=list)
    decision_sufficient: bool = True
    client_requirements_resolved: bool = True


class AlternativeFit(BaseModel):
    """A decision-sufficient peer from the same fit list. Not a second ranking model."""

    policy_id: str
    policy_name: str
    fit_score: float
    evidence_completeness: float
    decision_state: str
    strong_matches: list[str] = Field(default_factory=list)
    trade_offs: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class Recommendation(BaseModel):
    recommended_policy_id: str
    policy_name: str
    fit_score: float
    rationale: list[str]
    runner_up_policy_id: str | None = None
    caveats: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    decision_state: str = DecisionState.ELIGIBLE.value
    competing_policy_ids: list[str] = Field(default_factory=list)
    comparison_incomplete: list[str] = Field(default_factory=list)
    unresolved_must_haves: list[str] = Field(default_factory=list)
    alternatives: list[AlternativeFit] = Field(default_factory=list)
