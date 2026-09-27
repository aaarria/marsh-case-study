"""Policy Check. A challenge layer. The Phase 2 scorer remains the only source of fit and the winner.

The challenger may cite evidence and request targeted retrieval. It cannot set a score or a winner.
A material admitted fact is rescored by the same deterministic engine. Otherwise the provisional
recommendation stands.
"""
from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, Field

from app.models.client import Exposure
from app.models.fit import ClientRequirement, PolicyFitResult, Recommendation, RequirementClass
from app.models.policy import CoverageStatus, FeatureFact, PolicyDocument, PolicyExtractionResult, SourceRef
from app.policies.conditions import canonicalize_fact
from app.policies.evidence_contract import span_in_text
from app.policies.features import FEATURES
from app.policy_fit.criteria import compare_fact
from app.policy_fit.requirements import assign_weights
from app.policy_fit.scoring import recommend, score_policies
from app.policy_fit.scoring_config import DEFAULT_SCORING, ScoringConfig
from app.services.llm import LLMService

_FEATURE_QUERIES = {spec.key: spec.queries for spec in FEATURES}
_MISSING = {CoverageStatus.NOT_FOUND, CoverageStatus.UNKNOWN, CoverageStatus.REVIEW_REQUIRED}


class ChallengeClaim(BaseModel):
    policy_id: str
    requirement_id: str
    feature: str
    evidence_id: str | None = None
    quote: str | None = None
    materiality: str = "INFO"
    kind: Literal["FACT", "INTERPRETATION"] = "FACT"
    statement: str


class ScenarioCheck(BaseModel):
    scenario: str
    requirement_id: str
    feature: str
    policy_id: str
    status: str
    evidence: str | None = None
    limitation: str | None = None
    condition: str | None = None
    unresolved: bool = False
    result: str


class CheckGap(BaseModel):
    policy_id: str
    feature: str
    gap_type: Literal["EXPLICIT_GAP", "INSUFFICIENT_EVIDENCE", "CONDITIONAL_GAP", "MUST_HAVE_GAP", "COMPARISON_INCOMPLETE"]
    detail: str
    evidence: str | None = None


class RecommendationEvent(BaseModel):
    previous_policy_id: str
    reason: str
    evidence_change: list[str] = Field(default_factory=list)
    recalculated_policy_id: str
    decision_state: str
    advisor_override: str | None = None


class PolicyCheckResult(BaseModel):
    status: Literal["COMPLETED", "UNAVAILABLE"] = "COMPLETED"
    challenger_summary: str = ""
    challenges: list[ChallengeClaim] = Field(default_factory=list)
    material: bool = False
    scenarios: list[ScenarioCheck] = Field(default_factory=list)
    gaps: list[CheckGap] = Field(default_factory=list)
    sensitivity: Literal["STABLE", "SENSITIVE", "INCOMPLETE"] = "STABLE"
    sensitivity_notes: list[str] = Field(default_factory=list)
    retrieval_requests: list[str] = Field(default_factory=list)
    admitted_evidence: list[str] = Field(default_factory=list)
    recalculated: bool = False
    provisional_policy_id: str = ""
    final_policy_id: str = ""
    decision_state: str = ""
    note: str = ""


RetrieveFn = Callable[[str, str], list[SourceRef]]


