"""Recommendation changes go through the fit engine. Prompt text cannot set the winner."""
from __future__ import annotations

from typing import Any

from app.models.fit import ClientRequirement, CoverageExpectation, RequirementClass
from app.models.policy import PolicyDocument, PolicyExtractionResult
from app.policies.features import FEATURE_LABELS, map_text_to_features
from app.policy_fit.criteria import criterion_type_for
from app.policy_fit.requirements import assign_weights
from app.policy_fit.scoring import recommend, score_policies

VAGUE = "Your request did not specify a measurable requirement change."


def consider_recommendation_change(
    policy_ids: list[str],
    requirements: list[ClientRequirement],
    results: dict[str, PolicyExtractionResult],
    docs: dict[str, PolicyDocument],
    instruction: str,
    *,
    current_policy_id: str = "",
    override: bool = False,
    reviewer: str | None = None,
) -> dict[str, Any]:
    """Interpret a requirement change, then rescore every policy. The model does not pick the winner."""
    text = " ".join((instruction or "").split())
    if len(text) < 12:
        return {"ok": False, "applied": False, "message": VAGUE}
    ops = _interpret(text)
    updated, notes = _apply(requirements, ops) if ops else (None, None)
    if updated is None or not notes:
        return {"ok": False, "applied": False, "message": VAGUE, "interpreted_change": []}
    old_rows = assign_weights([item.model_copy(deep=True) for item in requirements])
    old_fits = score_policies(policy_ids, old_rows, results, []) if old_rows else []
    old_rec = recommend(old_fits, [], docs) if old_fits else None
    fits = score_policies(policy_ids, updated, results, [])
    provisional = recommend(fits, [], docs)
    requested = _match_policy(text, docs, policy_ids)
    scores = _scores(fits, docs)
    history = {
        "previous_policy_id": current_policy_id,
        "reason": text,
        "evidence_change": [feature for _op, features in (ops or []) for feature in features],
        "recalculated_policy_id": provisional.recommended_policy_id,
        "decision_state": provisional.decision_state,
        "advisor_override": None,
    }
    payload = {
        "interpreted_change": notes,
        "old_weights": _weights(old_rows),
        "new_weights": _weights(updated),
        "old_scores": _scores(old_fits, docs),
        "scores": scores,
        "old_recommendation": None if old_rec is None else old_rec.model_dump(mode="json"),
        "recommendation": provisional.model_dump(mode="json"),
        "requirements": [item.model_dump(mode="json") for item in updated],
        "fits": [fit.model_dump(mode="json") for fit in fits],
        "alternatives": [item.model_dump(mode="json") for item in provisional.alternatives],
        "decision_state": provisional.decision_state,
    }
    if requested and provisional.recommended_policy_id != requested:
        gaps = next((fit.must_have_gaps + fit.unresolved_must_haves + fit.comparison_incomplete for fit in fits if fit.policy_id == requested), [])
        if override and (reviewer or "").strip() and requested in {fit.policy_id for fit in fits}:
            forced = recommend(fits, [], docs, advisor_choice=requested)
            history["advisor_override"] = requested
            history["recalculated_policy_id"] = forced.recommended_policy_id
            history["decision_state"] = forced.decision_state
            return {
                **payload,
                "ok": True,
                "applied": True,
                "override": True,
                "supported": False,
                "message": "The fit engine does not support that policy on the current evidence. Recorded as an advisor override, not as a calculated winner.",
                "recommendation": forced.model_dump(mode="json"),
                "gaps": gaps,
                "history": history,
            }
        return {
            **payload,
            "ok": True,
            "applied": False,
            "override": False,
            "supported": False,
            "message": "The recalculated fit does not support that policy. It was not switched. An explicit advisor override can be recorded with your name.",
            "gaps": gaps,
        }
    if provisional.decision_state not in {"eligible", "advisor_override"} or not provisional.recommended_policy_id:
        return {
            **payload,
            "ok": True,
            "applied": False,
            "override": False,
            "supported": False,
            "message": "The recalculation does not meet the decision rules, so no policy was selected.",
            "unchanged": provisional.recommended_policy_id == current_policy_id,
        }
    same = provisional.recommended_policy_id == current_policy_id
    history["recalculated_policy_id"] = provisional.recommended_policy_id
    return {
        **payload,
        "ok": True,
        "applied": True,
        "override": False,
        "supported": True,
        "unchanged": same,
        "message": "Interpreted change: " + " ".join(notes) + (" The same policy remains the calculated fit." if same else " The calculated policy changed. Regenerate the pitch before approval."),
        "history": history,
    }


