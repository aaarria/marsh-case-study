"""FastAPI routes."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.api.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnswerRequest,
    AuditPreviewRequest,
    PitchStudioRequest,
    RecommendationChangeRequest,
    RewriteRequest,
    ScenarioRequest,
)
from app.auditing.audit import PitchAuditor
from app.config import get_settings
from app.graph.nodes import store
from app.models.pitch import Pitch, Slide, SlideBullet
from app.models.policy import ComparisonMatrix
from app.pitch.evidence_pack import EvidencePack
from app.pitch.rewrite import RewriteRefused, rewrite_bullet
from app.rag.retriever import get_retriever
from app.services import runs
from app.services.llm import LLMError, LLMQuotaExceeded, LLMUnavailable, get_llm
from app.utils.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api")
DOWNLOAD_KINDS = {"pitch_pptx", "audit_md", "audit_json"}


def _policy_ids(requested: list[str] | None) -> list[str]:
    known = [p.policy_id for p in store().list_policies()]
    if not known:
        raise HTTPException(503, "Policies are not ingested. Run `python scripts/ingest_policies.py --extract`.")
    if not requested:
        return known
    bad = [p for p in requested if p not in known]
    if bad:
        raise HTTPException(422, f"Unknown policy ids: {bad}")
    return requested


@router.get("/debug/recommendation")
def debug_recommendation(company: str = Query(min_length=2, max_length=120), priorities: str = Query(default="")):
    """Score every ingested policy for this client. Winner is the highest calculated score."""
    from app.policies.extraction import get_policy_facts
    from app.policy_fit.explain import explain_recommendation

    docs = {d.policy_id: d for d in store().list_policies()}
    if not docs:
        raise HTTPException(503, "Policies are not ingested.")
    prios = [p.strip() for p in priorities.split(",") if p.strip()][:12]
    try:
        results = get_policy_facts(list(docs))
    except Exception as exc:
        raise HTTPException(503, f"Policy evidence is not available: {exc}")
    return explain_recommendation(company, prios, results, docs)


@router.get("/health")
def health():
    settings = get_settings()
    try:
        rs = get_retriever().status()
    except Exception as exc:  # pragma: no cover
        rs = {"ready": False, "error": str(exc)}
    from app.services.bootstrap import bootstrap_status

    llm = get_llm()
    return {
        "status": "ok",
        "llm_configured": llm.available,
        "llm_provider": "gemini",
        "llm_model": llm.model,
        "model_free_tier_known": settings.model_free_tier_known,
        "research_configured": settings.research_available,
        "embedding_provider": settings.embedding_provider,
        "retrieval": rs,
        "bootstrap": bootstrap_status(),
    }


@router.get("/policies")
def list_policies():
    return {"policies": [d.model_dump(mode="json") for d in store().list_policies()]}


@router.post("/policies/upload")
async def upload_policy(request: Request, filename: str = Query(min_length=1, max_length=180)):
    """Store a brochure beside the supplied corpus. It is not added to the four-policy comparison."""
    name = Path(filename).name
    if not name.lower().endswith(".pdf"):
        raise HTTPException(422, "Only a PDF brochure can be stored. This file was not added.")
    body = await request.body()
    if len(body) < 8 or not body.startswith(b"%PDF-"):
        raise HTTPException(422, "That file is not a readable PDF. Nothing was added to the comparison.")
    if len(body) > 15 * 1024 * 1024:
        raise HTTPException(422, "That PDF is larger than 15 MB. Nothing was added to the comparison.")
    folder = get_settings().storage_path / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    path = folder / f"{stem}.pdf"
    path.write_bytes(body)
    (folder / f"{stem}.json").write_text(json.dumps({"original_name": name, "in_comparison": False}), encoding="utf-8")
    return {
        "stored": True,
        "status": "Uploaded",
        "selected": False,
        "in_comparison": False,
        "original_name": name,
        "message": "Uploaded. Not ingested and not selected. This file is not part of the four-policy comparison.",
    }


@router.get("/policies/uploads")
def list_uploads():
    folder = get_settings().storage_path / "uploads"
    rows = []
    if folder.exists():
        for path in sorted(folder.glob("*.json")):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                meta = {"original_name": path.stem, "failed": True}
            rows.append({
                "original_name": meta.get("original_name") or path.stem,
                "status": "Failed" if meta.get("failed") else "Uploaded",
                "selected": False,
                "in_comparison": False,
                "message": "Not ingested and not selected. The supplied four-policy comparison is unchanged.",
            })
    corpus = []
    for doc in store().list_policies():
        corpus.append({
            "policy_id": doc.policy_id,
            "policy_name": doc.policy_name,
            "status": "Ready",
            "in_comparison": True,
        })
    return {"uploads": rows, "corpus": corpus}


@router.get("/policies/{policy_id}/document")
def policy_document(policy_id: str):
    """The insurer brochure PDF, inline, so a deck's source links can open it at the cited page (#page=N)."""
    doc = next((d for d in store().list_policies() if d.policy_id == policy_id), None)
    if not doc:
        raise HTTPException(404, "Unknown policy id")
    settings = get_settings()
    p = Path(doc.document_path)
    p = (p if p.is_absolute() else settings.policies_path.parent.parent / p).resolve()
    if settings.policies_path.resolve() not in p.parents or not p.exists():
        raise HTTPException(404, "Brochure file not available")
    return FileResponse(str(p), media_type="application/pdf", filename=p.name, content_disposition_type="inline")


