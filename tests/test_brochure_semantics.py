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

    assert niva.facts["teleconsultation_opd"].coverage_status == CoverageStatus.COVERED
    assert "e-consultation" in (niva.facts["teleconsultation_opd"].value or "").lower()
    assert niva.facts["opd"].coverage_status == CoverageStatus.NOT_FOUND
    assert hdfc.facts["opd"].coverage_status == CoverageStatus.ADD_ON
    assert "wellbeing" in (hdfc.facts["opd"].value or "").lower()
    assert hdfc.facts["teleconsultation_opd"].coverage_status == CoverageStatus.NOT_FOUND

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
        tuple(item.policy_id for item in rec.alternatives),
    ))
    assert len(signatures) == 1


def test_exposure_hypotheses_do_not_enter_the_fit(books):
    from app.models.client import Exposure, FactKind
    from app.policy_fit.requirements import build_requirements

    hypothesis = Exposure(exposure_id="h", title="International workforce may need broader cover", description="Travel may matter.", basis=[], reasoning="Hypothesis", status=FactKind.INFERENCE, priority=2, feature_keys=["global_cover"])
    other = Exposure(exposure_id="o", title="Factory accidents", description="Sites raise accident exposure.", basis=[], reasoning="Hypothesis", status=FactKind.ASSUMPTION, priority=2, feature_keys=["personal_accident"])
    asked = Exposure(exposure_id="a", title="Advisor priority: no room rent capping", description="No room rent capping", basis=["advisor_priority"], reasoning="Selected", status=FactKind.FACT, priority=1, feature_keys=["room_rent"])
    first = build_requirements([hypothesis, asked])
    second = build_requirements([other, asked])
    assert next(row for row in first if row.feature == "global_cover").weight == 0
    assert next(row for row in first if row.feature == "room_rent").weight == 1
    assert next(row for row in second if row.feature == "room_rent").weight == 1
    left, _ = _profile(books, [_req("room_rent", "No room rent capping"), _hospital()])
    right_reqs = build_requirements([hypothesis, asked])
    # The selected room-rent priority keeps the client pool. The hypothesis does not add weight.
    assert abs(next(row for row in right_reqs if row.feature == "room_rent").weight - 1) < 1e-9
    assert left["hdfc_optima_secure_plus"].score == 100


def test_priority_combinations_change_scores_and_can_change_the_primary(books):
    room, rec_room = _profile(books, [_req("room_rent", "No room rent capping"), _hospital()])
    chronic, rec_chronic = _profile(books, [_req("chronic_conditions_day1", "Chronic conditions from day one"), _hospital()])
    both, rec_both = _profile(books, [
        _req("room_rent", "No room rent capping"),
        _req("chronic_conditions_day1", "Chronic conditions from day one"),
        _hospital(),
    ])
    room_global, rec_global = _profile(books, [
        _req("room_rent", "No room rent capping"),
        _req("global_cover", "Global cover for travel"),
        _hospital(),
    ])
    assert room["hdfc_optima_secure_plus"].score != both["hdfc_optima_secure_plus"].score
    assert rec_room.recommended_policy_id == "hdfc_optima_secure_plus"
    assert rec_chronic.recommended_policy_id == "abhi_activ_one"
    assert rec_both.decision_state == "close_decision"
    assert rec_both.recommended_policy_id == ""
    assert {item.policy_id for item in rec_both.alternatives} >= {"abhi_activ_one", "hdfc_optima_secure_plus"}
    assert rec_global.recommended_policy_id != rec_chronic.recommended_policy_id or room_global["abhi_activ_one"].score != chronic["abhi_activ_one"].score
    assert any(not fit.decision_sufficient for fit in room.values())
    assert {item.policy_id for item in rec_room.alternatives}.isdisjoint({"niva_reassure_2"}) or all(
        books["niva_reassure_2"].facts["room_rent"].coverage_status.value == "NOT_FOUND" for _ in [0]
    )
    insufficient = {fit.policy_id for fit in room.values() if not fit.decision_sufficient}
    assert insufficient.isdisjoint({item.policy_id for item in rec_room.alternatives})


