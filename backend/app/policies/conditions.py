"""Condition text and materiality for one canonical FeatureFact.

Materiality comes from the wording's effect on the benefit, not from a model confidence score.
The same condition text is kept once. Unlabeled boilerplate is INFO and does not change fit.
"""
from __future__ import annotations

import re

from app.models.policy import ConditionMateriality, CoverageStatus, FactCondition, FeatureFact, PolicyExtractionResult
from app.policies.evidence_contract import fold_span
from app.policies.features import FEATURES

_FEATURE_WORDS = {spec.key: tuple(word.lower() for word in spec.keywords) for spec in FEATURES}

_CRITICAL = ("not covered", "is excluded", "are excluded", "does not cover", "shall not be payable", "not payable")
_MATERIAL = (
    "waiting period", "co-pay", "co-payment", "copay", "sub-limit", "sublimit", "deductible",
    "available only", "only available", "only if", "network hospital", "minimum ", "maximum ",
    "subject to a limit", "capped at",
)
_MINOR = ("subject to", "provided that", "as per policy", "terms and conditions")


def canonicalize_results(results: dict[str, PolicyExtractionResult]) -> dict[str, PolicyExtractionResult]:
    """One validated fact book. Callers downstream must use this copy, not a second reading."""
    return {
        pid: result.model_copy(update={"facts": {key: canonicalize_fact(fact) for key, fact in result.facts.items()}})
        for pid, result in results.items()
    }


def canonicalize_fact(fact: FeatureFact) -> FeatureFact:
    fact = fact.model_copy(deep=True)
    details = _merge_conditions(fact)
    fact.condition_details = details
    texts = list(fact.conditions)
    for item in details:
        if item.text not in texts:
            texts.append(item.text)
    fact.conditions = texts
    if _policy_mismatch(fact) or _status_contradicts_quote(fact):
        fact.coverage_status = CoverageStatus.REVIEW_REQUIRED
        fact.notes = ((fact.notes or "") + " Conflicting or mismatched evidence is REVIEW_REQUIRED, not a scored fact.").strip()
    fact.coverage_tier = _coverage_tier(fact)
    fact.cap_type = _cap_type(fact)
    return fact


def _coverage_tier(fact: FeatureFact) -> str:
    text = " ".join(part for part in (fact.value, fact.original_quote, fact.variant_scope, *(src.section or "" for src in fact.sources)) if part).lower()
    if fact.coverage_status == CoverageStatus.REVIEW_REQUIRED:
        return "REVIEW_REQUIRED"
    if fact.coverage_status == CoverageStatus.EXCLUDED:
        return "EXCLUDED"
    if fact.coverage_status in {CoverageStatus.NOT_FOUND, CoverageStatus.UNKNOWN}:
        return "NOT_ESTABLISHED"
    if "rider" in text:
        return "RIDER"
    if fact.is_add_on or fact.coverage_status == CoverageStatus.ADD_ON:
        if "optional" in text:
            return "OPTIONAL"
        return "ADD_ON"
    if fact.coverage_status == CoverageStatus.CONDITIONAL or fact.variant_scope:
        return "CONDITIONAL"
    if fact.coverage_status == CoverageStatus.PARTIALLY_COVERED:
        return "CONDITIONAL"
    return "BASE"


def _cap_type(fact: FeatureFact) -> str | None:
    if fact.feature != "room_rent":
        return None
    text = " ".join(part for part in (fact.limit, fact.value, fact.original_quote) if part).lower()
    if fact.coverage_status in {CoverageStatus.NOT_FOUND, CoverageStatus.UNKNOWN, CoverageStatus.REVIEW_REQUIRED}:
        return "NOT_ESTABLISHED"
    if "at actual" in text:
        return "ACTUALS"
    if any(token in text for token in ("up to si", "up to sum insured", "up to the sum insured", "base sum insured")):
        return "SUM_INSURED"
    if any(token in text for token in ("no sub-limit", "no sublimit", "no capping", "unlimited")):
        return "NONE"
    return None


def extract_condition_details(fact: FeatureFact) -> list[FactCondition]:
    return _merge_conditions(fact)


def _merge_conditions(fact: FeatureFact) -> list[FactCondition]:
    found: dict[str, FactCondition] = {}
    for item in fact.condition_details:
        _keep(found, item.text, item.materiality)
    for raw in fact.conditions:
        text, materiality = _split_prefix(raw)
        if materiality is None:
            materiality = _materiality(text)
        _keep(found, text, materiality)
    blob = " ".join(part for part in (fact.value, fact.original_quote, fact.limit, fact.waiting_period, fact.copay, fact.deductible) if part)
    for sentence in _sentences(blob):
        if not _is_restriction(sentence):
            continue
        _keep(found, sentence, _materiality(sentence))
    return list(found.values())


def _keep(found: dict[str, FactCondition], text: str, materiality: ConditionMateriality) -> None:
    key = fold_span(text)
    if len(key) < 8:
        return
    current = found.get(key)
    rank = {ConditionMateriality.INFO: 0, ConditionMateriality.MINOR: 1, ConditionMateriality.MATERIAL: 2, ConditionMateriality.CRITICAL: 3}
    if current is None or rank[materiality] > rank[current.materiality]:
        found[key] = FactCondition(text=" ".join(text.split()), materiality=materiality)


def _split_prefix(raw: str) -> tuple[str, ConditionMateriality | None]:
    text = raw.strip()
    for name in ConditionMateriality:
        prefix = name.value + ":"
        if text.upper().startswith(prefix):
            return text[len(prefix):].strip(), name
    return text, None


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;])\s+", text)
    return [part.strip() for part in parts if part.strip()]


def _is_restriction(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in _CRITICAL + _MATERIAL + _MINOR)


def _materiality(text: str) -> ConditionMateriality:
    lowered = text.lower()
    if any(token in lowered for token in _CRITICAL):
        return ConditionMateriality.CRITICAL
    if any(token in lowered for token in ("zero waiting", "no waiting period", "day 1 cover")):
        return ConditionMateriality.INFO
    if any(token in lowered for token in _MATERIAL):
        return ConditionMateriality.MATERIAL
    if any(token in lowered for token in _MINOR):
        return ConditionMateriality.MINOR
    return ConditionMateriality.INFO


def _policy_mismatch(fact: FeatureFact) -> bool:
    if not fact.sources or not fact.policy_id:
        return False
    return all(src.policy_id != fact.policy_id for src in fact.sources)


def _status_contradicts_quote(fact: FeatureFact) -> bool:
    if fact.coverage_status not in {CoverageStatus.COVERED, CoverageStatus.PARTIALLY_COVERED, CoverageStatus.CONDITIONAL}:
        return False
    blob = " ".join(part for part in (fact.value, fact.original_quote, *(src.source_text for src in fact.sources[:1])) if part).lower()
    words = _FEATURE_WORDS.get(fact.feature, ())
    if not words or not blob:
        return False
    mentions_feature = any(word in blob for word in words)
    contradicts = any(token in blob for token in ("not covered", "is excluded", "are excluded", "does not cover"))
    return mentions_feature and contradicts
