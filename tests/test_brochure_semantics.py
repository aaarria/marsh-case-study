"""Brochure-backed normalization and scoring.

Scores come from the four supplied brochures. Policy identity is not an input.
"""
from __future__ import annotations

from itertools import permutations

import pytest

from app.models.fit import ClientRequirement, CoverageExpectation, RequirementClass
from app.models.policy import CoverageStatus, FeatureFact
from app.policies.conditions import canonicalize_results
from app.policies.extraction import get_policy_facts
from app.policy_fit.criteria import compare_fact, criterion_type_for
from app.policy_fit.requirements import assign_weights
from app.policy_fit.scoring import recommend, score_policies

POLICIES = ["abhi_activ_one", "care_supreme", "hdfc_optima_secure_plus", "niva_reassure_2"]


@pytest.fixture(scope="module")
def books(retriever):
    assert retriever.ready
    return canonicalize_results(get_policy_facts(POLICIES))


def _fact(feature: str, status: CoverageStatus, **kwargs) -> FeatureFact:
    quote = kwargs.pop("quote", None)
    return FeatureFact(
        policy_id=kwargs.pop("policy_id", "synthetic"),
        feature=feature,
        coverage_status=status,
        value=kwargs.pop("value", quote),
        original_quote=quote,
        **kwargs,
    )


def _req(feature: str, description: str, klass=RequirementClass.PREFERENCE, expectation=CoverageExpectation.EITHER, weight=1.0):
    return ClientRequirement(
        requirement_id=feature,
        description=description,
        feature=feature,
        type=criterion_type_for(feature),
        priority_weight=weight,
        requirement_class=klass,
        source="advisor_priority" if klass != RequirementClass.BASELINE else "baseline",
        client_asked=klass != RequirementClass.BASELINE,
        accept_add_on=expectation != CoverageExpectation.BASE,
        coverage_expectation=expectation,
    )


def _hospital():
    return _req("in_patient_hospitalisation", "In-patient hospitalisation", RequirementClass.BASELINE)


def _score(books, feature, pid, description, expectation=CoverageExpectation.EITHER):
    req = assign_weights([_req(feature, description, expectation=expectation)])[0]
    return compare_fact(req, books[pid].facts[feature])


def _profile(books, requirements):
    rows = assign_weights(requirements)
    fits = score_policies(POLICIES, rows, books)
    return {fit.policy_id: fit for fit in fits}, recommend(fits, [], {})


def _cell(fit, feature):
    return next(row for row in fit.contributions if row.feature == feature)


def test_room_rent_wordings_score_differently_without_a_policy_identity():
    req = assign_weights([_req("room_rent", "No room rent capping")])[0]
    req.type = criterion_type_for("room_rent")
    actuals = compare_fact(req, _fact("room_rent", CoverageStatus.COVERED, value="Room Rent: At actuals"))
    ceiling = compare_fact(req, _fact("room_rent", CoverageStatus.COVERED, value="Room Rent: Up to SI"))
    capped = compare_fact(req, _fact("room_rent", CoverageStatus.COVERED, limit="room rent capped at INR 5,000"))
    missing = compare_fact(req, _fact("room_rent", CoverageStatus.NOT_FOUND, value=None, quote=None))
    renamed = compare_fact(req, _fact("room_rent", CoverageStatus.COVERED, value="Room Rent: At actuals", policy_id="other-brochure"))
    assert actuals.score == 100
    assert ceiling.score == 75
    assert capped.score == 60
    assert missing.score is None and missing.status == CoverageStatus.NOT_FOUND
    assert renamed.score == actuals.score


