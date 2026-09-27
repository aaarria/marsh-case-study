"""Policy-side domain models: documents, chunks, source references, structured facts, comparison."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ContentType(str, Enum):
    SECTION = "section"
    CLAUSE = "clause"
    TABLE = "table"
    TABLE_ROW = "table_row"
    EXCLUSION = "exclusion"
    WAITING_PERIOD = "waiting_period"
    ELIGIBILITY = "eligibility"
    DEFINITION = "definition"
    CONDITION = "condition"  # footnotes / T&C
    ADD_ON = "add_on"
    DISCOUNT = "discount"
    PRICING = "pricing"
    MARKETING_STAT = "marketing_stat"  # insurer statistics; never coverage evidence
    LIST_ITEM = "list_item"


class CoverageStatus(str, Enum):
    COVERED = "COVERED"
    PARTIALLY_COVERED = "PARTIALLY_COVERED"
    CONDITIONAL = "CONDITIONAL"
    EXCLUDED = "EXCLUDED"
    ADD_ON = "ADD_ON"  # availability mode: optional/add-on at extra premium, not a weak COVERED
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ConditionMateriality(str, Enum):
    """How much a stated condition changes the benefit. Unlabeled text is not assigned a rank."""

    INFO = "INFO"
    MINOR = "MINOR"
    MATERIAL = "MATERIAL"
    CRITICAL = "CRITICAL"


class FactCondition(BaseModel):
    text: str
    materiality: ConditionMateriality = ConditionMateriality.INFO


class AvailabilityMode(str, Enum):
    """How the benefit is offered. Distinct from retrieval relevance and from fit score."""

    BASE_POLICY = "base_policy"
    OPTIONAL_ADD_ON = "optional_add_on"
    EXCLUDED = "excluded"
    NOT_FOUND = "not_found"
    CONDITIONAL = "conditional"


class PolicyDocument(BaseModel):
    policy_id: str
    policy_name: str
    insurer: str
    product_uin: str | None = None
    document_path: str
    file_name: str
    pages: int = 0
    variants: list[str] = Field(default_factory=list)
    page_flags: dict[int, str] = Field(default_factory=dict)  # e.g. {1: "image_only"}
    short_label: str = ""  # e.g. "Policy A"
    is_group_policy: str = "NOT_FOUND"  # brochures do not state group terms
    document_hash: str | None = None  # sha256 prefix of the source PDF bytes


class Chunk(BaseModel):
    chunk_id: str
    policy_id: str
    policy_name: str
    insurer_name: str | None = None
    source_document: str | None = None
    page_number: int
    section: str | None = None
    subsection: str | None = None
    clause: str | None = None
    content_type: ContentType = ContentType.CLAUSE
    parent_chunk_id: str | None = None
    source_text: str  # verbatim text as extracted
    search_text: str | None = None  # text with heading context prepended (used for indexing)
    footnote_refs: list[str] = Field(default_factory=list)  # chunk_ids of linked footnotes/conditions
    footnote_markers: list[str] = Field(default_factory=list)  # e.g. ["*", "(7)", "5"]
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def index_text(self) -> str:
        return self.search_text or self.source_text


class SourceRef(BaseModel):
    policy_id: str
    policy_name: str | None = None
    insurer_name: str | None = None
    product_name: str | None = None
    source_document: str | None = None
    chunk_id: str
    page: int
    section: str | None = None
    clause: str | None = None
    content_type: str | None = None
    source_text: str
    retrieval_relevance: float | None = Field(
        default=None, description="Retrieval relevance signal (rerank / fused score), NOT factual accuracy"
    )
    retrieval_method: str | None = None
    retrieval_score: float | None = None
    linked_conditions: list[str] = Field(default_factory=list, description="chunk_ids of footnotes/conditions")

    @classmethod
    def from_chunk(cls, c: Chunk, relevance: float | None = None, retrieval_method: str | None = None) -> "SourceRef":
        """The one place a brochure chunk becomes a citation. `relevance` is a retrieval signal, never accuracy."""
        score = round(relevance, 4) if relevance is not None else None
        return cls(
            policy_id=c.policy_id,
            policy_name=c.policy_name,
            insurer_name=c.insurer_name,
            product_name=c.policy_name,
            source_document=c.source_document,
            chunk_id=c.chunk_id,
            page=c.page_number,
            section=c.section,
            clause=c.clause,
            content_type=c.content_type.value,
            source_text=c.source_text,
            retrieval_relevance=score,
            retrieval_score=score,
            retrieval_method=retrieval_method,
            linked_conditions=list(c.footnote_refs),
        )


class RetrievedChunk(BaseModel):
    chunk: Chunk
    bm25_rank: int | None = None
    dense_rank: int | None = None
    dense_score: float | None = None  # cosine similarity
    fused_score: float = 0.0
    final_score: float = 0.0


class FeatureFact(BaseModel):
    """Structured, evidence-backed fact for one feature of one policy. Null / NOT_FOUND when not established."""

    policy_id: str
    feature: str
    coverage_status: CoverageStatus = CoverageStatus.NOT_FOUND
    availability_mode: AvailabilityMode = AvailabilityMode.NOT_FOUND
    value: str | None = None  # concise human-readable summary of what the policy says
    limit: str | None = None
    limit_numeric: float | None = None
    limit_original_text: str | None = None
    unit: str | None = None
    waiting_period: str | None = None
    waiting_period_days: float | None = None
    waiting_period_months: float | None = None
    deductible: str | None = None
    deductible_amount: float | None = None
    copay: str | None = None
    copay_percent: float | None = None
    eligibility: str | None = None
    sublimit: str | None = None
    exclusions: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    condition_details: list[FactCondition] = Field(default_factory=list)
    is_add_on: bool = False
    add_on_required: bool = False
    variant_scope: str | None = None  # e.g. "VIP+ only"
    coverage_tier: str | None = None  # BASE | CONDITIONAL | OPTIONAL | ADD_ON | RIDER | EXCLUDED | NOT_ESTABLISHED | REVIEW_REQUIRED
    cap_type: str | None = None
    original_quote: str | None = None
    source_page: int | None = None
    source_section: str | None = None
    source_chunk_id: str | None = None
    evidence_confidence: float | None = None
    retrieval_method: str | None = None
    insurer_name: str | None = None
    product_name: str | None = None
    source_document: str | None = None
    extracted_at: str | None = None
    schema_version: str | None = None
    prompt_version: str | None = None
    model: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    notes: str | None = None


class ComparisonCell(BaseModel):
    feature: str
    policy_id: str
    status: CoverageStatus
    display: str  # short cell text
    fact: FeatureFact


class ComparisonMatrix(BaseModel):
    features: list[str]
    policy_ids: list[str]
    cells: dict[str, dict[str, ComparisonCell]]  # feature -> policy_id -> cell


class PolicyExtractionResult(BaseModel):
    policy_id: str
    facts: dict[str, FeatureFact]  # feature -> fact
    generated_at: str
    model: str | None = None
