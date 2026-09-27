"""Evidence verification. This layer does not score, rank, or recommend a policy.

Deterministic checks run on every fact. When a language model is available, one batched
entailment call per policy may mark a fact REVIEW_REQUIRED. It cannot set a score.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.policy import CoverageStatus, FeatureFact, PolicyExtractionResult
from app.utils.logging import get_logger

log = get_logger(__name__)

_ESTABLISHED = {
    CoverageStatus.COVERED,
    CoverageStatus.PARTIALLY_COVERED,
    CoverageStatus.CONDITIONAL,
    CoverageStatus.EXCLUDED,
    CoverageStatus.ADD_ON,
}
Verdict = Literal["VERIFIED", "CONDITIONAL", "PARTIAL", "REJECTED", "REVIEW_REQUIRED", "NOT_ESTABLISHED"]


class _Verdict(BaseModel):
    feature: str
    verdict: Literal["VERIFIED", "CONDITIONAL", "PARTIAL", "REJECTED", "REVIEW_REQUIRED"]
    reason: str


class _Batch(BaseModel):
    verdicts: list[_Verdict] = Field(default_factory=list)


def verify_results(results: dict[str, PolicyExtractionResult]) -> dict[str, PolicyExtractionResult]:
    """Confirm each established fact is supported by its own policy's cited text."""
    verified: dict[str, PolicyExtractionResult] = {}
    for pid, result in results.items():
        facts = {key: _verify_fact(fact) for key, fact in result.facts.items()}
        verified[pid] = result.model_copy(update={"facts": facts})
    return verified


def verify_with_model(results: dict[str, PolicyExtractionResult], llm) -> dict[str, PolicyExtractionResult]:
    """Optional entailment. Unavailable models leave the deterministic verdict in place."""
    if llm is None or not getattr(llm, "available", False):
        return results
    updated: dict[str, PolicyExtractionResult] = {}
    for pid, result in results.items():
        facts = dict(result.facts)
        batch = _model_batch(pid, facts, llm)
        for feature, verdict, reason in batch:
            fact = facts.get(feature)
            if fact is None:
                continue
            note = f" ai_verification={verdict}. {reason}".strip()
            fact.notes = ((fact.notes or "") + note).strip()
            if verdict in {"REJECTED", "REVIEW_REQUIRED"} and fact.coverage_status in _ESTABLISHED:
                fact.coverage_status = CoverageStatus.REVIEW_REQUIRED
                fact.coverage_tier = "REVIEW_REQUIRED"
            facts[feature] = fact
        updated[pid] = result.model_copy(update={"facts": facts})
    return updated


def _verify_fact(fact: FeatureFact) -> FeatureFact:
    fact = fact.model_copy(deep=True)
    if fact.coverage_status not in _ESTABLISHED:
        return _mark(fact, "NOT_ESTABLISHED", "The supplied brochure does not establish this fact.")
    if fact.sources and all(src.policy_id and src.policy_id != fact.policy_id for src in fact.sources):
        return _mark(fact, "REVIEW_REQUIRED", "Cited evidence belongs to a different policy.", reject=True)
    quote = (fact.original_quote or fact.value or "").strip()
    texts = [src.source_text for src in fact.sources if src.source_text]
    if not quote or not texts:
        return _mark(fact, "REVIEW_REQUIRED", "An established status has no quote inside a cited passage.", reject=True)
    if not any(quote in text for text in texts):
        return _mark(fact, "REVIEW_REQUIRED", "The quote is not contained in the cited passage.", reject=True)
    pages = {src.page for src in fact.sources if src.page}
    if fact.source_page and pages and fact.source_page not in pages:
        return _mark(fact, "REVIEW_REQUIRED", "The recorded page does not match the cited passage.", reject=True)
    if fact.waiting_period_days is not None and not _days_in_quote(fact.waiting_period_days, quote):
        return _mark(fact, "REVIEW_REQUIRED", "The waiting-period number is not in the cited quote.", reject=True)
    if fact.copay_percent is not None and fact.copay_percent > 0 and str(int(fact.copay_percent)) not in quote:
        return _mark(fact, "REVIEW_REQUIRED", "The co-pay percentage is not in the cited quote.", reject=True)
    return _mark(fact, "VERIFIED", "Quote, policy, and page agree with the cited passage.")


def _days_in_quote(days: float, quote: str) -> bool:
    if days <= 0:
        return any(token in quote.lower() for token in ("zero", "day 1", "day-1", "0 day"))
    rendered = str(int(days)) if days == int(days) else str(days)
    return rendered in quote


def _mark(fact: FeatureFact, verdict: str, reason: str, reject: bool = False) -> FeatureFact:
    fact.notes = ((fact.notes or "") + f" verification={verdict} verification_mode=deterministic. {reason}").strip()
    if reject:
        fact.coverage_status = CoverageStatus.REVIEW_REQUIRED
        fact.coverage_tier = "REVIEW_REQUIRED"
    return fact


def _model_batch(pid: str, facts: dict[str, FeatureFact], llm) -> list[tuple[str, str, str]]:
    rows = []
    for feature, fact in facts.items():
        if fact.coverage_status not in _ESTABLISHED:
            continue
        quote = (fact.original_quote or fact.value or "")[:400]
        if not quote:
            continue
        rows.append(f"- feature={feature}; status={fact.coverage_status.value}; tier={fact.coverage_tier or ''}; quote={quote}")
    if not rows:
        return []
    system = (
        "You check whether a brochure quote supports the stated coverage status for that feature. "
        "Return one verdict per feature: VERIFIED, CONDITIONAL, PARTIAL, REJECTED, or REVIEW_REQUIRED. "
        "Do not score a policy, do not rank policies, and do not recommend a winner."
    )
    user = f"POLICY: {pid}\n" + "\n".join(rows[:12])
    try:
        out = llm.structured(system, user, _Batch, purpose="evidence_verification")
    except Exception as exc:
        log.warning("Evidence verification model skipped: %s", exc)
        return []
    return [(item.feature, item.verdict, item.reason) for item in out.verdicts]
