"""Score breakdown for a client. Same facts the graph scores. The model does not pick the winner."""
from __future__ import annotations

from app.exposure.mapping import _baseline
from app.models.client import ClientIntake, CompanyProfile
from app.models.policy import PolicyDocument, PolicyExtractionResult
from app.policy_fit.arena import run_arena
from app.policy_fit.gaps import analyse_gaps
from app.policy_fit.requirements import CLIENT_POOL, BASELINE_POOL, build_requirements
from app.policy_fit.scenarios import build_scenarios
from app.policy_fit.scoring import recommend, score_policies
from app.policy_fit.scoring_config import DEFAULT_SCORING
from app.policy_fit.stress import run_policy_check
from app.research.porter import limited_context


def _why(fit, rec) -> str:
    if rec.recommended_policy_id and fit.policy_id == rec.recommended_policy_id:
        return "Selected. Highest fit among policies that clear the decision-sufficiency gate."
    if fit.must_have_gaps:
        return "Not selected. Must-have gap: " + ", ".join(fit.must_have_gaps) + "."
    if fit.unresolved_must_haves and rec.decision_state == "incomplete_comparison":
        return "Not an automatic recommendation. Unresolved must-have: " + ", ".join(fit.unresolved_must_haves) + "."
    if not fit.decision_sufficient:
        return "Not selected. Evidence completeness is below the decision-sufficiency gate, so missing rows cannot create the winner."
    if rec.decision_state == "close_decision":
        return "Inside the close threshold. No automatic winner."
    return "Not selected. Lower fit than the decision-sufficient leader."


def explain_recommendation(
    company: str,
    priorities: list[str],
    results: dict[str, PolicyExtractionResult],
    docs: dict[str, PolicyDocument],
) -> dict:
    profile = CompanyProfile(company_name=company)
    intake = ClientIntake(company_name=company, client_priorities=priorities)
    exposures = _baseline(profile, intake)
    requirements = build_requirements(exposures)
    scenarios = build_scenarios(exposures)
    outcomes = run_arena(scenarios, results)
    gaps = analyse_gaps(scenarios, outcomes, exposures)
    policy_ids = list(results)
    fits = score_policies(policy_ids, requirements, results, gaps)
    rec = recommend(fits, gaps, docs)
    checked, _final, _fits, history = run_policy_check(policy_ids, requirements, results, fits, rec, docs, exposures, retrieve=None, llm=None)
    profile_status = {
        "company_name": company,
        "research_status": "NOT_REQUESTED",
        "research_note": "This endpoint scores policies. The graph research node owns verified company facts.",
    }
    market = limited_context(profile, profile_status["research_note"])
    by_id = {f.policy_id: f for f in fits}
    policies = {}
    for pid in policy_ids:
        name = docs[pid].policy_name if pid in docs else pid
        fit = by_id[pid]
        policies[name] = {
            "policy_id": pid,
            "score": fit.score,
            "evidence_completeness": fit.evidence_completeness,
            "confidence": fit.confidence,
            "eligible": fit.eligible,
            "decision_state": fit.decision_state,
            "must_have_gaps": fit.must_have_gaps,
            "unresolved": fit.unresolved,
            "explicit_exclusions": fit.explicit_exclusions,
            "comparison_incomplete": fit.comparison_incomplete,
            "decision_sufficient": fit.decision_sufficient,
            "unresolved_must_haves": fit.unresolved_must_haves,
            "why": _why(fit, rec),
            "components": {
                c.name: {"value": c.value, "weight": c.weight, "contribution": c.contribution, "explanation": c.explanation}
                for c in fit.components
            },
            "contributions": [row.model_dump() for row in fit.contributions],
        }
    has_client = any(r.requirement_class.value != "BASELINE" for r in requirements)
    has_base = any(r.requirement_class.value == "BASELINE" for r in requirements)
    if has_client and has_base:
        pools = {"client": CLIENT_POOL, "baseline": BASELINE_POOL}
    elif has_client:
        pools = {"client": 1.0, "baseline": 0.0}
    else:
        pools = {"client": 0.0, "baseline": 1.0}
    return {
        "client": company,
        "weight_pools": pools,
        "requirements": [r.model_dump() for r in requirements],
        "weights_sum": round(sum(r.weight for r in requirements), 6),
        "exposures": [{"title": e.title, "priority": e.priority, "feature_keys": e.feature_keys, "status": e.status.value} for e in exposures],
        "criteria": [{"requirement_id": r.requirement_id, "feature": r.feature, "class": r.requirement_class.value, "type": r.type.value, "priority_weight": r.priority_weight, "weight": round(r.weight, 4), "weight_valid": r.weight_valid, "accept_add_on": r.accept_add_on, "coverage_expectation": r.coverage_expectation.value} for r in requirements],
        "policies": policies,
        "decision_state": rec.decision_state,
        "comparison_incomplete": rec.comparison_incomplete,
        "competing_policy_ids": rec.competing_policy_ids,
        "recommended_policy": rec.policy_name if rec.recommended_policy_id else None,
        "recommended_policy_id": rec.recommended_policy_id or None,
        "fit_score": rec.fit_score,
        "unresolved_must_haves": rec.unresolved_must_haves,
        "recommendation_reason": rec.rationale[:4],
        "company_profile": profile_status,
        "market_context": market.model_dump(mode="json"),
        "provisional_recommendation": rec.model_dump(mode="json"),
        "policy_check": checked.model_dump(mode="json"),
        "recommendation_history": [event.model_dump(mode="json") for event in history],
        "final_recommendation": rec.model_dump(mode="json"),
        "scoring_config": {
            "close_threshold": DEFAULT_SCORING.close_threshold,
            "must_have_fail_below": DEFAULT_SCORING.must_have_fail_below,
            "advisor_pool": DEFAULT_SCORING.advisor_pool,
            "baseline_pool": DEFAULT_SCORING.baseline_pool,
            "min_decision_completeness": DEFAULT_SCORING.min_decision_completeness,
            "max_completeness_shortfall": DEFAULT_SCORING.max_completeness_shortfall,
            "add_on_accepted_score": DEFAULT_SCORING.add_on_accepted_score,
            "deductible_reference_inr": DEFAULT_SCORING.deductible_reference_inr,
            "deductible_formula": "score(A) = 100 * (1 - min(max(A, 0), R) / R)",
        },
    }