def test_four_brochures_normalize_the_same_concepts_under_different_names(books):
    abhi, care, hdfc, niva = (books[pid] for pid in POLICIES)

    assert "room rent" in (abhi.facts["room_rent"].value or "").lower()
    assert "base sum insured" in (abhi.facts["room_rent"].value or "").lower()
    assert _score(books, "room_rent", "abhi_activ_one", "No room rent capping").score == 75
    assert "up to si" in (care.facts["room_rent"].value or "").lower()
    assert _score(books, "room_rent", "care_supreme", "No room rent capping").score == 75
    assert "at actuals" in (hdfc.facts["room_rent"].value or "").lower()
    assert _score(books, "room_rent", "hdfc_optima_secure_plus", "No room rent capping").score == 100
    assert niva.facts["room_rent"].coverage_status == CoverageStatus.NOT_FOUND
    assert _score(books, "room_rent", "niva_reassure_2", "No room rent capping").score is None

    assert abhi.facts["chronic_conditions_day1"].coverage_status == CoverageStatus.CONDITIONAL
    assert abhi.facts["chronic_conditions_day1"].waiting_period_days == 0
    assert "zero waiting" in (abhi.facts["chronic_conditions_day1"].original_quote or "").lower()
    assert care.facts["chronic_conditions_day1"].coverage_status == CoverageStatus.ADD_ON
    assert care.facts["chronic_conditions_day1"].waiting_period_days == 30
    assert hdfc.facts["chronic_conditions_day1"].coverage_status == CoverageStatus.ADD_ON
    assert hdfc.facts["chronic_conditions_day1"].waiting_period_days == 31
    assert niva.facts["chronic_conditions_day1"].coverage_status == CoverageStatus.NOT_FOUND
    assert _score(books, "chronic_conditions_day1", "abhi_activ_one", "Chronic conditions from day one").score == 70
    assert _score(books, "chronic_conditions_day1", "care_supreme", "Chronic conditions from day one").score == 45
    assert _score(books, "chronic_conditions_day1", "hdfc_optima_secure_plus", "Chronic conditions from day one").score == 30

    assert "maternity" in (abhi.facts["maternity"].original_quote or abhi.facts["maternity"].value or "").lower()
    assert abhi.facts["maternity"].coverage_status == CoverageStatus.CONDITIONAL
    assert hdfc.facts["maternity"].coverage_status == CoverageStatus.ADD_ON
    assert hdfc.facts["maternity"].is_add_on
    assert "parenthood" in (hdfc.facts["maternity"].original_quote or "").lower()
    assert care.facts["maternity"].coverage_status == CoverageStatus.NOT_FOUND
    assert niva.facts["maternity"].coverage_status == CoverageStatus.NOT_FOUND
    either = _score(books, "maternity", "hdfc_optima_secure_plus", "Maternity benefits")
    base = _score(books, "maternity", "hdfc_optima_secure_plus", "Base policy must include maternity", CoverageExpectation.BASE)
    assert either.score == 60 and base.score == 0

    assert "super reload" in (abhi.facts["restore_recharge"].original_quote or "").lower()
    assert "unlimited automatic recharge" in (care.facts["restore_recharge"].value or "").lower()
    assert "automatic restore" in (hdfc.facts["restore_recharge"].value or "").lower()
    assert "reassure+" in (niva.facts["restore_recharge"].original_quote or niva.facts["restore_recharge"].value or "").lower()
    assert all(books[pid].facts["restore_recharge"].coverage_status == CoverageStatus.COVERED for pid in POLICIES)

    assert abhi.facts["global_cover"].coverage_status == CoverageStatus.CONDITIONAL
    assert "abroad" in (abhi.facts["global_cover"].original_quote or "").lower()
    assert care.facts["global_cover"].coverage_status == CoverageStatus.NOT_FOUND
    assert hdfc.facts["global_cover"].coverage_status == CoverageStatus.NOT_FOUND
    assert niva.facts["global_cover"].coverage_status == CoverageStatus.NOT_FOUND

    assert hdfc.facts["personal_accident"].coverage_status == CoverageStatus.ADD_ON
    assert niva.facts["personal_accident"].coverage_status == CoverageStatus.ADD_ON
    assert abhi.facts["personal_accident"].coverage_status == CoverageStatus.NOT_FOUND
    assert care.facts["personal_accident"].coverage_status == CoverageStatus.NOT_FOUND

    assert "healthreturns" in (abhi.facts["wellness_renewal_discount"].value or "").lower()
    assert care.facts["wellness_renewal_discount"].is_add_on
    assert hdfc.facts["wellness_renewal_discount"].coverage_status == CoverageStatus.NOT_FOUND
    assert "live healthy" in (niva.facts["wellness_renewal_discount"].original_quote or niva.facts["wellness_renewal_discount"].value or "").lower()

    assert hdfc.facts["teleconsultation_opd"].coverage_status == CoverageStatus.ADD_ON
    assert "wellbeing" in (hdfc.facts["teleconsultation_opd"].value or "").lower()
    assert niva.facts["teleconsultation_opd"].coverage_status == CoverageStatus.COVERED
    assert "e-consultation" in (niva.facts["teleconsultation_opd"].value or "").lower()

    assert hdfc.facts["copay"].copay_percent == 0
    assert niva.facts["copay"].coverage_status == CoverageStatus.ADD_ON
    assert niva.facts["copay"].copay_percent == 20
    assert abhi.facts["copay"].coverage_status == CoverageStatus.NOT_FOUND
    assert care.facts["copay"].coverage_status == CoverageStatus.NOT_FOUND
    assert hdfc.facts["deductible_options"].coverage_status == CoverageStatus.ADD_ON
    assert "25,000" in (hdfc.facts["deductible_options"].original_quote or "")
    assert niva.facts["deductible_options"].coverage_status == CoverageStatus.ADD_ON
    assert "20,000" in (niva.facts["deductible_options"].value or "")


