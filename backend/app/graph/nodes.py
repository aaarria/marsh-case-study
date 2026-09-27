"""Graph nodes. Deterministic where possible; LLM only inside the agents they call.

Each node emits progress events to SQLite so the UI can render progress before the run completes.
"""
from __future__ import annotations

import functools
import json
import time
from typing import Any, Callable

from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt

from app.auditing.audit import PitchAuditor, review_feedback
from app.config import get_settings
from app.exposure.mapping import features_for_run, map_exposures
from app.graph.state import AdvisoryState
from app.models.client import ClientIntake, CompanyProfile, Exposure, FactKind
from app.models.fit import PolicyFitResult, PolicyGap, Recommendation, ScenarioOutcome
from app.models.pitch import AuditReport, Pitch, Slide, SlideBullet
from app.models.policy import ComparisonMatrix, PolicyDocument, PolicyExtractionResult, SourceRef
from app.pitch.evidence_pack import EvidencePack, build_evidence_pack
from app.pitch.generator import generate_pitch
from app.pitch.pptx_builder import audit_markdown, build_pitch_deck
from app.policies.comparison import build_matrix
from app.policies.conditions import canonicalize_results
from app.policies.extraction import get_policy_facts
from app.policy_fit.arena import run_arena
from app.policy_fit.gaps import analyse_gaps
from app.policy_fit.scenarios import build_scenarios
from app.policy_fit.requirements import build_requirements
from app.policy_fit.scoring import recommend, score_policies
from app.policy_fit.scoring_config import DEFAULT_SCORING
from app.policy_fit.stress import default_retrieve, run_policy_check, unavailable_check
from app.rag.metadata_store import SQLiteMetadataStore
from app.research.company_research import research_company
from app.services.llm import get_llm
from app.research.porter import analyse_market, limited_context
from app.utils.logging import get_logger

log = get_logger(__name__)
_store: SQLiteMetadataStore | None = None
# Runs currently re-entering a node to consume an answer. LangGraph re-executes the whole node on
# resume, so without this the "started"/"waiting" events (and the awaiting status) would repeat.
RESUMING: set[str] = set()


def store() -> SQLiteMetadataStore:
    global _store
    if _store is None:
        _store = SQLiteMetadataStore()
    return _store


def _dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_dump(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _dump(v) for k, v in obj.items()}
    return obj


# State keys the API reads back outside the graph (dashboard thumbnails, edit/preview audits).
_ARTIFACTS = ("pitch", "matrix", "evidence_pack")


def node(name: str) -> Callable:
    """Wrap a node: progress events, timing, artifact persistence, error capture."""

    def deco(fn: Callable[[AdvisoryState], dict]) -> Callable[[AdvisoryState], dict]:
        @functools.wraps(fn)
        def wrapper(state: AdvisoryState) -> dict:
            run_id = state["run_id"]
            st = store()
            if run_id not in RESUMING:
                st.add_event(run_id, name, "started")
                _save_run(state, status="running", current_node=name)
            t0 = time.time()
            try:
                out = fn(state)
            except GraphInterrupt:
                raise  # human-in-the-loop pause, not an error
            except Exception as exc:
                st.add_event(run_id, name, "failed", str(exc)[:500])
                log.exception("Node %s failed", name)
                raise
            dt = time.time() - t0
            st.add_event(run_id, name, "completed", f"{dt:.1f}s")
            for k in _ARTIFACTS:
                if k in out:
                    st.save_artifact(run_id, k, out[k])
            return out

        return wrapper

    return deco


_RUN_BOOKKEEPING = ("policy_ids", "retries")


def _save_run(state: AdvisoryState, status: str, current_node: str | None = None, **extra: Any) -> None:
    st = store()
    prev = st.get_run(state["run_id"]) or {}
    data = {
        **{k: prev[k] for k in _RUN_BOOKKEEPING if k in prev},  # run-level bookkeeping lives outside graph state
        "current_node": current_node,
        "recommended_policy_id": (state.get("recommendation") or {}).get("recommended_policy_id"),
        "audit_gate": ((state.get("audit") or {}).get("summary") or {}).get("gate"),
        "error": state.get("error"),
        "outputs": state.get("outputs", {}),
        **extra,
    }
    st.save_run(state["run_id"], (state.get("intake") or {}).get("company_name", "?"), status, data)


def _docs(policy_ids: list[str]) -> dict[str, PolicyDocument]:
    return {p.policy_id: p for p in store().list_policies() if p.policy_id in policy_ids}


