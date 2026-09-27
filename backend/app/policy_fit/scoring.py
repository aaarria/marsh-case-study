"""Deterministic policy-fit scoring.

Fit is the weighted mean of criterion scores on requirements the brochure actually addresses.
Missing evidence is left out of that mean. It lowers evidence completeness instead.
Completeness is not added to fit. It is a decision-sufficiency gate.

Decision status:
- INELIGIBLE: a must-have has explicit exclusion, a rejected add-on, a score below
  must_have_fail_below, or unresolved evidence while another policy does evidence it.
- INCOMPLETE: no must-have failure, but completeness is below min_decision_completeness
  or more than max_completeness_shortfall behind the most complete peer.
  The policy stays eligible. It cannot win.
- INCOMPLETE_COMPARISON: every policy lacks reliable evidence for the same must-have,
  or no policy clears the sufficiency gate. There is no automatic recommendation.
- ELIGIBLE: decision-sufficient and strictly ahead of every other sufficient policy
  by more than close_threshold.
- CLOSE_DECISION: two or more decision-sufficient policies are within close_threshold.
  No policy id, name, or input order breaks the tie.

fit_score = sum(weight_i * criterion_score_i) / sum(weight_i)   over evidenced requirements only
contribution_i = (weight_i / evidenced_weight) * criterion_score_i
Those contributions sum to fit_score.

An LLM never computes these numbers. Policy id, insurer name, filename, list order,
retrieval rank, chunk count, and brochure length are not inputs.
"""
from __future__ import annotations

import math

from app.models.fit import (
    AlternativeFit,
    ClientRequirement,
    CriterionScore,
    DecisionState,
    FitComponent,
    PolicyFitResult,
    PolicyGap,
    Recommendation,
    Scenario,
    ScenarioOutcome,
)
from app.models.policy import CoverageStatus, FeatureFact, PolicyDocument, PolicyExtractionResult
from app.policy_fit.criteria import compare_fact, expectation_for
from app.policy_fit.requirements import requirements_from_scenarios
from app.policy_fit.scoring_config import DEFAULT_SCORING, ScoringConfig

_MISSING_STATUS = {"NOT_FOUND", "UNKNOWN", "REVIEW_REQUIRED"}


def score_policies(
    policy_ids: list[str],
    requirements: list[ClientRequirement],
    results: dict[str, PolicyExtractionResult],
    gaps: list[PolicyGap] | None = None,
    config: ScoringConfig | None = None,
) -> list[PolicyFitResult]:
    books = {pid: dict(results[pid].facts) if pid in results else {} for pid in policy_ids}
    return _score_books(policy_ids, requirements, books, gaps or [], config or DEFAULT_SCORING)


def score_all(policy_ids: list[str], scenarios: list[Scenario], outcomes: list[ScenarioOutcome], gaps: list[PolicyGap]) -> list[PolicyFitResult]:
    requirements = requirements_from_scenarios(scenarios)
    by_scenario = {s.scenario_id: s for s in scenarios}
    books: dict[str, dict[str, FeatureFact]] = {pid: {} for pid in policy_ids}
    for outcome in outcomes:
        if outcome.policy_id not in books:
            continue
        scenario = by_scenario.get(outcome.scenario_id)
        feature = scenario.feature_keys[0] if scenario and scenario.feature_keys else "in_patient_hospitalisation"
        books[outcome.policy_id][outcome.scenario_id] = _fact_from_outcome(outcome, feature)
    return _score_books(policy_ids, requirements, books, gaps, DEFAULT_SCORING)


def score_policy(policy_id: str, scenarios: list[Scenario], outcomes: list[ScenarioOutcome], gaps: list[PolicyGap]) -> PolicyFitResult:
    return score_all([policy_id], scenarios, outcomes, gaps)[0]


