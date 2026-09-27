"""Named scoring configuration. Criterion code reads these values; it does not embed them.

Deductible formula, for amount A (INR) and reference R (deductible_reference_inr, R > 0):

    score(A) = 100 * (1 - min(max(A, 0), R) / R)

Boundaries: A <= 0 → 100; A = R/2 → 50; A >= R → 0.

Decision sufficiency is not part of fit. A policy may compete only when its evidence
completeness is at least min_decision_completeness and is not more than
max_completeness_shortfall below the most complete peer that has no must-have gap.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScoringConfig:
    close_threshold: float = 5.0
    must_have_fail_below: float = 40.0
    advisor_pool: float = 0.65
    baseline_pool: float = 0.35
    # Completeness gate. It does not enter the fit average.
    min_decision_completeness: float = 0.5
    max_completeness_shortfall: float = 0.20
    contribution_tolerance: float = 1e-4
    challenge_score_margin: float = 25.0
    sensitivity_weight_delta: float = 0.1
    max_check_scenarios: int = 4
    add_on_accepted_score: float = 60.0
    failed_score: float = 0.0
    confidence_high_completeness: float = 0.8
    confidence_medium_completeness: float = 0.5
    # Waiting-period full fit, in days. Shorter or equal is full fit.
    waiting_full_days: dict[str, float] = field(default_factory=lambda: {
        "waiting_period_initial": 30.0,
        "waiting_period_specific": 24.0 * 30.0,
        "waiting_period_ped": 36.0 * 30.0,
    })
    waiting_partial_days: dict[str, float] = field(default_factory=lambda: {
        "waiting_period_initial": 90.0,
        "waiting_period_specific": 36.0 * 30.0,
        "waiting_period_ped": 48.0 * 30.0,
    })
    waiting_full_score: float = 100.0
    waiting_partial_score: float = 60.0
    waiting_long_score: float = 25.0
    waiting_unquantified_cap: float = 70.0
    waiting_default_full_days: float = 30.0
    copay_target_percent: float = 0.0
    copay_tolerance_percent: float = 0.0
    copay_partial_band: float = 20.0
    copay_full_score: float = 100.0
    copay_partial_score: float = 60.0
    copay_fail_score: float = 20.0
    copay_unquantified_score: float = 40.0
    deductible_reference_inr: float = 100_000.0
    deductible_unquantified_score: float = 70.0
    covered_score: float = 100.0
    conditional_score: float = 70.0
    partial_score: float = 50.0
    limit_no_target_score: float = 80.0
    limit_inside_tolerance_score: float = 60.0
    limit_below_target_score: float = 30.0
    sublimit_cap_score: float = 60.0
    sublimit_unquantified_cap: float = 70.0
    # "Up to sum insured" is a ceiling. It is not the same as room rent at actuals.
    room_rent_up_to_si_score: float = 75.0
    chronic_base_zero_score: float = 100.0
    chronic_variant_zero_score: float = 70.0
    chronic_addon_within_month_score: float = 45.0
    chronic_addon_after_month_score: float = 30.0
    cost_lever_score: float = 100.0
    cost_lever_partial_score: float = 80.0
    cost_lever_absent_score: float = 40.0
    eligibility_met_score: float = 100.0
    eligibility_conditional_score: float = 70.0
    eligibility_fail_score: float = 0.0
    geographic_met_score: float = 100.0
    geographic_conditional_score: float = 70.0
    geographic_fail_score: float = 0.0
    wellness_met_score: float = 100.0
    wellness_conditional_score: float = 70.0
    wellness_partial_score: float = 50.0
    condition_info_score: float = 100.0
    condition_minor_score: float = 80.0
    condition_material_score: float = 55.0
    condition_critical_score: float = 15.0


DEFAULT_SCORING = ScoringConfig()


def deductible_fit_score(amount: float, reference: float | None = None) -> float:
    """score(A) = 100 * (1 - min(max(A, 0), R) / R)."""
    ref = DEFAULT_SCORING.deductible_reference_inr if reference is None else reference
    if not math.isfinite(ref) or ref <= 0:
        raise ValueError("deductible reference must be a positive finite number")
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(float(amount)):
        raise ValueError("deductible amount must be a finite number")
    capped = min(max(float(amount), 0.0), ref)
    return 100.0 * (1.0 - capped / ref)
