"""Run manager: starts graph runs in background threads, resumes after advisor answers, reads state."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from langgraph.types import Command

from app.config import get_settings
from app.graph.builder import get_graph
from app.graph.nodes import RESUMING, store
from app.models.client import ClientIntake
from app.services.llm import quota_error_from
from app.utils.ids import new_id
from app.utils.logging import get_logger

log = get_logger(__name__)
_threads: dict[str, threading.Thread] = {}
# Serialises "is this run already executing? -> start its thread" so two concurrent requests (a double
# click on Approve, two tabs) can never resume the same graph twice or exceed the run ceiling.
_lock = threading.Lock()


class RunNotFound(LookupError):
    pass


class RunStateError(RuntimeError):
    pass


class TooManyRuns(RuntimeError):
    pass


def _launch(run_id: str, payload: Any, *, name: str, new_run: bool = False, prepare: Callable[[], None] | None = None) -> None:
    """Register and start the worker thread for `run_id` under the lock; raises if it is already live.

    `prepare` runs after the checks pass and before the thread starts (status/event bookkeeping), so a
    request that loses the race leaves no trace.
    """
    with _lock:
        if _is_executing(run_id):
            raise RunStateError("Run is already executing")
        if new_run:
            live = sum(1 for t in _threads.values() if t.is_alive())
            limit = get_settings().max_concurrent_runs
            if live >= limit:
                raise TooManyRuns(f"{live} analyses are already running (limit {limit}); wait for one to finish and try again")
        if prepare is not None:
            prepare()
        t = threading.Thread(target=_execute, args=(run_id, payload), daemon=True, name=f"{name}-{run_id}")
        _threads[run_id] = t
        t.start()


def _config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 60}


def pending_question(snap: Any) -> dict[str, Any] | None:
    """The interrupt payload the graph is paused on, if any (the question the advisor must answer)."""
    for task in getattr(snap, "tasks", []) or []:
        for intr in getattr(task, "interrupts", []) or []:
            value = getattr(intr, "value", None)
            if isinstance(value, dict):
                return value
    return None


def _execute(run_id: str, payload: Any) -> None:
    graph = get_graph()
    st = store()
    try:
        graph.invoke(payload, _config(run_id))
        # If the graph paused on a question, ask() has already set awaiting_review.
        snap = graph.get_state(_config(run_id))
        if pending_question(snap) is not None:
            return
        vals = snap.values or {}
        if vals.get("status") in {"approved", "rejected"}:
            return
        run = st.get_run(run_id) or {}
        if run.get("status") == "running":
            st.save_run(run_id, run.get("company_name", "?"), "completed", {k: v for k, v in run.items() if k not in {"run_id", "company_name", "status", "created_at", "updated_at"}})
    except Exception as exc:
        run = st.get_run(run_id) or {}
        data = {k: v for k, v in run.items() if k not in {"run_id", "company_name", "status", "created_at", "updated_at"}}
        quota = quota_error_from(exc)
        if quota is not None:
            # Free-tier quota: a clear, typed error the UI can turn into "retry later". No model switch.
            log.warning("Run %s paused by Gemini quota: %s", run_id, quota)
            data["error"] = str(quota)[:800]
            data["error_kind"] = "quota"
            data["retry_after"] = quota.retry_after
            data["retryable"] = True
        else:
            log.exception("Run %s failed", run_id)
            data["error"] = str(exc)[:800]
            data["error_kind"] = "error"
            data["retry_after"] = None
            data["retryable"] = True  # LangGraph checkpoints let us resume from the failed node
        st.save_run(run_id, run.get("company_name", "?"), "failed", data)
        st.add_event(run_id, "run", "failed", str(exc)[:500])
    finally:
        _threads.pop(run_id, None)


def start_run(intake: ClientIntake, policy_ids: list[str], background: bool = True) -> str:
    run_id = new_id("run")
    st = store()

    def prepare() -> None:
        st.save_run(run_id, intake.company_name, "running", {"current_node": None, "policy_ids": policy_ids})
        st.add_event(run_id, "run", "started", f"Analysis started for {intake.company_name}")

    initial = {"run_id": run_id, "intake": intake.model_dump(mode="json"), "policy_ids": policy_ids, "status": "running", "warnings": [], "audit_history": []}
    if background:
        _launch(run_id, initial, name="run", new_run=True, prepare=prepare)  # no record is written if refused
    else:
        prepare()
        _execute(run_id, initial)
    return run_id


def answer_run(run_id: str, answer: dict[str, Any], background: bool = True) -> None:
    """Resume a paused run with the advisor's answer; `action` must be one of the question's options."""
    graph = get_graph()
    snap = graph.get_state(_config(run_id))
    if not snap or not snap.values:
        raise RunNotFound(run_id)
    question = pending_question(snap)
    if question is None:
        raise RunStateError("Run is not waiting on a question")
    options = {o["id"] for o in question.get("options", [])}
    if not options and "human_review" in (snap.next or ()):
        options = {"approve", "edit", "regenerate", "reject"}  # checkpoint from before questions carried options
    if answer.get("action") not in options:
        raise RunStateError(f"Answer must be one of: {', '.join(sorted(options))}")
    if answer.get("action") == "approve":
        from app.api.advisor_view import approval_allowed

        gate = (((snap.values or {}).get("audit") or {}).get("summary") or {}).get("gate")
        allowed, reason = approval_allowed(gate, answer.get("reviewer"))
        if not allowed:
            raise RunStateError(reason)
        if gate == "FAIL":
            answer = {**answer, "advisor_override": True}
    st = store()

    def prepare() -> None:
        st.add_event(run_id, list(snap.next)[0] if snap.next else "question", "answer", answer["action"])
        run = st.get_run(run_id) or {}
        st.save_run(run_id, run.get("company_name", "?"), "running", {k: v for k, v in run.items() if k not in {"run_id", "company_name", "status", "created_at", "updated_at"}})
        RESUMING.add(run_id)

    payload = Command(resume=answer)
    if background:
        _launch(run_id, payload, name="resume", prepare=prepare)
    else:
        if _is_executing(run_id):
            raise RunStateError("Run is already executing")
        prepare()
        _execute(run_id, payload)


def _is_executing(run_id: str) -> bool:
    t = _threads.get(run_id)
    return bool(t and t.is_alive())


INTERRUPTED_MESSAGE = "The server restarted while this run was executing. Nothing was lost: completed steps are checkpointed and Retry resumes from the step that was interrupted."


def reconcile_orphaned_runs() -> list[str]:
    """Mark runs left in `running` by a previous process as failed/interrupted so they can be retried.

    Called at API startup. Without this, a run interrupted by a restart or deploy would show
    "Generating" forever and could never be retried (retry requires a failed status).
    """
    st = store()
    fixed: list[str] = []
    for run_id in st.run_ids_with_status("running"):
        if _is_executing(run_id):
            continue
        run = st.get_run(run_id) or {}
        data = {k: v for k, v in run.items() if k not in {"run_id", "company_name", "status", "created_at", "updated_at"}}
        data["error"] = INTERRUPTED_MESSAGE
        data["error_kind"] = "interrupted"
        data["retry_after"] = None
        data["retryable"] = True
        st.save_run(run_id, run.get("company_name", "?"), "failed", data)
        st.add_event(run_id, "run", "failed", "Interrupted by a server restart")
        fixed.append(run_id)
    if fixed:
        log.warning("Marked %d orphaned run(s) as interrupted: %s", len(fixed), ", ".join(fixed))
    return fixed


def retry_run(run_id: str, background: bool = True) -> None:
    """Resume a failed run from its last LangGraph checkpoint (the failed node re-executes).

    Used after a Gemini free-tier quota/rate-limit error once the window has reset, or after a
    server restart interrupted the run. The same configured model is used again; nothing is
    substituted.
    """
    st = store()
    run = st.get_run(run_id)
    if not run:
        raise RunNotFound(run_id)
    if _is_executing(run_id):
        raise RunStateError("Run is already executing")
    # A run that says `running` but has no live thread was orphaned (e.g. restart before reconcile ran).
    if run.get("status") not in {"failed", "running"}:
        raise RunStateError("Only failed or interrupted runs can be retried")
    snap = get_graph().get_state(_config(run_id))
    if not snap or not snap.values:
        raise RunStateError("No checkpoint to resume from; start a new analysis")
    data = {k: v for k, v in run.items() if k not in {"run_id", "company_name", "status", "created_at", "updated_at"}}
    for k in ("error", "error_kind", "retry_after"):
        data.pop(k, None)
    data["retries"] = int(data.get("retries") or 0) + 1

    def prepare() -> None:
        st.save_run(run_id, run.get("company_name", "?"), "running", data)
        st.add_event(run_id, "run", "retried", f"Retry {data['retries']} after failure")

    payload = None  # invoking with no input resumes pending tasks from the checkpoint
    if background:
        _launch(run_id, payload, name="retry", prepare=prepare)
    else:
        prepare()
        _execute(run_id, payload)


def get_state(run_id: str) -> dict[str, Any]:
    st = store()
    run = st.get_run(run_id)
    if not run:
        raise RunNotFound(run_id)
    try:
        snap = get_graph().get_state(_config(run_id))
        values = dict(snap.values or {})
        pending = list(snap.next or [])
        question = pending_question(snap)
    except Exception as exc:  # pragma: no cover
        log.warning("get_state failed for %s: %s", run_id, exc)
        values, pending, question = {}, [], None
    from app.api.advisor_view import build_advisor_view

    docs = {d.policy_id: d.model_dump(mode="json") for d in st.list_policies()}
    try:
        advisor = build_advisor_view(values, docs)
    except Exception as exc:  # pragma: no cover
        log.warning("advisor view failed for %s: %s", run_id, exc)
        advisor = {"error": "The advisor summary could not be prepared. Underlying results were not replaced."}
    return {"run": run, "values": values, "pending": pending, "question": question, "events": st.list_events(run_id), "advisor": advisor}
