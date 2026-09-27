"""Property tests for the deterministic fit engine. Winners follow the fixture facts, not an insurer name."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

from app.models.client import Exposure, FactKind
from app.models.fit import ClientRequirement, CoverageExpectation, CriterionType, RequirementClass
from app.models.policy import ConditionMateriality, CoverageStatus, FactCondition, FeatureFact, PolicyExtractionResult, SourceRef
from app.policy_fit.criteria import ADD_ON_ACCEPTED_SCORE, COMPARATOR_RULES, compare_fact, expectation_for
from app.policy_fit.requirements import assign_weights, build_requirements
from app.policy_fit.scoring import recommend, score_policies
from app.policy_fit.scoring_config import ScoringConfig, deductible_fit_score

ROOT = Path(__file__).resolve().parents[1]
ENGINE = [
    ROOT / "backend/app/policy_fit/scoring.py",
    ROOT / "backend/app/policy_fit/criteria.py",
    ROOT / "backend/app/policy_fit/requirements.py",
]
FORBIDDEN = ("hdfc", "abhi", "niva", "optima", "reassure", "activ one", "care supreme")


def _src(text: str = "clause", relevance: float = 0.2) -> SourceRef:
    return SourceRef(policy_id="ignored", chunk_id="c", page=3, section="Benefits", clause="p3.1", source_text=text, retrieval_relevance=relevance)


def _fact(feature: str, status: CoverageStatus, **kw) -> FeatureFact:
    sources = [] if status == CoverageStatus.NOT_FOUND else [_src(kw.pop("quote", "clause"))]
    return FeatureFact(policy_id="unused", feature=feature, coverage_status=status, sources=sources, **kw)


def _results(books: dict[str, dict[str, FeatureFact]]) -> dict[str, PolicyExtractionResult]:
    now = datetime.now(timezone.utc).isoformat()
    return {pid: PolicyExtractionResult(policy_id=pid, facts=facts, generated_at=now) for pid, facts in books.items()}


def _req(feature: str, klass: RequirementClass, *, accept_add_on: bool | None = None, description: str = "") -> ClientRequirement:
    text = description or feature
    if accept_add_on is None:
        accept_add_on = klass != RequirementClass.MUST_HAVE
    return ClientRequirement(
        requirement_id=feature,
        description=text,
        feature=feature,
        type=CriterionType.COVERAGE if feature not in {"copay", "waiting_period_ped", "non_medical_expenses_cover"} else (
            CriterionType.COPAYMENT if feature == "copay" else CriterionType.WAITING_PERIOD if feature.startswith("waiting") else CriterionType.COVERAGE
        ),
        priority_weight={RequirementClass.MUST_HAVE: 3, RequirementClass.PREFERENCE: 2, RequirementClass.BASELINE: 1}[klass],
        requirement_class=klass,
        hard_constraint=klass == RequirementClass.MUST_HAVE,
        source="test",
        client_asked=klass != RequirementClass.BASELINE,
        accept_add_on=accept_add_on,
    )


def _fit(ids, requirements, books):
    return {f.policy_id: f for f in score_policies(ids, assign_weights(requirements), _results(books), [])}


def test_same_input_same_output():
    reqs = assign_weights([_req("in_patient_hospitalisation", RequirementClass.BASELINE)])
    books = {"p": {"in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED)}}
    a = score_policies(["p"], reqs, _results(books), [])
    b = score_policies(["p"], reqs, _results(books), [])
    assert a[0].score == b[0].score
    assert [row.criterion_score for row in a[0].contributions] == [row.criterion_score for row in b[0].contributions]


def test_reorder_rename_and_file_order_do_not_change_scores():
    reqs = assign_weights([
        _req("in_patient_hospitalisation", RequirementClass.BASELINE),
        _req("maternity", RequirementClass.PREFERENCE),
    ])
    covered = _fact("in_patient_hospitalisation", CoverageStatus.COVERED)
    excluded = _fact("maternity", CoverageStatus.EXCLUDED, value="Listed under exclusions: maternity")
    found = _fact("maternity", CoverageStatus.COVERED, value="Maternity covered")
    books = {
        "alpha": {"in_patient_hospitalisation": covered, "maternity": excluded},
        "beta": {"in_patient_hospitalisation": covered, "maternity": found},
    }
    forward = _fit(["alpha", "beta"], reqs, books)
    backward = _fit(["beta", "alpha"], reqs, books)
    assert forward["alpha"].score == backward["alpha"].score
    assert forward["beta"].score == backward["beta"].score

    renamed = {
        "zzz": {"in_patient_hospitalisation": covered, "maternity": excluded},
        "mmm": {"in_patient_hospitalisation": covered, "maternity": found},
    }
    other = _fit(["mmm", "zzz"], reqs, renamed)
    assert other["zzz"].score == forward["alpha"].score
    assert other["mmm"].score == forward["beta"].score


def test_insurer_name_is_not_an_input_and_source_has_no_insurer_branch():
    blob = "\n".join(path.read_text(encoding="utf-8").lower() for path in ENGINE)
    for token in FORBIDDEN:
        assert token not in blob
    assert "if policy_id" not in blob
    assert "insurer ==" not in blob
    req = assign_weights([_req("maternity", RequirementClass.PREFERENCE)])[0]
    fact = _fact("maternity", CoverageStatus.COVERED)
    first = compare_fact(req, fact).score
    fact.policy_id = "someone-else"
    assert compare_fact(req, fact).score == first


def test_not_found_is_not_cover_or_exclusion_or_a_half_score():
    reqs = assign_weights([
        _req("in_patient_hospitalisation", RequirementClass.BASELINE),
        _req("maternity", RequirementClass.PREFERENCE),
    ])
    books = {
        "missing": {
            "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED),
            "maternity": _fact("maternity", CoverageStatus.NOT_FOUND),
        },
        "excluded": {
            "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED),
            "maternity": _fact("maternity", CoverageStatus.EXCLUDED),
        },
        "covered": {
            "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED),
            "maternity": _fact("maternity", CoverageStatus.COVERED),
        },
    }
    fits = _fit(["missing", "excluded", "covered"], reqs, books)
    missing_row = next(row for row in fits["missing"].contributions if row.feature == "maternity")
    excluded_row = next(row for row in fits["excluded"].contributions if row.feature == "maternity")
    covered_row = next(row for row in fits["covered"].contributions if row.feature == "maternity")
    assert missing_row.criterion_score is None and missing_row.contribution is None and missing_row.explicit_exclusion is False
    assert excluded_row.criterion_score == 0 and excluded_row.explicit_exclusion
    assert covered_row.criterion_score == 100
    assert fits["missing"].score == 100
    assert fits["missing"].evidence_completeness < fits["covered"].evidence_completeness
    assert fits["excluded"].score < fits["covered"].score
    assert fits["missing"].score != 50


def test_add_on_follows_the_requirement_not_a_global_score():
    fact = _fact("maternity", CoverageStatus.ADD_ON, is_add_on=True, value="Parenthood add-on")
    base = assign_weights([_req("maternity", RequirementClass.MUST_HAVE, accept_add_on=False, description="base policy must include maternity")])[0]
    optional = assign_weights([_req("maternity", RequirementClass.PREFERENCE, accept_add_on=True, description="maternity availability is acceptable even as an optional add-on")])[0]
    rejected = compare_fact(base, fact)
    accepted = compare_fact(optional, fact)
    assert rejected.score == 0 and rejected.must_have_gap
    assert accepted.score == ADD_ON_ACCEPTED_SCORE and not accepted.must_have_gap
    assert rejected.score != 100 and accepted.score != 100
    assert expectation_for(base) == CoverageExpectation.BASE
    assert expectation_for(optional) == CoverageExpectation.EITHER


def test_must_have_failure_is_not_hidden_by_a_high_average():
    reqs = assign_weights([
        _req("maternity", RequirementClass.MUST_HAVE, accept_add_on=False, description="base policy must include maternity"),
        _req("in_patient_hospitalisation", RequirementClass.BASELINE),
    ])
    books = {
        "gap": {
            "maternity": _fact("maternity", CoverageStatus.EXCLUDED),
            "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED),
        },
        "ok": {
            "maternity": _fact("maternity", CoverageStatus.COVERED),
            "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED),
        },
    }
    fits = score_policies(["gap", "ok"], reqs, _results(books), [])
    by = {f.policy_id: f for f in fits}
    assert by["gap"].must_have_gaps == ["maternity"]
    assert by["gap"].eligible is False
    assert by["gap"].decision_state == "not_eligible"
    assert fits[0].policy_id == "ok"
    rec = recommend(fits, [], {})
    assert rec.recommended_policy_id == "ok"
    assert rec.decision_state == "eligible"


def test_contributions_reconcile_weights_normalize_and_scores_stay_bounded():
    reqs = assign_weights([
        _req("maternity", RequirementClass.PREFERENCE),
        _req("in_patient_hospitalisation", RequirementClass.BASELINE),
        _req("room_rent", RequirementClass.BASELINE),
    ])
    assert abs(sum(r.weight for r in reqs) - 1) < 1e-9
    assert abs(sum(r.weight for r in reqs if r.requirement_class != RequirementClass.BASELINE) - 0.65) < 1e-9
    books = {"p": {
        "maternity": _fact("maternity", CoverageStatus.EXCLUDED),
        "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED),
        "room_rent": _fact("room_rent", CoverageStatus.COVERED, value="room rent at actuals"),
    }}
    fit = score_policies(["p"], reqs, _results(books), [])[0]
    credited = [row.contribution for row in fit.contributions if row.contribution is not None]
    assert abs(sum(credited) - fit.score) < 1e-6
    assert 0 <= fit.score <= 100
    maternity = next(row for row in fit.contributions if row.feature == "maternity")
    assert maternity.criterion_score == 0
    assert abs(maternity.contribution - 0) < 1e-9
    # 0.65 * 0 + 0.35 * 100, both evidenced, fit is 35
    assert abs(fit.score - 35) < 0.05


def test_identical_facts_produce_identical_criterion_scores():
    req = assign_weights([_req("air_ambulance", RequirementClass.PREFERENCE)])[0]
    left = _fact("air_ambulance", CoverageStatus.COVERED, limit="INR 2,50,000", limit_original_text="INR 2,50,000", limit_numeric=250000)
    right = left.model_copy(update={"policy_id": "other-file"})
    assert compare_fact(req, left).score == compare_fact(req, right).score


def test_irrelevant_features_and_brochure_length_do_not_change_fit():
    reqs = assign_weights([_req("in_patient_hospitalisation", RequirementClass.BASELINE)])
    short = {"in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED)}
    long = {
        **short,
        "maternity": _fact("maternity", CoverageStatus.COVERED),
        "ayush": _fact("ayush", CoverageStatus.COVERED),
        "global_cover": _fact("global_cover", CoverageStatus.COVERED),
    }
    fits = _fit(["short", "long"], reqs, {"short": short, "long": long})
    assert fits["short"].score == fits["long"].score


def test_retrieval_relevance_is_not_policy_quality():
    req = assign_weights([_req("in_patient_hospitalisation", RequirementClass.BASELINE)])[0]
    low = _fact("in_patient_hospitalisation", CoverageStatus.COVERED)
    low.sources[0].retrieval_relevance = 0.1
    high = _fact("in_patient_hospitalisation", CoverageStatus.COVERED)
    high.sources[0].retrieval_relevance = 0.99
    assert compare_fact(req, low).score == compare_fact(req, high).score == 100


def test_adversarial_clients_follow_the_facts():
    base = _fact("in_patient_hospitalisation", CoverageStatus.COVERED)
    ids = ["p1", "p2", "p3", "p4"]

    maternity = assign_weights([
        _req("maternity", RequirementClass.MUST_HAVE, accept_add_on=False, description="base policy must include maternity"),
        _req("in_patient_hospitalisation", RequirementClass.BASELINE),
    ])
    maternity_books = {
        "p1": {"maternity": _fact("maternity", CoverageStatus.EXCLUDED), "in_patient_hospitalisation": base},
        "p2": {"maternity": _fact("maternity", CoverageStatus.COVERED), "in_patient_hospitalisation": base},
        "p3": {"maternity": _fact("maternity", CoverageStatus.NOT_FOUND), "in_patient_hospitalisation": base},
        "p4": {"maternity": _fact("maternity", CoverageStatus.ADD_ON, is_add_on=True), "in_patient_hospitalisation": base},
    }
    fits = score_policies(ids, maternity, _results(maternity_books), [])
    assert recommend(fits, [], {}).recommended_policy_id == "p2"
    by = {f.policy_id: f for f in fits}
    assert by["p1"].explicit_exclusions == ["maternity"] and not by["p1"].eligible
    assert by["p4"].must_have_gaps == ["maternity"]
    assert next(row for row in by["p3"].contributions if row.feature == "maternity").criterion_score is None

    accident = assign_weights([
        _req("personal_accident", RequirementClass.PREFERENCE, description="accident cover is the priority"),
        _req("in_patient_hospitalisation", RequirementClass.BASELINE),
    ])
    accident_books = {pid: {"in_patient_hospitalisation": base, "personal_accident": _fact("personal_accident", CoverageStatus.NOT_FOUND)} for pid in ids}
    accident_books["p3"]["personal_accident"] = _fact("personal_accident", CoverageStatus.COVERED)
    fits = score_policies(ids, accident, _results(accident_books), [])
    by = {f.policy_id: f for f in fits}
    assert all(by[pid].eligible for pid in ids)
    assert by["p3"].decision_sufficient
    assert by["p3"].evidence_completeness > by["p1"].evidence_completeness
    assert not by["p1"].decision_sufficient
    assert "personal_accident" in by["p1"].unresolved
    assert "personal_accident" not in by["p1"].must_have_gaps
    rec = recommend(fits, [], {})
    assert rec.recommended_policy_id == "p3"
    assert rec.decision_state == "eligible"
    assert "p1" not in rec.competing_policy_ids

    oop_reqs = assign_weights([
        _req("non_medical_expenses_cover", RequirementClass.PREFERENCE, description="out-of-pocket consumables"),
        _req("in_patient_hospitalisation", RequirementClass.BASELINE),
    ])
    oop = {pid: {"in_patient_hospitalisation": base, "non_medical_expenses_cover": _fact("non_medical_expenses_cover", CoverageStatus.NOT_FOUND)} for pid in ids}
    oop["p1"]["non_medical_expenses_cover"] = _fact("non_medical_expenses_cover", CoverageStatus.COVERED)
    oop["p2"]["non_medical_expenses_cover"] = _fact("non_medical_expenses_cover", CoverageStatus.EXCLUDED)
    fits = score_policies(ids, oop_reqs, _results(oop), [])
    by = {f.policy_id: f for f in fits}
    assert by["p2"].explicit_exclusions == ["non_medical_expenses_cover"]
    assert by["p1"].eligible and by["p3"].eligible and by["p2"].eligible
    assert by["p2"].score < by["p1"].score
    assert by["p1"].decision_sufficient and not by["p3"].decision_sufficient
    rec = recommend(fits, [], {})
    assert rec.recommended_policy_id == "p1"
    assert "p2" not in rec.competing_policy_ids
    assert "p3" not in rec.competing_policy_ids

    wait_reqs = assign_weights([_req("waiting_period_ped", RequirementClass.PREFERENCE, description="waiting period sensitivity")])
    wait_reqs[0].target_value = 30
    wait_reqs[0].target_unit = "days"
    wait_reqs[0].tolerance = 0
    short = _fact("waiting_period_ped", CoverageStatus.COVERED, waiting_period="30 days", waiting_period_days=30)
    long = _fact("waiting_period_ped", CoverageStatus.COVERED, waiting_period="36 months", waiting_period_months=36, waiting_period_days=36 * 30)
    wait_books = {pid: {"waiting_period_ped": long} for pid in ids}
    wait_books["p4"] = {"waiting_period_ped": short}
    fits = score_policies(ids, wait_reqs, _results(wait_books), [])
    by = {f.policy_id: f for f in fits}
    assert by["p4"].score > by["p1"].score
    assert recommend(fits, [], {}).recommended_policy_id == "p4"

    order = ["b", "a", "d", "c"]
    plain = assign_weights([_req("in_patient_hospitalisation", RequirementClass.BASELINE)])
    same = {pid: {"in_patient_hospitalisation": base} for pid in order}
    fits = score_policies(order, plain, _results(same), [])
    assert len({f.score for f in fits}) == 1
    rec = recommend(fits, [], {})
    assert rec.decision_state == "close_decision"
    assert rec.recommended_policy_id == ""
    assert set(rec.competing_policy_ids) == {"a", "b", "c", "d"}


def test_missing_preference_does_not_make_a_policy_ineligible():
    reqs = assign_weights([
        _req("maternity", RequirementClass.PREFERENCE),
        _req("in_patient_hospitalisation", RequirementClass.BASELINE),
    ])
    books = {
        "silent": {
            "maternity": _fact("maternity", CoverageStatus.NOT_FOUND),
            "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED),
        },
        "stated": {
            "maternity": _fact("maternity", CoverageStatus.COVERED),
            "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED),
        },
    }
    fits = _fit(["silent", "stated"], reqs, books)
    assert fits["silent"].eligible
    assert "maternity" not in fits["silent"].must_have_gaps
    assert fits["silent"].evidence_completeness < fits["stated"].evidence_completeness
    row = next(item for item in fits["silent"].contributions if item.feature == "maternity")
    assert row.criterion_score is None and row.status == "NOT_FOUND"


def test_missing_must_have_makes_the_policy_ineligible_when_another_has_evidence():
    reqs = assign_weights([_req("maternity", RequirementClass.MUST_HAVE, accept_add_on=False, description="base policy must include maternity")])
    books = {
        "gap": {"maternity": _fact("maternity", CoverageStatus.NOT_FOUND)},
        "ok": {"maternity": _fact("maternity", CoverageStatus.COVERED)},
    }
    fits = score_policies(["gap", "ok"], reqs, _results(books), [])
    by = {fit.policy_id: fit for fit in fits}
    assert by["gap"].must_have_gaps == ["maternity"]
    assert by["gap"].eligible is False
    assert by["ok"].eligible
    assert recommend(fits, [], {}).recommended_policy_id == "ok"


def test_all_four_policies_missing_the_same_feature_is_comparison_incomplete():
    reqs = assign_weights([
        _req("maternity", RequirementClass.MUST_HAVE, accept_add_on=False, description="base policy must include maternity"),
        _req("in_patient_hospitalisation", RequirementClass.BASELINE),
    ])
    ids = ["p1", "p2", "p3", "p4"]
    books = {
        pid: {
            "maternity": _fact("maternity", CoverageStatus.NOT_FOUND),
            "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED),
        }
        for pid in ids
    }
    fits = score_policies(ids, reqs, _results(books), [])
    assert all(fit.eligible for fit in fits)
    assert all(fit.must_have_gaps == [] for fit in fits)
    assert all("maternity" in fit.comparison_incomplete for fit in fits)
    rec = recommend(fits, [], {})
    assert rec.decision_state == "incomplete_comparison"
    assert rec.recommended_policy_id == ""
    assert "maternity" in rec.comparison_incomplete
    assert "maternity" in rec.unresolved_must_haves
    assert all(fit.decision_sufficient is False for fit in fits)


def test_advisor_priority_weights_change_the_result():
    def weighted(maternity_weight: float, ayush_weight: float):
        reqs = assign_weights([
            ClientRequirement(
                requirement_id="maternity", description="maternity", feature="maternity", type=CriterionType.COVERAGE,
                priority_weight=maternity_weight, requirement_class=RequirementClass.PREFERENCE, source="advisor", client_asked=True, accept_add_on=True,
            ),
            ClientRequirement(
                requirement_id="ayush", description="ayush", feature="ayush", type=CriterionType.COVERAGE,
                priority_weight=ayush_weight, requirement_class=RequirementClass.PREFERENCE, source="advisor", client_asked=True, accept_add_on=True,
            ),
        ])
        books = {
            "mat": {
                "maternity": _fact("maternity", CoverageStatus.COVERED),
                "ayush": _fact("ayush", CoverageStatus.EXCLUDED),
            },
            "ayu": {
                "maternity": _fact("maternity", CoverageStatus.EXCLUDED),
                "ayush": _fact("ayush", CoverageStatus.COVERED),
            },
        }
        return recommend(score_policies(["mat", "ayu"], reqs, _results(books), []), [], {})

    assert abs(assign_weights([
        ClientRequirement(requirement_id="maternity", description="m", feature="maternity", type=CriterionType.COVERAGE, priority_weight=9, requirement_class=RequirementClass.PREFERENCE, source="a", client_asked=True),
        ClientRequirement(requirement_id="ayush", description="a", feature="ayush", type=CriterionType.COVERAGE, priority_weight=1, requirement_class=RequirementClass.PREFERENCE, source="a", client_asked=True),
    ])[0].weight - 0.9) < 1e-9
    assert weighted(9, 1).recommended_policy_id == "mat"
    assert weighted(1, 9).recommended_policy_id == "ayu"


def test_deductible_formula_boundaries():
    from app.policy_fit.scoring_config import deductible_fit_score

    reference = 100_000.0
    assert deductible_fit_score(0, reference) == 100
    assert deductible_fit_score(-500, reference) == 100
    assert deductible_fit_score(reference / 2, reference) == 50
    assert deductible_fit_score(reference, reference) == 0
    assert deductible_fit_score(reference * 3, reference) == 0
    req = assign_weights([_req("deductible_options", RequirementClass.PREFERENCE)])[0]
    req.type = CriterionType.DEDUCTIBLE
    assert compare_fact(req, _fact("deductible_options", CoverageStatus.COVERED, deductible_amount=0)).score == 100
    assert compare_fact(req, _fact("deductible_options", CoverageStatus.COVERED, deductible_amount=reference)).score == 0


def test_thresholds_come_from_scoring_config():
    from app.policy_fit.scoring_config import ScoringConfig

    reqs = assign_weights([_req("maternity", RequirementClass.MUST_HAVE, description="must include maternity")])
    books = {"p": {"maternity": _fact("maternity", CoverageStatus.CONDITIONAL)}}
    relaxed = score_policies(["p"], reqs, _results(books), [], config=ScoringConfig(must_have_fail_below=40))
    strict = score_policies(["p"], reqs, _results(books), [], config=ScoringConfig(must_have_fail_below=80))
    assert relaxed[0].eligible
    assert strict[0].eligible is False

    both = assign_weights([
        ClientRequirement(requirement_id="maternity", description="m", feature="maternity", type=CriterionType.COVERAGE, priority_weight=1, requirement_class=RequirementClass.PREFERENCE, source="a", client_asked=True, accept_add_on=True),
        ClientRequirement(requirement_id="ayush", description="a", feature="ayush", type=CriterionType.COVERAGE, priority_weight=1, requirement_class=RequirementClass.PREFERENCE, source="a", client_asked=True, accept_add_on=True),
    ])
    books = {
        "high": {"maternity": _fact("maternity", CoverageStatus.COVERED), "ayush": _fact("ayush", CoverageStatus.COVERED)},
        "mid": {"maternity": _fact("maternity", CoverageStatus.COVERED), "ayush": _fact("ayush", CoverageStatus.EXCLUDED)},
    }
    tight = recommend(score_policies(["high", "mid"], both, _results(books), [], config=ScoringConfig(close_threshold=5)), [], {})
    wide = recommend(score_policies(["high", "mid"], both, _results(books), [], config=ScoringConfig(close_threshold=60)), [], {})
    assert tight.recommended_policy_id == "high"
    assert wide.decision_state == "close_decision"
    assert wide.recommended_policy_id == ""


def test_exclusion_list_presence_is_not_a_fit_point():
    generic = assign_weights([_req("exclusions", RequirementClass.BASELINE, description="Exclusions")])[0]
    generic.type = CriterionType.EXCLUSION
    listed = _fact("exclusions", CoverageStatus.COVERED, value="standard exclusion list")
    assert compare_fact(generic, listed).score is None
    named = generic.model_copy(update={"description": "client needs maternity"})
    hit = _fact("exclusions", CoverageStatus.COVERED, value="maternity is not covered", exclusions=["maternity"])
    judged = compare_fact(named, hit)
    assert judged.score == 0 and judged.explicit_exclusion and judged.status == CoverageStatus.EXCLUDED
    absent = compare_fact(named, _fact("exclusions", CoverageStatus.COVERED, value="cosmetic surgery is not covered", exclusions=["cosmetic surgery"]))
    assert absent.score is None and absent.status == CoverageStatus.NOT_FOUND and absent.explicit_exclusion is False
    covered = compare_fact(named, _fact("exclusions", CoverageStatus.COVERED, value="maternity is covered"))
    assert covered.score == 100 and covered.status == CoverageStatus.COVERED


def test_recommendation_is_deterministic():
    reqs = assign_weights([_req("in_patient_hospitalisation", RequirementClass.BASELINE), _req("maternity", RequirementClass.PREFERENCE)])
    books = {
        "a": {"in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED), "maternity": _fact("maternity", CoverageStatus.EXCLUDED)},
        "b": {"in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED), "maternity": _fact("maternity", CoverageStatus.COVERED)},
    }
    first = recommend(score_policies(["a", "b"], reqs, _results(books), []), [], {})
    second = recommend(score_policies(["b", "a"], reqs, _results(books), []), [], {})
    assert first.recommended_policy_id == second.recommended_policy_id == "b"
    assert first.fit_score == second.fit_score


def test_evidence_states_stay_distinct():
    req = assign_weights([_req("maternity", RequirementClass.PREFERENCE)])[0]
    covered = compare_fact(req, _fact("maternity", CoverageStatus.COVERED))
    excluded = compare_fact(req, _fact("maternity", CoverageStatus.EXCLUDED))
    missing = compare_fact(req, _fact("maternity", CoverageStatus.NOT_FOUND))
    unknown = compare_fact(req, _fact("maternity", CoverageStatus.UNKNOWN))
    review = compare_fact(req, _fact("maternity", CoverageStatus.REVIEW_REQUIRED))
    partial = compare_fact(req, _fact("maternity", CoverageStatus.PARTIALLY_COVERED))
    conditional = compare_fact(req, _fact("maternity", CoverageStatus.CONDITIONAL))
    assert covered.status == CoverageStatus.COVERED and covered.score == 100
    assert excluded.status == CoverageStatus.EXCLUDED and excluded.score == 0 and excluded.explicit_exclusion
    assert missing.score is None and missing.status == CoverageStatus.NOT_FOUND and missing.explicit_exclusion is False
    assert unknown.score is None and unknown.status == CoverageStatus.UNKNOWN
    assert review.score is None and review.status == CoverageStatus.REVIEW_REQUIRED
    assert partial.score == 50 and conditional.score == 70
    assert {covered.score, excluded.score, missing.score, unknown.score, review.score} != {100}


def test_requirement_weights_are_explicit_and_safe():
    only_base = assign_weights([_req("in_patient_hospitalisation", RequirementClass.BASELINE), _req("room_rent", RequirementClass.BASELINE)])
    assert abs(sum(r.weight for r in only_base) - 1) < 1e-9
    only_client = assign_weights([_req("maternity", RequirementClass.PREFERENCE), _req("ayush", RequirementClass.PREFERENCE)])
    assert abs(sum(r.weight for r in only_client) - 1) < 1e-9
    both = assign_weights([_req("maternity", RequirementClass.PREFERENCE), _req("in_patient_hospitalisation", RequirementClass.BASELINE)])
    assert abs(sum(r.weight for r in both if r.requirement_class != RequirementClass.BASELINE) - 0.65) < 1e-9
    assert abs(sum(r.weight for r in both) - 1) < 1e-9
    assert assign_weights([]) == []

    zero, positive = assign_weights([
        _req("maternity", RequirementClass.BASELINE).model_copy(update={"priority_weight": 0}),
        _req("ayush", RequirementClass.BASELINE).model_copy(update={"priority_weight": 2, "requirement_id": "ayush"}),
    ])
    assert zero.weight == 0 and positive.weight == 1 and zero.weight_valid

    bad, good = assign_weights([
        _req("maternity", RequirementClass.BASELINE).model_copy(update={"priority_weight": -4}),
        _req("ayush", RequirementClass.BASELINE).model_copy(update={"priority_weight": 2, "requirement_id": "ayush"}),
    ])
    assert bad.weight == 0 and bad.weight_valid is False and good.weight == 1
    for poison in (float("nan"), float("inf"), float("-inf")):
        invalid, other = assign_weights([
            _req("maternity", RequirementClass.BASELINE).model_copy(update={"priority_weight": poison}),
            _req("ayush", RequirementClass.BASELINE).model_copy(update={"priority_weight": 1, "requirement_id": "ayush"}),
        ])
        assert invalid.weight == 0 and invalid.weight_valid is False
        assert other.weight == 1 and math.isfinite(other.weight)

    baseline = Exposure(exposure_id="b", title="Hospitalisation", description="baseline maternity", reasoning="r", priority=1.2, feature_keys=["maternity"])
    advisor = Exposure(exposure_id="a", title="Advisor priority maternity", description="maternity benefit", reasoning="r", priority=1.8, feature_keys=["maternity"])
    merged = build_requirements([baseline, advisor])
    assert len(merged) == 1
    assert merged[0].requirement_class == RequirementClass.PREFERENCE
    assert merged[0].weight == 1


def test_comparators_are_separate_and_documented():
    required = {"input", "unit", "target", "direction", "full_fit", "partial_fit", "failure", "missing_evidence", "exception"}
    assert set(COMPARATOR_RULES) == {item.value for item in CriterionType}
    for rule in COMPARATOR_RULES.values():
        assert required <= set(rule)
    cfg = ScoringConfig(
        covered_score=100,
        eligibility_met_score=91,
        geographic_met_score=92,
        wellness_met_score=93,
        limit_no_target_score=81,
        sublimit_cap_score=61,
        conditional_score=70,
        condition_material_score=55,
    )
    def judged(feature, kind, fact):
        req = assign_weights([_req(feature, RequirementClass.PREFERENCE)])[0]
        req.type = kind
        return compare_fact(req, fact, cfg).score
    assert judged("maternity", CriterionType.COVERAGE, _fact("maternity", CoverageStatus.COVERED)) == 100
    assert judged("eligibility_entry_age", CriterionType.ELIGIBILITY, _fact("eligibility_entry_age", CoverageStatus.COVERED)) == 91
    assert judged("global_cover", CriterionType.GEOGRAPHIC, _fact("global_cover", CoverageStatus.COVERED)) == 92
    assert judged("health_checkup", CriterionType.WELLNESS, _fact("health_checkup", CoverageStatus.COVERED)) == 93
    assert judged("sum_insured_options", CriterionType.LIMIT, _fact("sum_insured_options", CoverageStatus.COVERED, limit_numeric=500000)) == 81
    assert judged("room_rent", CriterionType.SUBLIMIT, _fact("room_rent", CoverageStatus.COVERED, limit="room rent capped at INR 5,000")) == 61


def test_condition_materiality_is_not_a_flat_stack():
    from app.policy_fit.scoring_config import DEFAULT_SCORING

    def scored(details, status=CoverageStatus.COVERED, **kw):
        req = assign_weights([_req("maternity", RequirementClass.PREFERENCE)])[0]
        fact = _fact("maternity", status, condition_details=details, **kw)
        return compare_fact(req, fact)

    info = scored([FactCondition(text="brochure footnote", materiality=ConditionMateriality.INFO)])
    minor = scored([FactCondition(text="network hospital", materiality=ConditionMateriality.MINOR)])
    material = scored([FactCondition(text="zone restriction", materiality=ConditionMateriality.MATERIAL)])
    critical = scored([FactCondition(text="benefit withdrawn", materiality=ConditionMateriality.CRITICAL)])
    assert info.score == 100 and info.condition_materiality == "INFO"
    assert minor.score == DEFAULT_SCORING.condition_minor_score
    assert material.score == DEFAULT_SCORING.condition_material_score
    assert critical.score == DEFAULT_SCORING.condition_critical_score
    stacked = scored([
        FactCondition(text="zone restriction", materiality=ConditionMateriality.MATERIAL),
        FactCondition(text="zone restriction", materiality=ConditionMateriality.MATERIAL),
        FactCondition(text="brochure footnote", materiality=ConditionMateriality.INFO),
    ])
    assert stacked.score == material.score
    addon = _fact("maternity", CoverageStatus.ADD_ON, is_add_on=True, condition_details=[FactCondition(text="rider limit", materiality=ConditionMateriality.CRITICAL)])
    optional = assign_weights([_req("maternity", RequirementClass.PREFERENCE, accept_add_on=True, description="optional add-on is acceptable")])[0]
    judged = compare_fact(optional, addon)
    assert judged.status == CoverageStatus.ADD_ON
    assert judged.score == DEFAULT_SCORING.condition_critical_score
    limited = assign_weights([_req("sum_insured_options", RequirementClass.PREFERENCE)])[0]
    limited.type = CriterionType.LIMIT
    cap = compare_fact(limited, _fact("sum_insured_options", CoverageStatus.COVERED, limit_numeric=1_000_000, condition_details=[FactCondition(text="room category", materiality=ConditionMateriality.MATERIAL)]))
    assert cap.score == DEFAULT_SCORING.condition_material_score
    must = assign_weights([_req("maternity", RequirementClass.MUST_HAVE, accept_add_on=False, description="base policy must include maternity")])[0]
    gap = compare_fact(must, _fact("maternity", CoverageStatus.COVERED, condition_details=[FactCondition(text="benefit withdrawn", materiality=ConditionMateriality.CRITICAL)]))
    assert gap.must_have_gap and gap.score == DEFAULT_SCORING.condition_critical_score


def test_add_on_expectation_is_requirement_specific():
    fact = _fact("maternity", CoverageStatus.ADD_ON, is_add_on=True)
    only = assign_weights([_req("maternity", RequirementClass.PREFERENCE, description="optional add-on only")])[0]
    only.coverage_expectation = CoverageExpectation.OPTIONAL_ADD_ON
    only.accept_add_on = True
    either = assign_weights([_req("maternity", RequirementClass.PREFERENCE, accept_add_on=True)])[0]
    base = assign_weights([_req("maternity", RequirementClass.MUST_HAVE, accept_add_on=False, description="base policy must include maternity")])[0]
    assert expectation_for(only) == CoverageExpectation.OPTIONAL_ADD_ON
    assert expectation_for(either) == CoverageExpectation.EITHER
    assert expectation_for(base) == CoverageExpectation.BASE
    assert compare_fact(only, fact).score == ADD_ON_ACCEPTED_SCORE
    assert compare_fact(either, fact).score == ADD_ON_ACCEPTED_SCORE
    assert compare_fact(base, fact).score == 0
    assert compare_fact(only, _fact("maternity", CoverageStatus.COVERED)).score == 100


def test_numeric_comparators_are_monotonic_and_bounded():
    waiting = assign_weights([_req("waiting_period_ped", RequirementClass.PREFERENCE)])[0]
    waiting.type = CriterionType.WAITING_PERIOD
    short = compare_fact(waiting, _fact("waiting_period_ped", CoverageStatus.COVERED, waiting_period_days=30)).score
    medium = compare_fact(waiting, _fact("waiting_period_ped", CoverageStatus.COVERED, waiting_period_days=800)).score
    long = compare_fact(waiting, _fact("waiting_period_ped", CoverageStatus.COVERED, waiting_period_days=2000)).score
    assert short >= medium >= long
    copay = assign_weights([_req("copay", RequirementClass.PREFERENCE)])[0]
    low = compare_fact(copay, _fact("copay", CoverageStatus.COVERED, copay_percent=0)).score
    mid = compare_fact(copay, _fact("copay", CoverageStatus.COVERED, copay_percent=10)).score
    high = compare_fact(copay, _fact("copay", CoverageStatus.COVERED, copay_percent=40)).score
    assert low >= mid >= high
    limit = assign_weights([_req("sum_insured_options", RequirementClass.PREFERENCE)])[0]
    limit.type = CriterionType.LIMIT
    limit.target_value = 1_000_000
    limit.tolerance = 0
    higher = compare_fact(limit, _fact("sum_insured_options", CoverageStatus.COVERED, limit_numeric=1_000_000)).score
    lower = compare_fact(limit, _fact("sum_insured_options", CoverageStatus.COVERED, limit_numeric=100_000)).score
    assert higher >= lower
    reference = 100_000
    amounts = [0, reference / 2, reference, reference * 2]
    scores = [deductible_fit_score(amount, reference) for amount in amounts]
    assert scores == [100, 50, 0, 0]
    assert scores == sorted(scores, reverse=True)
    deductible = assign_weights([_req("deductible_options", RequirementClass.PREFERENCE)])[0]
    deductible.type = CriterionType.DEDUCTIBLE
    earlier = None
    for amount in (0, 25_000, 50_000, 100_000, 250_000):
        score = compare_fact(deductible, _fact("deductible_options", CoverageStatus.COVERED, deductible_amount=amount)).score
        assert 0 <= score <= 100
        if earlier is not None:
            assert score <= earlier
        earlier = score


def test_sufficiency_gate_is_configurable_and_does_not_change_fit():
    reqs = assign_weights([
        _req("maternity", RequirementClass.PREFERENCE),
        _req("in_patient_hospitalisation", RequirementClass.BASELINE),
    ])
    books = {
        "thin": {
            "maternity": _fact("maternity", CoverageStatus.NOT_FOUND),
            "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED),
        },
        "full": {
            "maternity": _fact("maternity", CoverageStatus.CONDITIONAL),
            "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED),
        },
    }
    strict = {fit.policy_id: fit for fit in score_policies(["thin", "full"], reqs, _results(books), [])}
    loose_cfg = ScoringConfig(min_decision_completeness=0, max_completeness_shortfall=1)
    loose = {fit.policy_id: fit for fit in score_policies(["thin", "full"], reqs, _results(books), [], config=loose_cfg)}
    assert strict["thin"].score == loose["thin"].score == 100
    assert strict["thin"].decision_sufficient is False
    assert recommend(list(strict.values()), [], {}).recommended_policy_id == "full"
    assert loose["thin"].decision_sufficient and loose["full"].decision_sufficient
    assert recommend(list(loose.values()), [], {}).recommended_policy_id == "thin"


def test_names_files_chunks_and_wording_do_not_change_fit():
    reqs = assign_weights([_req("in_patient_hospitalisation", RequirementClass.BASELINE)])
    base = _fact("in_patient_hospitalisation", CoverageStatus.COVERED, value="In-patient hospitalisation is covered")
    renamed = base.model_copy(update={"policy_id": "other", "insurer_name": "Renamed Carrier", "product_name": "Other Product", "source_document": "other-file.pdf"})
    extra_sources = base.model_copy(deep=True)
    extra_sources.sources = extra_sources.sources + [_src("more clauses " + ("word " * 40), relevance=0.99), _src("another chunk")]
    worded = base.model_copy(update={"value": "The schedule confirms in-patient treatment in a hospital."})
    books = {"a": {"in_patient_hospitalisation": base}, "b": {"in_patient_hospitalisation": renamed}, "c": {"in_patient_hospitalisation": extra_sources}, "d": {"in_patient_hospitalisation": worded}}
    fits = _fit(["a", "b", "c", "d"], reqs, books)
    assert len({fit.score for fit in fits.values()}) == 1
    rec_forward = recommend(score_policies(["a", "b"], reqs, _results(books), []), [], {})
    rec_back = recommend(score_policies(["b", "a"], reqs, _results(books), []), [], {})
    assert rec_forward.recommended_policy_id == rec_back.recommended_policy_id
    assert rec_forward.decision_state == rec_back.decision_state == "close_decision"


def test_outputs_stay_finite_and_contributions_reconcile():
    reqs = assign_weights([
        _req("maternity", RequirementClass.PREFERENCE),
        _req("in_patient_hospitalisation", RequirementClass.BASELINE),
        _req("copay", RequirementClass.BASELINE),
    ])
    books = {"p": {
        "maternity": _fact("maternity", CoverageStatus.COVERED),
        "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.PARTIALLY_COVERED),
        "copay": _fact("copay", CoverageStatus.COVERED, copay_percent=10),
    }}
    fit = score_policies(["p"], reqs, _results(books), [])[0]
    assert math.isfinite(fit.score) and 0 <= fit.score <= 100
    credited = [row.contribution for row in fit.contributions if row.contribution is not None]
    assert abs(sum(credited) - fit.score) <= 1e-4
    for row in fit.contributions:
        if row.criterion_score is not None:
            assert 0 <= row.criterion_score <= 100 and math.isfinite(row.criterion_score)
            assert math.isfinite(row.contribution)
    try:
        deductible_fit_score(float("nan"), 100_000)
    except ValueError:
        pass
    else:
        raise AssertionError("non-finite deductible input must be rejected")

