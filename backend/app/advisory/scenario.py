"""Coverage Scenario Analysis. Evidence support only. It does not choose a policy."""
from __future__ import annotations

from typing import Any

from app.advisory.states import coverage_label, coverage_state
from app.models.fit import ClientRequirement, CoverageExpectation, RequirementClass
from app.models.policy import FeatureFact, PolicyExtractionResult
from app.policies.features import FEATURE_LABELS, map_text_to_features
from app.policy_fit.criteria import compare_fact, criterion_type_for


def books_from_values(values: dict[str, Any]) -> dict[str, PolicyExtractionResult]:
    """Canonical facts from the checkpoint. Matrix cells are the fallback for older runs."""
    raw = values.get("policy_facts") or {}
    if raw:
        return {pid: PolicyExtractionResult.model_validate(item) for pid, item in raw.items()}
    matrix = values.get("matrix") or {}
    grouped: dict[str, dict] = {pid: {} for pid in matrix.get("policy_ids") or []}
    for feature, by_policy in (matrix.get("cells") or {}).items():
        for pid, cell in (by_policy or {}).items():
            fact = (cell or {}).get("fact")
            if fact:
                grouped.setdefault(pid, {})[feature] = FeatureFact.model_validate(fact)
    return {pid: PolicyExtractionResult(policy_id=pid, facts=facts, generated_at="checkpoint") for pid, facts in grouped.items()}


def analyse_scenario(text: str, policy_ids: list[str], results: dict[str, PolicyExtractionResult], docs: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one advisor scenario against every policy in scope. Does not set a winner."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) < 12:
        return {
            "ok": False,
            "code": "AMBIGUOUS",
            "message": "Describe the client situation in a sentence. A short or empty note is not evaluated.",
            "features": [],
            "rows": [],
            "changes_recommendation": False,
        }
    features = map_text_to_features(cleaned, limit=3)
    if not features:
        return {
            "ok": False,
            "code": "AMBIGUOUS",
            "message": "This note does not map to a benefit in the brochures. No policy was judged, and the recommendation is unchanged.",
            "features": [],
            "rows": [],
            "changes_recommendation": False,
        }
    rows = []
    for feature in features:
        requirement = ClientRequirement(
            requirement_id=f"scenario-{feature}",
            description=FEATURE_LABELS.get(feature, feature),
            feature=feature,
            type=criterion_type_for(feature),
            priority_weight=1,
            requirement_class=RequirementClass.PREFERENCE,
            source="scenario",
            client_asked=True,
            accept_add_on=True,
            coverage_expectation=CoverageExpectation.EITHER,
        )
        cells = []
        for pid in policy_ids:
            book = results.get(pid)
            fact = book.facts.get(feature) if book else None
            cells.append(_cell(pid, feature, fact, requirement, docs))
        rows.append({"feature": feature, "label": FEATURE_LABELS.get(feature, feature), "cells": cells})
    return {
        "ok": True,
        "code": "EVALUATED",
        "message": "Scenario results are evidence for the advisor. They do not change the recommendation and they do not promise a claim payment.",
        "scenario": cleaned,
        "features": features,
        "rows": rows,
        "changes_recommendation": False,
    }


def _cell(policy_id: str, feature: str, fact: FeatureFact | None, requirement: ClientRequirement, docs: dict[str, Any]) -> dict[str, Any]:
    judgement = compare_fact(requirement, fact)
    status = fact.coverage_status.value if fact else "NOT_FOUND"
    state = coverage_state(status)
    src = next((s for s in (fact.sources if fact else []) if (s.source_text or "").strip()), None)
    quote = (src.source_text if src else None) or (fact.original_quote if fact else None)
    quote = quote.strip() if isinstance(quote, str) and quote.strip() else None
    doc = docs.get(policy_id)
    name = doc.get("policy_name") if isinstance(doc, dict) else getattr(doc, "policy_name", policy_id)
    conditions = []
    if fact:
        for item in fact.condition_details:
            if item.text and item.text not in conditions:
                conditions.append(item.text)
        for text in fact.conditions:
            if text and text not in conditions:
                conditions.append(text)
    return {
        "policy_id": policy_id,
        "policy_name": name or policy_id,
        "feature": feature,
        "state": state,
        "label": coverage_label(state),
        "explanation": judgement.note,
        "quote": quote,
        "page": (src.page if src else None) or (fact.source_page if fact else None),
        "section": src.section if src else None,
        "chunk_id": (src.chunk_id if src else None) or (fact.source_chunk_id if fact else None),
        "conditions": conditions[:4],
    }
