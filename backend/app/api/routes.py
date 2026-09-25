"""FastAPI routes."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.schemas import AnalyzeRequest, AnalyzeResponse, AnswerRequest, AuditPreviewRequest, RewriteRequest
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
    return {"bullet": new.model_dump(), "note": note}


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
