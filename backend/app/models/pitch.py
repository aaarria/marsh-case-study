"""Pitch, claims, audit models."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.models.policy import SourceRef


class SlideBullet(BaseModel):
    text: str
    source_chunk_ids: list[str] = Field(default_factory=list)  # brochure chunks behind a policy statement
    source_urls: list[str] = Field(default_factory=list)  # web pages behind a company statement
    policy_id: str | None = None
    kind: str = "policy"  # policy | company | marsh | recommendation | assumption


class Slide(BaseModel):
    slide_number: int
    title: str
    subtitle: str | None = None
    bullets: list[SlideBullet] = Field(default_factory=list)
    footnote: str | None = None
    layout: str = "bullets"  # bullets | two_column | recommendation | map


class Pitch(BaseModel):
    pitch_id: str
    company_name: str
    recommended_policy_id: str
    slides: list[Slide]
    version: int = 1
    disclaimer: str = (
        "Benefit statements are drawn from insurer product brochures; policy wording prevails. "
        "Coverage terms, limits and waiting periods apply as per the policy document."
    )


class ClaimType(str, Enum):
    POLICY = "POLICY"
    COMPANY = "COMPANY"
    MARSH_POSITIONING = "MARSH_POSITIONING"
    RECOMMENDATION = "RECOMMENDATION"
    ASSUMPTION = "ASSUMPTION"


class Claim(BaseModel):
    claim_id: str
    claim_text: str
    claim_type: ClaimType
    slide: int
    bullet_index: int | None = None
    policy_id: str | None = None
    feature_key: str | None = None
    numbers: list[str] = Field(default_factory=list)
    material: bool = True  # material policy claims must pass the evidence gate


class AuditStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    NOT_FOUND = "NOT_FOUND"
    UNCERTAIN = "UNCERTAIN"
    NOT_APPLICABLE = "NOT_APPLICABLE"  # non-policy claims (company / Marsh positioning)


class CheckResult(BaseModel):
    check: str  # citation | numerical | exclusion | coverage | contradiction
    passed: bool | None  # None = not applicable / inconclusive
    status: AuditStatus
    detail: str


class EvidencePassport(BaseModel):
    claim_id: str
    claim_text: str
    policy_id: str | None
    policy_name: str | None
    page: int | None
    section: str | None
    clause: str | None
    source_text: str | None
    retrieval_relevance: float | None
    verification: AuditStatus
    audit: str  # PASS | REVIEW | FAIL | N/A
    linked_conditions: list[str] = Field(default_factory=list)


class ClaimAudit(BaseModel):
    claim: Claim
    status: AuditStatus
    checks: list[CheckResult]
    evidence: list[SourceRef] = Field(default_factory=list)
    passport: EvidencePassport
    action: str  # NONE | REVIEW | CORRECT
    correction_hint: str | None = None


class AuditSummary(BaseModel):
    total_claims: int
    material_claims: int
    supported: int
    partially_supported: int
    contradicted: int
    not_found: int
    uncertain: int
    not_applicable: int
    gate: str  # PASS | FAIL | UNCERTAIN
    confidence_score: float  # 0-1 share of material claims fully supported (decision-support metric)


class AuditReport(BaseModel):
    audit_id: str
    pitch_id: str
    pitch_version: int
    summary: AuditSummary
    claims: list[ClaimAudit]
    major_policy_gaps: list[str] = Field(default_factory=list)
    generated_at: str
    note: str = "Retrieval relevance is a ranking signal, not factual accuracy."