def ask(state: AdvisoryState, name: str, question: dict[str, Any]) -> dict[str, Any]:
    """Pause the graph with a question for the advisor; the payload is what the chat renders.

    Resumes with the advisor's answer, a dict whose `action` is one of the option ids. Questions
    are asked only at genuine decision points; routine steps never pause.
    """
    run_id = state["run_id"]
    if run_id in RESUMING:
        RESUMING.discard(run_id)  # the answer is already there; interrupt() returns it without pausing
    else:
        _save_run(state, status="awaiting_review", current_node=name)
        store().add_event(run_id, name, "waiting", question["question"])
    answer = interrupt({"run_id": run_id, **question})
    if not isinstance(answer, dict) or answer.get("action") not in {o["id"] for o in question["options"]}:
        raise ValueError(f"Invalid answer to {question['question']} question")
    return answer


def _has_context(intake: ClientIntake) -> bool:
    return bool(intake.industry or intake.geography or intake.employee_count or intake.advisor_notes)


# ---------------- nodes ----------------
@node("research_company")
def research_node(state: AdvisoryState) -> dict:
    intake = ClientIntake.model_validate(state["intake"])
    profile = research_company(intake)
    return {"profile": _dump(profile)}


@node("market_intelligence")
def market_node(state: AdvisoryState) -> dict:
    """Porter context. Failure stays UNKNOWN and does not touch policy scores."""
    profile = CompanyProfile.model_validate(state["profile"])
    try:
        context = analyse_market(profile)
    except Exception as exc:
        context = limited_context(profile, f"Market context failed: {exc}")
    warnings = list(state.get("warnings") or [])
    if context.status != "OK":
        warnings.append(context.note)
    return {"market_context": context.model_dump(mode="json"), "warnings": warnings}


CONTEXT_FIELDS = ("industry", "geography", "employee_count", "advisor_notes", "client_priorities")


@node("confirm_context")
def context_node(state: AdvisoryState) -> dict:
    """Ask for context only when the deck would otherwise rest on assumptions alone."""
    intake = ClientIntake.model_validate(state["intake"])
    profile = CompanyProfile.model_validate(state["profile"])
    web_facts = any(f.kind == FactKind.FACT and f.sources and not f.sources[0].url.startswith("advisor://") for f in profile.facts)
    if _has_context(intake) or web_facts:
        return {"research_again": False}
    answer = ask(
        state,
        "confirm_context",
        {
            "question": "context",
            "message": f"I have nothing verified about {intake.company_name}: web research is off and only the name was given. Every company statement in the deck would be a labelled assumption. Add what you know, or continue with assumptions?",
            "options": [{"id": "add_context", "label": "Add what I know"}, {"id": "continue", "label": "Continue with assumptions"}],
            "fields": list(CONTEXT_FIELDS),
        },
    )
    if answer["action"] == "add_context":
        merged = intake.model_copy(update={k: answer[k] for k in CONTEXT_FIELDS if answer.get(k) not in (None, "", [])})
        return {"intake": merged.model_dump(mode="json"), "research_again": _has_context(merged)}
    return {"research_again": False}


@node("map_exposures")
def exposures_node(state: AdvisoryState) -> dict:
    exposures = map_exposures(CompanyProfile.model_validate(state["profile"]), ClientIntake.model_validate(state["intake"]))
    requirements = build_requirements(exposures)
    return {"exposures": _dump(exposures), "requirements": _dump(requirements), "features": features_for_run(exposures)}


def _canonical_facts(state: AdvisoryState) -> dict:
    if state.get("policy_facts"):
        return {pid: PolicyExtractionResult.model_validate(raw) for pid, raw in state["policy_facts"].items()}
    return canonicalize_results(get_policy_facts(state["policy_ids"]))


@node("policy_intelligence")
def policy_intelligence_node(state: AdvisoryState) -> dict:
    """Load each policy's cached facts independently, then validate them into one FeatureFact book."""
    warnings = list(state.get("warnings") or [])
    books = {}
    for pid in state["policy_ids"]:
        try:
            loaded = get_policy_facts([pid])
        except Exception as exc:
            warnings.append(f"Policy evidence unavailable for {pid}: {exc}. Missing evidence is not fabricated.")
            continue
        if pid not in loaded:
            warnings.append(f"Policy evidence missing for {pid}. Comparison continues with that policy unresolved.")
            continue
        books[pid] = loaded[pid]
    if not books:
        raise RuntimeError("No policy facts available. Run `python scripts/ingest_policies.py --extract` first.")
    canonical = canonicalize_results(books)
    from app.services.llm import get_llm
    from app.policies.verify import verify_with_model

    canonical = verify_with_model(canonical, get_llm())
    return {"policy_facts": {pid: result.model_dump(mode="json") for pid, result in canonical.items()}, "warnings": warnings}


