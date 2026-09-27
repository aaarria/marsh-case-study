"""Turn exposures and scenarios into typed client requirements with explicit normalized weights.

When the advisor states a priority, those requirements share the advisor pool (default 65%).
Baseline hospitalisation, waiting-period and out-of-pocket items share the baseline pool (default 35%).
A feature that is both baseline and a client priority is counted once, at the client class.

Inside a pool:
    weight_i = pool_weight * priority_weight_i / sum(priority_weight for that pool)

Zero priority contributes nothing. Negative, NaN, and infinite priorities are invalid:
they receive weight 0 and cannot change the denominator.
"""
from __future__ import annotations

import math

from app.models.client import Exposure, FactKind
from app.models.fit import ClientRequirement, CoverageExpectation, RequirementClass, Scenario
from app.policies.features import FEATURE_LABELS
from app.policy_fit.criteria import criterion_type_for
from app.policy_fit.scoring_config import DEFAULT_SCORING, ScoringConfig
from app.utils.ids import stable_id

# Pool split only. Per-requirement weights are priority_weight inside each pool.
CLIENT_POOL = DEFAULT_SCORING.advisor_pool
BASELINE_POOL = DEFAULT_SCORING.baseline_pool
_MUST_WORDS = ("must", "required", "mandatory", "non-negotiable")
_BASE_ONLY_WORDS = ("base policy", "base plan", "base cover", "not as an add-on", "not as an addon")
_ADD_ON_ONLY_WORDS = ("only as an add-on", "only as an addon", "add-on only", "addon only", "optional add-on only")
_ADD_ON_OK_WORDS = ("add-on", "addon", "rider", "optional")


def build_requirements(exposures: list[Exposure]) -> list[ClientRequirement]:
    """One requirement per feature. The stronger class wins when two exposures name the same feature."""
    chosen: dict[str, ClientRequirement] = {}
    rank = {RequirementClass.EXPOSURE: 0, RequirementClass.BASELINE: 1, RequirementClass.PREFERENCE: 2, RequirementClass.MUST_HAVE: 3}
    for exposure in exposures:
        klass = _class_for(exposure)
        for feature in exposure.feature_keys:
            req = _from_exposure(exposure, feature, klass)
            current = chosen.get(feature)
            if current is None or rank[klass] > rank[current.requirement_class]:
                chosen[feature] = req
            elif current is not None and rank[klass] == rank[current.requirement_class] and exposure.confidence > current.confidence:
                chosen[feature] = req
    return assign_weights(list(chosen.values()))


def requirements_from_scenarios(scenarios: list[Scenario]) -> list[ClientRequirement]:
    """Each scenario is its own requirement. Used by the arena path and property tests."""
    reqs = []
    for scenario in scenarios:
        feature = scenario.feature_keys[0] if scenario.feature_keys else "in_patient_hospitalisation"
        text = f"{scenario.title} {scenario.description}".lower()
        if any(word in text for word in _MUST_WORDS):
            klass = RequirementClass.MUST_HAVE
        elif scenario.client_asked:
            klass = RequirementClass.PREFERENCE
        else:
            klass = RequirementClass.BASELINE
        reqs.append(
            ClientRequirement(
                requirement_id=scenario.scenario_id,
                description=scenario.description or scenario.title,
                feature=feature,
                type=criterion_type_for(feature),
                priority_weight=float(scenario.weight) if scenario.weight else 1.0,
                requirement_class=klass,
                hard_constraint=klass == RequirementClass.MUST_HAVE,
                source=scenario.exposure_id,
                confidence=1.0,
                client_asked=scenario.client_asked or klass != RequirementClass.BASELINE,
                assumption=False,
                accept_add_on=_expectation(text, klass) != CoverageExpectation.BASE,
                coverage_expectation=_expectation(text, klass),
            )
        )
    return assign_weights(reqs)


