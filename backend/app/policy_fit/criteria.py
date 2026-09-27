"""Deterministic comparison rules. Scores are 0-100 or None when the brochure does not say.

NOT_FOUND, UNKNOWN, and REVIEW_REQUIRED score None. They are not cover and not exclusions.
EXCLUDED scores failed_score and is negative evidence.
ADD_ON is optional cover. It satisfies a requirement only when coverage_expectation is
OPTIONAL_ADD_ON or EITHER. It does not satisfy BASE.
Absence of an exclusion is not coverage. Absence of coverage is not an exclusion.

Retrieval relevance is never an input.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.models.fit import ClientRequirement, CoverageExpectation, CriterionType
from app.models.policy import ConditionMateriality, CoverageStatus, FeatureFact
from app.policy_fit.scoring_config import DEFAULT_SCORING, ScoringConfig, deductible_fit_score
from app.utils.numerals import extract_numbers

ADD_ON_ACCEPTED_SCORE = DEFAULT_SCORING.add_on_accepted_score
MUST_HAVE_FAIL_BELOW = DEFAULT_SCORING.must_have_fail_below
DEDUCTIBLE_REFERENCE_INR = DEFAULT_SCORING.deductible_reference_inr

FEATURE_TYPE = {
    "sum_insured_options": CriterionType.LIMIT,
    "room_rent": CriterionType.SUBLIMIT,
    "air_ambulance": CriterionType.LIMIT,
    "road_ambulance": CriterionType.LIMIT,
    "pre_post_hospitalisation": CriterionType.LIMIT,
    "copay": CriterionType.COPAYMENT,
    "deductible_options": CriterionType.DEDUCTIBLE,
    "waiting_period_initial": CriterionType.WAITING_PERIOD,
    "waiting_period_specific": CriterionType.WAITING_PERIOD,
    "waiting_period_ped": CriterionType.WAITING_PERIOD,
    "exclusions": CriterionType.EXCLUSION,
    "eligibility_entry_age": CriterionType.ELIGIBILITY,
    "family_composition": CriterionType.ELIGIBILITY,
    "global_cover": CriterionType.GEOGRAPHIC,
    "pricing_zones": CriterionType.GEOGRAPHIC,
    "health_checkup": CriterionType.WELLNESS,
    "teleconsultation_opd": CriterionType.WELLNESS,
    "wellness_renewal_discount": CriterionType.WELLNESS,
}

MISSING = {CoverageStatus.NOT_FOUND, CoverageStatus.UNKNOWN, CoverageStatus.REVIEW_REQUIRED}

_GENERIC_EXCLUSION_WORDS = {
    "exclusion", "exclusions", "excluded", "covered", "coverage", "standard", "list",
    "policy", "brochure", "benefit", "benefits", "not", "with", "from", "that", "this",
    "client", "needs", "need",
}

_MATERIALITY_RANK = {
    ConditionMateriality.INFO: 0,
    ConditionMateriality.MINOR: 1,
    ConditionMateriality.MATERIAL: 2,
    ConditionMateriality.CRITICAL: 3,
}

# Each comparator records input, unit, target, direction, and the four decision rules.
COMPARATOR_RULES: dict[str, dict[str, str]] = {
    "coverage": {
        "input": "coverage_status of a base-plan benefit fact",
        "unit": "status",
        "target": "base-plan cover of the named feature",
        "direction": "COVERED is best; EXCLUDED is worst",
        "full_fit": "COVERED scores covered_score",
        "partial_fit": "CONDITIONAL scores conditional_score; PARTIALLY_COVERED scores partial_score",
        "failure": "EXCLUDED scores failed_score",
        "missing_evidence": "NOT_FOUND, UNKNOWN, REVIEW_REQUIRED, or no fact scores None",
        "exception": "A named condition caps the score at that condition's materiality; conditions are not summed",
    },
    "limit": {
        "input": "limit_numeric or money parsed from the limit text",
        "unit": "INR",
        "target": "requirement.target_value, or none",
        "direction": "higher limit is better",
        "full_fit": "unlimited, up to sum insured, or amount >= target scores covered_score",
        "partial_fit": "inside tolerance scores limit_inside_tolerance_score; numeric with no target scores limit_no_target_score",
        "failure": "below the tolerance floor scores limit_below_target_score",
        "missing_evidence": "no numeric limit falls through to the coverage status; missing status scores None",
        "exception": "A named condition caps the limit score once, at the worst unique materiality",
    },
    "sublimit": {
        "input": "room-rent or sub-limit text and limit_numeric",
        "unit": "INR or at-actuals flag",
        "target": "no sub-limit / at actuals",
        "direction": "uncapped is better than a cap",
        "full_fit": "at actuals, no sub-limit, no capping, or unlimited scores covered_score",
        "partial_fit": "a stated numeric cap scores sublimit_cap_score",
        "failure": "EXCLUDED scores failed_score",
        "missing_evidence": "unquantified text is capped at sublimit_unquantified_cap; missing status scores None",
        "exception": "A named condition caps the score once",
    },
    "waiting_period": {
        "input": "waiting_period_days, or a duration parsed from waiting_period",
        "unit": "days",
        "target": "requirement target in days, else waiting_full_days for the feature",
        "direction": "shorter is better",
        "full_fit": "days <= full threshold scores waiting_full_score",
        "partial_fit": "days <= partial threshold scores waiting_partial_score",
        "failure": "longer than partial scores waiting_long_score",
        "missing_evidence": "unquantified text is capped at waiting_unquantified_cap; missing status scores None",
        "exception": "A client target replaces the feature baseline; tolerance widens only the partial band",
    },
    "copayment": {
        "input": "copay_percent, or a percent parsed from copay",
        "unit": "percent",
        "target": "requirement.target_value, else copay_target_percent",
        "direction": "lower is better",
        "full_fit": "percent <= target + tolerance scores copay_full_score",
        "partial_fit": "within copay_partial_band above that scores copay_partial_score",
        "failure": "above the partial band scores copay_fail_score",
        "missing_evidence": "mentioned but unquantified scores copay_unquantified_score; missing status scores None",
        "exception": "A named condition caps the score once",
    },
    "deductible": {
        "input": "deductible_amount A, or money parsed from deductible text",
        "unit": "INR",
        "target": "R = requirement.target_value or deductible_reference_inr",
        "direction": "lower is better",
        "full_fit": "score(A) = 100 * (1 - min(max(A, 0), R) / R); A <= 0 scores 100",
        "partial_fit": "A = R/2 scores 50",
        "failure": "A >= R scores 0",
        "missing_evidence": "options stated without an amount score deductible_unquantified_score; missing status scores None",
        "exception": "non-finite A or R is rejected; a named condition caps the score once",
    },
    "eligibility": {
        "input": "coverage_status of an eligibility fact",
        "unit": "status",
        "target": "the stated entry-age or family rule is met",
        "direction": "met is better than conditional or failed",
        "full_fit": "COVERED scores eligibility_met_score",
        "partial_fit": "CONDITIONAL scores eligibility_conditional_score",
        "failure": "any other established status scores eligibility_fail_score",
        "missing_evidence": "NOT_FOUND, UNKNOWN, REVIEW_REQUIRED, or no fact scores None",
        "exception": "A named condition caps the score once",
    },
    "exclusion": {
        "input": "exclusion text intersected with a concern the client named",
        "unit": "named concern",
        "target": "the concern is explicitly covered, or explicitly excluded",
        "direction": "explicit exclusion is worst; silence is not best",
        "full_fit": "only explicit coverage evidence of that concern scores covered_score",
        "partial_fit": "no partial credit",
        "failure": "EXCLUDED status, or the concern appearing in the exclusion text, scores failed_score",
        "missing_evidence": "an exclusion list that does not mention the concern scores None; it is not cover",
        "exception": "absence of an exclusion is never inferred as coverage",
    },
    "geographic": {
        "input": "coverage_status of a geography fact",
        "unit": "status",
        "target": "the stated geography is available",
        "direction": "met is better than conditional or absent",
        "full_fit": "COVERED scores geographic_met_score",
        "partial_fit": "CONDITIONAL scores geographic_conditional_score",
        "failure": "any other established status scores geographic_fail_score",
        "missing_evidence": "NOT_FOUND, UNKNOWN, REVIEW_REQUIRED, or no fact scores None",
        "exception": "A named condition caps the score once",
    },
    "add_on": {
        "input": "ADD_ON status or is_add_on, plus coverage_expectation",
        "unit": "availability",
        "target": "BASE, OPTIONAL_ADD_ON, or EITHER",
        "direction": "base cover satisfies every expectation; an add-on satisfies only OPTIONAL_ADD_ON or EITHER",
        "full_fit": "COVERED scores covered_score",
        "partial_fit": "an accepted add-on scores add_on_accepted_score",
        "failure": "an add-on against BASE scores failed_score and is a must-have gap when the requirement is must-have",
        "missing_evidence": "NOT_FOUND, UNKNOWN, REVIEW_REQUIRED, or no fact scores None",
        "exception": "a condition on an accepted add-on caps that add-on score once",
    },
    "condition": {
        "input": "condition_details, or a CONDITION/MATERIAL/CRITICAL prefix on conditions",
        "unit": "materiality",
        "target": "no material restriction on the benefit",
        "direction": "INFO is better than MINOR, MATERIAL, CRITICAL",
        "full_fit": "INFO, or no named condition on COVERED, scores condition_info_score",
        "partial_fit": "MINOR scores condition_minor_score; MATERIAL scores condition_material_score",
        "failure": "CRITICAL scores condition_critical_score and fails a must-have when below must_have_fail_below",
        "missing_evidence": "missing status scores None; a conditional status with no named condition uses conditional_score",
        "exception": "duplicate text is counted once; the worst unique materiality wins; conditions are not summed",
    },
    "wellness": {
        "input": "coverage_status of a wellness or service fact",
        "unit": "status",
        "target": "the service is included",
        "direction": "included is better than conditional or partial",
        "full_fit": "COVERED scores wellness_met_score",
        "partial_fit": "CONDITIONAL scores wellness_conditional_score; PARTIALLY_COVERED scores wellness_partial_score",
        "failure": "any other established status scores failed_score",
        "missing_evidence": "NOT_FOUND, UNKNOWN, REVIEW_REQUIRED, or no fact scores None",
        "exception": "A named condition caps the score once",
    },
}


@dataclass
class CriterionJudgement:
    score: float | None
    status: CoverageStatus
    explicit_exclusion: bool = False
    must_have_gap: bool = False
    unresolved: bool = False
    condition_materiality: str | None = None
    note: str = ""


def criterion_type_for(feature: str) -> CriterionType:
    return FEATURE_TYPE.get(feature, CriterionType.COVERAGE)


def expectation_for(requirement: ClientRequirement) -> CoverageExpectation:
    """BASE rejects add-ons. OPTIONAL_ADD_ON and EITHER accept the configured add-on score."""
    if requirement.coverage_expectation == CoverageExpectation.OPTIONAL_ADD_ON:
        return CoverageExpectation.OPTIONAL_ADD_ON
    if requirement.coverage_expectation == CoverageExpectation.BASE or not requirement.accept_add_on:
        return CoverageExpectation.BASE
    return CoverageExpectation.EITHER


def compare_fact(requirement: ClientRequirement, fact: FeatureFact | None, config: ScoringConfig | None = None) -> CriterionJudgement:
    """Score one requirement against one policy fact. Does not read policy id or insurer name."""
    cfg = config or DEFAULT_SCORING
    if fact is None or fact.coverage_status in MISSING:
        status = fact.coverage_status if fact is not None else CoverageStatus.NOT_FOUND
        gap = _is_must_have(requirement)
        return CriterionJudgement(
            score=None,
            status=status,
            unresolved=True,
            must_have_gap=gap,
            note=_missing_note(status),
        )

    status = fact.coverage_status
    if status == CoverageStatus.EXCLUDED:
        gap = _is_must_have(requirement)
        return CriterionJudgement(
            score=cfg.failed_score,
            status=status,
            explicit_exclusion=True,
            must_have_gap=gap,
            note="Explicit exclusion. Negative evidence. Absence of coverage was not inferred as an exclusion.",
        )

    addon = status == CoverageStatus.ADD_ON or fact.is_add_on or fact.add_on_required
    if addon or requirement.type == CriterionType.ADD_ON:
        judgement = _add_on(requirement, fact, status, cfg)
    else:
        scored = _typed_score(requirement, fact, status, cfg)
        if scored is None:
            return CriterionJudgement(
                score=None,
                status=CoverageStatus.NOT_FOUND,
                unresolved=True,
                must_have_gap=_is_must_have(requirement),
                note="No reliable evidence for this requirement. An exclusion list that does not mention the concern is not coverage.",
            )
        score, note, forced_status, explicit = scored
        judgement = CriterionJudgement(
            score=score,
            status=forced_status or status,
            explicit_exclusion=explicit,
            note=note,
        )
    return _finalize(judgement, requirement, fact, cfg)


def _typed_score(
    requirement: ClientRequirement, fact: FeatureFact, status: CoverageStatus, cfg: ScoringConfig,
) -> tuple[float, str, CoverageStatus | None, bool] | None:
    kind = requirement.type
    if kind == CriterionType.WAITING_PERIOD:
        score, note = _waiting(requirement, fact, status, cfg)
        return score, note, None, False
    if kind == CriterionType.COPAYMENT:
        score, note = _copay(requirement, fact, cfg)
        return score, note, None, False
    if kind == CriterionType.DEDUCTIBLE:
        score, note = _deductible(requirement, fact, cfg)
        return score, note, None, False
    if kind == CriterionType.LIMIT:
        score, note = _limit(requirement, fact, status, cfg)
        return score, note, None, False
    if kind == CriterionType.SUBLIMIT:
        score, note = _sublimit(requirement, fact, status, cfg)
        return score, note, None, False
    if kind == CriterionType.EXCLUSION:
        return _exclusion(requirement, fact, cfg)
    if kind == CriterionType.ELIGIBILITY:
        score, note = _eligibility(status, cfg)
        return score, note, None, False
    if kind == CriterionType.GEOGRAPHIC:
        score, note = _geographic(status, cfg)
        return score, note, None, False
    if kind == CriterionType.WELLNESS:
        score, note = _wellness(status, cfg)
        return score, note, None, False
    if kind == CriterionType.CONDITION:
        score, note, _materiality = _condition_score(fact, status, cfg)
        if score is None:
            return None
        return score, note, None, False
    score, note = _coverage(status, cfg)
    return score, note, None, False


def _add_on(requirement: ClientRequirement, fact: FeatureFact, status: CoverageStatus, cfg: ScoringConfig) -> CriterionJudgement:
    expectation = expectation_for(requirement)
    if status == CoverageStatus.COVERED and not fact.is_add_on and not fact.add_on_required:
        score, note = _coverage(status, cfg)
        return CriterionJudgement(score=score, status=status, note=f"{note} Expectation {expectation.value}.")
    if expectation == CoverageExpectation.BASE:
        gap = _is_must_have(requirement)
        return CriterionJudgement(
            score=cfg.failed_score,
            status=CoverageStatus.ADD_ON,
            must_have_gap=gap,
            note=f"Add-on does not satisfy a {expectation.value} requirement.",
        )
    return CriterionJudgement(
        score=cfg.add_on_accepted_score,
        status=CoverageStatus.ADD_ON,
        note=f"Optional add-on. Expectation {expectation.value} accepts add-on availability.",
    )


def _finalize(judgement: CriterionJudgement, requirement: ClientRequirement, fact: FeatureFact, cfg: ScoringConfig) -> CriterionJudgement:
    if judgement.score is None:
        judgement.must_have_gap = judgement.must_have_gap or _is_must_have(requirement)
        return judgement
    judgement.score = _clamp(judgement.score)
    if judgement.explicit_exclusion:
        judgement.must_have_gap = _is_must_have(requirement)
        return judgement
    unique = _unique_conditions(fact)
    if unique and requirement.type != CriterionType.CONDITION:
        worst_text, worst = max(unique, key=lambda item: _MATERIALITY_RANK[item[1]])
        cap = _materiality_score(worst, cfg)
        judgement.score = _clamp(min(judgement.score, cap))
        judgement.condition_materiality = worst.value
        judgement.note = (
            f"{judgement.note} Worst unique condition is {worst.value}: {worst_text}. "
            f"{len(unique)} unique condition(s); not summed."
        ).strip()
    elif unique:
        judgement.condition_materiality = max(unique, key=lambda item: _MATERIALITY_RANK[item[1]])[1].value
    if _is_must_have(requirement) and judgement.score < cfg.must_have_fail_below:
        judgement.must_have_gap = True
    elif judgement.must_have_gap and judgement.score >= cfg.must_have_fail_below and judgement.status != CoverageStatus.ADD_ON:
        judgement.must_have_gap = False
    return judgement


def _coverage(status: CoverageStatus, cfg: ScoringConfig) -> tuple[float, str]:
    if status == CoverageStatus.COVERED:
        return cfg.covered_score, "Stated as base-plan cover."
    if status == CoverageStatus.CONDITIONAL:
        return cfg.conditional_score, "Stated with conditions."
    if status == CoverageStatus.PARTIALLY_COVERED:
        return cfg.partial_score, "Stated with a partial scope."
    return cfg.failed_score, "Not established as cover."


def _eligibility(status: CoverageStatus, cfg: ScoringConfig) -> tuple[float, str]:
    if status == CoverageStatus.COVERED:
        return cfg.eligibility_met_score, "Eligibility rule is met."
    if status == CoverageStatus.CONDITIONAL:
        return cfg.eligibility_conditional_score, "Eligibility is conditional."
    return cfg.eligibility_fail_score, "Eligibility is not met."


def _geographic(status: CoverageStatus, cfg: ScoringConfig) -> tuple[float, str]:
    if status == CoverageStatus.COVERED:
        return cfg.geographic_met_score, "Geography is available."
    if status == CoverageStatus.CONDITIONAL:
        return cfg.geographic_conditional_score, "Geography is conditional."
    return cfg.geographic_fail_score, "Geography is not available."


def _wellness(status: CoverageStatus, cfg: ScoringConfig) -> tuple[float, str]:
    if status == CoverageStatus.COVERED:
        return cfg.wellness_met_score, "Wellness or service benefit is included."
    if status == CoverageStatus.CONDITIONAL:
        return cfg.wellness_conditional_score, "Wellness or service benefit is conditional."
    if status == CoverageStatus.PARTIALLY_COVERED:
        return cfg.wellness_partial_score, "Wellness or service benefit is partial."
    return cfg.failed_score, "Wellness or service benefit is not established."


def _exclusion(requirement: ClientRequirement, fact: FeatureFact, cfg: ScoringConfig) -> tuple[float, str, CoverageStatus | None, bool] | None:
    """Intersect the exclusion text with a client concern. Silence is not cover."""
    concerns = _client_concerns(requirement)
    blob = " ".join(
        [*(fact.exclusions or []), fact.value or "", fact.original_quote or "", fact.limit or ""]
        + [src.source_text for src in fact.sources]
    ).lower()
    listed = " ".join(fact.exclusions or []).lower()
    hits = [word for word in concerns if word in listed]
    negative = ("not covered", "is excluded", "are excluded", "exclusion", "not payable", "does not cover")
    if any(token in blob for token in negative):
        for word in concerns:
            if word in blob and word not in hits:
                hits.append(word)
    if hits:
        return cfg.failed_score, f"Explicit exclusion. The text names the client's concern: {', '.join(hits)}.", CoverageStatus.EXCLUDED, True
    if concerns and _explicit_coverage(blob, concerns):
        return cfg.covered_score, "Explicit coverage evidence names the client's concern and does not exclude it.", CoverageStatus.COVERED, False
    return None


def _explicit_coverage(blob: str, concerns: list[str]) -> bool:
    if any(token in blob for token in ("not covered", "excluded", "exclusion", "not payable")):
        return False
    return any(word in blob and "covered" in blob for word in concerns)


def _client_concerns(requirement: ClientRequirement) -> list[str]:
    raw = f"{requirement.description} {requirement.feature}".lower().replace("_", " ")
    words = []
    for token in raw.replace(":", " ").replace("/", " ").split():
        word = "".join(ch for ch in token if ch.isalpha())
        if len(word) < 5 or word in _GENERIC_EXCLUSION_WORDS:
            continue
        if word not in words:
            words.append(word)
    if requirement.feature == "exclusions":
        return [word for word in words if word != "exclusions"]
    return words


def _waiting(requirement: ClientRequirement, fact: FeatureFact, status: CoverageStatus, cfg: ScoringConfig) -> tuple[float, str]:
    days = fact.waiting_period_days
    if days is None and fact.waiting_period:
        nums = extract_numbers(fact.waiting_period)
        if nums.durations:
            val, unit = nums.durations[0]
            days = val if unit == "day" else val * 30 if unit == "month" else val * 365
    full = requirement.target_value if requirement.target_unit in {"day", "days"} and requirement.target_value is not None else cfg.waiting_full_days.get(requirement.feature, cfg.waiting_default_full_days)
    partial = full + (requirement.tolerance or 0.0) if requirement.target_value is not None else cfg.waiting_partial_days.get(requirement.feature, full)
    if days is None:
        base, note = _coverage(status, cfg)
        return min(base, cfg.waiting_unquantified_cap), note + " Waiting length is not quantified."
    if not math.isfinite(days):
        return cfg.waiting_unquantified_cap, "Waiting length is not a finite number."
    original = fact.waiting_period or f"{days:g} days"
    if days <= full:
        return cfg.waiting_full_score, f"Waiting period {original} is within the full-fit threshold."
    if days <= partial:
        return cfg.waiting_partial_score, f"Waiting period {original} is longer than the full-fit threshold but inside tolerance."
    return cfg.waiting_long_score, f"Waiting period {original} is longer than the partial-fit threshold."


def _copay(requirement: ClientRequirement, fact: FeatureFact, cfg: ScoringConfig) -> tuple[float, str]:
    percent = fact.copay_percent
    if percent is None and fact.copay:
        nums = extract_numbers(fact.copay)
        if nums.percents:
            percent = nums.percents[0]
    target = requirement.target_value if requirement.target_value is not None else cfg.copay_target_percent
    tolerance = requirement.tolerance if requirement.tolerance is not None else cfg.copay_tolerance_percent
    if percent is None or not math.isfinite(percent):
        return cfg.copay_unquantified_score, "Co-payment is mentioned but the percent is not a finite number."
    original = fact.copay or f"{percent:g}%"
    if percent <= target + tolerance:
        return cfg.copay_full_score, f"Co-payment {original} meets the target."
    if percent <= target + tolerance + cfg.copay_partial_band:
        return cfg.copay_partial_score, f"Co-payment {original} is above the target but inside the partial band."
    return cfg.copay_fail_score, f"Co-payment {original} is above the partial band. Higher co-pay is a worse fit."


def _deductible(requirement: ClientRequirement, fact: FeatureFact, cfg: ScoringConfig) -> tuple[float, str]:
    amount = fact.deductible_amount
    text = " ".join(x for x in (fact.deductible, fact.limit, fact.original_quote, fact.value) if x)
    if amount is None and text:
        nums = extract_numbers(text)
        if nums.money:
            amount = nums.money[0]
    if amount is None or not math.isfinite(amount):
        lowered = text.lower()
        if "no deductible" in lowered or "nil deductible" in lowered:
            return cfg.covered_score, "Brochure states no deductible."
        return cfg.deductible_unquantified_score, "Deductible options are stated without a mandatory amount."
    cap = requirement.target_value or cfg.deductible_reference_inr
    original = fact.deductible or fact.limit_original_text or f"INR {amount:g}"
    score = deductible_fit_score(amount, cap)
    return score, f"Deductible score = 100 * (1 - min(max(A, 0), R) / R) with A={amount:g} and R={cap:g}."


def _limit(requirement: ClientRequirement, fact: FeatureFact, status: CoverageStatus, cfg: ScoringConfig) -> tuple[float, str]:
    text = " ".join(x for x in (fact.limit, fact.limit_original_text, fact.original_quote, fact.value) if x)
    nums = extract_numbers(text) if text else None
    if nums and (nums.unlimited or nums.up_to_sum_insured):
        return cfg.covered_score, "Limit is up to sum insured or unlimited."
    amount = fact.limit_numeric
    if amount is None and nums and nums.money:
        amount = nums.money[0]
    if amount is not None and not math.isfinite(amount):
        amount = None
    if requirement.target_value is not None and amount is not None:
        tolerance = requirement.tolerance or 0.0
        floor = requirement.target_value * (1.0 - tolerance)
        original = fact.limit_original_text or fact.limit or text
        if amount >= requirement.target_value:
            return cfg.covered_score, f"Limit {original} meets the target."
        if amount >= floor:
            return cfg.limit_inside_tolerance_score, f"Limit {original} is inside tolerance of the target."
        return cfg.limit_below_target_score, f"Limit {original} is below the target."
    if amount is not None:
        return cfg.limit_no_target_score, "A numeric limit is stated. No client target was set, so this is not full fit."
    return _coverage(status, cfg)


def _sublimit(requirement: ClientRequirement, fact: FeatureFact, status: CoverageStatus, cfg: ScoringConfig) -> tuple[float, str]:
    text = " ".join(x for x in (fact.limit, fact.original_quote, fact.value, fact.sublimit) if x).lower()
    if any(token in text for token in ("at actual", "no sub-limit", "no sublimit", "no capping", "unlimited")):
        return cfg.covered_score, "No sub-limit; benefit is at actuals."
    if fact.limit_numeric is not None or (fact.limit and any(ch.isdigit() for ch in fact.limit)):
        return cfg.sublimit_cap_score, "A sub-limit or cap is stated."
    base, note = _coverage(status, cfg)
    return min(base, cfg.sublimit_unquantified_cap), note


def _condition_score(fact: FeatureFact, status: CoverageStatus, cfg: ScoringConfig) -> tuple[float | None, str, str | None]:
    unique = _unique_conditions(fact)
    if not unique:
        if status in MISSING:
            return None, "No condition evidence.", None
        if status == CoverageStatus.COVERED:
            return cfg.condition_info_score, "No named condition on stated cover.", ConditionMateriality.INFO.value
        base, note = _coverage(status, cfg)
        return base, note, None
    worst_text, worst = max(unique, key=lambda item: _MATERIALITY_RANK[item[1]])
    return _materiality_score(worst, cfg), f"Condition {worst.value}: {worst_text}.", worst.value


def _unique_conditions(fact: FeatureFact) -> list[tuple[str, ConditionMateriality]]:
    found: dict[str, ConditionMateriality] = {}

    def keep(text: str, materiality: ConditionMateriality) -> None:
        key = " ".join(text.lower().split())
        if not key:
            return
        current = found.get(key)
        if current is None or _MATERIALITY_RANK[materiality] > _MATERIALITY_RANK[current]:
            found[key] = materiality

    for item in fact.condition_details:
        keep(item.text, item.materiality)
    for raw in fact.conditions:
        text = raw.strip()
        materiality = None
        for name in ConditionMateriality:
            prefix = name.value + ":"
            if text.upper().startswith(prefix):
                materiality = name
                text = text[len(prefix):].strip()
                break
        if materiality is None:
            continue
        keep(text, materiality)
    return list(found.items())


def _materiality_score(materiality: ConditionMateriality, cfg: ScoringConfig) -> float:
    return {
        ConditionMateriality.INFO: cfg.condition_info_score,
        ConditionMateriality.MINOR: cfg.condition_minor_score,
        ConditionMateriality.MATERIAL: cfg.condition_material_score,
        ConditionMateriality.CRITICAL: cfg.condition_critical_score,
    }[materiality]


def _is_must_have(requirement: ClientRequirement) -> bool:
    return requirement.requirement_class.value == "MUST_HAVE" or requirement.hard_constraint


def _missing_note(status: CoverageStatus) -> str:
    if status == CoverageStatus.UNKNOWN:
        return "UNKNOWN. This is not cover and not an exclusion."
    if status == CoverageStatus.REVIEW_REQUIRED:
        return "REVIEW_REQUIRED. This is not positive evidence."
    return "NOT_FOUND. Missing evidence lowers completeness. It is not cover and not an exclusion."


def _clamp(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(100.0, value))