def recommend(
    fits: list[PolicyFitResult],
    gaps: list[PolicyGap],
    docs: dict[str, PolicyDocument],
    assumptions: list[str] | None = None,
    advisor_choice: str | None = None,
) -> Recommendation:
    if not fits:
        raise ValueError("No policy fit results to recommend from")
    if advisor_choice:
        chosen = next(f for f in fits if f.policy_id == advisor_choice)
        return _named(chosen, fits, gaps, docs, assumptions, DecisionState.ADVISOR_OVERRIDE, [])

    top = fits[0]
    peers = [top] + [f for f in fits if f.policy_id in top.close_call_with]
    base_assumptions = [
        "All four documents are retail individual/family-floater product brochures; group/corporate terms are not stated. The recommendation assumes a voluntary or employee-choice retail plan facilitated by Marsh.",
        "Brochure content is summary-level; the policy wording prevails.",
    ]
    shared_assumptions = base_assumptions + list(assumptions or [])
    if fits and all(not fit.eligible for fit in fits):
        return _unresolved(top, fits, docs, gaps, shared_assumptions, DecisionState.NOT_ELIGIBLE, "A must-have requirement failed or is missing where another policy has evidence. The failure is not averaged away.")
    if top.decision_state == DecisionState.CLOSE_DECISION.value:
        return _unresolved(top, peers, docs, gaps, shared_assumptions, DecisionState.CLOSE_DECISION, "Scores are within the close threshold. No insurer name, policy id, or file order is used to pick a winner.")
    if top.decision_state in {DecisionState.INCOMPLETE_COMPARISON.value, DecisionState.INCOMPLETE.value} or not top.decision_sufficient:
        reason = "The comparison is not sufficient for an automatic recommendation."
        if top.unresolved_must_haves:
            reason = "Unresolved must-have: " + ", ".join(top.unresolved_must_haves) + ". It is not treated as satisfied or failed. Advisor review is required."
        elif top.comparison_incomplete:
            reason = "Comparison incomplete for: " + ", ".join(top.comparison_incomplete) + ". No automatic recommendation."
        else:
            reason = "No policy clears the evidence-completeness gate. Missing rows are not dropped in a way that creates a winner."
        return _unresolved(top, peers or fits, docs, gaps, shared_assumptions, DecisionState.INCOMPLETE_COMPARISON, reason)
    if not top.eligible:
        return _unresolved(top, peers or fits, docs, gaps, shared_assumptions, DecisionState.NOT_ELIGIBLE, "A must-have requirement failed or is missing where another policy has evidence. The failure is not averaged away.")
    return _named(top, fits, gaps, docs, assumptions, DecisionState.ELIGIBLE, [])


def _score_books(
    policy_ids: list[str],
    requirements: list[ClientRequirement],
    books: dict[str, dict[str, FeatureFact]],
    gaps: list[PolicyGap],
    config: ScoringConfig,
) -> list[PolicyFitResult]:
    fits = [_one(pid, requirements, books.get(pid, {}), gaps, config) for pid in policy_ids]
    _apply_comparison_sufficiency(fits)
    _apply_decision_gate(fits, config)
    return _rank(fits, config)


