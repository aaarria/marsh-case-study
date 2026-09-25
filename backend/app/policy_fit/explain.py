"""Score breakdown for a client. Same path as the graph: exposures, scenarios, arena, score_all, recommend."""
from __future__ import annotations

from app.exposure.mapping import _baseline
from app.models.client import ClientIntake, CompanyProfile
from app.models.fit import PolicyFitResult
from app.models.policy import PolicyDocument, PolicyExtractionResult
from app.policy_fit.arena import run_arena
from app.policy_fit.gaps import analyse_gaps
from app.policy_fit.scenarios import build_scenarios
from app.policy_fit.scoring import recommend, score_all


def _components(fit: PolicyFitResult) -> dict:
    return {
        c.name: {"value": c.value, "weight": c.weight, "contribution": c.contribution, "explanation": c.explanation}
        for c in fit.components
    }


def explain_recommendation(
    company: str,
    priorities: list[str],
    results: dict[str, PolicyExtractionResult],
    docs: dict[str, PolicyDocument],
) -> dict:
    profile = CompanyProfile(company_name=company)
    intake = ClientIntake(company_name=company, client_priorities=priorities)
    exposures = _baseline(profile, intake)
    scenarios = build_scenarios(exposures)
    outcomes = run_arena(scenarios, results)
    gaps = analyse_gaps(scenarios, outcomes, exposures)
    policy_ids = sorted(results)
    fits = score_all(policy_ids, scenarios, outcomes, gaps)
    rec = recommend(fits, gaps, docs)
    by_id = {f.policy_id: f for f in fits}
    policies = {}
    for pid in policy_ids:
        name = docs[pid].policy_name if pid in docs else pid
        fit = by_id[pid]
        policies[name] = {"policy_id": pid, "score": fit.score, "components": _components(fit)}
    return {
        "client": company,
        "exposures": [{"title": e.title, "priority": e.priority, "feature_keys": e.feature_keys, "status": e.status.value} for e in exposures],
        "criteria": [{"scenario_id": s.scenario_id, "title": s.title, "feature_keys": s.feature_keys, "weight": round(s.weight, 3)} for s in scenarios],
        "policies": policies,
        "recommended_policy": rec.policy_name,
        "recommended_policy_id": rec.recommended_policy_id,
        "fit_score": rec.fit_score,
    }
