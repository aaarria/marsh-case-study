"""Recommendation changes go through the Phase 2 engine. Prompt text cannot set the winner."""
from __future__ import annotations

from typing import Any

from app.models.fit import ClientRequirement, CoverageExpectation, Recommendation, RequirementClass
from app.models.policy import PolicyDocument, PolicyExtractionResult
from app.policies.features import FEATURE_LABELS, map_text_to_features
from app.policy_fit.criteria import criterion_type_for
from app.policy_fit.requirements import assign_weights
from app.policy_fit.scoring import recommend, score_policies


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
    """Recalculate fit from the instruction's benefits. Apply a named policy only as an explicit override."""
    text = " ".join((instruction or "").split())
    if len(text) < 12:
        return {"ok": False, "applied": False, "message": "Say which client priority should be recalculated."}
    features = map_text_to_features(text, limit=4)
    if not features:
        return {
            "ok": False,
            "applied": False,
            "message": "That request does not name a brochure benefit, so the fit engine was not rerun and the recommendation is unchanged.",
        }
    updated = _merge(requirements, features)
    fits = score_policies(policy_ids, assign_weights(updated), results, [])
    provisional = recommend(fits, [], docs)
    requested = _match_policy(text, docs, policy_ids)
    scores = [{"policy_id": fit.policy_id, "policy_name": docs[fit.policy_id].policy_name if fit.policy_id in docs else fit.policy_id, "fit_score": fit.score, "decision_state": fit.decision_state} for fit in fits]
    history = {
        "previous_policy_id": current_policy_id,
        "reason": text,
        "evidence_change": features,
        "recalculated_policy_id": provisional.recommended_policy_id,
        "decision_state": provisional.decision_state,
        "advisor_override": None,
    }
    if requested and provisional.recommended_policy_id != requested:
        gaps = next((fit.must_have_gaps + fit.unresolved_must_haves + fit.comparison_incomplete for fit in fits if fit.policy_id == requested), [])
        if override and (reviewer or "").strip() and requested in {fit.policy_id for fit in fits}:
            forced = recommend(fits, [], docs, advisor_choice=requested)
            history["advisor_override"] = requested
            history["recalculated_policy_id"] = forced.recommended_policy_id
            history["decision_state"] = forced.decision_state
            return {
                "ok": True,
                "applied": True,
                "override": True,
                "supported": False,
                "message": "The fit engine does not support that policy on the current evidence. Recorded as an advisor override, not as a calculated winner.",
                "recommendation": forced.model_dump(mode="json"),
                "requirements": [item.model_dump(mode="json") for item in updated],
                "fits": [fit.model_dump(mode="json") for fit in fits],
                "scores": scores,
                "gaps": gaps,
                "history": history,
            }
        return {
            "ok": True,
            "applied": False,
            "override": False,
            "supported": False,
            "message": "The recalculated fit does not support that policy. It was not switched. An explicit advisor override can be recorded with your name.",
            "recommendation": provisional.model_dump(mode="json"),
            "scores": scores,
            "gaps": gaps,
            "decision_state": provisional.decision_state,
        }
    if provisional.decision_state not in {"eligible", "advisor_override"} or not provisional.recommended_policy_id:
        return {
            "ok": True,
            "applied": False,
            "override": False,
            "supported": False,
            "message": "The recalculation does not meet the decision rules, so the recommendation is unchanged.",
            "recommendation": provisional.model_dump(mode="json"),
            "scores": scores,
            "decision_state": provisional.decision_state,
            "unchanged": provisional.recommended_policy_id == current_policy_id,
        }
    history["recalculated_policy_id"] = provisional.recommended_policy_id
    return {
        "ok": True,
        "applied": True,
        "override": False,
        "supported": True,
        "unchanged": provisional.recommended_policy_id == current_policy_id,
        "message": "The fit engine was rerun on the updated requirements. Regenerate the pitch before approval." if provisional.recommended_policy_id != current_policy_id else "The recalculation keeps the same policy.",
        "recommendation": provisional.model_dump(mode="json"),
        "requirements": [item.model_dump(mode="json") for item in updated],
        "fits": [fit.model_dump(mode="json") for fit in fits],
        "scores": scores,
        "history": history,
    }


def _merge(requirements: list[ClientRequirement], features: list[str]) -> list[ClientRequirement]:
    by_feature = {item.feature: item.model_copy(deep=True) for item in requirements}
    for feature in features:
        current = by_feature.get(feature)
        if current and current.requirement_class == RequirementClass.MUST_HAVE:
            current.client_asked = True
            continue
        by_feature[feature] = ClientRequirement(
            requirement_id=feature,
            description=FEATURE_LABELS.get(feature, feature.replace("_", " ")),
            feature=feature,
            type=criterion_type_for(feature),
            priority_weight=2.0,
            requirement_class=RequirementClass.PREFERENCE,
            source="advisor-change",
            client_asked=True,
            accept_add_on=True,
            coverage_expectation=CoverageExpectation.EITHER,
        )
    return list(by_feature.values())


def _match_policy(text: str, docs: dict[str, PolicyDocument], policy_ids: list[str]) -> str | None:
    lowered = text.lower()
    hits = [pid for pid in policy_ids if pid in docs and docs[pid].policy_name.lower() in lowered]
    if len(hits) == 1:
        return hits[0]
    return None