def run_policy_check(
    policy_ids: list[str],
    requirements: list[ClientRequirement],
    results: dict[str, PolicyExtractionResult],
    fits: list[PolicyFitResult],
    recommendation: Recommendation,
    docs: dict[str, PolicyDocument],
    exposures: list[Exposure] | None = None,
    *,
    retrieve: RetrieveFn | None = None,
    llm: LLMService | None = None,
    config: ScoringConfig | None = None,
) -> tuple[PolicyCheckResult, Recommendation, list[PolicyFitResult], list[RecommendationEvent]]:
    """Challenge the provisional recommendation. Return the check, the recommendation to keep, fits, and history."""
    cfg = config or DEFAULT_SCORING
    provisional_id = recommendation.recommended_policy_id
    try:
        allowed = _evidence_index(fits)
        challenges = _deterministic_challenges(fits, recommendation, cfg)
        challenges.extend(_validated_llm_challenges(llm, challenges, allowed, recommendation))
        requests = _retrieval_requests(fits, requirements)
        admitted, books = _admit(policy_ids, results, requests, retrieve, allowed)
        scenarios = _scenarios(policy_ids, requirements, books, cfg)
        gaps = _gaps(fits)
        sensitivity, notes = _sensitivity(policy_ids, requirements, books, docs, recommendation, cfg)
        material = bool(admitted)
        history: list[RecommendationEvent] = []
        final_rec = recommendation
        final_fits = fits
        recalculated = False
        if material:
            final_fits = score_policies(policy_ids, requirements, books, [], config=cfg)
            final_rec = recommend(final_fits, [], docs)
            recalculated = True
            history.append(RecommendationEvent(
                previous_policy_id=provisional_id,
                reason="Policy Check admitted validated evidence that was not in the provisional fact book.",
                evidence_change=admitted,
                recalculated_policy_id=final_rec.recommended_policy_id,
                decision_state=final_rec.decision_state,
            ))
        else:
            history.append(RecommendationEvent(
                previous_policy_id=provisional_id,
                reason="Policy Check found no material validated evidence that changes the fact book.",
                evidence_change=[],
                recalculated_policy_id=provisional_id,
                decision_state=recommendation.decision_state,
            ))
        summary = challenges[0].statement if challenges else "No evidence-backed case that another policy fits better on the current facts."
        result = PolicyCheckResult(
            status="COMPLETED",
            challenger_summary=summary,
            challenges=challenges,
            material=material,
            scenarios=scenarios,
            gaps=gaps,
            sensitivity=sensitivity,
            sensitivity_notes=notes,
            retrieval_requests=requests,
            admitted_evidence=admitted,
            recalculated=recalculated,
            provisional_policy_id=provisional_id,
            final_policy_id=final_rec.recommended_policy_id,
            decision_state=final_rec.decision_state,
            note="Policy Check does not choose the winner. Recalculation uses the deterministic scorer.",
        )
        return result, final_rec, final_fits, history
    except Exception as exc:
        unavailable = PolicyCheckResult(
            status="UNAVAILABLE",
            challenger_summary="",
            provisional_policy_id=provisional_id,
            final_policy_id=provisional_id,
            decision_state=recommendation.decision_state,
            note=f"Policy Check unavailable: {exc}. The provisional recommendation was not stress-tested.",
        )
        return unavailable, recommendation, fits, []


def unavailable_check(recommendation: Recommendation, reason: str) -> PolicyCheckResult:
    return PolicyCheckResult(
        status="UNAVAILABLE",
        provisional_policy_id=recommendation.recommended_policy_id,
        final_policy_id=recommendation.recommended_policy_id,
        decision_state=recommendation.decision_state,
        note=f"Policy Check unavailable: {reason}. The provisional recommendation was not stress-tested.",
    )


def _deterministic_challenges(fits: list[PolicyFitResult], recommendation: Recommendation, cfg: ScoringConfig) -> list[ChallengeClaim]:
    leader_id = recommendation.recommended_policy_id
    leader = next((fit for fit in fits if fit.policy_id == leader_id), None)
    if leader is None:
        return []
    claims: list[ChallengeClaim] = []
    for other in fits:
        if other.policy_id == leader.policy_id:
            continue
        by_feature = {row.feature: row for row in other.contributions}
        for row in leader.contributions:
            peer = by_feature.get(row.feature)
            if peer is None or peer.criterion_score is None or row.criterion_score is None:
                continue
            if peer.criterion_score < row.criterion_score + cfg.challenge_score_margin:
                continue
            if not peer.source_chunk_id and not peer.evidence:
                continue
            claims.append(ChallengeClaim(
                policy_id=other.policy_id,
                requirement_id=peer.requirement_id,
                feature=peer.feature,
                evidence_id=peer.source_chunk_id,
                quote=peer.evidence,
                materiality="MATERIAL" if peer.requirement_class == RequirementClass.MUST_HAVE.value else "MINOR",
                kind="FACT",
                statement=f"{other.policy_id} scores {peer.criterion_score:.0f} on {peer.feature} against {row.criterion_score:.0f}, on cited evidence.",
            ))
    return claims[:6]


def _validated_llm_challenges(llm: LLMService | None, existing: list[ChallengeClaim], allowed: dict[str, str], recommendation: Recommendation) -> list[ChallengeClaim]:
    if llm is None or not llm.available:
        return []

    class _Claim(BaseModel):
        policy_id: str
        feature: str
        evidence_id: str
        statement: str
        kind: Literal["FACT", "INTERPRETATION"] = "INTERPRETATION"

    class _Out(BaseModel):
        claims: list[_Claim] = Field(default_factory=list)

    user = "\n".join([
        f"Provisional recommendation id: {recommendation.recommended_policy_id or '(none)'}",
        "Cite only evidence ids from this list. Do not name a winner and do not give a score.",
        *[f"- {eid}: {quote[:240]}" for eid, quote in list(allowed.items())[:20]],
        "Existing deterministic challenges:",
        *[c.statement for c in existing[:4]],
    ])
    try:
        out = llm.structured(
            "Challenge the provisional health-insurance recommendation using only the evidence ids provided. Never invent a benefit. Never output a fit score or a recommended policy id.",
            user,
            _Out,
            purpose="policy_check_challenger",
        )
    except Exception:
        return []
    kept: list[ChallengeClaim] = []
    for claim in out.claims:
        quote = allowed.get(claim.evidence_id)
        if quote is None:
            continue
        kept.append(ChallengeClaim(
            policy_id=claim.policy_id,
            requirement_id=claim.feature,
            feature=claim.feature,
            evidence_id=claim.evidence_id,
            quote=quote,
            materiality="INFO",
            kind=claim.kind,
            statement=claim.statement,
        ))
    return kept


