"""Adversarial auditors. Each returns a CheckResult; aggregation happens in audit.py.

Deterministic checks (citation, numerical, exclusion, contradiction) run first; the coverage
auditor calls the configured Gemini model only for material policy claims and has an offline heuristic.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from app.models.pitch import AuditStatus, CheckResult, Claim, ClaimType
from app.models.policy import CoverageStatus, FeatureFact, SourceRef
from app.services.llm import LLMQuotaExceeded, LLMService, LLMUnavailable
from app.utils.logging import get_logger
from app.utils.numerals import durations_to_days, extract_numbers

log = get_logger(__name__)

_STOP = {"the", "and", "for", "with", "that", "this", "from", "policy", "cover", "covered", "covers", "under", "your", "their", "are", "is", "of", "to", "in", "on", "a", "an", "as", "per", "by", "or", "be", "at", "up", "subject", "brochure", "insurer", "employees", "employee", "plan"}
_WORD = re.compile(r"[a-z][a-z\-]{2,}")
_POSITIVE = re.compile(r"\b(covered|covers|cover|includes|included|pays|payable|available|provides|offers|reimburses?|unlimited|restore[sd]?|up to|at actuals)\b", re.IGNORECASE)
_NEGATIVE = re.compile(r"\b(not covered|excluded|exclusion|not payable|does not cover|no cover|isn't covered|not included)\b", re.IGNORECASE)


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def _overlap(claim: str, sources: list[str]) -> float:
    cw = _words(claim)
    if not cw:
        return 0.0
    blob = " ".join(sources).lower()
    sw = set(_WORD.findall(blob))
    # stem-lite: prefix match on 5 chars
    hits = 0
    for w in cw:
        if w in sw or any(x.startswith(w[:5]) for x in sw if len(w) >= 5):
            hits += 1
    return hits / len(cw)


# ---------------- citation ----------------
def citation_check(claim: Claim, sources: list[SourceRef], missing_ids: list[str]) -> CheckResult:
    if claim.claim_type != ClaimType.POLICY:
        return CheckResult(check="citation", passed=None, status=AuditStatus.NOT_APPLICABLE, detail="Non-policy claim; citation not required.")
    if not sources:
        return CheckResult(check="citation", passed=False, status=AuditStatus.NOT_FOUND, detail="Policy claim has no evidence citation." + (f" Unknown chunk ids: {missing_ids}" if missing_ids else ""))
    wrong = [s.chunk_id for s in sources if claim.policy_id and s.policy_id != claim.policy_id]
    if wrong:
        return CheckResult(check="citation", passed=False, status=AuditStatus.CONTRADICTED, detail=f"Cited evidence belongs to a different policy: {wrong}")
    ov = _overlap(claim.claim_text, [s.source_text for s in sources])
    if ov < 0.15:
        return CheckResult(check="citation", passed=False, status=AuditStatus.UNCERTAIN, detail=f"Cited text shares little content with the claim (overlap {ov:.0%}).")
    return CheckResult(check="citation", passed=True, status=AuditStatus.SUPPORTED, detail=f"Citation resolves to {sources[0].policy_name or sources[0].policy_id} p.{sources[0].page}" + (f", {sources[0].section}" if sources[0].section else "") + f" (term overlap {ov:.0%}).")


# ---------------- numerical ----------------
def _num_sets(text: str) -> dict[str, set]:
    ex = extract_numbers(text)
    return {
        "money": {round(v) for v in ex.money},
        "percent": {round(v, 2) for v in ex.percents},
        "days": {round(d) for d in durations_to_days(ex.durations)},
        "mult": {round(v, 2) for v in ex.multipliers},
        "flags": ({"unlimited"} if ex.unlimited else set()) | ({"upto_si"} if ex.up_to_sum_insured else set()),
    }


def numerical_check(claim: Claim, sources: list[SourceRef], condition_texts: list[str]) -> CheckResult:
    if claim.claim_type != ClaimType.POLICY:
        return CheckResult(check="numerical", passed=None, status=AuditStatus.NOT_APPLICABLE, detail="Non-policy claim.")
    cs = _num_sets(claim.claim_text)
    if not any(cs.values()):
        return CheckResult(check="numerical", passed=None, status=AuditStatus.NOT_APPLICABLE, detail="No numbers in claim.")
    if not sources:
        return CheckResult(check="numerical", passed=False, status=AuditStatus.NOT_FOUND, detail="Numbers stated without evidence.")
    src_text = " ".join([s.source_text for s in sources] + condition_texts)
    ss = _num_sets(src_text)
    missing: list[str] = []
    conflicting: list[str] = []
    for kind in ("money", "percent", "days", "mult", "flags"):
        for v in cs[kind]:
            if v in ss[kind]:
                continue
            # lakh/rounding tolerance for money (e.g. 2.5 lakh vs 2,50,000)
            if kind == "money" and any(abs(v - w) <= max(1, 0.005 * w) for w in ss[kind]):
                continue
            if kind == "days" and any(abs(v - w) <= max(2, 0.03 * w) for w in ss[kind]):
                continue  # month/year conversions (36 months ~ 3 years)
            label = {"money": "INR ", "percent": "", "days": "", "mult": "", "flags": ""}[kind] + (f"{v}%" if kind == "percent" else f"{v} days" if kind == "days" else f"{v}x" if kind == "mult" else str(v))
            if ss[kind]:
                conflicting.append(label)
            else:
                missing.append(label)
    if conflicting:
        return CheckResult(check="numerical", passed=False, status=AuditStatus.CONTRADICTED, detail=f"Claim figures {conflicting} do not match the cited evidence figures.")
    if missing:
        return CheckResult(check="numerical", passed=False, status=AuditStatus.NOT_FOUND, detail=f"Claim figures {missing} are not present in the cited evidence.")
    return CheckResult(check="numerical", passed=True, status=AuditStatus.SUPPORTED, detail="All figures in the claim appear in the cited evidence.")


# ---------------- exclusion ----------------
def exclusion_check(claim: Claim, fact: FeatureFact | None, exclusion_chunks: list[SourceRef]) -> CheckResult:
    if claim.claim_type != ClaimType.POLICY:
        return CheckResult(check="exclusion", passed=None, status=AuditStatus.NOT_APPLICABLE, detail="Non-policy claim.")
    positive = bool(_POSITIVE.search(claim.claim_text)) and not _NEGATIVE.search(claim.claim_text)
    if not positive:
        return CheckResult(check="exclusion", passed=True, status=AuditStatus.SUPPORTED, detail="Claim does not assert positive coverage.")
    if fact is not None and fact.coverage_status == CoverageStatus.EXCLUDED:
        return CheckResult(check="exclusion", passed=False, status=AuditStatus.CONTRADICTED, detail=f"Policy extraction marks '{fact.feature}' as EXCLUDED for this policy: {fact.value or ''}")
    cw = _words(claim.claim_text) - {"treatment", "expenses", "hospitalisation", "hospitalization", "medical"}
    hits = []
    for ref in exclusion_chunks:
        ew = _words(ref.source_text)
        shared = cw & ew
        if len(shared) >= 2 and len(shared) / max(1, len(cw)) >= 0.3:
            hits.append((ref, shared))
    if hits:
        ref, shared = hits[0]
        return CheckResult(check="exclusion", passed=False, status=AuditStatus.UNCERTAIN, detail=f"An exclusion clause (p.{ref.page}) mentions the same subject ({', '.join(sorted(shared)[:4])}); confirm the benefit is not excluded.")
    detail = "No conflicting exclusion found."
    if fact is not None and fact.coverage_status == CoverageStatus.CONDITIONAL and fact.conditions and not re.search(r"subject to|provided|waiting|only|condition|variant|up to|co-?pay|deductible", claim.claim_text, re.IGNORECASE):
        return CheckResult(check="exclusion", passed=False, status=AuditStatus.PARTIALLY_SUPPORTED, detail=f"Benefit is conditional in the brochure but the claim states it unconditionally. Condition: {fact.conditions[0][:140]}")
    if fact is not None and fact.is_add_on and not re.search(r"add-?on|optional|rider", claim.claim_text, re.IGNORECASE):
        return CheckResult(check="exclusion", passed=False, status=AuditStatus.PARTIALLY_SUPPORTED, detail="Benefit is an optional add-on in the brochure but the claim presents it as included.")
    return CheckResult(check="exclusion", passed=True, status=AuditStatus.SUPPORTED, detail=detail)


# ---------------- contradiction ----------------
def contradiction_check(claim: Claim, fact: FeatureFact | None, all_claims: list[Claim]) -> CheckResult:
    if claim.claim_type != ClaimType.POLICY:
        return CheckResult(check="contradiction", passed=None, status=AuditStatus.NOT_APPLICABLE, detail="Non-policy claim.")
    positive = bool(_POSITIVE.search(claim.claim_text)) and not _NEGATIVE.search(claim.claim_text)
    if fact is not None and positive and fact.coverage_status in {CoverageStatus.NOT_FOUND, CoverageStatus.UNKNOWN}:
        return CheckResult(check="contradiction", passed=False, status=AuditStatus.UNCERTAIN, detail=f"Comparison matrix has '{fact.feature}' as {fact.coverage_status.value} for this policy; the claim asserts coverage.")
    # cross-claim numeric consistency on the same feature/policy
    mine = _num_sets(claim.claim_text)
    for other in all_claims:
        if other.claim_id == claim.claim_id or other.claim_type != ClaimType.POLICY or other.policy_id != claim.policy_id or not claim.feature_key or other.feature_key != claim.feature_key:
            continue
        theirs = _num_sets(other.claim_text)
        for kind in ("money", "percent", "days"):
            if mine[kind] and theirs[kind] and mine[kind] != theirs[kind] and not (mine[kind] & theirs[kind]):
                return CheckResult(check="contradiction", passed=False, status=AuditStatus.CONTRADICTED, detail=f"Conflicts with another claim on slide {other.slide} about '{claim.feature_key}' ({kind}: {sorted(mine[kind])} vs {sorted(theirs[kind])}).")
    return CheckResult(check="contradiction", passed=True, status=AuditStatus.SUPPORTED, detail="No internal contradiction detected.")


# ---------------- coverage (LLM) ----------------
class CoverageVerdict(BaseModel):
    status: Literal["SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED", "NOT_FOUND", "UNCERTAIN"]
    detail: str


COVERAGE_SYSTEM = """You are an adversarial insurance auditor. Decide whether the CLAIM is supported by the EVIDENCE excerpts from the insurer's brochure.
- SUPPORTED: the evidence states what the claim says (same benefit, same figures, same conditions or the claim mentions them).
- PARTIALLY_SUPPORTED: the benefit is there but the claim overstates it, omits a stated condition/limit/variant restriction, or generalises.
- CONTRADICTED: the evidence says something different (different figure, benefit excluded, or add-on presented as included).
- NOT_FOUND: the evidence does not address the claim.
- UNCERTAIN: cannot decide from the excerpts.
Be strict about numbers and conditions. Reply with a one-sentence detail quoting the decisive phrase."""


def coverage_check(claim: Claim, sources: list[SourceRef], condition_texts: list[str], llm: LLMService | None) -> CheckResult:
    if claim.claim_type != ClaimType.POLICY:
        return CheckResult(check="coverage", passed=None, status=AuditStatus.NOT_APPLICABLE, detail="Non-policy claim.")
    if not sources:
        return CheckResult(check="coverage", passed=False, status=AuditStatus.NOT_FOUND, detail="No evidence to verify against.")
    texts = [s.source_text for s in sources]
    if llm is None or not llm.available:
        ov = _overlap(claim.claim_text, texts + condition_texts)
        if ov >= 0.5:
            return CheckResult(check="coverage", passed=True, status=AuditStatus.SUPPORTED, detail=f"Heuristic (no LLM): strong term overlap with evidence ({ov:.0%}).")
        if ov >= 0.25:
            return CheckResult(check="coverage", passed=None, status=AuditStatus.PARTIALLY_SUPPORTED, detail=f"Heuristic (no LLM): partial term overlap with evidence ({ov:.0%}).")
        return CheckResult(check="coverage", passed=None, status=AuditStatus.UNCERTAIN, detail=f"Heuristic (no LLM): weak overlap with evidence ({ov:.0%}); manual review.")
    ev = "\n".join(f"[{i + 1}] (p.{s.page}{', ' + s.section if s.section else ''}) {s.source_text[:700]}" for i, s in enumerate(sources[:5]))
    if condition_texts:
        ev += "\nLINKED CONDITIONS/FOOTNOTES:\n" + "\n".join(f"- {c[:400]}" for c in condition_texts[:4])
    try:
        v = llm.structured(COVERAGE_SYSTEM, f"CLAIM: {claim.claim_text}\n\nEVIDENCE:\n{ev}", CoverageVerdict, purpose="coverage_audit", model=llm.audit_model)
    except LLMUnavailable:
        return coverage_check(claim, sources, condition_texts, None)
    except LLMQuotaExceeded:
        raise  # an audit must not silently downgrade to UNCERTAIN because quota ran out
    except Exception as exc:
        log.warning("Coverage auditor failed: %s", exc)
        return CheckResult(check="coverage", passed=None, status=AuditStatus.UNCERTAIN, detail=f"Coverage auditor error: {exc}")
    st = AuditStatus(v.status)
    return CheckResult(check="coverage", passed=True if st == AuditStatus.SUPPORTED else (None if st in {AuditStatus.PARTIALLY_SUPPORTED, AuditStatus.UNCERTAIN} else False), status=st, detail=v.detail)