@node("compare_policies")
def compare_node(state: AdvisoryState) -> dict:
    pids = state["policy_ids"]
    results = _canonical_facts(state)
    if not results:
        raise RuntimeError("No policy facts available. Run `python scripts/ingest_policies.py --extract` first.")
    matrix = build_matrix(results, state.get("features"), pids)
    return {"matrix": _dump(matrix)}


@node("policy_fit_arena")
def arena_node(state: AdvisoryState) -> dict:
    """Deterministic recommendation. The model does not set fit or the winner."""
    pids = state["policy_ids"]
    exposures = [Exposure.model_validate(e) for e in state["exposures"]]
    results = _canonical_facts(state)
    requirements = build_requirements(exposures)
    scenarios = build_scenarios(exposures)
    outcomes = run_arena(scenarios, results)
    gaps = analyse_gaps(scenarios, outcomes, exposures)
    fits = score_policies(pids, requirements, results, gaps)
    rec = recommend(fits, gaps, _docs(pids))
    return {
        "scenarios": _dump(scenarios),
        "outcomes": _dump(outcomes),
        "gaps": _dump(gaps),
        "fits": _dump(fits),
        "requirements": _dump(requirements),
        "recommendation": _dump(rec),
        "provisional_recommendation": _dump(rec),
    }


@node("policy_check")
def policy_check_node(state: AdvisoryState) -> dict:
    """Challenge the provisional recommendation. Recalculation, when it happens, is the same scorer."""
    provisional = Recommendation.model_validate(state.get("provisional_recommendation") or state["recommendation"])
    try:
        exposures = [Exposure.model_validate(e) for e in state["exposures"]]
        requirements = build_requirements(exposures)
        results = _canonical_facts(state)
        fits = [PolicyFitResult.model_validate(f) for f in state["fits"]]
        checked, final, new_fits, history = run_policy_check(
            state["policy_ids"],
            requirements,
            results,
            fits,
            provisional,
            _docs(state["policy_ids"]),
            exposures,
            retrieve=default_retrieve,
            llm=get_llm(),
        )
    except Exception as exc:
        checked = unavailable_check(provisional, str(exc))
        final, new_fits, history = provisional, [PolicyFitResult.model_validate(f) for f in state["fits"]], []
    prior = list(state.get("recommendation_history") or [])
    return {
        "policy_check": checked.model_dump(mode="json"),
        "recommendation": _dump(final),
        "fits": _dump(new_fits),
        "recommendation_history": prior + [event.model_dump(mode="json") for event in history],
    }