def _retrieval_requests(fits: list[PolicyFitResult], requirements: list[ClientRequirement]) -> list[str]:
    wanted = {req.feature for req in requirements if req.requirement_class != RequirementClass.BASELINE}
    requests: list[str] = []
    for fit in fits:
        for row in fit.contributions:
            if row.feature in wanted and row.status in {status.value for status in _MISSING}:
                token = f"{fit.policy_id}:{row.feature}"
                if token not in requests:
                    requests.append(token)
    return requests[:2]


def _admit(
    policy_ids: list[str],
    results: dict[str, PolicyExtractionResult],
    requests: list[str],
    retrieve: RetrieveFn | None,
    allowed: dict[str, str],
) -> tuple[list[str], dict[str, PolicyExtractionResult]]:
    books = {pid: result.model_copy(deep=True) for pid, result in results.items()}
    if retrieve is None:
        return [], books
    admitted: list[str] = []
    for token in requests:
        policy_id, _, feature = token.partition(":")
        if policy_id not in policy_ids or not feature:
            continue
        try:
            refs = retrieve(policy_id, feature)
        except Exception:
            continue
        fact = _fact_from_retrieval(policy_id, feature, refs)
        if fact is None:
            continue
        current = books.get(policy_id)
        if current is None:
            continue
        previous = current.facts.get(feature)
        if previous is not None and previous.coverage_status not in _MISSING:
            continue
        facts = dict(current.facts)
        facts[feature] = fact
        books[policy_id] = current.model_copy(update={"facts": facts})
        label = fact.sources[0].chunk_id if fact.sources else feature
        admitted.append(f"{policy_id}:{feature}:{label}")
        if fact.sources:
            allowed[fact.sources[0].chunk_id] = fact.sources[0].source_text
    return admitted, books


def _fact_from_retrieval(policy_id: str, feature: str, refs: list[SourceRef]) -> FeatureFact | None:
    for ref in refs:
        if ref.policy_id != policy_id or not ref.source_text:
            continue
        if not span_in_text(ref.source_text, ref.source_text):
            continue
        lowered = ref.source_text.lower()
        if any(token in lowered for token in ("not covered", "is excluded", "are excluded", "does not cover")):
            status = CoverageStatus.EXCLUDED
        elif any(token in lowered for token in ("add-on", "add on", "optional cover", "rider")):
            status = CoverageStatus.ADD_ON
        elif any(token in lowered for token in ("covered", "payable", "we pay", "will pay")):
            status = CoverageStatus.COVERED
        else:
            continue
        fact = FeatureFact(
            policy_id=policy_id,
            feature=feature,
            coverage_status=status,
            value=ref.source_text[:400],
            original_quote=ref.source_text[:400],
            sources=[ref],
            source_chunk_id=ref.chunk_id,
            source_page=ref.page,
        )
        return canonicalize_fact(fact)
    return None


def _scenarios(policy_ids: list[str], requirements: list[ClientRequirement], books: dict[str, PolicyExtractionResult], cfg: ScoringConfig) -> list[ScenarioCheck]:
    chosen = [req for req in requirements if req.client_asked] or list(requirements)
    chosen = chosen[: cfg.max_check_scenarios]
    rows: list[ScenarioCheck] = []
    for req in chosen:
        for pid in policy_ids:
            book = books.get(pid)
            fact = book.facts.get(req.feature) if book else None
            judgement = compare_fact(req, fact, cfg)
            condition = None
            if fact and fact.condition_details:
                condition = fact.condition_details[0].text
            rows.append(ScenarioCheck(
                scenario=req.description or req.feature,
                requirement_id=req.requirement_id,
                feature=req.feature,
                policy_id=pid,
                status=judgement.status.value,
                evidence=(fact.original_quote or fact.value) if fact else None,
                limitation=fact.limit if fact else None,
                condition=condition,
                unresolved=judgement.unresolved or judgement.score is None,
                result=judgement.note,
            ))
    return rows


