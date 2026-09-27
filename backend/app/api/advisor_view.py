"""Advisor-facing view of graph state.

This module does not score, rank, or choose a policy. It labels values the
Phase 2 and Phase 3 engines already produced.
"""
from __future__ import annotations

from typing import Any

from app.advisory.states import coverage_label, coverage_state
from app.policies.features import FEATURE_LABELS

_STATUS = {
    "COVERED": "Covered under the supplied brochure",
    "PARTIALLY_COVERED": "Available subject to stated conditions",
    "CONDITIONAL": "Available subject to stated conditions",
    "ADD_ON": "Available as an add-on",
    "EXCLUDED": "Excluded under the supplied brochure",
    "NOT_FOUND": "Not established from supplied brochure",
    "UNKNOWN": "Not established from supplied brochure",
    "REVIEW_REQUIRED": "Review required",
}

_DECISION = {
    "eligible": "ELIGIBLE",
    "close_decision": "CLOSE DECISION",
    "incomplete": "INCOMPLETE",
    "incomplete_comparison": "INCOMPLETE COMPARISON",
    "not_eligible": "INELIGIBLE",
    "advisor_override": "ADVISOR OVERRIDE",
}

_FACT = {"FACT": "VERIFIED", "INFERENCE": "ASSUMPTION", "ASSUMPTION": "ASSUMPTION", "UNKNOWN": "UNKNOWN"}
_FACT_WORDING = {
    "VERIFIED": "Verified from public source",
    "ASSUMPTION": "Working hypothesis. Not used as a verified policy requirement",
    "UNKNOWN": "Not established from available sources",
    "ADVISOR": "Provided by advisor",
}

_GAP = {
    "EXPLICIT_GAP": "Not covered",
    "INSUFFICIENT_EVIDENCE": "Not established from the available evidence",
    "CONDITIONAL_GAP": "Conditional",
    "MUST_HAVE_GAP": "Must-have not met",
    "COMPARISON_INCOMPLETE": "Comparison incomplete",
}

# Rows an advisor can scan. Anything outside this list still appears when the client asked for it.
_ROWS = (
    "in_patient_hospitalisation",
    "room_rent",
    "icu",
    "road_ambulance",
    "pre_post_hospitalisation",
    "waiting_period_initial",
    "waiting_period_specific",
    "waiting_period_ped",
    "copay",
    "deductible_options",
    "exclusions",
    "maternity",
    "personal_accident",
    "non_medical_expenses_cover",
    "restore_recharge",
    "sum_insured_growth_bonus",
    "health_checkup",
    "teleconsultation_opd",
    "opd",
)

_RECOMMENDATION_WORDING = "Recommended based on the configured client requirements and documented policy evidence."


def approval_allowed(gate: str | None, reviewer: str | None) -> tuple[bool, str]:
    """PASS and UNCERTAIN may export. FAIL needs a named advisor override. Anything else stays blocked."""
    if gate == "PASS":
        return True, ""
    if gate == "UNCERTAIN":
        return True, ""
    if gate == "FAIL" and (reviewer or "").strip():
        return True, "Advisor override recorded. Critical claims were not cleared by the audit."
    if gate == "FAIL":
        return False, "Approval needs a passing audit, or your name recorded as an explicit override."
    return False, "Export needs a completed audit."


def lookup_evidence(values: dict[str, Any], policy_id: str, feature: str) -> dict[str, Any] | None:
    """Return the canonical quote already stored on the comparison cell. Does not write a new quote."""
    cell = (((values.get("matrix") or {}).get("cells") or {}).get(feature) or {}).get(policy_id)
    if not cell:
        return None
    fact = cell.get("fact") or {}
    sources = fact.get("sources") or []
    src = next((s for s in sources if (s.get("source_text") or "").strip()), None)
    quote = (src or {}).get("source_text") or fact.get("original_quote") or None
    if isinstance(quote, str):
        quote = quote.strip() or None
    conditions = []
    for item in fact.get("condition_details") or []:
        text = (item.get("text") if isinstance(item, dict) else None) or ""
        if text and text not in conditions:
            conditions.append(text)
    for text in fact.get("conditions") or []:
        if text and text not in conditions:
            conditions.append(text)
    status = str(cell.get("status") or fact.get("coverage_status") or "NOT_FOUND")
    return {
        "policy_id": policy_id,
        "insurer": (src or {}).get("policy_name") or fact.get("insurer_name"),
        "product": fact.get("product_name"),
        "source_document": fact.get("source_document") or (src or {}).get("policy_name"),
        "page": (src or {}).get("page") or fact.get("source_page"),
        "section": (src or {}).get("section") or fact.get("source_section"),
        "quote": quote,
        "feature": feature,
        "feature_label": FEATURE_LABELS.get(feature, feature.replace("_", " ")),
        "status": status,
        "status_label": _STATUS.get(status, "Not established from supplied brochure"),
        "conditions": conditions[:6],
        "chunk_id": (src or {}).get("chunk_id") or fact.get("source_chunk_id"),
    }