@node("confirm_recommendation")
def close_call_node(state: AdvisoryState) -> dict:
    """Pause when scores are close, evidence is incomplete, or a must-have failed. Otherwise the score stands."""
    fits = [PolicyFitResult.model_validate(f) for f in state["fits"]]
    best = fits[0]
    unresolved = best.decision_state in {"close_decision", "incomplete_comparison", "incomplete", "not_eligible"}
    if state.get("recommendation_confirmed") or not (unresolved or best.close_call_with):
        return {"recommendation_confirmed": True}
    docs = _docs(state["policy_ids"])
    by_id = {f.policy_id: f for f in fits}
    tied = [best.policy_id, *best.close_call_with] if best.close_call_with else [f.policy_id for f in fits]
    scores = [by_id[pid].score for pid in tied]
    unique_leader = max(scores) - min(scores) > 0
    options = [{"id": pid, "label": docs[pid].policy_name if pid in docs else pid, "score": by_id[pid].score, "confidence": by_id[pid].confidence, "explanation": by_id[pid].explanation[:3]} for pid in tied]
    if unique_leader:
        options.append({"id": "keep", "label": "Let the score decide"})
    if best.decision_state == "not_eligible":
        message = "Every policy fails a must-have requirement. Pick one to pitch anyway, or stop and revise the requirement."
    elif best.decision_state in {"incomplete_comparison", "incomplete"}:
        message = "The brochures do not support an automatic recommendation. Missing evidence is not cover, and an unresolved must-have is not a pass or a fail. Pick a policy only as an explicit override."
    else:
        message = f"{len(tied)} policies score within {DEFAULT_SCORING.close_threshold:g} fit points. Pick one. The score is not broken by policy name or file order."
    answer = ask(
        state,
        "confirm_recommendation",
        {"question": "close_call", "message": message, "options": options, "recommended": best.policy_id if unique_leader else None},
    )
    choice = answer["action"]
    gap_models = [PolicyGap.model_validate(g) for g in state["gaps"]]
    if choice == "keep" and unique_leader:
        leader = max(tied, key=lambda pid: by_id[pid].score)
        rec = recommend(fits, gap_models, docs, advisor_choice=leader)
        rec.decision_state = "eligible"
        rec.rationale[0] = f"Score decides: {rec.policy_name} at {by_id[leader].score}/100."
        reordered = [by_id[leader], *(f for f in fits if f.policy_id != leader)]
        return {"fits": _dump(reordered), "recommendation": _dump(rec), "recommendation_confirmed": True, "recommendation_history": _history(state, best.policy_id, leader, "Advisor accepted the score leader.", rec)}
    if choice not in by_id:
        return {"recommendation_confirmed": True}
    reordered = [by_id[choice], *(f for f in fits if f.policy_id != choice)]
    rec = recommend(reordered, gap_models, docs, advisor_choice=choice)
    other = docs[best.policy_id].policy_name if best.policy_id in docs else best.policy_id
    rec.rationale[0] = f"Advisor's choice ({by_id[choice].score}/100); {other} scored {best.score}/100, within the close-call margin."
    return {"fits": _dump(reordered), "recommendation": _dump(rec), "recommendation_confirmed": True, "recommendation_history": _history(state, best.policy_id, choice, "Advisor override.", rec)}


def _history(state: AdvisoryState, previous: str, chosen: str, reason: str, rec: Recommendation) -> list[dict]:
    prior = list(state.get("recommendation_history") or [])
    prior.append({
        "previous_policy_id": previous,
        "reason": reason,
        "evidence_change": [],
        "recalculated_policy_id": chosen,
        "decision_state": rec.decision_state,
        "advisor_override": chosen,
    })
    return prior


@node("evidence_pack")
def evidence_pack_node(state: AdvisoryState) -> dict:
    pack = build_evidence_pack(
        Recommendation.model_validate(state["recommendation"]),
        ComparisonMatrix.model_validate(state["matrix"]),
        [Exposure.model_validate(e) for e in state["exposures"]],
        [ScenarioOutcome.model_validate(o) for o in state["outcomes"]],
        [PolicyGap.model_validate(g) for g in state["gaps"]],
        [PolicyFitResult.model_validate(f) for f in state["fits"]],
        _docs(state["policy_ids"]),
        CompanyProfile.model_validate(state["profile"]),
    )
    return {"evidence_pack": _dump(pack)}


@node("generate_pitch")
def pitch_node(state: AdvisoryState) -> dict:
    version = (state.get("pitch") or {}).get("version", 0) + 1
    pitch, warnings = generate_pitch(
        CompanyProfile.model_validate(state["profile"]),
        [Exposure.model_validate(e) for e in state["exposures"]],
        Recommendation.model_validate(state["recommendation"]),
        EvidencePack.model_validate(state["evidence_pack"]),
        version=version,
        feedback=state.get("regenerate_feedback"),
    )
    return {"pitch": _dump(pitch), "pitch_warnings": warnings, "regenerate_feedback": None, "pitch_stale": False}


@node("audit_pitch")
def audit_node(state: AdvisoryState) -> dict:
    auditor = PitchAuditor(store=store(), matrix=ComparisonMatrix.model_validate(state["matrix"]))
    pitch = Pitch.model_validate(state["pitch"])
    pack = EvidencePack.model_validate(state["evidence_pack"])
    report = auditor.audit(pitch, allowed_features=[i.feature_key for i in pack.items], major_gaps=pack.gaps)
    history = list(state.get("audit_history", [])) + [{"pitch_version": pitch.version, **report.summary.model_dump()}]
    return {"audit": _dump(report), "audit_history": history}