def _interpret(text: str) -> list[tuple[str, list[str]]] | None:
    lowered = text.lower()
    features = map_text_to_features(lowered, limit=6)
    if not features:
        return None
    decrease = any(phrase in lowered for phrase in ("reduce", "less important", "depriorit", "not just", "lower the"))
    increase = any(phrase in lowered for phrase in ("focus more", "prioritize", "prioritise", "more weight", "increase"))
    add = any(phrase in lowered for phrase in ("add ", "wants", "want ", "include", "need ", "client priority", "cover"))
    ops: list[tuple[str, list[str]]] = []
    if decrease:
        ops.append(("decrease", features))
    elif increase:
        ops.append(("increase", features))
    elif add:
        ops.append(("add", features))
    return ops or None


def _apply(requirements: list[ClientRequirement], ops: list[tuple[str, list[str]]]) -> tuple[list[ClientRequirement] | None, list[str] | None]:
    by_feature = {item.feature: item.model_copy(deep=True) for item in requirements}
    notes: list[str] = []
    for op, features in ops:
        for feature in features:
            label = FEATURE_LABELS.get(feature, feature.replace("_", " "))
            current = by_feature.get(feature)
            if op == "decrease":
                if current is None or current.requirement_class not in {RequirementClass.PREFERENCE, RequirementClass.MUST_HAVE}:
                    continue
                current.priority_weight = max(float(current.priority_weight) * 0.25, 0.05)
                notes.append(f"Reduce relative importance of {label}.")
            elif op == "increase":
                if current is None or current.requirement_class == RequirementClass.BASELINE:
                    by_feature[feature] = _preference(feature, 2.0)
                    notes.append(f"Add {label} as a client priority.")
                else:
                    current.priority_weight = float(current.priority_weight) * 2
                    if current.requirement_class == RequirementClass.EXPOSURE:
                        current.requirement_class = RequirementClass.PREFERENCE
                    notes.append(f"Increase priority of {label}.")
            elif current is None or current.requirement_class in {RequirementClass.BASELINE, RequirementClass.EXPOSURE}:
                by_feature[feature] = _preference(feature, 1.0)
                notes.append(f"Add {label} as a client priority.")
            else:
                notes.append(f"{label} remains a client priority.")
    if not notes:
        return None, None
    return assign_weights(list(by_feature.values())), notes


def _preference(feature: str, weight: float) -> ClientRequirement:
    return ClientRequirement(
        requirement_id=feature,
        description=FEATURE_LABELS.get(feature, feature.replace("_", " ")),
        feature=feature,
        type=criterion_type_for(feature),
        priority_weight=weight,
        requirement_class=RequirementClass.PREFERENCE,
        source="advisor-change",
        client_asked=True,
        accept_add_on=True,
        coverage_expectation=CoverageExpectation.EITHER,
    )


def _weights(requirements: list[ClientRequirement]) -> list[dict[str, Any]]:
    return [
        {"feature": item.feature, "requirement_class": item.requirement_class.value, "weight": round(item.weight, 4), "priority_weight": item.priority_weight}
        for item in requirements
        if item.weight > 0
    ]


def _scores(fits, docs: dict[str, PolicyDocument]) -> list[dict[str, Any]]:
    return [
        {
            "policy_id": fit.policy_id,
            "policy_name": docs[fit.policy_id].policy_name if fit.policy_id in docs else fit.policy_id,
            "fit_score": fit.score,
            "evidence_completeness": fit.evidence_completeness,
            "decision_state": fit.decision_state,
            "decision_sufficient": fit.decision_sufficient,
            "contributions": [
                {"feature": row.feature, "weight": row.weight, "criterion_score": row.criterion_score, "contribution": row.contribution, "status": row.status}
                for row in fit.contributions
            ],
        }
        for fit in fits
    ]


def _match_policy(text: str, docs: dict[str, PolicyDocument], policy_ids: list[str]) -> str | None:
    lowered = text.lower()
    hits = [pid for pid in policy_ids if pid in docs and docs[pid].policy_name.lower() in lowered]
    if len(hits) == 1:
        return hits[0]
    return None