def test_wellness_and_opd_are_separate_requirements(books):
    fits, rec = _profile(books, [
        _req("wellness_renewal_discount", "Wellness and OPD", weight=1),
        _req("opd", "Wellness and OPD", weight=1),
        _hospital(),
    ])
    rows = assign_weights([
        _req("wellness_renewal_discount", "Wellness and OPD", weight=1),
        _req("opd", "Wellness and OPD", weight=1),
        _hospital(),
    ])
    assert abs(sum(row.weight for row in rows if row.feature != "in_patient_hospitalisation") - 0.65) < 1e-9
    assert abs(rows[0].weight - rows[1].weight) < 1e-9
    assert _cell(fits["niva_reassure_2"], "opd").criterion_score is None
    assert _cell(fits["care_supreme"], "opd").criterion_score is not None
    assert _cell(fits["hdfc_optima_secure_plus"], "opd").criterion_score is not None
    assert _cell(fits["abhi_activ_one"], "opd").criterion_score is None
    assert len({fit.score for fit in fits.values()}) > 1
    assert rec.decision_state in {"eligible", "close_decision", "incomplete_comparison"}


def test_opd_is_not_econsultation_and_accident_cover_is_not_a_waiting_exception(books):
    from app.policies.features import map_text_to_features

    niva = books["niva_reassure_2"]
    care = books["care_supreme"]
    hdfc = books["hdfc_optima_secure_plus"]
    abhi = books["abhi_activ_one"]

    assert niva.facts["opd"].coverage_status == CoverageStatus.NOT_FOUND
    assert niva.facts["opd"].coverage_tier == "NOT_ESTABLISHED"
    assert niva.facts["teleconsultation_opd"].coverage_status == CoverageStatus.COVERED
    assert "e-consultation" in (niva.facts["teleconsultation_opd"].value or "").lower()
    assert "opd" not in (niva.facts["teleconsultation_opd"].value or "").lower()

    assert care.facts["opd"].coverage_status == CoverageStatus.ADD_ON
    assert "physical consultation" in (care.facts["opd"].original_quote or care.facts["opd"].value or "").lower()
    assert care.facts["opd"].is_add_on

    assert hdfc.facts["opd"].coverage_status == CoverageStatus.ADD_ON
    assert hdfc.facts["opd"].coverage_tier == "ADD_ON"
    assert "outpatient" in (hdfc.facts["opd"].original_quote or "").lower()
    assert hdfc.facts["teleconsultation_opd"].coverage_status == CoverageStatus.NOT_FOUND

    exception = care.facts["accident_waiting_exception"]
    assert "not applicable on accident" in (exception.original_quote or "").lower()
    assert care.facts["personal_accident"].coverage_status == CoverageStatus.NOT_FOUND
    assert _score(books, "personal_accident", "care_supreme", "Accident cover").score is None

    assert hdfc.facts["personal_accident"].coverage_tier == "RIDER"
    assert niva.facts["personal_accident"].coverage_tier == "OPTIONAL"
    assert abhi.facts["opd"].coverage_status == CoverageStatus.NOT_FOUND
    assert abhi.facts["personal_accident"].coverage_status == CoverageStatus.NOT_FOUND
    assert _score(books, "opd", "abhi_activ_one", "Wellness and OPD").score is None
    assert _score(books, "personal_accident", "abhi_activ_one", "Accident cover").score is None

    assert map_text_to_features("wellness and OPD") == ["wellness_renewal_discount", "opd"]
    assert map_text_to_features("accident cover") == ["personal_accident"]
    mapped_exception = map_text_to_features("initial waiting period not applicable on accident cases")
    assert "personal_accident" not in mapped_exception
    assert "accident_waiting_exception" in mapped_exception

    accident, rec_accident = _profile(books, [_req("personal_accident", "Accident cover", weight=1.8), _hospital()])
    assert _cell(accident["care_supreme"], "personal_accident").criterion_score is None
    assert _cell(accident["hdfc_optima_secure_plus"], "personal_accident").criterion_score is not None
    assert _cell(accident["niva_reassure_2"], "personal_accident").criterion_score is not None
    assert _cell(accident["abhi_activ_one"], "personal_accident").criterion_score is None
    assert rec_accident.decision_state in {"eligible", "close_decision", "incomplete_comparison"}


