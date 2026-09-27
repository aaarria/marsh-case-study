"""Policy Fit Arena: test all four policies against the same scenarios (deterministic)."""
from __future__ import annotations

from app.models.fit import Scenario, ScenarioOutcome
from app.models.policy import CoverageStatus, FeatureFact, PolicyExtractionResult

STATUS_VALUE = {
    CoverageStatus.COVERED: 1.0,
    CoverageStatus.CONDITIONAL: 0.75,
    CoverageStatus.PARTIALLY_COVERED: 0.5,
    CoverageStatus.ADD_ON: 0.35,  # available, but only at extra premium
    CoverageStatus.EXCLUDED: 0.0,
    CoverageStatus.NOT_FOUND: None,
    CoverageStatus.UNKNOWN: None,
    CoverageStatus.REVIEW_REQUIRED: None,
}

TERMS_FEATURES = {"waiting_period_initial", "waiting_period_specific", "waiting_period_ped", "exclusions", "copay", "deductible_options", "pricing_zones", "premium_illustration", "tenure", "eligibility_entry_age", "renewal_portability"}


def _rationale(fact: FeatureFact) -> str:
    if fact.coverage_status in {CoverageStatus.NOT_FOUND, CoverageStatus.UNKNOWN, CoverageStatus.REVIEW_REQUIRED}:
        return "The brochure does not address this feature; treated as unknown, not as excluded."
    parts = []
    if fact.value:
        parts.append(fact.value)
    if fact.limit and fact.limit not in (fact.value or ""):
        parts.append(f"Limit: {fact.limit}")
    if fact.waiting_period:
        parts.append(f"Waiting period: {fact.waiting_period}")
    if fact.copay:
        parts.append(f"Co-pay: {fact.copay}")
    if fact.is_add_on:
        parts.append("Available only as optional/add-on cover at additional premium.")
    if fact.variant_scope:
        parts.append(f"Applies to: {fact.variant_scope}.")
    return " ".join(parts)[:600]


def evaluate_scenario(scenario: Scenario, fact: FeatureFact) -> ScenarioOutcome:
    status = fact.coverage_status
    value = STATUS_VALUE[status]
    # Terms features are informational: a specified waiting period is not "coverage"; treat known terms as conditional context
    if scenario.feature_keys[0] in TERMS_FEATURES and status == CoverageStatus.COVERED:
        status = CoverageStatus.CONDITIONAL
        value = 0.75
    conditions = list(dict.fromkeys(fact.conditions))
    if fact.waiting_period:
        conditions.append(f"Waiting period: {fact.waiting_period}")
    if fact.is_add_on:
        conditions.append("Optional / add-on cover at extra premium")
    if fact.variant_scope:
        conditions.append(f"Variant scope: {fact.variant_scope}")
    limitations = []
    if fact.limit:
        limitations.append(fact.limit)
    limitations.extend(fact.exclusions)
    return ScenarioOutcome(
        scenario_id=scenario.scenario_id,
        policy_id=fact.policy_id,
        status=status,
        rationale=_rationale(fact),
        conditions=conditions,
        limitations=limitations,
        sources=fact.sources,
        value=value,
    )


def run_arena(scenarios: list[Scenario], results: dict[str, PolicyExtractionResult]) -> list[ScenarioOutcome]:
    outcomes: list[ScenarioOutcome] = []
    for sc in scenarios:
        key = sc.feature_keys[0]
        for pid, res in results.items():
            fact = res.facts.get(key) or FeatureFact(policy_id=pid, feature=key, coverage_status=CoverageStatus.NOT_FOUND)
            outcomes.append(evaluate_scenario(sc, fact))
    return outcomes