def build_advisor_view(values: dict[str, Any] | None, docs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Shape checkpoint values for the advisor. Missing pieces stay missing."""
    values = values or {}
    docs = docs or {}
    return {
        "company": _company(values),
        "recommendation": _recommendation(values, docs),
        "why": _why(values, docs),
        "comparison": _comparison(values, docs),
        "policy_check": _policy_check(values, docs),
        "audit": _audit(values),
    }


def _name(docs: dict[str, Any], policy_id: str | None) -> str | None:
    if not policy_id:
        return None
    doc = docs.get(policy_id) or {}
    if isinstance(doc, dict):
        return doc.get("policy_name") or policy_id
    return getattr(doc, "policy_name", None) or policy_id


def _insurer(docs: dict[str, Any], policy_id: str | None) -> str | None:
    if not policy_id:
        return None
    doc = docs.get(policy_id) or {}
    if isinstance(doc, dict):
        return doc.get("insurer")
    return getattr(doc, "insurer", None)


def _company(values: dict[str, Any]) -> dict[str, Any] | None:
    profile = values.get("profile")
    if not profile:
        return None
    facts = []
    for fact in profile.get("facts") or []:
        kind = str(fact.get("kind") or "UNKNOWN")
        label = _FACT.get(kind, "UNKNOWN")
        sources = fact.get("sources") or []
        url = sources[0].get("url") if sources and isinstance(sources[0], dict) else ""
        if str(url).startswith("advisor://"):
            label_for_words = "ADVISOR"
        else:
            label_for_words = label
        facts.append({
            "text": fact.get("text") or "",
            "field": fact.get("field"),
            "label": label,
            "wording": _FACT_WORDING.get(label_for_words, _FACT_WORDING["UNKNOWN"]),
        })
    exposures = []
    for item in values.get("exposures") or []:
        status = str(item.get("status") or "UNKNOWN")
        label = _FACT.get(status, "UNKNOWN")
        exposures.append({
            "title": item.get("title") or "",
            "description": item.get("description") or "",
            "label": label,
            "wording": _FACT_WORDING.get(label, _FACT_WORDING["UNKNOWN"]),
            "rationale": item.get("reasoning") or "",
        })
    market = values.get("market_context") or {}
    return {
        "company_name": profile.get("company_name"),
        "industry": profile.get("industry"),
        "size": profile.get("size"),
        "footprint": profile.get("geography"),
        "characteristics": list(profile.get("business_characteristics") or [])[:6],
        "research_status": profile.get("research_status"),
        "research_note": profile.get("research_note"),
        "facts": facts,
        "exposures": exposures,
        "market": {
            "status": market.get("status") or "UNKNOWN",
            "note": market.get("note") or "",
            "context": market.get("industry_context") or "",
            "hypotheses": list(market.get("hypotheses") or [])[:4],
        },
    }


def _recommendation(values: dict[str, Any], docs: dict[str, Any]) -> dict[str, Any] | None:
    rec = values.get("recommendation")
    if not rec:
        return None
    pid = rec.get("recommended_policy_id") or ""
    state = rec.get("decision_state") or ("eligible" if pid else "incomplete")
    fits = values.get("fits") or []
    fit = next((row for row in fits if row.get("policy_id") == pid), None)
    automatic = bool(pid) and state in {"eligible", "advisor_override"}
    asked = any(req.get("client_asked") for req in (values.get("requirements") or []))
    history = values.get("recommendation_history") or []
    changed = any(event.get("evidence_change") for event in history)
    if automatic and not asked:
        wording = "No specific client priority was selected. The comparison therefore uses the standard baseline coverage criteria."
    elif automatic:
        wording = _RECOMMENDATION_WORDING
    else:
        wording = "No automatic recommendation. Missing evidence is not cover, and an unresolved must-have is not a pass or a fail."
    return {
        "automatic": automatic,
        "policy_id": pid if automatic else "",
        "policy_name": _name(docs, pid) if automatic else None,
        "fit_score": rec.get("fit_score") if automatic else None,
        "evidence_completeness": None if fit is None else fit.get("evidence_completeness"),
        "confidence": None if fit is None else fit.get("confidence"),
        "decision_state": state,
        "decision_label": _DECISION.get(state, state.replace("_", " ").upper()),
        "wording": wording,
        "baseline_only": not asked,
        "drivers": list(rec.get("rationale") or [])[:4] if automatic else [],
        "gaps": [*list(rec.get("comparison_incomplete") or []), *list(rec.get("unresolved_must_haves") or []), *list(rec.get("caveats") or [])][:6],
        "changed_after_check": bool(changed),
        "scores": [
            {
                "policy_id": row.get("policy_id"),
                "policy_name": _name(docs, row.get("policy_id")),
                "fit_score": row.get("score"),
                "evidence_completeness": row.get("evidence_completeness"),
                "confidence": row.get("confidence"),
                "decision_label": _DECISION.get(row.get("decision_state") or "", row.get("decision_state") or ""),
                "decision_sufficient": row.get("decision_sufficient"),
            }
            for row in fits
        ],
        "requirements": _requirement_breakdown(fits, docs),
        "alternatives": [
            {
                "policy_id": item.get("policy_id"),
                "policy_name": item.get("policy_name") or _name(docs, item.get("policy_id")),
                "fit_score": item.get("fit_score"),
                "evidence_completeness": item.get("evidence_completeness"),
                "decision_state": item.get("decision_state"),
                "strong_matches": list(item.get("strong_matches") or []),
                "trade_offs": list(item.get("trade_offs") or []),
                "evidence": list(item.get("evidence") or []),
            }
            for item in (rec.get("alternatives") or [])
        ],
    }


def _why(values: dict[str, Any], docs: dict[str, Any]) -> list[dict[str, Any]]:
    rec = values.get("recommendation") or {}
    pid = rec.get("recommended_policy_id") or ""
    if not pid:
        return []
    fit = next((row for row in (values.get("fits") or []) if row.get("policy_id") == pid), None)
    if not fit:
        return []
    rows = []
    for item in fit.get("contributions") or []:
        if item.get("requirement_class") == "BASELINE" and not item.get("must_have_gap"):
            continue
        status = str(item.get("status") or "NOT_FOUND")
        rows.append({
            "requirement": item.get("description") or FEATURE_LABELS.get(item.get("feature") or "", item.get("feature")),
            "feature": item.get("feature"),
            "priority": item.get("requirement_class") or "",
            "result": _STATUS.get(status, "Not established"),
            "page": item.get("source_page"),
            "policy_name": _name(docs, pid),
            "impact": item.get("note") or "",
            "contribution": item.get("contribution"),
            "weight": item.get("weight"),
            "chunk_id": item.get("source_chunk_id"),
        })
    rows.sort(key=lambda row: abs(row["contribution"] or 0), reverse=True)
    return rows[:8]


def _comparison(values: dict[str, Any], docs: dict[str, Any]) -> dict[str, Any] | None:
    matrix = values.get("matrix")
    if not matrix:
        return None
    order = list(matrix.get("policy_ids") or [])
    asked = {req.get("feature") for req in (values.get("requirements") or []) if req.get("client_asked") and req.get("feature")}
    present = set(matrix.get("features") or [])
    if asked:
        features = [key for key in _ROWS if key in asked and key in present]
        features += [key for key in asked if key in present and key not in features]
    else:
        features = [key for key in _ROWS if key in present]
    if not features:
        features = list(matrix.get("features") or [])[:8]
    cells = matrix.get("cells") or {}
    rows = []
    for feature in features[:14]:
        by_policy = cells.get(feature) or {}
        row_cells = []
        for pid in order:
            raw = by_policy.get(pid) or {}
            fact = raw.get("fact") or {}
            sources = fact.get("sources") or []
            src = next((item for item in sources if isinstance(item, dict)), {})
            state = coverage_state(str(raw.get("status") or "NOT_FOUND"))
            row_cells.append({
                "policy_id": pid,
                "policy_name": _name(docs, pid),
                "state": state,
                "status_label": coverage_label(state),
                "page": src.get("page") or fact.get("source_page"),
                "section": src.get("section") or fact.get("source_section"),
            })
        rows.append({
            "feature": feature,
            "label": FEATURE_LABELS.get(feature, feature.replace("_", " ")),
            "cells": row_cells,
        })
    return {
        "policies": [{"policy_id": pid, "policy_name": _name(docs, pid), "insurer": _insurer(docs, pid)} for pid in order],
        "rows": rows,
        "baseline": not bool(asked),
    }


def _requirement_breakdown(fits: list[dict[str, Any]], docs: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for fit in fits:
        for row in fit.get("contributions") or []:
            if row.get("requirement_class") not in {"PREFERENCE", "MUST_HAVE"}:
                continue
            feature = row.get("feature") or ""
            if feature not in grouped:
                grouped[feature] = {
                    "feature": feature,
                    "label": row.get("description") or FEATURE_LABELS.get(feature, feature),
                    "concept": FEATURE_LABELS.get(feature, feature),
                    "weight": row.get("weight"),
                    "results": [],
                }
                order.append(feature)
            score = row.get("criterion_score")
            status = str(row.get("status") or "NOT_FOUND")
            grouped[feature]["results"].append({
                "policy_id": fit.get("policy_id"),
                "policy_name": _name(docs, fit.get("policy_id")),
                "criterion_score": score,
                "status": status,
                "label": _STATUS.get(status, status) if score is None else f"{_STATUS.get(status, status)} {score:g}",
            })
    return [grouped[feature] for feature in order]


def _policy_check(values: dict[str, Any], docs: dict[str, Any]) -> dict[str, Any] | None:
    check = values.get("policy_check")
    if not check:
        return None
    status = check.get("status") or "UNAVAILABLE"
    challenges = check.get("challenges") or []
    first = challenges[0] if challenges else None
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in check.get("scenarios") or []:
        key = row.get("requirement_id") or row.get("feature") or row.get("scenario")
        if key not in groups:
            groups[key] = {
                "scenario": row.get("scenario") or key,
                "requirement": FEATURE_LABELS.get(row.get("feature") or "", row.get("feature") or ""),
                "feature": row.get("feature"),
                "outcomes": [],
                "limitation": None,
            }
            order.append(key)
        groups[key]["outcomes"].append({
            "policy_id": row.get("policy_id"),
            "policy_name": _name(docs, row.get("policy_id")),
            "result": _STATUS.get(str(row.get("status") or "NOT_FOUND"), "Not established"),
            "evidence": row.get("evidence"),
            "limitation": row.get("limitation") or row.get("condition"),
        })
        if not groups[key]["limitation"] and (row.get("unresolved") or row.get("limitation")):
            groups[key]["limitation"] = row.get("limitation") or "Not established from the available evidence."
    gaps = []
    for gap in check.get("gaps") or []:
        kind = gap.get("gap_type") or "INSUFFICIENT_EVIDENCE"
        gaps.append({
            "kind": kind,
            "meaning": _GAP.get(kind, "Not established from the available evidence"),
            "feature": FEATURE_LABELS.get(gap.get("feature") or "", gap.get("feature") or ""),
            "policy_name": _name(docs, gap.get("policy_id")),
            "detail": gap.get("detail") or "",
            "evidence": gap.get("evidence"),
        })
    done = status == "COMPLETED"
    if status != "COMPLETED":
        note = check.get("note") or "Policy Check did not finish. The recommendation was not stress-tested."
    elif check.get("recalculated"):
        note = "The recommendation was recalculated because validated brochure evidence was added. The model did not choose a new winner."
    else:
        note = "Policy Check did not change the recommendation."
    return {
        "status": status,
        "stability": check.get("sensitivity") if done else "UNAVAILABLE",
        "challenge_found": bool(first) and done,
        "challenge": None if not first else {
            "requirement": FEATURE_LABELS.get(first.get("feature") or "", first.get("feature") or ""),
            "feature": first.get("feature"),
            "policy_id": first.get("policy_id"),
            "policy_name": _name(docs, first.get("policy_id")),
            "evidence_id": first.get("evidence_id"),
            "explanation": first.get("statement") or "",
            "materiality": first.get("materiality") or "INFO",
        },
        "scenarios": [groups[key] for key in order][:4],
        "gaps": gaps[:8],
        "note": note,
        "checked": {"challenge": done, "scenarios": done, "gaps": done},
    }


def _audit(values: dict[str, Any]) -> dict[str, Any] | None:
    audit = values.get("audit") or {}
    summary = audit.get("summary")
    if not summary:
        return None
    review = (summary.get("partially_supported") or 0) + (summary.get("uncertain") or 0)
    gate = summary.get("gate")
    return {
        "supported": summary.get("supported") or 0,
        "review": review,
        "contradicted": summary.get("contradicted") or 0,
        "not_found": summary.get("not_found") or 0,
        "gate": gate,
        "status_label": "PASS" if gate == "PASS" else "REVIEW REQUIRED" if gate == "UNCERTAIN" else "BLOCKED" if gate == "FAIL" else "NOT AUDITED",
    }