def test_recalculation_interprets_a_measurable_change_and_refuses_a_vague_one(books):
    from app.advisory.change import VAGUE, consider_recommendation_change
    from app.models.policy import PolicyDocument

    docs = {pid: PolicyDocument(policy_id=pid, policy_name=pid, insurer="x", document_path="p", file_name="f") for pid in POLICIES}
    vague = consider_recommendation_change(POLICIES, [], books, docs, "Please make the cover better overall for everyone.")
    assert vague["ok"] is False and vague["applied"] is False and vague["message"] == VAGUE
    focused = consider_recommendation_change(POLICIES, [], books, docs, "Focus more on chronic conditions from day one.")
    assert focused["ok"] is True
    assert any("Chronic" in line or "chronic" in line.lower() for line in focused["interpreted_change"])
    assert focused["new_weights"]
    assert focused["recommendation"]["recommended_policy_id"] == "abhi_activ_one"
    added = consider_recommendation_change(
        POLICIES,
        assign_weights([_req("global_cover", "Global cover for travel")]),
        books,
        docs,
        "Add maternity as a client priority.",
    )
    assert any(row["feature"] == "maternity" for row in added["new_weights"])
    assert any(row["feature"] == "global_cover" for row in added["old_weights"])


def test_opd_is_not_econsultation_and_accident_cover_is_not_a_waiting_exception(books):
    from app.policies.features import map_text_to_features

    assert map_text_to_features("wellness and OPD") == ["wellness_renewal_discount", "opd"]
    assert map_text_to_features("accident cover") == ["personal_accident"]
    assert map_text_to_features("Initial Wait Period (not applicable on accident cases)") == ["accident_waiting_exception"]

    niva = books["niva_reassure_2"]
    care = books["care_supreme"]
    hdfc = books["hdfc_optima_secure_plus"]
    abhi = books["abhi_activ_one"]

    assert niva.facts["teleconsultation_opd"].coverage_status == CoverageStatus.COVERED
    assert niva.facts["opd"].coverage_status == CoverageStatus.NOT_FOUND
    assert niva.facts["opd"].coverage_tier == "NOT_ESTABLISHED"
    assert "e-consultation" not in (niva.facts["opd"].value or "").lower()
    assert _score(books, "opd", "niva_reassure_2", "Wellness and OPD").score is None

    assert care.facts["opd"].coverage_status == CoverageStatus.ADD_ON
    assert "physical consultation" in (care.facts["opd"].original_quote or care.facts["opd"].value or "").lower()
    assert care.facts["opd"].feature != care.facts["teleconsultation_opd"].feature or care.facts["teleconsultation_opd"].coverage_status != care.facts["opd"].coverage_status
    assert "care opd" in (care.facts["opd"].original_quote or care.facts["opd"].value or "").lower()

    assert hdfc.facts["opd"].coverage_status == CoverageStatus.ADD_ON
    assert hdfc.facts["opd"].coverage_tier == "ADD_ON"
    assert "outpatient" in (hdfc.facts["opd"].original_quote or "").lower()
    assert hdfc.facts["teleconsultation_opd"].coverage_status == CoverageStatus.NOT_FOUND

    assert care.facts["personal_accident"].coverage_status == CoverageStatus.NOT_FOUND
    exception = care.facts["accident_waiting_exception"]
    assert exception.coverage_status == CoverageStatus.CONDITIONAL
    assert "not applicable on accident" in (exception.original_quote or "").lower()
    assert _score(books, "personal_accident", "care_supreme", "accident cover").score is None

    assert hdfc.facts["personal_accident"].coverage_status == CoverageStatus.ADD_ON
    assert hdfc.facts["personal_accident"].coverage_tier == "RIDER"
    assert "rider" in f"{hdfc.facts['personal_accident'].original_quote or ''} {hdfc.facts['personal_accident'].value or ''}".lower()

    assert niva.facts["personal_accident"].coverage_status == CoverageStatus.ADD_ON
    assert niva.facts["personal_accident"].coverage_tier == "OPTIONAL"
    assert "personal accident" in (niva.facts["personal_accident"].original_quote or "").lower()

    assert abhi.facts["opd"].coverage_status == CoverageStatus.NOT_FOUND
    assert abhi.facts["opd"].coverage_tier == "NOT_ESTABLISHED"
    assert abhi.facts["personal_accident"].coverage_status == CoverageStatus.NOT_FOUND
    assert abhi.facts["personal_accident"].coverage_tier == "NOT_ESTABLISHED"
    assert "accident_waiting_exception" not in abhi.facts or abhi.facts["accident_waiting_exception"].coverage_status == CoverageStatus.NOT_FOUND

    wellness, rec_wellness = _profile(books, [
        _req("wellness_renewal_discount", "Wellness and OPD", weight=1),
        _req("opd", "Wellness and OPD", weight=1),
        _hospital(),
    ])
    assert _cell(wellness["niva_reassure_2"], "opd").criterion_score is None
    assert wellness["niva_reassure_2"].decision_sufficient is False
    assert _cell(wellness["care_supreme"], "opd").criterion_score is not None
    assert _cell(wellness["hdfc_optima_secure_plus"], "opd").criterion_score is not None
    assert _cell(wellness["abhi_activ_one"], "opd").criterion_score is None
    assert rec_wellness.recommended_policy_id == "care_supreme"
    assert wellness["care_supreme"].score == pytest.approx(72.375)
    assert wellness["care_supreme"].decision_sufficient is True
    assert wellness["hdfc_optima_secure_plus"].score == pytest.approx(80.7407, abs=1e-3)
    assert wellness["niva_reassure_2"].score == pytest.approx(78.3333, abs=1e-3)
    assert wellness["abhi_activ_one"].score == pytest.approx(100)
    assert wellness["hdfc_optima_secure_plus"].decision_sufficient is False
    assert wellness["abhi_activ_one"].decision_sufficient is False

    accident, rec_accident = _profile(books, [_req("personal_accident", "accident cover", weight=1.8), _hospital()])
    assert _cell(accident["care_supreme"], "personal_accident").criterion_score is None
    assert accident["care_supreme"].decision_sufficient is False
    assert _cell(accident["abhi_activ_one"], "personal_accident").criterion_score is None
    assert _cell(accident["hdfc_optima_secure_plus"], "personal_accident").criterion_score == 60
    assert _cell(accident["niva_reassure_2"], "personal_accident").criterion_score == 55
    assert accident["hdfc_optima_secure_plus"].score == pytest.approx(74)
    assert accident["niva_reassure_2"].score == pytest.approx(70.75)
    assert rec_accident.decision_state == "close_decision"
    assert rec_accident.recommended_policy_id == ""
    assert {item.policy_id for item in rec_accident.alternatives} == {"hdfc_optima_secure_plus", "niva_reassure_2"}


def test_verified_facts_keep_tier_and_do_not_borrow_another_policy(books):
    hdfc_maternity = books["hdfc_optima_secure_plus"].facts["maternity"]
    hdfc_accident = books["hdfc_optima_secure_plus"].facts["personal_accident"]
    niva_room = books["niva_reassure_2"].facts["room_rent"]
    assert hdfc_maternity.coverage_tier == "ADD_ON"
    assert hdfc_accident.coverage_tier == "RIDER"
    assert niva_room.coverage_tier == "NOT_ESTABLISHED"
    assert "verification_mode=deterministic" in (hdfc_maternity.notes or "")
    assert hdfc_maternity.coverage_status.value != "REVIEW_REQUIRED"

