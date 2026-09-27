"""Comparison matrix, scenario arena, gap analysis and deterministic scoring."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.client import Exposure, FactKind
from app.models.policy import CoverageStatus, FeatureFact, PolicyDocument, PolicyExtractionResult, SourceRef
from app.policies.comparison import build_matrix, cell_display
from app.exposure.mapping import features_for_run, map_exposures_to_features
from app.policy_fit.arena import run_arena
from app.policy_fit.gaps import analyse_gaps
from app.policy_fit.scenarios import build_scenarios
from app.policy_fit.scoring import recommend, score_all
from app.services.llm import LLMService


def _src(pid: str, text: str = "evidence", rel: float = 0.8) -> SourceRef:
    return SourceRef(policy_id=pid, chunk_id=f"{pid}:x", page=2, section="Benefits", clause="p2.1", source_text=text, retrieval_relevance=rel)


def _fact(pid, feature, status, **kw) -> FeatureFact:
    return FeatureFact(policy_id=pid, feature=feature, coverage_status=status, sources=[_src(pid)] if status != CoverageStatus.NOT_FOUND else [], **kw)


def _results():
    now = datetime.now(timezone.utc).isoformat()
    a = {"maternity": _fact("A", "maternity", CoverageStatus.EXCLUDED, value="Listed under exclusions: maternity"),
         "in_patient_hospitalisation": _fact("A", "in_patient_hospitalisation", CoverageStatus.COVERED, value="Up to sum insured"),
         "air_ambulance": _fact("A", "air_ambulance", CoverageStatus.COVERED, limit="INR 5,00,000")}
    b = {"maternity": _fact("B", "maternity", CoverageStatus.NOT_FOUND),
         "in_patient_hospitalisation": _fact("B", "in_patient_hospitalisation", CoverageStatus.COVERED, value="Up to SI"),
         "air_ambulance": _fact("B", "air_ambulance", CoverageStatus.ADD_ON, is_add_on=True, limit="INR 5 lacs per year")}
    c = {"maternity": _fact("C", "maternity", CoverageStatus.CONDITIONAL, conditions=["VIP+ only", "BSI >= INR 50 lacs"], limit="INR 1 Lac"),
         "in_patient_hospitalisation": _fact("C", "in_patient_hospitalisation", CoverageStatus.COVERED),
         "air_ambulance": _fact("C", "air_ambulance", CoverageStatus.NOT_FOUND)}
    return {pid: PolicyExtractionResult(policy_id=pid, facts=f, generated_at=now) for pid, f in {"A": a, "B": b, "C": c}.items()}


def _exposures():
    return [
        Exposure(exposure_id="e1", title="Young workforce planning families", description="maternity and dependent cover", basis=[], reasoning="", status=FactKind.INFERENCE, priority=1.5, feature_keys=["maternity"]),
        Exposure(exposure_id="e2", title="Hospitalisation of employees", description="hospital admission", basis=[], reasoning="", status=FactKind.FACT, priority=1.0, feature_keys=["in_patient_hospitalisation"]),
        Exposure(exposure_id="e3", title="Remote site operations", description="air ambulance evacuation", basis=[], reasoning="", status=FactKind.ASSUMPTION, priority=1.0, feature_keys=["air_ambulance"]),
    ]


def test_matrix_cells_keep_provenance():
    m = build_matrix(_results(), features=["maternity", "air_ambulance"], policy_order=["A", "B", "C"])
    assert m.cells["maternity"]["A"].status == CoverageStatus.EXCLUDED
    assert m.cells["maternity"]["B"].display == "Not specified in supplied brochure"
    assert m.cells["air_ambulance"]["A"].fact.sources[0].page == 2
    assert cell_display(m.cells["air_ambulance"]["B"].fact).startswith("Add-on")


def test_not_found_is_not_excluded_in_scoring():
    res = _results()
    scenarios = build_scenarios(_exposures())
    outcomes = run_arena(scenarios, res)
    gaps = analyse_gaps(scenarios, outcomes, _exposures())
    fits = score_all(["A", "B", "C"], scenarios, outcomes, gaps)
    by = {f.policy_id: f for f in fits}
    a_mat = next(row for row in by["A"].contributions if row.feature == "maternity")
    b_mat = next(row for row in by["B"].contributions if row.feature == "maternity")
    assert a_mat.explicit_exclusion and a_mat.criterion_score == 0
    assert b_mat.unresolved and b_mat.criterion_score is None and b_mat.contribution is None
    assert b_mat.criterion_score != 0 and a_mat.status == "EXCLUDED" and b_mat.status == "NOT_FOUND"
    assert all(0 <= f.score <= 100 for f in fits)
    for fit in fits:
        credited = [row.contribution for row in fit.contributions if row.contribution is not None]
        assert abs(sum(credited) - fit.score) < 0.05
        assert fit.explanation


def test_same_scenarios_for_every_policy():
    scenarios = build_scenarios(_exposures())
    outcomes = run_arena(scenarios, _results())
    per_policy = {pid: {o.scenario_id for o in outcomes if o.policy_id == pid} for pid in ["A", "B", "C"]}
    assert per_policy["A"] == per_policy["B"] == per_policy["C"] == {s.scenario_id for s in scenarios}


def test_gap_analysis_types_and_severity():
    scenarios = build_scenarios(_exposures())
    outcomes = run_arena(scenarios, _results())
    gaps = analyse_gaps(scenarios, outcomes, _exposures())
    a = [g for g in gaps if g.policy_id == "A"]
    assert any(g.gap_type == "EXCLUSION" and g.severity == "HIGH" for g in a)
    b = [g for g in gaps if g.policy_id == "B"]
    assert any(g.gap_type == "UNCERTAINTY" for g in b) and any(g.gap_type == "GAP" for g in b)
    c = [g for g in gaps if g.policy_id == "C"]
    assert any(g.gap_type == "CONDITION" for g in c)


def test_recommendation_carries_caveats_and_assumptions():
    res = _results()
    scenarios = build_scenarios(_exposures())
    outcomes = run_arena(scenarios, res)
    gaps = analyse_gaps(scenarios, outcomes, _exposures())
    fits = score_all(["A", "B", "C"], scenarios, outcomes, gaps)
    docs = {pid: PolicyDocument(policy_id=pid, policy_name=f"Policy {pid}", insurer="x", document_path="p", file_name="f") for pid in "ABC"}
    rec = recommend(fits, gaps, docs)
    assert rec.recommended_policy_id == fits[0].policy_id
    assert any("retail" in a for a in rec.assumptions)
    assert rec.fit_score == fits[0].score


def test_deterministic_scoring_is_repeatable():
    res = _results()
    s = build_scenarios(_exposures())
    o = run_arena(s, res)
    g = analyse_gaps(s, o, _exposures())
    f1 = score_all(["A", "B", "C"], s, o, g)
    f2 = score_all(["A", "B", "C"], s, o, g)
    assert [x.score for x in f1] == [x.score for x in f2]


def test_exposures_map_to_features():
    exps = map_exposures_to_features([Exposure(exposure_id="x", title="Diabetes prevalence", description="chronic lifestyle diseases", basis=[], reasoning="", feature_keys=[])], llm=LLMService(mock_handler=None))
    assert "chronic_conditions_day1" in exps[0].feature_keys
    run_keys = features_for_run(exps)
    assert "waiting_period_ped" in run_keys and "chronic_conditions_day1" in run_keys


POLICIES = {
    "abhi_activ_one": "ABHI Activ One",
    "care_supreme": "Care Supreme",
    "hdfc_optima_secure_plus": "HDFC ERGO Optima Secure+",
    "niva_reassure_2": "Niva Bupa ReAssure 2.0",
}

BASELINE_FEATURES = [
    "in_patient_hospitalisation", "room_rent", "restore_recharge",
    "waiting_period_initial", "waiting_period_ped", "chronic_conditions_day1",
    "non_medical_expenses_cover", "copay",
]


def _brochure(pid: str, covered: set[str], excluded: set[str]) -> dict[str, FeatureFact]:
    facts = {}
    for feature in BASELINE_FEATURES + ["maternity", "waiting_period_specific", "ayush"]:
        if feature in covered:
            facts[feature] = _fact(pid, feature, CoverageStatus.COVERED, value="Stated in the brochure")
        elif feature in excluded:
            facts[feature] = _fact(pid, feature, CoverageStatus.EXCLUDED, value="Listed under exclusions")
        else:
            facts[feature] = _fact(pid, feature, CoverageStatus.NOT_FOUND)
    return facts


def _priority_results():
    """Same baseline evidence. Care states maternity; HDFC states AYUSH and excludes maternity."""
    now = datetime.now(timezone.utc).isoformat()
    books = {
        "abhi_activ_one": _brochure("abhi_activ_one", set(BASELINE_FEATURES), set()),
        "care_supreme": _brochure("care_supreme", set(BASELINE_FEATURES) | {"maternity", "waiting_period_specific"}, {"ayush"}),
        "hdfc_optima_secure_plus": _brochure("hdfc_optima_secure_plus", set(BASELINE_FEATURES) | {"ayush"}, {"maternity"}),
        "niva_reassure_2": _brochure("niva_reassure_2", set(BASELINE_FEATURES), set()),
    }
    return {pid: PolicyExtractionResult(policy_id=pid, facts=facts, generated_at=now) for pid, facts in books.items()}


def _docs():
    return {pid: PolicyDocument(policy_id=pid, policy_name=name, insurer=name, document_path="p", file_name="f") for pid, name in POLICIES.items()}


def test_priorities_change_the_recommendation():
    from app.policy_fit.explain import explain_recommendation

    results = _priority_results()
    docs = _docs()
    maternity = explain_recommendation("Acme", ["maternity"], results, docs)
    ayush = explain_recommendation("Acme", ["ayush"], results, docs)
    assert maternity["recommended_policy"] == "Care Supreme"
    assert maternity["decision_state"] == "eligible"
    assert maternity["competing_policy_ids"] == []
    assert ayush["recommended_policy"] == "HDFC ERGO Optima Secure+"
    assert maternity["recommended_policy"] != ayush["recommended_policy"]
    care = maternity["policies"]["Care Supreme"]
    hdfc = maternity["policies"]["HDFC ERGO Optima Secure+"]
    silent = maternity["policies"]["ABHI Activ One"]
    assert care["score"] > hdfc["score"]
    assert care["decision_sufficient"] and not silent["decision_sufficient"]
    assert silent["eligible"] and silent["evidence_completeness"] < care["evidence_completeness"]
    assert "maternity" in silent["unresolved"] and "maternity" not in silent["must_have_gaps"]
    assert maternity["scoring_config"]["deductible_formula"].startswith("score(A)")
    assert silent["why"]
    for name in POLICIES.values():
        row = maternity["policies"][name]
        assert "requirement_fit" in row["components"] and "evidence_completeness" in row["components"]
        credited = [c["contribution"] for c in row["contributions"] if c["contribution"] is not None]
        assert abs(sum(credited) - row["score"]) < 0.05
    assert abs(maternity["weights_sum"] - 1) < 1e-6


def test_tie_does_not_prefer_hdfc():
    now = datetime.now(timezone.utc).isoformat()
    facts = {pid: _brochure(pid, set(BASELINE_FEATURES), set()) for pid in ("hdfc_optima_secure_plus", "abhi_activ_one")}
    results = {pid: PolicyExtractionResult(policy_id=pid, facts=f, generated_at=now) for pid, f in facts.items()}
    scenarios = build_scenarios(_exposures())
    outcomes = run_arena(scenarios, results)
    fits = score_all(["hdfc_optima_secure_plus", "abhi_activ_one"], scenarios, outcomes, [])
    assert fits[0].score == fits[1].score
    docs = {pid: PolicyDocument(policy_id=pid, policy_name=pid, insurer="x", document_path="p", file_name="f") for pid in facts}
    rec = recommend(fits, [], docs)
    assert rec.recommended_policy_id == ""
    assert rec.decision_state in {"close_decision", "incomplete_comparison"}
    assert set(rec.competing_policy_ids) == set(facts)


def test_extra_pages_do_not_win_the_comparison():
    """Features only the long brochure mentions are not in the ranking. A feature the client asked for still is."""
    from app.models.fit import Scenario, ScenarioOutcome
    from app.models.policy import SourceRef
    from app.policy_fit.scoring import score_all

    shared = [Scenario(scenario_id=f"s{i}", exposure_id="e", title="t", description="d", feature_keys=["f"]) for i in range(4)]
    only_long = [Scenario(scenario_id=f"x{i}", exposure_id="e", title="t", description="d", feature_keys=["f"]) for i in range(8)]
    asked = Scenario(scenario_id="asked", exposure_id="e", title="Maternity", description="d", feature_keys=["maternity"], client_asked=True)
    src = SourceRef(policy_id="thin", chunk_id="c", page=1, source_text="x", retrieval_relevance=0.8)

    def row(pid, scenario, status, value):
        return ScenarioOutcome(scenario_id=scenario.scenario_id, policy_id=pid, status=status, rationale="", value=value, sources=[src.model_copy(update={"policy_id": pid})] if value is not None else [])

    outcomes = []
    for sc in shared:
        outcomes += [row("thin", sc, CoverageStatus.COVERED, 1.0), row("full", sc, CoverageStatus.COVERED, 1.0)]
    for sc in only_long:
        outcomes += [row("thin", sc, CoverageStatus.NOT_FOUND, None), row("full", sc, CoverageStatus.COVERED, 1.0)]
    tied = {f.policy_id: f for f in score_all(["thin", "full"], shared + only_long, outcomes, [])}
    assert abs(tied["full"].score - tied["thin"].score) < 5

    outcomes += [row("thin", asked, CoverageStatus.NOT_FOUND, None), row("full", asked, CoverageStatus.COVERED, 1.0)]
    ordered = score_all(["thin", "full"], shared + only_long + [asked], outcomes, [])
    by = {f.policy_id: f for f in ordered}
    assert by["full"].client_requirements_resolved
    assert not by["thin"].client_requirements_resolved
    assert by["thin"].eligible and by["full"].eligible
    assert "maternity" in by["thin"].unresolved
    assert "maternity" not in by["thin"].must_have_gaps
    assert by["thin"].evidence_completeness < by["full"].evidence_completeness
    assert abs(by["full"].score - by["thin"].score) < 5
    assert by["thin"].decision_sufficient is False
    assert recommend(ordered, [], {}).recommended_policy_id == "full"
