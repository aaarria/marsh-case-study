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
    assert m.cells["maternity"]["B"].display == "Not found"
    assert m.cells["air_ambulance"]["A"].fact.sources[0].page == 2
    assert cell_display(m.cells["air_ambulance"]["B"].fact).startswith("Add-on")


def test_not_found_is_not_excluded_in_scoring():
    res = _results()
    scenarios = build_scenarios(_exposures())
    outcomes = run_arena(scenarios, res)
    gaps = analyse_gaps(scenarios, outcomes, _exposures())
    fits = score_all(["A", "B", "C"], scenarios, outcomes, gaps)
    by = {f.policy_id: f for f in fits}
    # B (maternity NOT_FOUND) must score higher on exclusion_risk than A (maternity EXCLUDED)
    excl = {pid: next(c for c in f.components if c.name == "exclusion_risk").value for pid, f in by.items()}
    assert excl["A"] > 0 and excl["B"] == 0
    unc = {pid: next(c for c in f.components if c.name == "uncertainty").value for pid, f in by.items()}
    assert unc["B"] > 0
    assert all(0 <= f.score <= 100 for f in fits)
    assert all(len(f.components) == 4 and f.explanation for f in fits)


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
    assert ayush["recommended_policy"] == "HDFC ERGO Optima Secure+"
    assert maternity["recommended_policy"] != ayush["recommended_policy"]
    scores = {name: row["score"] for name, row in maternity["policies"].items()}
    assert maternity["recommended_policy"] == max(scores, key=scores.get)
    for name in POLICIES.values():
        row = maternity["policies"][name]
        assert set(row["components"]) == {"exposure_coverage", "evidence_strength", "exclusion_risk", "uncertainty"}


def test_tie_does_not_prefer_hdfc():
    now = datetime.now(timezone.utc).isoformat()
    facts = {pid: _brochure(pid, set(BASELINE_FEATURES), set()) for pid in ("hdfc_optima_secure_plus", "abhi_activ_one")}
    results = {pid: PolicyExtractionResult(policy_id=pid, facts=f, generated_at=now) for pid, f in facts.items()}
    scenarios = build_scenarios(_exposures())
    outcomes = run_arena(scenarios, results)
    fits = score_all(["hdfc_optima_secure_plus", "abhi_activ_one"], scenarios, outcomes, [])
    assert fits[0].score == fits[1].score
    assert fits[0].policy_id == "abhi_activ_one"


def test_silence_does_not_beat_documented_coverage():
    """A brochure that mentions few scenarios (all covered) must not outscore one that documents most of them."""
    from app.models.fit import Scenario, ScenarioOutcome
    from app.models.policy import SourceRef
    from app.policy_fit.scoring import score_all

    scenarios = [Scenario(scenario_id=f"s{i}", exposure_id="e", title="t", description="d", feature_keys=["f"]) for i in range(21)]
    src = SourceRef(policy_id="thin", chunk_id="c", page=1, source_text="x", retrieval_relevance=0.8)
    outcomes = []
    for i, sc in enumerate(scenarios):
        # "thin": 6 covered, 15 not addressed at all
        covered = i < 6
        outcomes.append(ScenarioOutcome(scenario_id=sc.scenario_id, policy_id="thin", status=CoverageStatus.COVERED if covered else CoverageStatus.NOT_FOUND, rationale="", value=1.0 if covered else None, sources=[src] if covered else []))
        # "full": 13 covered, 3 conditional, 3 add-on, 2 not addressed
        st, val = (CoverageStatus.COVERED, 1.0) if i < 13 else (CoverageStatus.CONDITIONAL, 0.75) if i < 16 else (CoverageStatus.ADD_ON, 0.35) if i < 19 else (CoverageStatus.NOT_FOUND, None)
        outcomes.append(ScenarioOutcome(scenario_id=sc.scenario_id, policy_id="full", status=st, rationale="", value=val, sources=[src.model_copy(update={"policy_id": "full"})] if val is not None else []))
    fits = {f.policy_id: f for f in score_all(["thin", "full"], scenarios, outcomes, [])}
    assert fits["full"].score > fits["thin"].score + 10
    assert fits["thin"].confidence == "LOW" and fits["full"].confidence == "HIGH"