@router.post("/client/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    pids = _policy_ids(req.selected_policy_ids)
    try:
        run_id = runs.start_run(req, pids)
    except runs.TooManyRuns as exc:
        raise HTTPException(429, str(exc))
    return AnalyzeResponse(run_id=run_id, status="running")


@router.get("/runs")
def list_runs(limit: int = Query(default=20, ge=1, le=100)):
    return {"runs": store().list_runs(limit)}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    try:
        return runs.get_state(run_id)
    except runs.RunNotFound:
        raise HTTPException(404, "Run not found")


@router.delete("/runs/{run_id}")
def delete_run(run_id: str):
    """Remove a pitch from the list. A run that is still generating is left in place."""
    try:
        runs.delete_run(run_id)
    except runs.RunNotFound:
        raise HTTPException(404, "Run not found")
    except runs.RunStateError as exc:
        raise HTTPException(409, str(exc))
    return {"run_id": run_id, "deleted": True}


@router.post("/runs/{run_id}/retry")
def retry_run(run_id: str):
    """Resume a failed run (e.g. after a Gemini free-tier quota window resets). Same model, no substitution."""
    try:
        runs.retry_run(run_id)
    except runs.RunNotFound:
        raise HTTPException(404, "Run not found")
    except runs.RunStateError as exc:
        raise HTTPException(409, str(exc))
    return {"run_id": run_id, "status": "running"}


@router.get("/runs/{run_id}/events")
def run_events(run_id: str):
    if not store().get_run(run_id):
        raise HTTPException(404, "Run not found")
    return {"events": store().list_events(run_id)}


@router.get("/runs/{run_id}/artifacts/{kind}")
def run_artifact(run_id: str, kind: str):
    art = store().get_artifact(run_id, kind)
    if art is None:
        raise HTTPException(404, f"Artifact '{kind}' not available")
    return art


def _pitch_from_slides(base: Pitch, slides) -> Pitch:
    new = []
    for i, s in enumerate(slides, start=1):
        bullets = [SlideBullet.model_validate(b) for b in s.bullets]
        new.append(Slide(slide_number=i, title=s.title, subtitle=s.subtitle, bullets=bullets, footnote=s.footnote, layout=s.layout))
    p = base.model_copy(deep=True)
    p.slides = new
    p.version += 1
    return p


@router.post("/runs/{run_id}/audit-preview")
def audit_preview(run_id: str, req: AuditPreviewRequest):
    """Audit edited slides without saving them, so the advisor sees the gate before committing an edit."""
    st = store()
    pitch_d = st.get_artifact(run_id, "pitch")
    matrix_d = st.get_artifact(run_id, "matrix")
    pack_d = st.get_artifact(run_id, "evidence_pack")
    if not pitch_d or not matrix_d:
        raise HTTPException(404, "Run has no pitch to audit yet")
    pitch = _pitch_from_slides(Pitch.model_validate(pitch_d), req.slides)
    pack = EvidencePack.model_validate(pack_d) if pack_d else None
    auditor = PitchAuditor(store=st, matrix=ComparisonMatrix.model_validate(matrix_d))
    report = auditor.audit(pitch, allowed_features=[i.feature_key for i in pack.items] if pack else None, major_gaps=pack.gaps if pack else None)
    return {"audit": report.model_dump(mode="json"), "pitch_version": pitch.version}


@router.post("/runs/{run_id}/rewrite")
def rewrite(run_id: str, req: RewriteRequest):
    """Rewrite one bullet as instructed (the deck's ⌘K). Nothing is saved: the advisor accepts or rejects the proposal, then saves and re-audits."""
    st = store()
    pitch_d = st.get_artifact(run_id, "pitch")
    if not pitch_d:
        raise HTTPException(404, "Run has no pitch to edit yet")
    pack_d = st.get_artifact(run_id, "evidence_pack")
    bullet = SlideBullet(text=req.text, source_chunk_ids=req.source_chunk_ids, source_urls=req.source_urls, kind=req.kind)
    try:
        new, note = rewrite_bullet(Pitch.model_validate(pitch_d), EvidencePack.model_validate(pack_d) if pack_d else None, req.slide_number, bullet, req.instruction)
    except RewriteRefused as exc:
        raise HTTPException(422, str(exc))
    except LLMUnavailable as exc:
        raise HTTPException(503, f"AI edit needs GEMINI_API_KEY: {exc}")
    except LLMQuotaExceeded as exc:
        raise HTTPException(429, f"Gemini free-tier limit reached; retry later. {exc}")
    except LLMError as exc:
        # Transient Gemini failures (503 "high demand" after retries, bad JSON) are the user's to retry, not a server fault.
        raise HTTPException(502, f"Gemini could not complete the edit; retry in a moment. {exc}")
    audit = _audit_proposed_bullet(st, run_id, Pitch.model_validate(pitch_d), new, req.slide_number, req.text)
    return {"bullet": new.model_dump(), "note": note, "audit": audit}


@router.post("/runs/{run_id}/scenario")
def run_scenario(run_id: str, req: ScenarioRequest):
    """Coverage Scenario Analysis. Does not change the recommendation."""
    from app.advisory.scenario import analyse_scenario, books_from_values
    from app.services.runs import RunNotFound, get_state

    try:
        state = get_state(run_id)
    except RunNotFound:
        raise HTTPException(404, "Run not found")
    values = state.get("values") or {}
    docs = {d.policy_id: d.model_dump(mode="json") for d in store().list_policies()}
    policy_ids = list(values.get("policy_ids") or docs)
    return analyse_scenario(req.text, policy_ids, books_from_values(values), docs)


@router.post("/runs/{run_id}/pitch-studio")
def pitch_studio(run_id: str, req: PitchStudioRequest):
    """Propose a structured slide change. Facts stay locked. Nothing is saved until the advisor accepts it through review."""
    from app.advisory.studio import propose_transformation

    st = store()
    pitch_d = st.get_artifact(run_id, "pitch")
    matrix_d = st.get_artifact(run_id, "matrix")
    pack_d = st.get_artifact(run_id, "evidence_pack")
    if not pitch_d or not matrix_d:
        raise HTTPException(404, "The pitch is not ready for the studio yet.")
    pitch = Pitch.model_validate(pitch_d)
    slide = next((item for item in pitch.slides if item.slide_number == req.slide_number), None)
    if slide is None:
        raise HTTPException(404, "That slide is not in the deck.")
    names = [doc.policy_name for doc in st.list_policies()]
    proposal = propose_transformation(slide, req.instruction, pitch, policy_names=names)
    if not proposal.get("ok"):
        raise HTTPException(422, proposal.get("message") or "The proposal was refused.")
    proposed_slide = Slide.model_validate(proposal["slide"])
    patched = pitch.model_copy(deep=True)
    patched.slides = [proposed_slide if item.slide_number == req.slide_number else item for item in patched.slides]
    pack = EvidencePack.model_validate(pack_d) if pack_d else None
    auditor = PitchAuditor(store=st, matrix=ComparisonMatrix.model_validate(matrix_d))
    report = auditor.audit(patched, allowed_features=[i.feature_key for i in pack.items] if pack else None, major_gaps=pack.gaps if pack else None)
    proposal["audit"] = {"gate": report.summary.gate, "supported": report.summary.supported, "contradicted": report.summary.contradicted, "not_found": report.summary.not_found}
    proposal["audit_blocks_accept"] = report.summary.gate == "FAIL"
    if isinstance(proposal.get("locks"), dict):
        proposal["locks"]["AUDIT_LOCK"] = report.summary.gate
    proposal["acceptable"] = report.summary.gate != "FAIL"
    return proposal


@router.post("/runs/{run_id}/recommendation-change")
def recommendation_change(run_id: str, req: RecommendationChangeRequest):
    """Rerun the fit engine. A named policy is applied only when the engine supports it, or as a named override."""
    from app.advisory.change import consider_recommendation_change
    from app.advisory.scenario import books_from_values
    from app.models.fit import ClientRequirement
    from app.services.runs import RunNotFound, RunStateError, get_state, save_recalculation

    try:
        state = get_state(run_id)
    except RunNotFound:
        raise HTTPException(404, "Run not found")
    values = state.get("values") or {}
    docs = {d.policy_id: d for d in store().list_policies()}
    policy_ids = list(values.get("policy_ids") or docs)
    requirements = [ClientRequirement.model_validate(item) for item in (values.get("requirements") or [])]
    current = (values.get("recommendation") or {}).get("recommended_policy_id") or ""
    try:
        result = consider_recommendation_change(
            policy_ids,
            requirements,
            books_from_values(values),
            docs,
            req.instruction,
            current_policy_id=current,
            override=req.override,
            reviewer=req.reviewer,
        )
    except Exception as exc:
        raise HTTPException(422, f"The recommendation was not changed. {exc}")
    if not result.get("ok"):
        raise HTTPException(422, result.get("message") or "The recommendation was not changed.")
    ready = bool(result.get("applied"))
    if req.apply:
        try:
            result = save_recalculation(run_id, result)
        except RunStateError as exc:
            raise HTTPException(409, str(exc))
    else:
        result = {**result, "applied": False, "ready_to_apply": ready}
    return result


@router.get("/runs/{run_id}/evidence")
def run_evidence(run_id: str, policy_id: str = Query(min_length=1), feature: str = Query(min_length=1)):
    """Canonical quote for one comparison cell. Absence is reported; it is not turned into coverage."""
    from app.api.advisor_view import lookup_evidence
    from app.services.runs import RunNotFound, get_state

    try:
        state = get_state(run_id)
    except RunNotFound:
        raise HTTPException(404, "Run not found")
    found = lookup_evidence(state.get("values") or {}, policy_id, feature)
    if found is None:
        raise HTTPException(404, "No evidence cell for that policy and feature. That is not the same as an exclusion.")
    doc = next((d for d in store().list_policies() if d.policy_id == policy_id), None)
    if doc:
        found["insurer"] = found.get("insurer") or doc.insurer
        found["product"] = found.get("product") or doc.policy_name
        found["source_document"] = found.get("source_document") or doc.file_name
    return found


def _audit_proposed_bullet(st, run_id: str, pitch: Pitch, bullet: SlideBullet, slide_number: int, original: str) -> dict:
    """Audit one proposed bullet before the advisor can accept it. A failed audit does not apply the edit."""
    matrix_d = st.get_artifact(run_id, "matrix")
    pack_d = st.get_artifact(run_id, "evidence_pack")
    if not matrix_d:
        return {"status": "UNAVAILABLE", "detail": "The proposed wording could not be audited, so it was not accepted."}
    patched = pitch.model_copy(deep=True)
    index = None
    for slide in patched.slides:
        if slide.slide_number != slide_number:
            continue
        for i, current in enumerate(slide.bullets):
            if current.text == original:
                slide.bullets[i] = bullet
                index = i
                break
    if index is None:
        return {"status": "UNAVAILABLE", "detail": "The proposed wording could not be matched to a bullet, so it was not accepted."}
    pack = EvidencePack.model_validate(pack_d) if pack_d else None
    auditor = PitchAuditor(store=st, matrix=ComparisonMatrix.model_validate(matrix_d))
    try:
        report = auditor.audit(patched, allowed_features=[i.feature_key for i in pack.items] if pack else None, major_gaps=pack.gaps if pack else None)
    except Exception as exc:
        log.warning("rewrite audit failed: %s", exc)
        return {"status": "UNAVAILABLE", "detail": "The proposed wording could not be audited, so it was not accepted."}
    match = next((item for item in report.claims if item.claim.slide == slide_number and item.claim.bullet_index == index), None)
    if match is None:
        return {"status": "NOT_APPLICABLE", "detail": "This wording is not a policy claim."}
    return {"status": match.status.value, "detail": match.correction_hint or (match.checks[0].detail if match.checks else "")}


@router.post("/runs/{run_id}/answer")
def answer(run_id: str, req: AnswerRequest):
    """Answer the question the run is paused on (context, close call, or review) and resume it."""
    payload = req.model_dump(exclude_none=True)
    if req.action == "edit":
        if not req.slides:
            raise HTTPException(422, "Edit requires slides")
        payload["slides"] = [s.model_dump() for s in req.slides]
    try:
        runs.answer_run(run_id, payload)
    except runs.RunNotFound:
        raise HTTPException(404, "Run not found")
    except runs.RunStateError as exc:
        raise HTTPException(409, str(exc))
    return {"run_id": run_id, "action": req.action, "status": "running"}


@router.get("/downloads/{run_id}/{kind}")
def download(run_id: str, kind: str):
    if kind not in DOWNLOAD_KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(DOWNLOAD_KINDS)}")
    run = store().get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    path = (run.get("outputs") or {}).get(kind)
    if not path or not Path(path).exists():
        raise HTTPException(404, "Output not generated yet (approve the pitch first)")
    p = Path(path).resolve()
    if get_settings().outputs_path.resolve() not in p.parents:
        raise HTTPException(403, "Invalid path")
    return FileResponse(str(p), filename=p.name)
