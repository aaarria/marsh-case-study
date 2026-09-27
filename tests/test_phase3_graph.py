"""Phase 3 graph, Policy Check, and canonical-fact tests. Scoring stays in the Phase 2 engine."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.graph.builder import AGENT_NODES, HAPPY_PATH, build_graph
from app.models.client import CompanyProfile
from app.models.fit import ClientRequirement, CriterionType, RequirementClass
from app.models.policy import ConditionMateriality, CoverageStatus, FeatureFact, PolicyExtractionResult, SourceRef
from app.policies.conditions import canonicalize_fact
from app.policy_fit.requirements import assign_weights
from app.policy_fit.scoring import recommend, score_policies
from app.policy_fit.stress import run_policy_check, unavailable_check
from app.research.porter import analyse_market
from app.services.llm import LLMService

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("hdfc", "abhi", "niva", "optima", "reassure", "activ one", "care supreme")


def _fact(feature: str, status: CoverageStatus, **kw) -> FeatureFact:
    sources = [] if status == CoverageStatus.NOT_FOUND else [SourceRef(policy_id="unused", chunk_id="c", page=3, source_text=kw.pop("quote", "clause"))]
    return FeatureFact(policy_id="unused", feature=feature, coverage_status=status, sources=sources, **kw)


def _req(feature: str, klass: RequirementClass) -> ClientRequirement:
    return ClientRequirement(
        requirement_id=feature,
        description=feature,
        feature=feature,
        type=CriterionType.COVERAGE,
        priority_weight=2 if klass != RequirementClass.BASELINE else 1,
        requirement_class=klass,
        hard_constraint=klass == RequirementClass.MUST_HAVE,
        source="test",
        client_asked=klass != RequirementClass.BASELINE,
        accept_add_on=klass != RequirementClass.MUST_HAVE,
    )


def _results(books):
    now = datetime.now(timezone.utc).isoformat()
    return {pid: PolicyExtractionResult(policy_id=pid, facts=facts, generated_at=now) for pid, facts in books.items()}


def test_purposeful_nodes_exist_in_order_without_duplicates():
    compiled = build_graph()
    drawing = compiled.get_graph()
    names = set(drawing.nodes)
    assert set(AGENT_NODES) == {
        "company_research", "market_intelligence", "exposure_requirements", "policy_intelligence",
        "policy_comparison", "deterministic_recommendation", "policy_check", "pitch", "audit", "pitch_revision",
    }
    assert len(AGENT_NODES) == len(set(AGENT_NODES.values()))
    for node in AGENT_NODES.values():
        assert node in names
    edges = {(edge.source, edge.target) for edge in drawing.edges}
    for left, right in zip(HAPPY_PATH, HAPPY_PATH[1:]):
        assert (left, right) in edges


def test_graph_state_round_trips():
    from app.graph.state import AdvisoryState

    state: AdvisoryState = {
        "run_id": "r",
        "market_context": {"status": "LIMITED"},
        "requirements": [{"requirement_id": "maternity"}],
        "policy_facts": {},
        "provisional_recommendation": {"recommended_policy_id": ""},
        "policy_check": {"status": "UNAVAILABLE"},
        "recommendation_history": [],
    }
    assert state["policy_check"]["status"] == "UNAVAILABLE"
    assert list(state)  # JSON-serialisable dict fields, not a private object


def test_porter_failure_does_not_change_a_score():
    def boom(system, user, schema):
        raise RuntimeError("malformed json")

    context = analyse_market(CompanyProfile(company_name="Acme"), LLMService(mock_handler=boom))
    assert context.status == "LIMITED"
    assert all(force.status == "UNKNOWN" for force in context.forces)
    reqs = assign_weights([_req("in_patient_hospitalisation", RequirementClass.BASELINE)])
    books = {"p": {"in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED)}}
    first = score_policies(["p"], reqs, _results(books), [])[0].score
    second = score_policies(["p"], reqs, _results(books), [])[0].score
    assert first == second
    assert "fit" not in context.model_dump_json()


def test_invalid_priority_stays_visible():
    bad, good = assign_weights([
        _req("maternity", RequirementClass.PREFERENCE).model_copy(update={"priority_weight": float("nan")}),
        _req("ayush", RequirementClass.PREFERENCE).model_copy(update={"priority_weight": 2, "requirement_id": "ayush"}),
    ])
    assert bad.priority_status == "REVIEW_REQUIRED"
    assert bad.weight_valid is False and bad.weight == 0
    assert good.weight == 1
    assert {bad.requirement_id, good.requirement_id} == {"maternity", "ayush"}


def test_conditions_are_extracted_once_and_conflicts_are_review_required():
    fact = _fact(
        "maternity",
        CoverageStatus.COVERED,
        value="Maternity is covered subject to a waiting period of 24 months. Maternity is covered subject to a waiting period of 24 months.",
    )
    canonical = canonicalize_fact(fact)
    assert canonical.condition_details
    assert len({item.text for item in canonical.condition_details}) == len(canonical.condition_details)
    assert any(item.materiality == ConditionMateriality.MATERIAL for item in canonical.condition_details)
    conflict = canonicalize_fact(_fact("maternity", CoverageStatus.COVERED, value="Maternity is not covered."))
    assert conflict.coverage_status == CoverageStatus.REVIEW_REQUIRED


def test_challenger_cannot_invent_or_override_and_material_retrieval_rescores():
    reqs = assign_weights([
        _req("maternity", RequirementClass.PREFERENCE),
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
    books["p2"]["maternity"] = _fact("maternity", CoverageStatus.COVERED, value="Maternity is covered.")
    fits = score_policies(ids, reqs, _results(books), [])
    rec = recommend(fits, [], {})
    assert rec.recommended_policy_id == "p2"

    def invent(system, user, schema):
        return schema(claims=[{"policy_id": "p9", "feature": "maternity", "evidence_id": "made-up", "statement": "Pick p9 because it pays everything.", "kind": "FACT"}])

    unchanged, final, _, history = run_policy_check(ids, reqs, _results(books), fits, rec, {}, llm=LLMService(mock_handler=invent))
    assert final.recommended_policy_id == "p2"
    assert unchanged.recalculated is False
    assert all(claim.evidence_id != "made-up" for claim in unchanged.challenges)
    assert history[0].recalculated_policy_id == "p2"

    def retrieve(policy_id, feature):
        return [SourceRef(policy_id=policy_id, chunk_id=f"{policy_id}-m", page=4, source_text="Maternity is covered under the base policy.")]

    checked, recalculated, _, history = run_policy_check(ids, reqs, _results(books), fits, rec, {}, retrieve=retrieve)
    assert checked.material and checked.recalculated
    assert checked.admitted_evidence
    assert any(row.policy_id == pid for pid in ids for row in checked.scenarios)
    assert len({row.policy_id for row in checked.scenarios}) == 4
    assert history[0].previous_policy_id == "p2"
    assert recalculated.decision_state in {"close_decision", "eligible", "incomplete_comparison"}
    assert "p9" not in {row.policy_id for row in checked.scenarios}


def test_sensitivity_and_gap_labels():
    reqs = assign_weights([
        _req("maternity", RequirementClass.PREFERENCE),
        _req("ayush", RequirementClass.PREFERENCE),
        _req("in_patient_hospitalisation", RequirementClass.BASELINE),
    ])
    books = {
        "p1": {
            "maternity": _fact("maternity", CoverageStatus.COVERED),
            "ayush": _fact("ayush", CoverageStatus.COVERED),
            "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED),
        },
        "p2": {
            "maternity": _fact("maternity", CoverageStatus.EXCLUDED),
            "ayush": _fact("ayush", CoverageStatus.EXCLUDED),
            "in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED),
        },
    }
    fits = score_policies(["p1", "p2"], reqs, _results(books), [])
    rec = recommend(fits, [], {})
    checked, final, _, _ = run_policy_check(["p1", "p2"], reqs, _results(books), fits, rec, {})
    assert rec.recommended_policy_id == "p1"
    assert checked.sensitivity == "STABLE"
    assert final.recommended_policy_id == "p1"
    assert any(gap.gap_type == "EXPLICIT_GAP" for gap in checked.gaps)
    assert all(gap.gap_type != "EXPLICIT_GAP" or gap.evidence or "excluded" in gap.detail.lower() for gap in checked.gaps)
    missing = next(gap for gap in checked.gaps if gap.gap_type == "INSUFFICIENT_EVIDENCE") if any(g.gap_type == "INSUFFICIENT_EVIDENCE" for g in checked.gaps) else None
    if missing:
        assert missing.evidence is None


def test_policy_check_failure_keeps_the_provisional_recommendation():
    reqs = assign_weights([_req("in_patient_hospitalisation", RequirementClass.BASELINE)])
    books = {"p": {"in_patient_hospitalisation": _fact("in_patient_hospitalisation", CoverageStatus.COVERED)}}
    fits = score_policies(["p"], reqs, _results(books), [])
    rec = recommend(fits, [], {})
    checked, kept, _, history = run_policy_check(["p"], reqs, _results(books), "bad-fits", rec, {})
    assert checked.status == "UNAVAILABLE"
    assert "not stress-tested" in checked.note
    assert kept.recommended_policy_id == rec.recommended_policy_id
    assert history == []
    fallback = unavailable_check(rec, "retrieval down")
    assert fallback.final_policy_id == rec.recommended_policy_id


def test_new_modules_have_no_insurer_scoring_branch():
    blob = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in [
            ROOT / "backend/app/policy_fit/stress.py",
            ROOT / "backend/app/policies/conditions.py",
            ROOT / "backend/app/research/porter.py",
        ]
    )
    for token in FORBIDDEN:
        assert token not in blob
    assert "if policy_id ==" not in blob
