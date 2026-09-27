"""Counterfactual gap analysis: "If this policy were selected, which exposures remain unresolved?" """
from __future__ import annotations

from app.models.client import Exposure
from app.models.fit import PolicyGap, Scenario, ScenarioOutcome
from app.models.policy import CoverageStatus
from app.policies.features import FEATURE_LABELS

SEVERITY_BY_STATUS = {
    CoverageStatus.EXCLUDED: "HIGH",
    CoverageStatus.NOT_FOUND: "MEDIUM",
    CoverageStatus.UNKNOWN: "MEDIUM",
    CoverageStatus.REVIEW_REQUIRED: "MEDIUM",
    CoverageStatus.ADD_ON: "MEDIUM",
    CoverageStatus.PARTIALLY_COVERED: "MEDIUM",
    CoverageStatus.CONDITIONAL: "LOW",
}

GAP_TYPE_BY_STATUS = {
    CoverageStatus.EXCLUDED: "EXCLUSION",
    CoverageStatus.NOT_FOUND: "UNCERTAINTY",
    CoverageStatus.UNKNOWN: "UNCERTAINTY",
    CoverageStatus.REVIEW_REQUIRED: "UNCERTAINTY",
    CoverageStatus.ADD_ON: "GAP",
    CoverageStatus.PARTIALLY_COVERED: "LIMITATION",
    CoverageStatus.CONDITIONAL: "CONDITION",
}


def analyse_gaps(scenarios: list[Scenario], outcomes: list[ScenarioOutcome], exposures: list[Exposure]) -> list[PolicyGap]:
    sc_by_id = {s.scenario_id: s for s in scenarios}
    exp_by_id = {e.exposure_id: e for e in exposures}
    gaps: list[PolicyGap] = []
    for o in outcomes:
        if o.status == CoverageStatus.COVERED:
            continue
        sc = sc_by_id.get(o.scenario_id)
        if not sc:
            continue
        exp = exp_by_id.get(sc.exposure_id)
        feature = FEATURE_LABELS.get(sc.feature_keys[0], sc.feature_keys[0])
        severity = SEVERITY_BY_STATUS.get(o.status, "LOW")
        if exp and exp.priority >= 1.5 and severity == "MEDIUM":
            severity = "HIGH"
        if o.status == CoverageStatus.CONDITIONAL and not o.conditions:
            continue
        if o.status == CoverageStatus.EXCLUDED:
            detail = f"{feature} is explicitly excluded; exposure '{exp.title if exp else sc.title}' remains unresolved."
        elif o.status in {CoverageStatus.NOT_FOUND, CoverageStatus.UNKNOWN}:
            detail = f"The brochure does not address {feature}; exposure '{exp.title if exp else sc.title}' cannot be confirmed as covered (unknown, not excluded)."
        elif o.status == CoverageStatus.ADD_ON:
            detail = f"{feature} is only available as an optional add-on at extra premium."
        elif o.status == CoverageStatus.PARTIALLY_COVERED:
            detail = f"{feature} is capped or limited: {', '.join(o.limitations[:2]) or 'see source'}."
        else:
            detail = f"{feature} is covered subject to conditions: {'; '.join(o.conditions[:3])}."
        gaps.append(PolicyGap(policy_id=o.policy_id, exposure_id=sc.exposure_id, gap_type=GAP_TYPE_BY_STATUS.get(o.status, "GAP"), detail=detail, severity=severity, sources=o.sources[:2]))
    return gaps