@node("human_review")
def human_review_node(state: AdvisoryState) -> dict:
    """Pause for the advisor. Resumes with {"action": approve|edit|regenerate|reject, ...}."""
    summary = (state.get("audit") or {}).get("summary") or {}
    decision = ask(
        state,
        "human_review",
        {
            "question": "review",
            "pitch_version": (state.get("pitch") or {}).get("version"),
            "audit_gate": summary.get("gate"),
            "message": "The draft is audited. Approve to export, edit slides on the right, regenerate with feedback, or reject.",
            "options": [{"id": "approve", "label": "Approve & export"}, {"id": "edit", "label": "Save edits & re-audit"}, {"id": "regenerate", "label": "Regenerate"}, {"id": "reject", "label": "Reject"}],
        },
    )
    out: dict[str, Any] = {"review": decision}
    if decision["action"] == "edit":
        pitch = Pitch.model_validate(state["pitch"])
        slides = decision.get("slides") or []
        new_slides: list[Slide] = []
        for i, s in enumerate(slides, start=1):
            bullets = [SlideBullet.model_validate(b) if isinstance(b, dict) else SlideBullet(text=str(b)) for b in s.get("bullets", [])]
            new_slides.append(Slide(slide_number=i, title=s.get("title", f"Slide {i}"), subtitle=s.get("subtitle"), bullets=bullets, footnote=s.get("footnote"), layout=s.get("layout", "bullets")))
        if new_slides:
            pitch.slides = new_slides
        pitch.version += 1
        out.update({"pitch": _dump(pitch)})
    elif decision["action"] == "regenerate":
        fb = decision.get("feedback") or ""
        if state.get("audit"):
            fb = (fb + "\n" + review_feedback(AuditReport.model_validate(state["audit"]))).strip()
        out.update({"regenerate_feedback": fb or None})
    return out


@node("export_outputs")
def export_node(state: AdvisoryState) -> dict:
    from app.api.advisor_view import approval_allowed

    settings = get_settings()
    pitch = Pitch.model_validate(state["pitch"])
    report = AuditReport.model_validate(state["audit"]) if state.get("audit") else None
    gate = report.summary.gate if report else None
    reviewer = ((state.get("review") or {}).get("reviewer") or "").strip()
    allowed, reason = approval_allowed(gate, reviewer)
    if not allowed:
        raise RuntimeError(reason)
    pack = EvidencePack.model_validate(state["evidence_pack"])
    refs: dict[str, SourceRef] = {}
    for it in pack.items:
        for s in it.sources:
            refs[s.chunk_id] = s
    # resolve any chunk ids not in the pack (e.g. added by an advisor edit)
    missing = [cid for s in pitch.slides for b in s.bullets for cid in b.source_chunk_ids if cid not in refs]
    if missing:
        for c in store().get_chunks(missing):
            refs[c.chunk_id] = SourceRef.from_chunk(c)
    safe = "".join(ch if ch.isalnum() else "_" for ch in pitch.company_name)[:40]
    stem = f"{safe}_{state['run_id'][-8:]}"
    outputs: dict[str, str] = {}
    deck = build_pitch_deck(pitch, refs, settings.presentations_path / f"{stem}_pitch_v{pitch.version}.pptx", report)
    outputs["pitch_pptx"] = str(deck)
    if report:
        md_path = settings.audit_reports_path / f"{stem}_audit_v{pitch.version}.md"
        md_path.write_text(audit_markdown(report, pitch), encoding="utf-8")
        outputs["audit_md"] = str(md_path)
        js = settings.audit_reports_path / f"{stem}_audit_v{pitch.version}.json"
        js.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
        outputs["audit_json"] = str(js)
    status = "approved" if (state.get("review") or {}).get("action") == "approve" else state.get("status", "running")
    new_state = {**state, "outputs": outputs, "status": status}
    _save_run(new_state, status=status, current_node="export_outputs")
    return {"outputs": outputs, "status": status}


@node("finalize_rejected")
def reject_node(state: AdvisoryState) -> dict:
    _save_run({**state, "status": "rejected"}, status="rejected", current_node="finalize_rejected")
    return {"status": "rejected"}


# ---------------- routers ----------------
def route_after_context(state: AdvisoryState) -> str:
    return "research_company" if state.get("research_again") else "map_exposures"


def route_after_review(state: AdvisoryState) -> str:
    action = (state.get("review") or {}).get("action")
    return {"approve": "export_outputs", "edit": "audit_pitch", "regenerate": "generate_pitch", "reject": "finalize_rejected"}.get(action, "human_review")
