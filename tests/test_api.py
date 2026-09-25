"""API + LangGraph integration tests (offline: no LLM / research keys; requires ingested indexes)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(retriever):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _wait(client, run_id, target: set[str], timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/api/runs/{run_id}").json()
        if r["run"]["status"] in target or r["run"]["status"] == "failed":
            return r
        time.sleep(0.5)
    raise AssertionError("timeout waiting for run")


def _wait_for_question(client, run_id, kind: str):
    """Wait until the run pauses on `kind`; a close call on the way is answered by keeping the score."""
    while True:
        st = _wait(client, run_id, {"awaiting_review"})
        assert st["run"]["status"] == "awaiting_review", st["run"].get("error")
        q = st["question"]
        assert q, "paused without a question"
        if q["question"] == kind:
            return st
        assert q["question"] == "close_call", q
        assert client.post(f"/api/runs/{run_id}/answer", json={"action": "keep"}).status_code == 200


def test_health_and_policies(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["retrieval"]["ready"]
    p = client.get("/api/policies").json()
    assert len(p["policies"]) == 4


def test_analyze_validation(client):
    assert client.post("/api/client/analyze", json={"company_name": "A"}).status_code == 422
    assert client.post("/api/client/analyze", json={"company_name": "Acme", "employee_count": 0}).status_code == 422


def test_full_run_review_edit_and_approve(client):
    r = client.post("/api/client/analyze", json={"company_name": "Acme Logistics", "industry": "Logistics", "employee_count": 2500, "client_priorities": ["accident cover"]})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    st = _wait_for_question(client, run_id, "review")
    assert "human_review" in st["pending"]
    assert {o["id"] for o in st["question"]["options"]} == {"approve", "edit", "regenerate", "reject"}
    nodes = [e["node"] for e in st["events"]]
    for n in ["research_company", "confirm_context", "map_exposures", "compare_policies", "policy_fit_arena", "confirm_recommendation", "evidence_pack", "generate_pitch", "audit_pitch"]:
        assert n in nodes
    assert not any(e["node"] == "confirm_context" and e["status"] == "waiting" for e in st["events"]), "context was given; must not ask"
    full = client.get(f"/api/runs/{run_id}").json()["values"]
    assert full["recommendation"]["recommended_policy_id"]
    assert full["audit"]["summary"]["gate"] in {"PASS", "UNCERTAIN", "FAIL"}
    assert any(f.startswith("Retrieval relevance") or "not factual accuracy" in f for f in [full["audit"]["note"]])
    pitch = client.get(f"/api/runs/{run_id}/artifacts/pitch").json()
    assert 3 <= len(pitch["slides"]) <= 5

    # every policy claim carries an Evidence Passport pointing at brochure text
    for ca in full["audit"]["claims"]:
        if ca["claim"]["claim_type"] == "POLICY":
            assert ca["passport"]["claim_id"] == ca["claim"]["claim_id"]

    # preview audit of an edited slide with a fabricated number -> must not pass silently
    slides = pitch["slides"]
    slides[1]["bullets"].append({"text": "Dental implants covered up to INR 9,99,999.", "source_chunk_ids": [], "policy_id": full["recommendation"]["recommended_policy_id"], "kind": "policy"})
    pa = client.post(f"/api/runs/{run_id}/audit-preview", json={"slides": slides})
    assert pa.status_code == 200
    rep = pa.json()["audit"]
    fake = next(c for c in rep["claims"] if "Dental" in c["claim"]["claim_text"])
    assert fake["status"] == "NOT_FOUND" and rep["summary"]["gate"] == "FAIL"

    # an answer that is not one of the question's options is refused
    assert client.post(f"/api/runs/{run_id}/answer", json={"action": "continue"}).status_code == 409
    assert client.post(f"/api/runs/{run_id}/answer", json={"action": "edit"}).status_code == 422
    # edit (advisor removes the fabricated bullet again) then approve
    slides[1]["bullets"].pop()
    e = client.post(f"/api/runs/{run_id}/answer", json={"action": "edit", "slides": slides, "reviewer": "test"})
    assert e.status_code == 200
    st = _wait_for_question(client, run_id, "review")
    assert st["question"]["pitch_version"] == 2
    a = client.post(f"/api/runs/{run_id}/answer", json={"action": "approve", "reviewer": "test"})
    assert a.status_code == 200
    st = _wait(client, run_id, {"approved"})
    assert st["run"]["status"] == "approved"
    outputs = st["run"]["outputs"]
    assert {"pitch_pptx", "audit_md", "audit_json"} <= set(outputs)
    d = client.get(f"/api/downloads/{run_id}/pitch_pptx")
    assert d.status_code == 200 and d.headers["content-type"].startswith("application/vnd.openxmlformats")
    # the deck's brochure links resolve: the PDF is served inline so #page=N opens the cited page
    pid = st["values"]["recommendation"]["recommended_policy_id"]
    b = client.get(f"/api/policies/{pid}/document")
    assert b.status_code == 200 and b.headers["content-type"] == "application/pdf" and b.headers["content-disposition"].startswith("inline")
    assert client.get("/api/policies/nope/document").status_code == 404
    # further answers on a finished run are rejected
    assert client.post(f"/api/runs/{run_id}/answer", json={"action": "approve"}).status_code == 409


def test_name_only_intake_asks_for_context_then_uses_it(client):
    """With research off and only a name, the run pauses to ask for context instead of silently assuming."""
    r = client.post("/api/client/analyze", json={"company_name": "Nameless Traders"})
    run_id = r.json()["run_id"]
    st = _wait_for_question(client, run_id, "context")
    assert "confirm_context" in st["pending"]
    assert {o["id"] for o in st["question"]["options"]} == {"add_context", "continue"}
    a = client.post(f"/api/runs/{run_id}/answer", json={"action": "add_context", "industry": "Retail", "employee_count": 300, "client_priorities": ["maternity"]})
    assert a.status_code == 200
    st = _wait_for_question(client, run_id, "review")
    vals = st["values"]
    assert vals["intake"]["industry"] == "Retail" and vals["intake"]["employee_count"] == 300
    # research ran again with the added context and the profile now carries the advisor facts
    assert sum(1 for e in st["events"] if e["node"] == "research_company" and e["status"] == "completed") == 2
    assert any("Retail" in f["text"] for f in vals["profile"]["facts"])
    # asked exactly once: re-entering confirm_context after research must not ask again
    assert sum(1 for e in st["events"] if e["node"] == "confirm_context" and e["status"] == "waiting") == 1
    assert client.post(f"/api/runs/{run_id}/answer", json={"action": "reject", "note": "test"}).status_code == 200
    assert _wait(client, run_id, {"rejected"})["run"]["status"] == "rejected"


def test_name_only_intake_can_continue_with_assumptions(client):
    r = client.post("/api/client/analyze", json={"company_name": "Nameless Traders"})
    run_id = r.json()["run_id"]
    _wait_for_question(client, run_id, "context")
    assert client.post(f"/api/runs/{run_id}/answer", json={"action": "continue"}).status_code == 200
    st = _wait_for_question(client, run_id, "review")
    assert sum(1 for e in st["events"] if e["node"] == "research_company" and e["status"] == "completed") == 1
    assert st["values"]["recommendation"]["recommended_policy_id"]


def test_close_call_lets_the_advisor_choose(client, monkeypatch):
    """When the advisor picks the runner-up in a close call, the recommendation follows the choice and says so."""
    from app.graph import nodes
    from app.models.fit import FitComponent, PolicyFitResult

    docs = nodes.store().list_policies()
    a, b = docs[0].policy_id, docs[1].policy_id
    comp: list[FitComponent] = []
    fits = [PolicyFitResult(policy_id=a, score=72.0, confidence="MEDIUM", components=comp, explanation=["a"], evaluated_scenarios=3, unknown_scenarios=0, close_call_with=[b]), PolicyFitResult(policy_id=b, score=70.0, confidence="MEDIUM", components=comp, explanation=["b"], evaluated_scenarios=3, unknown_scenarios=0, close_call_with=[a])]
    state = {"run_id": "run_closecall_test", "intake": {"company_name": "Tie Co"}, "policy_ids": [a, b], "fits": [f.model_dump(mode="json") for f in fits], "gaps": []}
    asked: list[dict] = []

    def fake_ask(_state, _name, question):
        asked.append(question)
        return {"action": b}

    monkeypatch.setattr(nodes, "ask", fake_ask)
    try:
        out = nodes.close_call_node(state)
        assert asked and asked[0]["question"] == "close_call" and {o["id"] for o in asked[0]["options"]} == {a, b, "keep"}
        assert out["recommendation"]["recommended_policy_id"] == b and out["recommendation_confirmed"] is True
        assert out["fits"][0]["policy_id"] == b and "Advisor's choice" in out["recommendation"]["rationale"][0]
        # keeping the score, or no close call at all, never asks
        asked.clear()
        assert nodes.close_call_node({**state, "recommendation_confirmed": True}) == {"recommendation_confirmed": True} and not asked
        fits[0].close_call_with = []
        assert nodes.close_call_node({**state, "fits": [f.model_dump(mode="json") for f in fits]}) == {"recommendation_confirmed": True} and not asked
    finally:
        with nodes.store()._conn() as c:
            c.execute("DELETE FROM runs WHERE run_id=?", (state["run_id"],))
            c.execute("DELETE FROM run_events WHERE run_id=?", (state["run_id"],))


def test_orphaned_running_run_is_marked_interrupted_and_retryable(client):
    """A run left in `running` by a dead process must not stay 'Generating' forever."""
    from app.graph.nodes import store
    from app.services import runs as run_svc

    st = store()
    run_id = "run_orphan_test"
    st.save_run(run_id, "Orphan Co", "running", {"policy_ids": [], "current_node": "generate_pitch"})
    try:
        fixed = run_svc.reconcile_orphaned_runs()
        assert run_id in fixed
        summary = next(r for r in client.get("/api/runs?limit=100").json()["runs"] if r["run_id"] == run_id)
        assert summary["status"] == "failed"
        assert summary["error_kind"] == "interrupted"
        assert summary["retryable"] is True
        # No checkpoint exists for this synthetic run, so retry is refused with a clear 409 rather than a 500.
        r = client.post(f"/api/runs/{run_id}/retry")
        assert r.status_code == 409 and "checkpoint" in r.json()["detail"].lower()
    finally:
        with st._conn() as c:
            c.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM run_events WHERE run_id=?", (run_id,))


def test_concurrent_launches_are_serialised(client, monkeypatch):
    """Double-submitting an answer must not resume a graph twice; the run ceiling must answer 429."""
    import threading

    from app.services import runs as run_svc

    # A worker that stays alive until released stands in for a long Gemini call.
    release = threading.Event()
    monkeypatch.setattr(run_svc, "_execute", lambda run_id, payload: release.wait(5))
    try:
        run_svc._launch("run_lock_a", None, name="t")
        with pytest.raises(run_svc.RunStateError):
            run_svc._launch("run_lock_a", None, name="t")  # second submit for the same run
        monkeypatch.setattr(run_svc.get_settings(), "max_concurrent_runs", 1)
        with pytest.raises(run_svc.TooManyRuns):
            run_svc._launch("run_lock_b", None, name="t", new_run=True)
        r = client.post("/api/client/analyze", json={"company_name": "Ceiling Co"})
        assert r.status_code == 429 and "limit 1" in r.json()["detail"]
        assert not any(x["company_name"] == "Ceiling Co" for x in client.get("/api/runs?limit=100").json()["runs"])  # nothing half-created
    finally:
        release.set()
        for rid in ("run_lock_a", "run_lock_b"):
            t = run_svc._threads.pop(rid, None)
            if t:
                t.join(1)
