"""Evidence contract: a retrieved/extracted fact is valid only with provenance.

A normalized value without the original source span is invalid. Page numbers and policy ids
come from retrieved chunk metadata, never from the model.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from app.models.policy import (
    AvailabilityMode,
    Chunk,
    CoverageStatus,
    FeatureFact,
    PolicyDocument,
    SourceRef,
)
from app.utils.numerals import extract_numbers

EVIDENCE_SCHEMA_VERSION = "evidence-v1"

# Keep in lockstep with EXTRACTION_SYSTEM in extraction.py (hashed there; this is the schema id).
def prompt_hash(system_prompt: str) -> str:
    return hashlib.sha1(system_prompt.encode("utf-8")).hexdigest()[:16]


def fold_span(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).strip().lower()


def span_in_text(span: str, text: str) -> bool:
    if not span or not text:
        return False
    return fold_span(span) in fold_span(text)


def availability_mode(status: CoverageStatus, is_add_on: bool) -> AvailabilityMode:
    if status == CoverageStatus.ADD_ON or is_add_on:
        return AvailabilityMode.OPTIONAL_ADD_ON
    if status == CoverageStatus.EXCLUDED:
        return AvailabilityMode.EXCLUDED
    if status == CoverageStatus.CONDITIONAL:
        return AvailabilityMode.CONDITIONAL
    if status in {CoverageStatus.NOT_FOUND, CoverageStatus.UNKNOWN, CoverageStatus.REVIEW_REQUIRED}:
        return AvailabilityMode.NOT_FOUND
    return AvailabilityMode.BASE_POLICY


def retrieval_method_for(bm25_rank: int | None, dense_rank: int | None, reranked: bool) -> str:
    if reranked:
        return "hybrid_rrf_rerank"
    if bm25_rank is not None and dense_rank is not None:
        return "hybrid_rrf"
    if bm25_rank is not None:
        return "bm25"
    if dense_rank is not None:
        return "dense"
    return "typed_chunk"


def source_ref_from_chunk(
    chunk: Chunk,
    relevance: float | None,
    *,
    insurer_name: str | None = None,
    source_document: str | None = None,
    retrieval_method: str | None = None,
) -> SourceRef:
    ref = SourceRef.from_chunk(chunk, relevance)
    ref.insurer_name = insurer_name or getattr(chunk, "insurer_name", None)
    ref.product_name = chunk.policy_name
    ref.source_document = source_document or chunk.source_document
    ref.retrieval_method = retrieval_method
    return ref


def attach_numeric_fields(fact: FeatureFact) -> FeatureFact:
    """Fill typed numeric fields without replacing original clause text."""
    limit_src = fact.limit_original_text or fact.limit
    if limit_src:
        fact.limit_original_text = limit_src
        nums = extract_numbers(limit_src)
        if nums.money:
            fact.limit_numeric = nums.money[0]
            fact.unit = fact.unit or "INR"
        elif nums.percents:
            fact.limit_numeric = nums.percents[0]
            fact.unit = fact.unit or "percent"
        elif nums.unlimited:
            fact.unit = fact.unit or "unlimited"
        elif nums.up_to_sum_insured:
            fact.unit = fact.unit or "sum_insured"
        if nums.durations and not fact.waiting_period:
            fact.waiting_period = limit_src
        elif nums.durations and fact.limit_numeric is None:
            val, unit = nums.durations[0]
            fact.limit_numeric = val
            fact.unit = fact.unit or ("days" if unit == "day" else "months" if unit == "month" else unit)

    wait_src = fact.waiting_period
    if wait_src:
        nums = extract_numbers(wait_src)
        if nums.durations:
            val, unit = nums.durations[0]
            if unit == "day":
                fact.waiting_period_days = val
            elif unit == "month":
                fact.waiting_period_months = val
                fact.waiting_period_days = val * 30
            elif unit == "year":
                fact.waiting_period_months = val * 12
                fact.waiting_period_days = val * 365
            if not fact.unit:
                fact.unit = "days" if unit == "day" else "months" if unit == "month" else "years"

    if fact.copay:
        nums = extract_numbers(fact.copay)
        if nums.percents:
            fact.copay_percent = nums.percents[0]

    if fact.deductible:
        nums = extract_numbers(fact.deductible)
        if nums.money:
            fact.deductible_amount = nums.money[0]
    return fact


def validate_extraction(
    policy_id: str,
    feature: str,
    ext_status: CoverageStatus,
    evidence_ids: list[str],
    quote: str | None,
    items: list[tuple[str, Chunk, float | None]],
    *,
    feature_keywords: tuple[str, ...] = (),
) -> tuple[CoverageStatus, list[tuple[str, Chunk, float | None]], str | None, str | None]:
    """Return (status, cited items, original_quote, notes).

    Invented evidence ids, invented quotes, and policy/page mismatches are rejected.
    NOT_FOUND is never rewritten to EXCLUDED.
    """
    by_id = {eid: (ch, sc) for eid, ch, sc in items}
    cited: list[tuple[str, Chunk, float | None]] = []
    for eid in evidence_ids:
        hit = by_id.get(eid)
        if not hit:
            continue
        ch, sc = hit
        if ch.policy_id != policy_id:
            continue
        cited.append((eid, ch, sc))

    if ext_status == CoverageStatus.NOT_FOUND:
        return CoverageStatus.NOT_FOUND, [], None, None

    if not cited:
        return (
            CoverageStatus.NOT_FOUND,
            [],
            None,
            "Downgraded to NOT_FOUND: extraction cited no retrieved evidence passages.",
        )

    if quote:
        if not any(span_in_text(quote, ch.source_text) for _, ch, _ in cited):
            return (
                CoverageStatus.NOT_FOUND,
                [],
                None,
                "Downgraded to NOT_FOUND: quoted span is not present in the cited retrieved chunk.",
            )
        original_quote = quote
    else:
        original_quote = cited[0][1].source_text

    if feature_keywords:
        blob = " ".join(ch.source_text for _, ch, _ in cited)
        strong = [k for k in feature_keywords if not re.search(r"\d", k)]
        if strong and not any(k.lower() in blob.lower() for k in strong):
            return (
                CoverageStatus.REVIEW_REQUIRED,
                cited,
                original_quote,
                "REVIEW_REQUIRED: cited chunk does not mention the feature keywords.",
            )

    return ext_status, cited, original_quote, None


def stamp_fact(
    fact: FeatureFact,
    *,
    doc: PolicyDocument | None,
    extracted_at: str | None = None,
    schema_version: str = EVIDENCE_SCHEMA_VERSION,
    prompt_version: str | None = None,
    model: str | None = None,
    retrieval_method: str | None = None,
    evidence_confidence: float | None = None,
) -> FeatureFact:
    fact.insurer_name = (doc.insurer if doc else None) or fact.insurer_name
    fact.product_name = (doc.policy_name if doc else None) or fact.product_name
    fact.source_document = (doc.file_name if doc else None) or fact.source_document
    fact.extracted_at = extracted_at or datetime.now(timezone.utc).isoformat()
    fact.schema_version = schema_version
    fact.prompt_version = prompt_version
    fact.model = model
    if retrieval_method:
        fact.retrieval_method = retrieval_method
    if evidence_confidence is not None:
        fact.evidence_confidence = evidence_confidence
    fact.availability_mode = availability_mode(fact.coverage_status, fact.is_add_on)
    fact.add_on_required = fact.is_add_on or fact.coverage_status == CoverageStatus.ADD_ON
    if fact.sources:
        src = fact.sources[0]
        fact.source_page = src.page
        fact.source_section = src.section
        fact.source_chunk_id = src.chunk_id
        if fact.original_quote is None:
            fact.original_quote = src.source_text
    attach_numeric_fields(fact)
    if fact.limit_numeric is not None and not (fact.original_quote or fact.limit_original_text or fact.limit):
        fact.coverage_status = CoverageStatus.NOT_FOUND
        fact.notes = (fact.notes or "") + " Invalid: numeric value without source text."
        fact.limit_numeric = None
    return fact
