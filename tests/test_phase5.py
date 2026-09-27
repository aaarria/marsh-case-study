"""Phase 5: scenario analysis, matrix states, pitch studio locks, recommendation changes, order symmetry."""
from datetime import datetime, timezone

from app.advisory.change import consider_recommendation_change
from app.advisory.scenario import analyse_scenario
from app.advisory.states import coverage_state
from app.advisory.studio import lock_problems, propose_transformation
from app.api.advisor_view import build_advisor_view
from app.models.fit import ClientRequirement, CoverageExpectation, RequirementClass
from app.models.pitch import Pitch, Slide, SlideBullet
from app.models.policy import CoverageStatus, FeatureFact, PolicyDocument, PolicyExtractionResult, SourceRef
from app.policy_fit.requirements import assign_weights
from app.policy_fit.scoring import recommend, score_policies

NOW = datetime.now(timezone.utc).isoformat()


def _doc(pid: str, name: str) -> PolicyDocument:
    return PolicyDocument(policy_id=pid, policy_name=name, insurer="Insurer", document_path="x", file_name="x.pdf")


def _fact(pid: str, feature: str, status: CoverageStatus, quote: str = "clause text here") -> FeatureFact:
    sources = [] if status == CoverageStatus.NOT_FOUND else [SourceRef(policy_id=pid, chunk_id=f"{pid}-{feature}", page=4, section="Benefit", source_text=quote)]
    return FeatureFact(policy_id=pid, feature=feature, coverage_status=status, original_quote=None if status == CoverageStatus.NOT_FOUND else quote, sources=sources)


def _books(rows: dict[str, list[FeatureFact]]) -> dict[str, PolicyExtractionResult]:
    return {pid: PolicyExtractionResult(policy_id=pid, facts={fact.feature: fact for fact in facts}, generated_at=NOW) for pid, facts in rows.items()}


def _req(feature: str) -> ClientRequirement:
    return ClientRequirement(
        requirement_id=feature,
        description=feature,
        feature=feature,
        type="coverage",
        priority_weight=2,
        requirement_class=RequirementClass.PREFERENCE,
        source="test",
        client_asked=True,
        accept_add_on=True,
        coverage_expectation=CoverageExpectation.EITHER,
    )


def test_scenario_evaluates_every_policy_and_does_not_pick_a_winner():
    ids = ["b", "a", "d", "c"]
    books = _books({
        "a": [_fact("a", "maternity", CoverageStatus.COVERED, "Maternity is covered under the base policy.")],
        "b": [_fact("b", "maternity", CoverageStatus.ADD_ON, "Maternity is an optional add-on.")],
        "c": [_fact("c", "maternity", CoverageStatus.EXCLUDED, "Maternity is not covered.")],
        "d": [_fact("d", "maternity", CoverageStatus.NOT_FOUND)],
    })
    docs = {pid: {"policy_name": pid} for pid in ids}
    out = analyse_scenario("The client wants strong maternity coverage.", ids, books, docs)
    assert out["ok"] and out["changes_recommendation"] is False
    cells = {cell["policy_id"]: cell["state"] for cell in out["rows"][0]["cells"]}
    assert list(cells) == ids
    assert cells == {"a": "COVERED", "b": "ADD_ON", "c": "EXCLUDED", "d": "NOT_ESTABLISHED"}
    assert "recommended_policy_id" not in out
    vague = analyse_scenario("hello", ids, books, docs)
    assert vague["ok"] is False and vague["rows"] == []
    unnamed = analyse_scenario("The client wants something nicer in general.", ids, books, docs)
    assert unnamed["ok"] is False


def test_matrix_states_do_not_score_and_keep_policy_order():
    values = {
        "matrix": {
            "features": ["maternity"],
            "policy_ids": ["d", "a", "c", "b"],
            "cells": {
                "maternity": {
                    "a": {"status": "COVERED"},
                    "b": {"status": "ADD_ON"},
                    "c": {"status": "EXCLUDED"},
                    "d": {"status": "NOT_FOUND"},
                }
            },
        },
        "requirements": [{"feature": "maternity", "client_asked": True}],
    }
    docs = {pid: {"policy_name": pid, "insurer": "x"} for pid in "abcd"}
    view = build_advisor_view(values, docs)
    row = view["comparison"]["rows"][0]
    assert [cell["policy_id"] for cell in row["cells"]] == ["d", "a", "c", "b"]
    assert [cell["state"] for cell in row["cells"]] == ["NOT_ESTABLISHED", "COVERED", "EXCLUDED", "ADD_ON"]
    assert coverage_state("REVIEW_REQUIRED") == "REVIEW_REQUIRED"
    assert coverage_state("PARTIALLY_COVERED") == "PARTIAL"
    assert "fit_score" not in row