def _one(policy_id: str, requirements: list[ClientRequirement], book: dict[str, FeatureFact], gaps: list[PolicyGap], config: ScoringConfig) -> PolicyFitResult:
    rows: list[CriterionScore] = []
    for req in requirements:
        fact = book.get(req.requirement_id) or book.get(req.feature)
        judgement = compare_fact(req, fact, config)
        evidence = None
        page = None
        section = None
        chunk = None
        if fact is not None and fact.sources:
            evidence = fact.original_quote or fact.sources[0].source_text
            page = fact.sources[0].page
            section = fact.sources[0].section or fact.source_section
            chunk = fact.sources[0].chunk_id
        elif fact is not None:
            evidence = fact.original_quote or fact.value
            page = fact.source_page
            section = fact.source_section
            chunk = fact.source_chunk_id
        rows.append(
            CriterionScore(
                requirement_id=req.requirement_id,
                feature=req.feature,
                description=req.description,
                requirement_class=req.requirement_class.value,
                type=req.type.value,
                weight=req.weight,
                criterion_score=judgement.score,
                status=judgement.status.value,
                evidence=evidence,
                source_page=page,
                source_section=section,
                source_chunk_id=chunk,
                must_have_gap=judgement.must_have_gap,
                explicit_exclusion=judgement.explicit_exclusion,
                unresolved=judgement.unresolved,
                coverage_expectation=expectation_for(req).value,
                condition_materiality=judgement.condition_materiality,
                note=judgement.note,
            )
        )
    evidenced = [row for row in rows if row.criterion_score is not None]
    evidenced_weight = sum(_safe_weight(row.weight) for row in evidenced)
    completeness = sum(_safe_weight(row.weight) for row in rows if row.criterion_score is not None)
    if evidenced_weight <= 0 or not math.isfinite(evidenced_weight):
        fit = 0.0
    else:
        fit = sum(_safe_weight(row.weight) * _safe_score(row.criterion_score) for row in evidenced) / evidenced_weight
    if not math.isfinite(fit):
        fit = 0.0
    fit = min(100.0, max(0.0, fit))
    for row in rows:
        if row.criterion_score is None or evidenced_weight <= 0:
            row.contribution = None
        else:
            row.contribution = (_safe_weight(row.weight) / evidenced_weight) * _safe_score(row.criterion_score)
    score = round(fit, 4)
    credited = [row for row in rows if row.contribution is not None]
    for row in credited:
        row.contribution = round(row.contribution or 0.0, 4)
    if credited:
        drift = round(score - sum(row.contribution or 0.0 for row in credited), 4)
        credited[-1].contribution = round((credited[-1].contribution or 0.0) + drift, 4)

    client_rows = [row for row in rows if row.requirement_class != "BASELINE"]
    client_resolved = all(not row.unresolved for row in client_rows)
    gaps_found = [row.feature for row in rows if row.must_have_gap]
    eligible = not gaps_found
    unresolved = [row.feature for row in rows if row.unresolved]
    exclusions = [row.feature for row in rows if row.explicit_exclusion]
    if completeness >= config.confidence_high_completeness and eligible:
        confidence = "HIGH"
    elif completeness >= config.confidence_medium_completeness:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    high_gaps = [g for g in gaps if g.policy_id == policy_id and g.severity == "HIGH"]
    explanation = [
        f"Fit {score}/100 is the weighted mean of {len(evidenced)} evidenced requirements. {len(unresolved)} requirements have no brochure evidence and are omitted from the mean.",
        f"Evidence completeness is {completeness:.0%} of requirement weight. It is not added to the fit score.",
    ]
    if exclusions:
        explanation.append("Explicit exclusions: " + ", ".join(exclusions) + ". These score 0. They are not the same as missing evidence.")
    if gaps_found:
        explanation.append("Must-have gaps: " + ", ".join(gaps_found) + ".")
    elif high_gaps:
        explanation.append(f"{len(high_gaps)} high-severity gaps identified.")
    else:
        explanation.append("No must-have gap identified from the brochure evidence.")
    for row in rows:
        if row.contribution is None:
            explanation.append(f"{row.feature} [{row.requirement_class}] weight {row.weight:.0%}: {row.status}. No contribution. {row.note}")
        else:
            explanation.append(
                f"{row.feature} [{row.requirement_class}] weight {row.weight:.0%} × {row.criterion_score:.0f} = {row.contribution:.2f}. {row.note}"
            )
    components = [
        FitComponent(
            name="requirement_fit",
            value=round(score / 100.0, 4),
            weight=1.0,
            contribution=round(score / 100.0, 4),
            explanation="Weighted mean of evidenced criterion scores, on a 0-1 scale. Contributions on each requirement sum back to the 0-100 fit score.",
        ),
        FitComponent(
            name="evidence_completeness",
            value=round(completeness, 4),
            weight=0.0,
            contribution=0.0,
            explanation="Share of requirement weight with a brochure fact. Weight 0 so it cannot change the fit score. Retrieval rank is not used.",
        ),
    ]
    return PolicyFitResult(
        policy_id=policy_id,
        score=score,
        components=components,
        explanation=explanation,
        evaluated_scenarios=len(evidenced),
        unknown_scenarios=len(unresolved),
        confidence=confidence,
        evidence_completeness=round(completeness, 4),
        eligible=eligible,
        must_have_gaps=gaps_found,
        unresolved=unresolved,
        explicit_exclusions=exclusions,
        contributions=rows,
        client_requirements_resolved=client_resolved,
        decision_sufficient=eligible,
    )


