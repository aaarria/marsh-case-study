"""Advisor screen for the existing Marsh API.

This file does not score policies, write the pitch, or audit claims.
It calls the FastAPI service and shows what that service already returns.

Run the API first, then from the repo root:

    backend/.venv/bin/pip install -r advisor_ui/requirements.txt
    backend/.venv/bin/streamlit run advisor_ui/app.py
"""
from __future__ import annotations

import os
import time

import httpx
import streamlit as st

PRIORITIES = [
    "maternity benefits",
    "accident cover",
    "chronic conditions from day one",
    "no room rent capping",
    "global cover for travel",
    "wellness and OPD",
    "cost control / co-pay options",
]

STEPS = {
    "research_company": "Researching company",
    "confirm_context": "Context check",
    "map_exposures": "Mapping exposures",
    "compare_policies": "Comparing policies",
    "policy_fit_arena": "Calculating recommendation",
    "policy_check": "Running Policy Check",
    "confirm_recommendation": "Recommendation check",
    "evidence_pack": "Evidence pack",
    "generate_pitch": "Drafting the pitch",
    "audit_pitch": "Auditing the pitch",
    "human_review": "Waiting for your review",
    "export_outputs": "Exporting the deck",
}


def configured_api() -> str:
    """Local default is localhost. Streamlit Cloud reads API_URL from the app secrets."""
    try:
        secret = st.secrets.get("API_URL")
    except Exception:
        secret = None
    return str(secret or os.environ.get("API_URL") or "http://localhost:8000").rstrip("/")


def api_base() -> str:
    return st.session_state.get("api_url", configured_api()).rstrip("/")


def request(method: str, path: str, **kwargs):
    timeout = kwargs.pop("timeout", 60)
    with httpx.Client(timeout=timeout) as client:
        response = client.request(method, f"{api_base()}{path}", **kwargs)
    if response.status_code >= 400:
        detail = response.text
        try:
            body = response.json()
            detail = body.get("detail", detail)
        except Exception:
            pass
        raise RuntimeError(str(detail))
    if "application/json" in response.headers.get("content-type", ""):
        return response.json()
    return response.content


def load_state(run_id: str) -> dict:
    return request("GET", f"/api/runs/{run_id}")


def latest_step(events: list[dict]) -> str:
    for event in reversed(events or []):
        node = event.get("node") or event.get("kind") or ""
        if node in STEPS:
            return STEPS[node]
    return "Working"


def show_recommendation(advisor: dict):
    rec = advisor.get("recommendation") or {}
    if not rec:
        return
    name = rec.get("policy_name") or "No automatic recommendation"
    score = rec.get("fit_score")
    st.subheader(name)
    if score is not None:
        st.caption(f"Fit {score} / 100. This score stays on the advisor view. It is not written onto the client slides.")
    if rec.get("wording"):
        st.write(rec["wording"])
    scores = rec.get("scores") or []
    if scores:
        st.dataframe(
            [
                {
                    "Policy": row.get("policy_name"),
                    "Fit": row.get("fit_score"),
                    "Completeness": row.get("evidence_completeness"),
                    "Decision": row.get("decision_label"),
                }
                for row in scores
            ],
            hide_index=True,
            use_container_width=True,
        )


def show_comparison(advisor: dict):
    comparison = advisor.get("comparison") or {}
    rows = comparison.get("rows") or []
    if not rows:
        return
    st.subheader("Comparison")
    table = []
    for row in rows:
        item = {"Criterion": row.get("label")}
        for cell in row.get("cells") or []:
            item[cell.get("policy_name") or cell.get("policy_id")] = cell.get("status_label")
        table.append(item)
    st.dataframe(table, hide_index=True, use_container_width=True)


def show_pitch(values: dict):
    pitch = values.get("pitch") or {}
    slides = pitch.get("slides") or []
    if not slides:
        return
    st.subheader("Pitch")
    for slide in slides:
        with st.expander(f"{slide.get('slide_number', '')}. {slide.get('title', 'Slide')}", expanded=slide.get("slide_number") == 1):
            if slide.get("subtitle"):
                st.caption(slide["subtitle"])
            for bullet in slide.get("bullets") or []:
                text = bullet.get("text") if isinstance(bullet, dict) else str(bullet)
                st.markdown(f"- {text}")


def show_audit(values: dict):
    audit = values.get("audit") or {}
    summary = audit.get("summary") or {}
    if not summary:
        return
    gate = summary.get("gate") or "NOT AUDITED"
    confidence = summary.get("confidence_score")
    st.subheader(f"Audit gate: {gate}")
    if confidence is not None:
        st.caption(f"Evidence confidence {confidence:.0%}. Share of material policy claims that are fully supported.")
    cols = st.columns(4)
    cols[0].metric("Supported", summary.get("supported", 0))
    cols[1].metric("Contradicted", summary.get("contradicted", 0))
    cols[2].metric("Not found", summary.get("not_found", 0))
    cols[3].metric("Not a policy claim", summary.get("not_applicable", 0))
    flagged = [
        claim
        for claim in audit.get("claims") or []
        if (claim.get("status") in {"CONTRADICTED", "NOT_FOUND", "PARTIALLY_SUPPORTED", "UNCERTAIN"})
        and ((claim.get("claim") or {}).get("claim_type") == "POLICY")
    ]
    if not flagged:
        return
    st.markdown("**Claims for review**")
    for claim in flagged:
        body = claim.get("claim") or {}
        passport = claim.get("passport") or {}
        where = f"Slide {body.get('slide')}: {body.get('claim_text', '')}"
        source = ""
        if passport.get("policy_name"):
            source = f"{passport.get('policy_name')}, p. {passport.get('page')}, {passport.get('section') or ''}"
        st.warning(f"{claim.get('status')} · {claim.get('action')}\n\n{where}\n\n{source}\n\n{claim.get('correction_hint') or ''}")