def test_pitch_studio_locks_numbers_evidence_and_recommendation():
    slide = Slide(slide_number=2, title="Fit", layout="map", bullets=[
        SlideBullet(text="Maternity is covered after 36 months.", source_chunk_ids=["c1"], policy_id="p1", kind="policy"),
    ])
    pitch = Pitch(pitch_id="p", company_name="North", recommended_policy_id="p1", slides=[slide])
    refused = propose_transformation(slide, "Invent a dental benefit of 12 days.", pitch)
    assert refused["ok"] is False
    number = propose_transformation(slide, "Change the number to 12 months.", pitch)
    assert number["ok"] is False
    switch = propose_transformation(slide, "Change the recommendation to another policy.", pitch)
    assert switch["ok"] is False
    assert propose_transformation(slide, "Remove the exclusion from this slide.", pitch)["ok"] is False
    assert propose_transformation(slide, "Delete the evidence citation.", pitch)["ok"] is False
    visual = propose_transformation(slide, "Turn this into a 3-step process flow.", pitch)
    assert visual["ok"] and visual["intent"] == "process_flow"
    proposed = Slide.model_validate(visual["slide"])
    assert proposed.bullets[0].text == slide.bullets[0].text
    assert proposed.bullets[0].source_chunk_ids == ["c1"]
    broken = proposed.model_copy(deep=True)
    broken.bullets[0].text = "Maternity is covered after 12 months."
    assert lock_problems(slide, broken, pitch)


def test_recommendation_change_uses_the_engine_and_refuses_an_unsupported_switch():
    ids = ["p1", "p2"]
    books = _books({
        "p1": [_fact("p1", "maternity", CoverageStatus.COVERED, "Maternity is covered.")],
        "p2": [_fact("p2", "maternity", CoverageStatus.EXCLUDED, "Maternity is not covered.")],
    })
    docs = {"p1": _doc("p1", "Alpha Cover"), "p2": _doc("p2", "Beta Cover")}
    supported = consider_recommendation_change(ids, [], books, docs, "The client wants strong maternity coverage.", current_policy_id="")
    assert supported["supported"] is True
    assert supported["recommendation"]["recommended_policy_id"] == "p1"
    blocked = consider_recommendation_change(ids, [_req("maternity")], books, docs, "Change the recommendation to Beta Cover because the client prioritizes maternity.", current_policy_id="p1")
    assert blocked["supported"] is False and blocked["applied"] is False
    assert blocked["recommendation"]["recommended_policy_id"] != "p2"
    override = consider_recommendation_change(ids, [_req("maternity")], books, docs, "Change the recommendation to Beta Cover because the client prioritizes maternity.", current_policy_id="p1", override=True, reviewer="Aaria")
    assert override["override"] is True and override["recommendation"]["decision_state"] == "advisor_override"
    assert override["recommendation"]["recommended_policy_id"] == "p2"


def test_policy_order_does_not_change_scores_or_the_winner():
    facts = {
        "p1": [_fact("p1", "maternity", CoverageStatus.COVERED, "Maternity is covered.")],
        "p2": [_fact("p2", "maternity", CoverageStatus.EXCLUDED, "Maternity is not covered.")],
        "p3": [_fact("p3", "maternity", CoverageStatus.NOT_FOUND)],
        "p4": [_fact("p4", "maternity", CoverageStatus.ADD_ON, "Maternity is an optional add-on.")],
    }
    reqs = assign_weights([_req("maternity")])
    docs = {pid: _doc(pid, pid) for pid in facts}
    forward = ["p1", "p2", "p3", "p4"]
    reverse = list(reversed(forward))
    fit_a = score_policies(forward, reqs, _books(facts), [])
    fit_b = score_policies(reverse, reqs, _books(facts), [])
    assert {row.policy_id: row.score for row in fit_a} == {row.policy_id: row.score for row in fit_b}
    rec_a = recommend(fit_a, [], docs)
    rec_b = recommend(fit_b, [], docs)
    assert rec_a.recommended_policy_id == rec_b.recommended_policy_id == "p1"


def _must(feature: str) -> ClientRequirement:
    item = _req(feature)
    item.requirement_class = RequirementClass.MUST_HAVE
    return item