def _apply_comparison_sufficiency(fits: list[PolicyFitResult]) -> None:
    """If every policy is NOT_FOUND on a requirement, that requirement is comparison-incomplete.

    It is not a must-have gap, and it does not make every policy ineligible.
    """
    if not fits:
        return
    width = len(fits[0].contributions)
    for index in range(width):
        rows = [fit.contributions[index] for fit in fits if index < len(fit.contributions)]
        if len(rows) != len(fits):
            continue
        if all(row.criterion_score is None and row.status in _MISSING_STATUS for row in rows):
            for row in rows:
                row.comparison_incomplete = True
                row.must_have_gap = False
                row.note = f"{row.note} COMPARISON_INCOMPLETE: no policy evidences this requirement.".strip()
    for fit in fits:
        fit.comparison_incomplete = [row.feature for row in fit.contributions if row.comparison_incomplete]
        fit.must_have_gaps = [row.feature for row in fit.contributions if row.must_have_gap]
        fit.unresolved = [row.feature for row in fit.contributions if row.unresolved or row.comparison_incomplete]
        fit.unresolved_must_haves = [
            row.feature for row in fit.contributions
            if row.requirement_class == "MUST_HAVE" and (row.must_have_gap or row.comparison_incomplete or row.unresolved or row.criterion_score is None)
        ]
        fit.explicit_exclusions = [row.feature for row in fit.contributions if row.explicit_exclusion]
        fit.eligible = not fit.must_have_gaps
        client_rows = [row for row in fit.contributions if row.requirement_class in {"PREFERENCE", "MUST_HAVE"}]
        fit.client_requirements_resolved = all(
            row.criterion_score is not None and not row.unresolved and not row.comparison_incomplete for row in client_rows
        )
        fit.explanation = [
            line for line in fit.explanation
            if not line.startswith("Must-have gaps") and not line.startswith("No must-have") and not line.startswith("Comparison incomplete") and not line.startswith("Unresolved must-have")
        ]
        if fit.must_have_gaps:
            fit.explanation.append("Must-have gaps: " + ", ".join(fit.must_have_gaps) + ".")
        elif fit.unresolved_must_haves:
            fit.explanation.append(
                "Unresolved must-have: " + ", ".join(fit.unresolved_must_haves) + ". Not satisfied and not failed. No automatic recommendation."
            )
        elif fit.comparison_incomplete:
            fit.explanation.append(
                "Comparison incomplete for: " + ", ".join(fit.comparison_incomplete) + ". No positive score and no exclusion penalty."
            )
        else:
            fit.explanation.append("No must-have gap identified from the brochure evidence.")


def _apply_decision_gate(fits: list[PolicyFitResult], config: ScoringConfig) -> None:
    """Completeness decides who may compete. It does not change fit."""
    if not fits:
        return
    open_must_haves = []
    for fit in fits:
        for feature in fit.comparison_incomplete:
            row = next((item for item in fit.contributions if item.feature == feature), None)
            if row is not None and row.requirement_class == "MUST_HAVE" and feature not in open_must_haves:
                open_must_haves.append(feature)
    peers = [fit for fit in fits if not fit.must_have_gaps]
    best = max((fit.evidence_completeness for fit in peers), default=0.0)
    for fit in fits:
        if fit.must_have_gaps or open_must_haves:
            fit.decision_sufficient = False
        else:
            shortfall = best - fit.evidence_completeness
            below_floor = fit.evidence_completeness + 1e-9 < config.min_decision_completeness
            behind = shortfall > config.max_completeness_shortfall + 1e-9
            fit.decision_sufficient = not below_floor and not behind
        if fit.decision_sufficient and fit.evidence_completeness >= config.confidence_high_completeness:
            fit.confidence = "HIGH"
        elif fit.evidence_completeness >= config.confidence_medium_completeness:
            fit.confidence = "MEDIUM"
        else:
            fit.confidence = "LOW"
        if not fit.decision_sufficient and not fit.must_have_gaps and not open_must_haves:
            fit.explanation.append(
                f"Decision-insufficient: completeness {fit.evidence_completeness:.0%} is below the gate "
                f"(floor {config.min_decision_completeness:.0%}, shortfall limit {config.max_completeness_shortfall:.0%}). "
                "Fit is unchanged."
            )


