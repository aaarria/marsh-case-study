"""Explainable, deterministic policy-fit scoring.

fit = 100 * clip(0.60*coverage + 0.15*evidence_strength - 0.15*exclusion_risk - 0.10*uncertainty)
Every component is reported with its weight, value, contribution and a plain-language explanation.
This is a decision-support metric, not objective truth.

Coverage is normalised over *every* scenario. A scenario the brochure says nothing about takes a
neutral 0.5 (it is neither covered nor excluded) and is additionally penalised through `uncertainty`.
Normalising over evaluated scenarios only would let a two-page marketing sheet that mentions six
benefits score full coverage and beat a brochure that actually documents thirteen.
"""
from __future__ import annotations

from app.models.fit import FitComponent, PolicyFitResult, PolicyGap, Recommendation, Scenario, ScenarioOutcome
from app.models.policy import CoverageStatus, PolicyDocument

UNKNOWN_VALUE = 0.5  # neutral prior for NOT_FOUND / UNKNOWN scenarios inside the coverage component
WEIGHTS = {"exposure_coverage": 0.60, "evidence_strength": 0.15, "exclusion_risk": -0.15, "uncertainty": -0.10}


def score_policy(policy_id: str, scenarios: list[Scenario], outcomes: list[ScenarioOutcome], gaps: list[PolicyGap]) -> PolicyFitResult:
    sc_by_id = {s.scenario_id: s for s in scenarios}
    mine = [o for o in outcomes if o.policy_id == policy_id]
    evaluated = [o for o in mine if o.value is not None]
    unknown = [o for o in mine if o.value is None]
    all_w = sum(sc_by_id[o.scenario_id].weight for o in mine) or 1.0
    coverage = sum(sc_by_id[o.scenario_id].weight * (o.value if o.value is not None else UNKNOWN_VALUE) for o in mine) / all_w if mine else 0.0

    # evidence strength: share of evaluated outcomes with sources, blended with mean retrieval relevance
    with_src = [o for o in evaluated if o.sources]
    citation_completeness = len(with_src) / len(evaluated) if evaluated else 0.0
    rel = [s.retrieval_relevance for o in with_src for s in o.sources if s.retrieval_relevance is not None]
    mean_rel = sum(rel) / len(rel) if rel else 0.0
    evidence_strength = 0.6 * citation_completeness + 0.4 * min(1.0, mean_rel)

    exclusion_risk = sum(sc_by_id[o.scenario_id].weight for o in mine if o.status == CoverageStatus.EXCLUDED) / all_w
    uncertainty = sum(sc_by_id[o.scenario_id].weight for o in unknown) / all_w

    components = [
        FitComponent(name="exposure_coverage", value=round(coverage, 3), weight=WEIGHTS["exposure_coverage"], contribution=round(WEIGHTS["exposure_coverage"] * coverage, 3),
                     explanation=f"Weighted coverage across all {len(mine)} scenarios (covered=1, conditional~0.75, partial=0.5, add-on=0.35, excluded=0; the {len(unknown)} not addressed by the brochure count a neutral 0.5)."),
        FitComponent(name="evidence_strength", value=round(evidence_strength, 3), weight=WEIGHTS["evidence_strength"], contribution=round(WEIGHTS["evidence_strength"] * evidence_strength, 3),
                     explanation=f"{len(with_src)}/{len(evaluated)} outcomes cite brochure evidence; mean retrieval relevance {mean_rel:.2f} (a ranking signal, not accuracy)."),
        FitComponent(name="exclusion_risk", value=round(exclusion_risk, 3), weight=WEIGHTS["exclusion_risk"], contribution=round(WEIGHTS["exclusion_risk"] * exclusion_risk, 3),
                     explanation="Share of scenario weight that hits an explicit exclusion."),
        FitComponent(name="uncertainty", value=round(uncertainty, 3), weight=WEIGHTS["uncertainty"], contribution=round(WEIGHTS["uncertainty"] * uncertainty, 3),
                     explanation=f"{len(unknown)} of {len(mine)} scenarios are NOT_FOUND/UNKNOWN in the brochure (penalised, but never treated as excluded)."),
    ]
    raw = sum(c.contribution for c in components)
    score = round(max(0.0, min(1.0, raw)) * 100, 1)
    confidence = "HIGH" if uncertainty < 0.2 and citation_completeness > 0.8 else "MEDIUM" if uncertainty < 0.5 else "LOW"
    high_gaps = [g for g in gaps if g.policy_id == policy_id and g.severity == "HIGH"]
    explanation = [
        f"Covers {sum(1 for o in evaluated if o.status == CoverageStatus.COVERED)} of {len(mine)} client scenarios outright; {sum(1 for o in evaluated if o.status == CoverageStatus.CONDITIONAL)} with conditions; {sum(1 for o in evaluated if o.status == CoverageStatus.ADD_ON)} only via add-ons.",
        f"{len(unknown)} scenarios could not be assessed from the brochure (uncertainty penalty).",
        f"{len(high_gaps)} high-severity gaps identified." if high_gaps else "No high-severity gaps identified from the brochure evidence.",
    ]
    return PolicyFitResult(policy_id=policy_id, score=score, components=components, explanation=explanation, evaluated_scenarios=len(evaluated), unknown_scenarios=len(unknown), confidence=confidence)


def score_all(policy_ids: list[str], scenarios: list[Scenario], outcomes: list[ScenarioOutcome], gaps: list[PolicyGap]) -> list[PolicyFitResult]:
    results = [score_policy(pid, scenarios, outcomes, gaps) for pid in policy_ids]
    # Highest score wins. An exact tie breaks on policy id, not on catalog order
    # (HDFC is filed as "Policy A", so a stable sort would have preferred it).
    results.sort(key=lambda r: (-r.score, r.policy_id))
    for r in results:
        r.close_call_with = [o.policy_id for o in results if o.policy_id != r.policy_id and abs(o.score - r.score) <= 5.0]
    return results


def recommend(fits: list[PolicyFitResult], gaps: list[PolicyGap], docs: dict[str, PolicyDocument], assumptions: list[str] | None = None) -> Recommendation:
    if not fits:
        raise ValueError("No policy fit results to recommend from")
    best = fits[0]
    runner = fits[1] if len(fits) > 1 else None
    doc = docs.get(best.policy_id)
    name = doc.policy_name if doc else best.policy_id
    rationale = [f"Highest policy-fit score ({best.score}/100) across the client's scenarios."] + best.explanation
    if runner:
        rationale.append(f"Runner-up: {docs[runner.policy_id].policy_name if runner.policy_id in docs else runner.policy_id} at {runner.score}/100" + (" (close call; advisor judgement recommended)." if runner.policy_id in best.close_call_with else "."))
    caveats = list(dict.fromkeys(g.detail for g in gaps if g.policy_id == best.policy_id and g.severity in {"HIGH", "MEDIUM"}))[:5]
    if best.confidence == "LOW":
        caveats.insert(0, "Low evidence confidence: many scenarios are not addressed by the brochure; confirm with the full policy wording before presenting.")
    base_assumptions = [
        "All four documents are retail individual/family-floater product brochures; group/corporate terms are not stated. The recommendation assumes a voluntary or employee-choice retail plan facilitated by Marsh.",
        "Brochure content is summary-level; the policy wording prevails.",
    ]
    return Recommendation(
        recommended_policy_id=best.policy_id,
        policy_name=name,
        fit_score=best.score,
        rationale=rationale,
        runner_up_policy_id=runner.policy_id if runner else None,
        caveats=caveats,
        assumptions=base_assumptions + list(assumptions or []),
    )