def test_incomplete_close_and_unchanged_recommendation_changes():
    ids = ["p1", "p2"]
    docs = {"p1": _doc("p1", "Alpha Cover"), "p2": _doc("p2", "Beta Cover")}
    missing = _books({
        "p1": [_fact("p1", "maternity", CoverageStatus.NOT_FOUND)],
        "p2": [_fact("p2", "maternity", CoverageStatus.NOT_FOUND)],
    })
    incomplete = consider_recommendation_change(ids, [_must("maternity")], missing, docs, "The client wants strong maternity coverage.")
    assert incomplete["applied"] is False and incomplete["supported"] is False
    assert not incomplete["recommendation"]["recommended_policy_id"]
    assert incomplete["recommendation"]["decision_state"] in {"incomplete_comparison", "incomplete"}

    both = _books({
        "p1": [_fact("p1", "maternity", CoverageStatus.COVERED, "Maternity is covered.")],
        "p2": [_fact("p2", "maternity", CoverageStatus.COVERED, "Maternity is covered.")],
    })
    close = consider_recommendation_change(ids, [], both, docs, "The client wants strong maternity coverage.", current_policy_id="p1")
    assert close["applied"] is False and close["supported"] is False
    assert not close["recommendation"]["recommended_policy_id"]
    assert close["recommendation"]["decision_state"] == "close_decision"

    split = _books({
        "p1": [_fact("p1", "maternity", CoverageStatus.COVERED, "Maternity is covered.")],
        "p2": [_fact("p2", "maternity", CoverageStatus.EXCLUDED, "Maternity is not covered.")],
    })
    same = consider_recommendation_change(ids, [], split, docs, "The client wants strong maternity coverage.", current_policy_id="p1")
    assert same["supported"] is True and same["unchanged"] is True
    assert same["recommendation"]["recommended_policy_id"] == "p1"


def test_scenario_cannot_force_an_insurer_and_keeps_missing_evidence_missing():
    ids = ["p1", "p2"]
    books = _books({
        "p1": [_fact("p1", "maternity", CoverageStatus.EXCLUDED, "Maternity is not covered.")],
        "p2": [_fact("p2", "maternity", CoverageStatus.NOT_FOUND)],
    })
    docs = {pid: {"policy_name": name} for pid, name in (("p1", "Alpha Cover"), ("p2", "Beta Cover"))}
    forced = analyse_scenario("Change the recommendation to Alpha Cover for maternity.", ids, books, docs)
    assert forced["ok"] is True and forced["changes_recommendation"] is False
    assert "recommended_policy_id" not in forced
    cells = {cell["policy_id"]: cell["state"] for cell in forced["rows"][0]["cells"]}
    assert cells["p1"] == "EXCLUDED" and cells["p2"] == "NOT_ESTABLISHED"


def test_phase5_routes_and_advisor_files_stay_symmetric(retriever):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.main import app

    root = Path(__file__).resolve().parents[1]
    with TestClient(app) as http:
        assert http.post("/api/runs/missing-run/scenario", json={"text": "The client wants strong maternity coverage."}).status_code == 404
        assert http.post("/api/runs/missing-run/pitch-studio", json={"slide_number": 1, "instruction": "Make this more executive-friendly."}).status_code == 404
        assert http.post("/api/runs/missing-run/recommendation-change", json={"instruction": "The client wants strong maternity coverage."}).status_code == 404
        listed = http.get("/api/policies/uploads")
        assert listed.status_code == 200
        corpus = listed.json()["corpus"]
        assert len(corpus) == 4
        assert all(row["status"] == "Ready" and row["in_comparison"] is True for row in corpus)
        stored = http.post("/api/policies/upload?filename=side.pdf", content=b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF")
        assert stored.status_code == 200
        assert stored.json()["status"] == "Uploaded" and stored.json()["selected"] is False and stored.json()["in_comparison"] is False

    banned = ("hdfc", "abhi", "niva", "optima", "reassure", "activ one", "care supreme")
    watched = [
        "backend/app/advisory/scenario.py",
        "backend/app/advisory/change.py",
        "backend/app/advisory/studio.py",
        "backend/app/advisory/states.py",
        "frontend/src/components/advisor/brief.tsx",
        "frontend/src/components/advisor/scenario-panel.tsx",
        "frontend/src/components/advisor/recommendation-change.tsx",
        "frontend/src/components/advisor/pitch-studio.tsx",
    ]
    for rel in watched:
        text = (root / rel).read_text().lower()
        assert not any(token in text for token in banned), rel
    ui = "\n".join((root / rel).read_text() for rel in watched if rel.startswith("frontend"))
    assert "COVERAGE SCENARIO ANALYSIS" in ui
    assert "COVERAGE & EVIDENCE MATRIX" in ui
    assert "ADVISORY PITCH STUDIO" in ui