def _rank(fits: list[PolicyFitResult], config: ScoringConfig) -> list[PolicyFitResult]:
    ranked = sorted(fits, key=lambda f: (f.decision_sufficient, not f.must_have_gaps, f.score), reverse=True)
    if not ranked:
        return ranked
    sufficient = [fit for fit in ranked if fit.decision_sufficient]
    open_must = any(fit.unresolved_must_haves and not fit.must_have_gaps for fit in ranked) and any(
        row.comparison_incomplete and row.requirement_class == "MUST_HAVE" for fit in ranked for row in fit.contributions
    )
    if open_must or not sufficient:
        state = DecisionState.NOT_ELIGIBLE if ranked and all(fit.must_have_gaps for fit in ranked) else DecisionState.INCOMPLETE_COMPARISON
        visible = [fit.policy_id for fit in ranked if not fit.must_have_gaps] or [fit.policy_id for fit in ranked]
        for fit in ranked:
            if fit.must_have_gaps and state == DecisionState.NOT_ELIGIBLE:
                fit.decision_state = DecisionState.NOT_ELIGIBLE.value
                fit.close_call_with = []
            elif fit.must_have_gaps:
                fit.decision_state = DecisionState.NOT_ELIGIBLE.value
                fit.close_call_with = []
            else:
                fit.decision_state = state.value
                fit.close_call_with = [pid for pid in visible if pid != fit.policy_id]
        return ranked
    top = sufficient[0]
    peers = [fit.policy_id for fit in sufficient if abs(fit.score - top.score) <= config.close_threshold]
    state = DecisionState.CLOSE_DECISION if len(peers) > 1 else DecisionState.ELIGIBLE
    peer_set = set(peers)
    for fit in ranked:
        fit.close_call_with = []
        if fit.must_have_gaps:
            fit.decision_state = DecisionState.NOT_ELIGIBLE.value
        elif not fit.decision_sufficient:
            fit.decision_state = DecisionState.INCOMPLETE.value
        elif fit.policy_id in peer_set and len(peer_set) > 1:
            fit.close_call_with = [pid for pid in peers if pid != fit.policy_id]
            fit.decision_state = state.value
        else:
            fit.decision_state = DecisionState.ELIGIBLE.value
    return ranked


def _safe_weight(value: float) -> float:
    if not math.isfinite(value) or value < 0:
        return 0.0
    return value


def _safe_score(value: float | None) -> float:
    if value is None or not math.isfinite(value):
        return 0.0
    return min(100.0, max(0.0, value))


def _fact_from_outcome(outcome: ScenarioOutcome, feature: str) -> FeatureFact:
    return FeatureFact(
        policy_id=outcome.policy_id,
        feature=feature,
        coverage_status=outcome.status,
        value=outcome.rationale or None,
        limit=outcome.limitations[0] if outcome.limitations else None,
        conditions=list(outcome.conditions),
        exclusions=list(outcome.limitations) if outcome.status == CoverageStatus.EXCLUDED else [],
        is_add_on=outcome.status == CoverageStatus.ADD_ON,
        sources=list(outcome.sources),
    )


