"""Phase 5.1: locked wording rewrites, and a deterministic product smoke through the API."""
import pytest
from fastapi.testclient import TestClient

from app.advisory.studio import propose_transformation
from app.models.pitch import Pitch, Slide, SlideBullet
from app.services.llm import LLMService


def _slide(text: str, *, kind: str = "policy") -> tuple[Slide, Pitch]:
    slide = Slide(slide_number=2, title="Fit", layout="map", bullets=[
        SlideBullet(text=text, source_chunk_ids=["c1"], policy_id="p1", kind=kind),
    ])
    return slide, Pitch(pitch_id="p", company_name="North", recommended_policy_id="p1", slides=[slide])


def _llm(text: str):
    def handle(_system, _user, schema):
        return schema(title="Fit", subtitle="Facts unchanged.", bullets=[text])

    return LLMService(mock_handler=handle)


def test_wording_rewrite_keeps_numbers_evidence_and_recommendation():
    slide, pitch = _slide("Maternity is covered after 36 months.")
    out = propose_transformation(slide, "Make this more concise and executive-friendly.", pitch, llm=_llm("Maternity cover applies after 36 months."), policy_names=["Alpha Cover"])
    assert out["ok"] is True and out["wording"] is True
    proposed = Slide.model_validate(out["slide"])
    assert proposed.bullets[0].text == "Maternity cover applies after 36 months."
    assert proposed.bullets[0].source_chunk_ids == ["c1"]
    assert proposed.bullets[0].policy_id == "p1"
    assert out["changes"][0]["before"] != out["changes"][0]["after"]
    assert out["locks"]["EVIDENCE_LOCK"] == ["c1"]
    assert out["locks"]["RECOMMENDATION_LOCK"] == "p1"


def test_wording_rejects_numbers_waiting_periods_exclusions_names_and_new_benefits():
    slide, pitch = _slide("Maternity is covered after 36 months.")
    number = propose_transformation(slide, "Make this more concise.", pitch, llm=_llm("Maternity is covered after 12 months."))
    assert number["ok"] is False
    assert "verified policy fact" in number["message"]

    waiting, waiting_pitch = _slide("The waiting period is 36 months.")
    dropped = propose_transformation(waiting, "Make this easier to scan.", waiting_pitch, llm=_llm("Cover starts after 36 months."))
    assert dropped["ok"] is False

    excluded, excluded_pitch = _slide("Maternity is excluded after 36 months.")
    flipped = propose_transformation(excluded, "Make this more commercially polished.", excluded_pitch, llm=_llm("Maternity is covered after 36 months."))
    assert flipped["ok"] is False

    named, named_pitch = _slide("Alpha Cover includes maternity after 36 months.")
    renamed = propose_transformation(named, "Make this suitable for a senior HR audience.", named_pitch, llm=_llm("Beta Cover includes maternity after 36 months."), policy_names=["Alpha Cover", "Beta Cover"])
    assert renamed["ok"] is False

    rec, rec_pitch = _slide("Recommend Alpha Cover for this client.", kind="recommendation")
    switched = propose_transformation(rec, "Make this more concise.", rec_pitch, llm=_llm("Recommend Beta Cover for this client."), policy_names=["Alpha Cover", "Beta Cover"])
    assert switched["ok"] is False

    invented = propose_transformation(slide, "Strengthen the executive takeaway.", pitch, llm=_llm("Maternity is covered after 36 months, with personal accident cover."))
    assert invented["ok"] is False
    assert "verified policy fact" in invented["message"]

    kept = propose_transformation(slide, "Reduce the text.", pitch, llm=_llm("Maternity cover applies after 36 months."))
    assert kept["ok"] is True
    assert Slide.model_validate(kept["slide"]).bullets[0].source_chunk_ids == ["c1"]


@pytest.fixture(scope="module")
def client(retriever):
    from app.main import app

    with TestClient(app) as http:
        yield http


def test_product_smoke_covers_comparison_scenario_and_studio(client):
    """Deterministic stand-in for the browser flow. Model output is the offline graph, not Gemini wording."""
    import time
    created = client.post("/api/client/analyze", json={"company_name": "Northwind Logistics", "industry": "Logistics", "employee_count": 800, "client_priorities": ["maternity benefits"]})
    assert created.status_code == 200
    run_id = created.json()["run_id"]
    deadline = time.time() + 90
    state = None
    while time.time() < deadline:
        state = client.get(f"/api/runs/{run_id}").json()
        if state["run"]["status"] in {"awaiting_review", "failed", "approved"}:
            question = state.get("question") or {}
            if state["run"]["status"] != "awaiting_review" or question.get("question") in {"review", "context"}:
                break
            if question.get("question") == "close_call":
                assert client.post(f"/api/runs/{run_id}/answer", json={"action": "keep"}).status_code == 200
        time.sleep(0.4)
    assert state and state["run"]["status"] == "awaiting_review", state["run"] if state else None
    if state["question"]["question"] == "context":
        assert client.post(f"/api/runs/{run_id}/answer", json={"action": "continue"}).status_code == 200
        deadline = time.time() + 90
        while time.time() < deadline:
            state = client.get(f"/api/runs/{run_id}").json()
            if state["run"]["status"] == "failed":
                break
            question = state.get("question") or {}
            if question.get("question") == "close_call":
                client.post(f"/api/runs/{run_id}/answer", json={"action": "keep"})
            elif question.get("question") == "review":
                break
            time.sleep(0.4)
    assert state["question"]["question"] == "review"
    advisor = state["advisor"]
    assert len(advisor["comparison"]["policies"]) == 4
    assert advisor["comparison"]["rows"]
    assert advisor["recommendation"]
    assert advisor["policy_check"]
    cell = advisor["comparison"]["rows"][0]
    evidence = client.get(f"/api/runs/{run_id}/evidence", params={"policy_id": cell["cells"][0]["policy_id"], "feature": cell["feature"]})
    assert evidence.status_code == 200
    assert evidence.json()["status_label"]
    scenario = client.post(f"/api/runs/{run_id}/scenario", json={"text": "The client wants strong maternity coverage."})
    assert scenario.status_code == 200
    body = scenario.json()
    assert body["changes_recommendation"] is False and len(body["rows"][0]["cells"]) == 4
    slide_number = state["values"]["pitch"]["slides"][0]["slide_number"]
    studio = client.post(f"/api/runs/{run_id}/pitch-studio", json={"slide_number": slide_number, "instruction": "Make this more concise and executive-friendly."})
    assert studio.status_code == 200
    proposal = studio.json()
    assert proposal["ok"] is True and proposal["audit"]["gate"] in {"PASS", "UNCERTAIN", "FAIL"}
    assert proposal["locks"]["EVIDENCE_LOCK"] is not None
    if proposal["audit_blocks_accept"]:
        assert proposal["acceptable"] is False
    else:
        edited = state["values"]["pitch"]["slides"]
        edited[0] = proposal["slide"]
        saved = client.post(f"/api/runs/{run_id}/answer", json={"action": "edit", "slides": edited, "reviewer": "smoke"})
        assert saved.status_code == 200