def _gaps(fits: list[PolicyFitResult]) -> list[CheckGap]:
    gaps: list[CheckGap] = []
    seen: set[tuple[str, str, str]] = set()
    for fit in fits:
        for row in fit.contributions:
            gap_type = _gap_type(row)
            if gap_type is None:
                continue
            key = (fit.policy_id, row.feature, gap_type)
            if key in seen:
                continue
            seen.add(key)
            evidence = row.evidence if gap_type != "INSUFFICIENT_EVIDENCE" else None
            detail = row.note or "Evidence is unavailable."
            if gap_type == "INSUFFICIENT_EVIDENCE":
                detail = f"Evidence is unavailable for {row.feature}. Absence is not an exclusion and not cover."
            gaps.append(CheckGap(policy_id=fit.policy_id, feature=row.feature, gap_type=gap_type, detail=detail, evidence=evidence))
    return gaps


def _gap_type(row) -> str | None:
    if row.comparison_incomplete and row.requirement_class == "MUST_HAVE":
        return "COMPARISON_INCOMPLETE"
    if row.must_have_gap:
        return "MUST_HAVE_GAP"
    if row.comparison_incomplete:
        return "COMPARISON_INCOMPLETE"
    if row.explicit_exclusion or row.status == "EXCLUDED":
        return "EXPLICIT_GAP"
    if row.status in {"NOT_FOUND", "UNKNOWN", "REVIEW_REQUIRED"} or row.criterion_score is None:
        return "INSUFFICIENT_EVIDENCE"
    if row.status in {"CONDITIONAL", "ADD_ON"} or row.condition_materiality in {"MATERIAL", "CRITICAL"}:
        return "CONDITIONAL_GAP"
    return None


def _sensitivity(policy_ids, requirements, books, docs, recommendation: Recommendation, cfg: ScoringConfig) -> tuple[str, list[str]]:
    if recommendation.decision_state in {"incomplete_comparison", "incomplete", "not_eligible"} or not recommendation.recommended_policy_id:
        return "INCOMPLETE", ["The provisional decision is not a single automatic winner, so sensitivity is INCOMPLETE."]
    winner = recommendation.recommended_policy_id
    notes: list[str] = []
    changed = False
    for req in requirements:
        if req.requirement_class == RequirementClass.BASELINE:
            continue
        remaining = [item.model_copy(deep=True) for item in requirements if item.requirement_id != req.requirement_id]
        if not remaining:
            continue
        rec = recommend(score_policies(policy_ids, assign_weights(remaining), books, [], config=cfg), [], docs)
        if rec.recommended_policy_id and rec.recommended_policy_id != winner:
            changed = True
            notes.append(f"Removing {req.feature} changes the deterministic winner.")
    lone = _single_brochure_features(policy_ids, requirements, books)
    for feature in lone:
        remaining = [item.model_copy(deep=True) for item in requirements if item.feature != feature]
        if len(remaining) == len(requirements):
            continue
        rec = recommend(score_policies(policy_ids, assign_weights(remaining), books, [], config=cfg), [], docs)
        if rec.recommended_policy_id and rec.recommended_policy_id != winner:
            changed = True
            notes.append(f"Removing singly-documented feature {feature} changes the deterministic winner.")
    delta = cfg.sensitivity_weight_delta
    for factor in (1 - delta, 1 + delta):
        perturbed = [item.model_copy(deep=True, update={"priority_weight": item.priority_weight * factor}) for item in requirements]
        rec = recommend(score_policies(policy_ids, assign_weights(perturbed), books, [], config=cfg), [], docs)
        if rec.recommended_policy_id and rec.recommended_policy_id != winner:
            changed = True
            notes.append(f"Priority perturbation {factor:.2f} changes the deterministic winner.")
    return ("SENSITIVE" if changed else "STABLE"), notes


def _single_brochure_features(policy_ids, requirements, books) -> list[str]:
    lone: list[str] = []
    for req in requirements:
        evidenced = []
        for pid in policy_ids:
            book = books.get(pid)
            fact = book.facts.get(req.feature) if book else None
            if fact is not None and fact.coverage_status not in _MISSING:
                evidenced.append(pid)
        if len(evidenced) == 1:
            lone.append(req.feature)
    return lone


def _evidence_index(fits: list[PolicyFitResult]) -> dict[str, str]:
    index: dict[str, str] = {}
    for fit in fits:
        for row in fit.contributions:
            if row.source_chunk_id and row.evidence:
                index[row.source_chunk_id] = row.evidence
    return index


def default_retrieve(policy_id: str, feature: str) -> list[SourceRef]:
    """Targeted per-policy retrieval. Failure returns no evidence rather than a fabricated fact."""
    from app.rag.retriever import get_retriever

    queries = list(_FEATURE_QUERIES.get(feature, (feature.replace("_", " "),)))
    evidence = get_retriever().retrieve_feature(queries, policy_id, top_k=3)
    return [SourceRef.from_chunk(hit.chunk, hit.final_score, retrieval_method="hybrid_rrf") for hit in evidence.results]