def alternative_fits(fits: list[PolicyFitResult], docs: dict, primary_id: str = "", limit: int = 2) -> list[AlternativeFit]:
    """Decision-sufficient peers only, ordered by fit then criterion scores. Incomplete policies are omitted."""
    peers = [fit for fit in fits if fit.decision_sufficient and fit.eligible and fit.policy_id != primary_id]
    # Evidence text breaks an exact score tie for display. Policy id is not a ranking input.
    peers.sort(key=lambda fit: (
        -fit.score,
        tuple((row.feature, -1 if row.criterion_score is None else row.criterion_score) for row in sorted(fit.contributions, key=lambda row: row.feature)),
        tuple((row.feature, row.evidence or "") for row in sorted(fit.contributions, key=lambda row: row.feature)),
    ))
    found: list[AlternativeFit] = []
    for fit in peers[:limit]:
        client = [row for row in fit.contributions if row.requirement_class in {"PREFERENCE", "MUST_HAVE"}]
        ranked = sorted((row for row in client if row.criterion_score is not None), key=lambda row: -row.criterion_score)
        strong = [f"{row.description}: {row.criterion_score:.0f}" for row in ranked[:2]]
        trade_offs = []
        for row in client:
            if row.criterion_score is None:
                trade_offs.append(f"{row.description}: not specified in the supplied brochure.")
            elif row.status in {"ADD_ON", "CONDITIONAL", "EXCLUDED"} or row.criterion_score < 70:
                trade_offs.append(f"{row.description}: {row.status} {row.criterion_score:.0f}")
        evidence = []
        for row in ranked[:2]:
            if row.evidence:
                page = f" p.{row.source_page}" if row.source_page else ""
                evidence.append(f"{row.feature}{page}: {row.evidence[:180]}")
        doc = docs.get(fit.policy_id)
        found.append(AlternativeFit(
            policy_id=fit.policy_id,
            policy_name=doc.policy_name if doc else fit.policy_id,
            fit_score=fit.score,
            evidence_completeness=fit.evidence_completeness,
            decision_state=fit.decision_state,
            strong_matches=strong,
            trade_offs=trade_offs[:3],
            evidence=evidence,
        ))
    return found


def _named(chosen, fits, gaps, docs, assumptions, state: DecisionState, competing: list[str]) -> Recommendation:
    doc = docs.get(chosen.policy_id)
    name = doc.policy_name if doc else chosen.policy_id
    options = alternative_fits(fits, docs, chosen.policy_id)
    runner = options[0] if options else None
    if state == DecisionState.ADVISOR_OVERRIDE:
        lead = f"Advisor override. {name} is pitched at fit {chosen.score}/100."
    else:
        lead = f"Highest fit among decision-sufficient policies ({chosen.score}/100). Evidence completeness is a separate gate and is not added to fit."
    rationale = [lead] + chosen.explanation[:4]
    if runner:
        rationale.append(f"Alternative: {runner.policy_name} at {runner.fit_score}/100.")
    caveats = list(dict.fromkeys(g.detail for g in gaps if g.policy_id == chosen.policy_id and g.severity in {"HIGH", "MEDIUM"}))[:5]
    caveats.extend(f"Must-have gap: {feature}." for feature in chosen.must_have_gaps)
    if chosen.confidence == "LOW":
        caveats.insert(0, "Low evidence completeness: confirm with the full policy wording before presenting.")
    base_assumptions = [
        "All four documents are retail individual/family-floater product brochures; group/corporate terms are not stated. The recommendation assumes a voluntary or employee-choice retail plan facilitated by Marsh.",
        "Brochure content is summary-level; the policy wording prevails.",
    ]
    return Recommendation(
        recommended_policy_id=chosen.policy_id,
        policy_name=name,
        fit_score=chosen.score,
        rationale=rationale,
        runner_up_policy_id=runner.policy_id if runner else None,
        caveats=caveats[:6],
        assumptions=base_assumptions + list(assumptions or []),
        decision_state=state.value,
        competing_policy_ids=competing,
        comparison_incomplete=list(chosen.comparison_incomplete),
        unresolved_must_haves=list(chosen.unresolved_must_haves),
        alternatives=options,
    )


def _unresolved(top, peers, docs, gaps, assumptions, state: DecisionState, reason: str) -> Recommendation:
    labels = {
        DecisionState.CLOSE_DECISION: "Close decision",
        DecisionState.INCOMPLETE_COMPARISON: "Incomplete comparison",
        DecisionState.NOT_ELIGIBLE: "Not eligible",
    }
    return Recommendation(
        recommended_policy_id="",
        policy_name=labels.get(state, "Unresolved"),
        fit_score=top.score,
        rationale=[reason] + top.explanation[:3],
        runner_up_policy_id=None,
        caveats=[g.detail for g in gaps if g.severity == "HIGH"][:5],
        assumptions=assumptions,
        decision_state=state.value,
        competing_policy_ids=[f.policy_id for f in peers],
        comparison_incomplete=list(dict.fromkeys(feature for fit in peers for feature in fit.comparison_incomplete)),
        unresolved_must_haves=list(dict.fromkeys(feature for fit in peers for feature in fit.unresolved_must_haves)),
        alternatives=alternative_fits(peers, docs, ""),
    )