def test_client_profiles_change_the_recommendation(books):
    room, rec_a = _profile(books, [_req("room_rent", "No room rent capping", weight=1.8), _hospital()])
    assert rec_a.recommended_policy_id == "hdfc_optima_secure_plus"
    assert rec_a.decision_state == "eligible"
    assert room["hdfc_optima_secure_plus"].score == 100
    assert room["abhi_activ_one"].score == room["care_supreme"].score == 83.75
    assert _cell(room["niva_reassure_2"], "room_rent").criterion_score is None
    assert room["niva_reassure_2"].decision_sufficient is False

    chronic, rec_b = _profile(books, [_req("chronic_conditions_day1", "Chronic conditions from day one", weight=1.8), _hospital()])
    assert rec_b.recommended_policy_id == "abhi_activ_one"
    assert [chronic[pid].score for pid in ("abhi_activ_one", "care_supreme", "hdfc_optima_secure_plus")] == [80.5, 64.25, 54.5]
    assert _cell(chronic["abhi_activ_one"], "chronic_conditions_day1").criterion_score == 70
    assert _cell(chronic["care_supreme"], "chronic_conditions_day1").criterion_score == 45
    assert _cell(chronic["hdfc_optima_secure_plus"], "chronic_conditions_day1").criterion_score == 30

    maternity, rec_c = _profile(books, [_req("maternity", "Maternity benefits", weight=1.8), _hospital()])
    assert rec_c.recommended_policy_id == "abhi_activ_one"
    assert maternity["abhi_activ_one"].score == 80.5
    assert _cell(maternity["hdfc_optima_secure_plus"], "maternity").criterion_score == 60
    assert maternity["hdfc_optima_secure_plus"].score == 74

    base_only, rec_base = _profile(books, [
        _req("maternity", "Base policy must include maternity", expectation=CoverageExpectation.BASE, weight=1.8),
        _hospital(),
    ])
    assert rec_base.recommended_policy_id == "abhi_activ_one"
    assert _cell(base_only["hdfc_optima_secure_plus"], "maternity").criterion_score == 0
    assert base_only["hdfc_optima_secure_plus"].score == 35

    cost, rec_d = _profile(books, [
        _req("copay", "Cost control co-pay options"),
        _req("deductible_options", "Cost control deductible options"),
    ])
    assert rec_d.recommended_policy_id == "niva_reassure_2"
    assert cost["niva_reassure_2"].score == 90
    assert _cell(cost["niva_reassure_2"], "copay").criterion_score == 80
    assert _cell(cost["niva_reassure_2"], "deductible_options").criterion_score == 100
    assert cost["hdfc_optima_secure_plus"].score == 70
    assert _cell(cost["hdfc_optima_secure_plus"], "copay").criterion_score == 40
    assert cost["abhi_activ_one"].decision_sufficient is False
    assert cost["care_supreme"].decision_sufficient is False

    travel, rec_e = _profile(books, [_req("global_cover", "Global cover for travel", weight=1.8), _hospital()])
    assert rec_e.recommended_policy_id == "abhi_activ_one"
    assert _cell(travel["abhi_activ_one"], "global_cover").criterion_score == 70
    assert travel["abhi_activ_one"].score == 80.5
    assert travel["hdfc_optima_secure_plus"].decision_sufficient is False

    winners = {
        rec_a.recommended_policy_id,
        rec_b.recommended_policy_id,
        rec_d.recommended_policy_id,
        rec_e.recommended_policy_id,
    }
    assert len(winners) >= 3


def test_weight_and_requirement_changes_move_the_result(books):
    hospital_only, rec = _profile(books, [_hospital()])
    assert rec.recommended_policy_id == ""
    assert rec.decision_state == "close_decision"
    assert {fit.score for fit in hospital_only.values()} == {100}

    room_heavy, rec_room = _profile(books, [
        _req("room_rent", "No room rent capping", weight=20),
        _req("maternity", "Maternity benefits", weight=1),
        _hospital(),
    ])
    maternity_heavy, rec_mat = _profile(books, [
        _req("room_rent", "No room rent capping", weight=1),
        _req("maternity", "Maternity benefits", weight=20),
        _hospital(),
    ])
    assert rec_room.recommended_policy_id == "hdfc_optima_secure_plus"
    assert rec_mat.recommended_policy_id == "abhi_activ_one"
    assert room_heavy["hdfc_optima_secure_plus"].score > maternity_heavy["hdfc_optima_secure_plus"].score


def test_policy_order_does_not_change_scores_or_the_recommendation(books):
    rows = assign_weights([_req("room_rent", "No room rent capping", weight=1.8), _hospital()])
    signatures = set()
    for order in permutations(POLICIES):
        fits = score_policies(list(order), rows, books)
        rec = recommend(fits, [], {})
        signatures.add((
            tuple(sorted((fit.policy_id, fit.score, fit.decision_sufficient, fit.decision_state) for fit in fits)),
            rec.recommended_policy_id,
            rec.decision_state,
        ))
    assert len(signatures) == 1
