"""Phase 4 product contract. The view labels engine output. It does not score."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.advisor_view import approval_allowed, build_advisor_view, lookup_evidence
from app.models.client import CompanyProfile, Exposure, FactKind
from app.models.fit import Recommendation
from app.models.pitch import Pitch, Slide, SlideBullet
from app.models.policy import CoverageStatus, FeatureFact, PolicyDocument, SourceRef
from app.pitch.evidence_pack import EvidenceItem, EvidencePack
from app.pitch.generator import generate_pitch
from app.pitch.pptx_builder import _parts, build_pitch_deck
from app.services.llm import LLMService

ROOT = Path(__file__).resolve().parents[1]


def test_advisor_view_does_not_invent_a_winner_or_a_quote():
    values = {
        "profile": {"company_name": "Northwind", "industry": "Logistics", "facts": [{"text": "Ships freight", "kind": "ASSUMPTION", "field": "overview"}]},
        "exposures": [{"title": "Maternity", "description": "Asked for", "status": "UNKNOWN", "reasoning": "Advisor priority"}],
        "recommendation": {"recommended_policy_id": "", "policy_name": "", "fit_score": 0, "decision_state": "incomplete_comparison", "rationale": ["unused"]},
        "fits": [{"policy_id": "p2", "score": 80, "confidence": "LOW", "evidence_completeness": 0.2, "decision_state": "incomplete_comparison", "contributions": []}],
        "matrix": {
            "features": ["maternity"],
            "policy_ids": ["p2", "p1"],
            "cells": {
                "maternity": {
                    "p1": {"status": "NOT_FOUND", "fact": {"coverage_status": "NOT_FOUND", "sources": []}},
                    "p2": {"status": "EXCLUDED", "fact": {"coverage_status": "EXCLUDED", "original_quote": "Maternity is not covered.", "sources": [{"source_text": "Maternity is not covered.", "page": 4, "chunk_id": "c2", "policy_name": "Second"}]}},
                }
            },
        },
        "requirements": [{"feature": "maternity", "client_asked": True}],
        "policy_check": {"status": "COMPLETED", "sensitivity": "INCOMPLETE", "challenges": [], "scenarios": [], "gaps": [{"gap_type": "INSUFFICIENT_EVIDENCE", "feature": "maternity", "policy_id": "p1", "detail": "No passage"}], "recalculated": False},
    }
    docs = {"p1": {"policy_name": "First Cover", "insurer": "First"}, "p2": {"policy_name": "Second Cover", "insurer": "Second"}}
    view = build_advisor_view(values, docs)
    assert view["recommendation"]["automatic"] is False
    assert view["recommendation"]["policy_id"] == ""
    assert view["recommendation"]["fit_score"] is None
    assert view["recommendation"]["decision_label"] == "INCOMPLETE COMPARISON"
    assert view["company"]["facts"][0]["label"] == "ASSUMPTION"
    assert view["company"]["exposures"][0]["label"] == "UNKNOWN"
    assert view["comparison"]["policies"][0]["policy_id"] == "p2"
    labels = {cell["policy_id"]: cell["status_label"] for cell in view["comparison"]["rows"][0]["cells"]}
    assert labels == {"p2": "Excluded under the supplied brochure", "p1": "Not established from supplied brochure"}
    assert view["comparison"]["baseline"] is False
    assert view["recommendation"]["baseline_only"] is False
    missing = lookup_evidence(values, "p1", "maternity")
    assert missing["quote"] is None and missing["status_label"] == "Not established from supplied brochure"
    found = lookup_evidence(values, "p2", "maternity")
    assert found["quote"] == "Maternity is not covered."
    assert view["policy_check"]["stability"] == "INCOMPLETE"
    assert view["policy_check"]["gaps"][0]["meaning"] == "Not established from the available evidence"
    legacy = build_advisor_view(
        {"recommendation": {"recommended_policy_id": "p1", "fit_score": 70, "rationale": ["documented"]}, "fits": [{"policy_id": "p1", "score": 70, "confidence": "HIGH", "evidence_completeness": 1, "contributions": []}]},
        {"p1": {"policy_name": "Sample Cover"}},
    )
    assert legacy["recommendation"]["automatic"] is True
    assert legacy["recommendation"]["fit_score"] == 70
    assert legacy["recommendation"]["policy_name"] == "Sample Cover"


def test_approval_blocks_a_failed_audit_without_a_named_override():
    assert approval_allowed("PASS", None)[0] is True
    assert approval_allowed("UNCERTAIN", None)[0] is True
    assert approval_allowed("FAIL", None)[0] is False
    assert approval_allowed("FAIL", "Aaria")[0] is True
    assert approval_allowed(None, "Aaria")[0] is False


def test_canvas_and_pptx_share_slide_wording(tmp_path):
    profile = CompanyProfile(company_name="Northwind", industry="Logistics", size="2,500 employees", geography="India", overview="Northwind moves freight across India for a workforce of 2,500.")
    exposures = [Exposure(exposure_id="e1", title="Accident cover", description="Workplace accidents need a defined benefit.", basis=[], reasoning="Asked", status=FactKind.ASSUMPTION, feature_keys=["personal_accident"], priority=1.8)]
    rec = Recommendation(recommended_policy_id="p1", policy_name="Sample Cover", fit_score=81, rationale=["Accident wording is documented."], decision_state="eligible")
    item = EvidenceItem(evidence_id="E1", policy_id="p1", policy_name="Sample Cover", feature_key="personal_accident", feature_label="Personal accident", status=CoverageStatus.COVERED, statement="Personal accident is covered.", sources=[SourceRef(policy_id="p1", policy_name="Sample Cover", chunk_id="c1", page=3, section="Accident", source_text="Personal accident is covered.")])
    pack = EvidencePack(recommended_policy_id="p1", items=[item])
    pitch, _warnings = generate_pitch(profile, exposures, rec, pack, llm=LLMService())
    assert 3 <= len(pitch.slides) <= 5
    out = build_pitch_deck(pitch, {"c1": item.sources[0]}, tmp_path / "deck.pptx")
    from pptx import Presentation

    prs = Presentation(str(out))
    texts = [" ".join(run.text for shape in slide.shapes if shape.has_text_frame for paragraph in shape.text_frame.paragraphs for run in paragraph.runs) for slide in prs.slides]
    assert pitch.slides[0].layout == "cover"
    assert texts and profile.company_name in texts[0]
    assert len(texts) == len(pitch.slides)
    assert len(texts) <= 5
    for slide, rendered in zip(pitch.slides, texts):
        label, body = _parts(slide.bullets[0].text) if slide.bullets else ("", "")
        assert slide.title in rendered or profile.company_name in rendered
        if body:
            assert body[:40] in rendered or label in rendered
    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    assert not getattr(run.hyperlink, "address", None)
    ts = (ROOT / "frontend/src/lib/slide-text.ts").read_text()
    assert "assumption:" in ts and "why this policy:" in ts and "watch-out:" in ts


def test_pitch_fit_score_uses_the_calculated_score_not_a_model_number():
    """A model that writes 87.1 when the engine scored 67.1 must not leave that figure in the deck."""
    from app.pitch.generator import BulletOut, SlideOut

    profile = CompanyProfile(company_name="POPXO", industry="Media", overview="POPXO is a digital media company.")
    exposures = [Exposure(exposure_id="e1", title="Hospitalisation", description="Employees need in-patient cover.", basis=[], reasoning="Baseline", status=FactKind.ASSUMPTION, feature_keys=["in_patient_hospitalisation"], priority=1)]
    rec = Recommendation(recommended_policy_id="p1", policy_name="HDFC ERGO Optima Secure+", fit_score=67.1, rationale=["Room rent is at actuals."], decision_state="eligible")
    item = EvidenceItem(evidence_id="E1", policy_id="p1", policy_name="HDFC ERGO Optima Secure+", feature_key="room_rent", feature_label="Room rent", status=CoverageStatus.COVERED, statement="Room rent is at actuals.", sources=[SourceRef(policy_id="p1", policy_name="HDFC ERGO Optima Secure+", chunk_id="c1", page=2, section="Room", source_text="Room Rent: At actuals.")])
    pack = EvidencePack(recommended_policy_id="p1", items=[item])

    class _Writer:
        available = True

        def structured(self, system, user, schema, purpose="", temperature=0.2):
            return schema(slides=[
                SlideOut(title="POPXO at a glance", bullets=[BulletOut(text="INDUSTRY|Digital media", kind="assumption")]),
                SlideOut(title="From exposure to benefit", bullets=[BulletOut(text="Room rent|Room rent is at actuals.", kind="policy", evidence_ids=["E1"])]),
                SlideOut(title="Why Marsh", bullets=[BulletOut(text="PERSPECTIVE|See the client's risks from more than one angle.", kind="marsh")]),
                SlideOut(title="One policy. Clear rationale.", subtitle="HDFC ERGO Optima Secure+", bullets=[
                    BulletOut(text="Recommended Policy: HDFC ERGO Optima Secure+ with a decision-support fit score of 87.1/100", kind="policy", evidence_ids=["E1"]),
                    BulletOut(text="SCORE|87.1/100", kind="recommendation"),
                ]),
            ])

    pitch, warnings = generate_pitch(profile, exposures, rec, pack, llm=_Writer())
    blob = " ".join(b.text for s in pitch.slides for b in s.bullets)
    assert "87.1" not in blob
    assert "67.1" not in blob
    assert "/100" not in blob
    assert any("not brochure evidence" in line or "not shown to the client" in line for line in warnings)


def test_upload_stays_out_of_the_policy_corpus(retriever):
    from app.main import app

    with TestClient(app) as http:
        bad = http.post("/api/policies/upload?filename=notes.txt", content=b"hello")
        assert bad.status_code == 422
        fake = http.post("/api/policies/upload?filename=extra.pdf", content=b"not a pdf")
        assert fake.status_code == 422
        before = {p["policy_id"] for p in http.get("/api/policies").json()["policies"]}
        ok = http.post("/api/policies/upload?filename=extra.pdf", content=b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF")
        assert ok.status_code == 200
        body = ok.json()
        assert body["in_comparison"] is False and body["stored"] is True
        after = {p["policy_id"] for p in http.get("/api/policies").json()["policies"]}
        assert after == before


def test_new_product_files_do_not_hardcode_a_winner():
    banned = ("hdfc", "abhi", "niva", "optima", "reassure", "activ one", "care supreme")
    for rel in ("backend/app/api/advisor_view.py", "frontend/src/components/advisor/brief.tsx", "frontend/src/lib/slide-text.ts"):
        text = (ROOT / rel).read_text().lower()
        assert not any(token in text for token in banned)