def assign_weights(requirements: list[ClientRequirement], config: ScoringConfig | None = None) -> list[ClientRequirement]:
    """Split weight into pools, then by each requirement's own priority_weight.

    If advisor requirements and baseline requirements both exist, use advisor_pool and
    baseline_pool. Otherwise the nonempty scored pool gets 1. Exposure hypotheses use
    exposure_pool, which is 0 unless configured, so they do not enter the fit.

    Inside a pool:
        weight_i = pool * priority_weight_i / sum(usable priority_weight in that pool).

    Usable priorities are finite and >= 0. Zero adds nothing. Negative, NaN, and
    infinite values are marked weight_valid False and stay at weight 0.
    """
    if not requirements:
        return []
    cfg = config or DEFAULT_SCORING
    advisor_pool = _pool(cfg.advisor_pool, "advisor_pool")
    baseline_pool = _pool(cfg.baseline_pool, "baseline_pool")
    exposure_pool = _pool(cfg.exposure_pool, "exposure_pool")
    client = [r for r in requirements if r.requirement_class in {RequirementClass.PREFERENCE, RequirementClass.MUST_HAVE}]
    baseline = [r for r in requirements if r.requirement_class == RequirementClass.BASELINE]
    exposure = [r for r in requirements if r.requirement_class == RequirementClass.EXPOSURE]
    if client and baseline:
        _split(client, advisor_pool)
        _split(baseline, baseline_pool)
    elif client:
        _split(client, 1.0)
    elif baseline:
        _split(baseline, 1.0)
    _split(exposure, exposure_pool if (client or baseline) else (exposure_pool or 0.0))
    if not client and not baseline and exposure:
        _split(exposure, exposure_pool)
    return requirements


def _pool(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return float(value)


def _usable_priority(value: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, bool) or not math.isfinite(number) or number < 0:
        return None
    return number


def _split(rows: list[ClientRequirement], pool: float) -> None:
    usable: list[tuple[ClientRequirement, float]] = []
    for row in rows:
        parsed = _usable_priority(row.priority_weight)
        if parsed is None:
            row.weight = 0.0
            row.weight_valid = False
            row.priority_status = "REVIEW_REQUIRED"
        else:
            row.weight_valid = True
            row.priority_status = "VALID"
            usable.append((row, parsed))
    raw = sum(priority for _, priority in usable)
    if raw <= 0 or not math.isfinite(raw):
        for row, _priority in usable:
            row.weight = 0.0
        return
    for row, priority in usable:
        row.weight = pool * (priority / raw)


def _class_for(exposure: Exposure) -> RequirementClass:
    text = f"{exposure.title} {exposure.description}".lower()
    if any(word in text for word in _MUST_WORDS):
        return RequirementClass.MUST_HAVE
    stated = exposure.title.lower().startswith("advisor priority") or "advisor_priority" in (exposure.basis or [])
    if stated:
        return RequirementClass.PREFERENCE
    if "baseline_programme" in (exposure.basis or []):
        return RequirementClass.BASELINE
    return RequirementClass.EXPOSURE


def _expectation(text: str, klass: RequirementClass) -> CoverageExpectation:
    if any(phrase in text for phrase in _BASE_ONLY_WORDS):
        return CoverageExpectation.BASE
    if any(phrase in text for phrase in _ADD_ON_ONLY_WORDS):
        return CoverageExpectation.OPTIONAL_ADD_ON
    if klass == RequirementClass.MUST_HAVE and not any(phrase in text for phrase in _ADD_ON_OK_WORDS):
        return CoverageExpectation.BASE
    return CoverageExpectation.EITHER


def _from_exposure(exposure: Exposure, feature: str, klass: RequirementClass) -> ClientRequirement:
    text = f"{exposure.title} {exposure.description}".lower()
    label = FEATURE_LABELS.get(feature, feature)
    return ClientRequirement(
        requirement_id=stable_id(exposure.exposure_id, feature, length=12),
        description=f"{label}: {exposure.description}".strip(),
        feature=feature,
        type=criterion_type_for(feature),
        priority_weight=float(exposure.priority),
        requirement_class=klass,
        hard_constraint=klass == RequirementClass.MUST_HAVE,
        source="advisor_priority" if klass in {RequirementClass.PREFERENCE, RequirementClass.MUST_HAVE} else ("exposure_hypothesis" if klass == RequirementClass.EXPOSURE else "baseline"),
        confidence=float(exposure.confidence),
        client_asked=klass in {RequirementClass.PREFERENCE, RequirementClass.MUST_HAVE},
        assumption=exposure.status == FactKind.ASSUMPTION,
        accept_add_on=_expectation(text, klass) != CoverageExpectation.BASE,
        coverage_expectation=_expectation(text, klass),
    )