def answer_form(run_id: str, question: dict):
    st.divider()
    st.subheader("The run is waiting for you")
    st.write(question.get("message") or "Choose how to continue.")
    options = question.get("options") or []
    kind = question.get("question")
    with st.form("answer"):
        labels = {opt["label"]: opt["id"] for opt in options}
        choice = st.radio("Action", list(labels), index=0)
        payload: dict = {"action": labels[choice]}
        if kind == "context" and labels[choice] == "add_context":
            payload["industry"] = st.text_input("Industry")
            payload["geography"] = st.text_input("Geography")
            count = st.number_input("Employee count", min_value=0, value=0, step=1)
            if count:
                payload["employee_count"] = int(count)
            payload["advisor_notes"] = st.text_area("Notes")
        if kind == "review":
            if labels[choice] == "approve" and question.get("audit_gate") == "FAIL":
                payload["reviewer"] = st.text_input("Your name, required to export over a failed audit")
            if labels[choice] == "regenerate":
                payload["feedback"] = st.text_area("What should the next draft change?")
        submitted = st.form_submit_button("Send")
    if submitted:
        request("POST", f"/api/runs/{run_id}/answer", json=payload)
        st.session_state["poll"] = True
        st.rerun()


def show_run(run_id: str):
    state = load_state(run_id)
    run = state.get("run") or {}
    status = run.get("status") or "running"
    advisor = state.get("advisor") or {}
    values = state.get("values") or {}
    st.caption(f"{run.get('company_name', '')} · {status} · {latest_step(state.get('events') or [])}")
    if run.get("error"):
        st.error(run["error"])
    show_recommendation(advisor)
    show_comparison(advisor)
    check = advisor.get("policy_check") or {}
    if check.get("note"):
        st.info(check["note"])
    show_pitch(values)
    show_audit(values)
    question = state.get("question")
    if question:
        answer_form(run_id, question)
    if status in {"approved", "completed"}:
        for kind, label in (("pitch_pptx", "Download PowerPoint"), ("audit_md", "Download audit")):
            try:
                blob = request("GET", f"/api/downloads/{run_id}/{kind}", timeout=30)
            except Exception:
                continue
            st.download_button(label, data=blob, file_name=f"{run_id}_{kind}")
    if status == "running" and st.session_state.get("poll"):
        time.sleep(4)
        st.rerun()


def main():
    st.set_page_config(page_title="Marsh Health Policy Advisory", layout="wide")
    st.title("Marsh Health Policy Advisory")
    st.caption("Evidence-led health insurance comparison. This screen uses the existing API.")

    with st.sidebar:
        st.session_state["api_url"] = st.text_input("API", configured_api())
        try:
            health = request("GET", "/api/health", timeout=20)
            st.success("API ready" if health.get("status") == "ok" else "API responded")
            st.caption(f"{health.get('llm_model') or 'no model'} · research {'on' if health.get('research_configured') else 'off'}")
        except Exception as exc:
            st.error(f"API unreachable. {exc}")
            health = None
        if st.button("Refresh runs"):
            st.session_state.pop("runs", None)

    if "run_id" not in st.session_state:
        st.session_state["run_id"] = None

    try:
        policies = request("GET", "/api/policies").get("policies") or []
    except Exception as exc:
        st.error(str(exc))
        return

    company = st.text_input("Company name", placeholder="Ola Electric")
    chosen = st.multiselect("Client priorities", PRIORITIES)
    names = {item["policy_name"]: item["policy_id"] for item in policies}
    picked = st.multiselect("Brochures", list(names), default=list(names))
    if st.button("Start pitch", type="primary", disabled=len(company.strip()) < 2 or len(picked) < 2):
        body = {
            "company_name": company.strip(),
            "client_priorities": chosen,
            "selected_policy_ids": None if len(picked) == len(names) else [names[name] for name in picked],
        }
        started = request("POST", "/api/client/analyze", json=body, timeout=30)
        st.session_state["run_id"] = started["run_id"]
        st.session_state["poll"] = True
        st.rerun()

    if st.session_state.get("run_id"):
        try:
            show_run(st.session_state["run_id"])
        except Exception as exc:
            st.error(str(exc))
            if st.button("Resume from last checkpoint"):
                try:
                    request("POST", f"/api/runs/{st.session_state['run_id']}/retry")
                    st.session_state["poll"] = True
                    st.rerun()
                except Exception as retry_exc:
                    st.error(str(retry_exc))


if __name__ == "__main__":
    main()
