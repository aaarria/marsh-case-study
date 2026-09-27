"""Advisory Pitch Studio. Layout and wording proposals stay inside the slide model.

Facts, numbers, evidence ids, and the recommendation are locked. The model does not edit the PPTX.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.models.pitch import Pitch, Slide
from app.policies.features import map_claim_to_features

INTENTS = (
    "simplify",
    "executive_summary",
    "comparison",
    "process_flow",
    "timeline",
    "two_column",
    "key_takeaways",
    "risk_to_coverage_mapping",
)

_FORBIDDEN = (
    ("invent", "A pitch edit cannot invent a benefit."),
    ("add a benefit", "A pitch edit cannot add a benefit."),
    ("new benefit", "A pitch edit cannot add a benefit."),
    ("change the number", "Numbers are locked. They are not changed in the pitch studio."),
    ("change the waiting", "Waiting periods are locked."),
    ("remove the exclusion", "An exclusion cannot be removed by a wording edit."),
    ("delete the evidence", "Evidence references are locked."),
    ("drop the citation", "Evidence references are locked."),
    ("remove the citation", "Evidence references are locked."),
    ("change the recommendation", "A recommendation change has to rerun the fit engine. It is not a slide edit."),
    ("switch the recommendation", "A recommendation change has to rerun the fit engine. It is not a slide edit."),
)

_INTENT_WORDS = (
    ("process", "process_flow"),
    ("3-step", "process_flow"),
    ("three-step", "process_flow"),
    ("timeline", "timeline"),
    ("two column", "two_column"),
    ("two-column", "two_column"),
    ("comparison", "comparison"),
    ("easier to scan", "comparison"),
    ("executive", "executive_summary"),
    ("senior hr", "executive_summary"),
    ("takeaway", "key_takeaways"),
    ("hierarchy", "key_takeaways"),
    ("risk", "risk_to_coverage_mapping"),
    ("diagram", "process_flow"),
    ("simplify", "simplify"),
    ("less text", "simplify"),
    ("reduce", "simplify"),
)

_NUMBERS = re.compile(r"\d[\d,]*(?:\.\d+)?")
_EXCLUSION = ("not covered", "excluded", "exclusion", "does not cover", "isn't covered", "is not covered")
LOCKS = ("FACT_LOCK", "NUMBER_LOCK", "POLICY_LOCK", "RECOMMENDATION_LOCK", "EVIDENCE_LOCK", "AUDIT_LOCK")
_FACT_MESSAGE = "That change would alter a verified policy fact, so it was not applied."

_WORDING_SYSTEM = (
    "You rewrite presentation language for a Marsh client advisor. "
    "You may change wording, sentence length, tone, headings, and how existing points are grouped. "
    "You must keep every number, limit, waiting period, exclusion, policy name, coverage state, and recommendation exactly as written. "
    "Do not add a benefit, a limit, a waiting period, or a policy that is not already in the slide. "
    "Return the same number of bullets, in the same order. Do not return evidence ids or policy ids."
)


class WordingDraft(BaseModel):
    title: str
    subtitle: str | None = None
    bullets: list[str] = Field(default_factory=list)


def propose_transformation(slide: Slide, instruction: str, pitch: Pitch, *, llm=None, policy_names: list[str] | None = None) -> dict:
    """Return a proposed slide or a refusal. Nothing is saved here."""
    text = " ".join((instruction or "").split())
    if len(text) < 8:
        return {"ok": False, "message": "Say how the slide should change. Facts and the recommendation stay locked.", "locks": list(LOCKS)}
    lowered = text.lower()
    for phrase, message in _FORBIDDEN:
        if phrase in lowered:
            return {"ok": False, "message": message, "intent": None, "locks": list(LOCKS)}
    intent = _intent(lowered)
    proposed = _structural(slide, intent)
    names = [name for name in (policy_names or []) if name and len(name) >= 4]
    writer = _writer(llm)
    wording = False
    if writer is not None:
        try:
            draft = writer.structured(_WORDING_SYSTEM, _wording_prompt(slide, text, names), WordingDraft, purpose="pitch_studio", temperature=0.2)
        except Exception:
            return {"ok": False, "intent": intent, "message": "The wording proposal could not be checked, so it was not applied.", "locks": list(LOCKS)}
        applied, problem = _apply_wording(proposed, slide, draft)
        if problem:
            return {"ok": False, "intent": intent, "message": problem, "locks": list(LOCKS)}
        proposed = applied
        wording = True
    problems = lock_problems(slide, proposed, pitch) + _preservation_problems(slide, proposed, pitch, names)
    if problems:
        return {"ok": False, "intent": intent, "message": _FACT_MESSAGE if wording else problems[0], "locks": list(LOCKS)}
    changes = [
        {"index": index, "before": before.text, "after": after.text}
        for index, (before, after) in enumerate(zip(slide.bullets, proposed.bullets))
        if before.text != after.text
    ]
    if slide.title != proposed.title:
        changes.insert(0, {"index": -1, "before": slide.title, "after": proposed.title})
    return {
        "ok": True,
        "intent": intent,
        "wording": wording,
        "message": (
            "Proposed wording change. Locked facts, numbers, evidence, and the recommendation were checked and left unchanged. Audit this before applying."
            if wording
            else "Proposed presentation change. Policy facts, numbers, evidence, and the recommendation are unchanged. Audit this before accepting."
        ),
        "slide": proposed.model_dump(mode="json"),
        "changes": changes,
        "locks": {
            "FACT_LOCK": True,
            "NUMBER_LOCK": True,
            "POLICY_LOCK": True,
            "RECOMMENDATION_LOCK": pitch.recommended_policy_id or "",
            "EVIDENCE_LOCK": [cid for bullet in proposed.bullets for cid in bullet.source_chunk_ids],
            "AUDIT_LOCK": "pending",
        },
    }


def lock_problems(original: Slide, proposed: Slide, pitch: Pitch) -> list[str]:
    """Refuse a proposal that moves a fact, a number, a citation, or the recommended policy."""
    problems: list[str] = []
    if proposed.slide_number != original.slide_number:
        problems.append("The proposal targets a different slide.")
    original_ids = [cid for bullet in original.bullets for cid in bullet.source_chunk_ids]
    proposed_ids = [cid for bullet in proposed.bullets for cid in bullet.source_chunk_ids]
    if original_ids != proposed_ids:
        problems.append("Evidence references are locked and cannot be moved or removed.")
    original_urls = [url for bullet in original.bullets for url in bullet.source_urls]
    proposed_urls = [url for bullet in proposed.bullets for url in bullet.source_urls]
    if original_urls != proposed_urls:
        problems.append("Source links are locked.")
    original_numbers = _numbers(" ".join(bullet.text for bullet in original.bullets))
    proposed_numbers = _numbers(" ".join(bullet.text for bullet in proposed.bullets))
    if not original_numbers <= proposed_numbers:
        problems.append("A locked number is missing from the proposal.")
    if not proposed_numbers <= original_numbers:
        problems.append("The proposal introduces a number that was not on the slide.")
    for before, after in zip(original.bullets, proposed.bullets):
        if before.kind == "policy" and before.policy_id and after.policy_id != before.policy_id:
            problems.append("A policy citation cannot be reassigned.")
        if before.kind == "policy" and _numbers(before.text) and _numbers(before.text) != _numbers(after.text):
            problems.append("Numbers on a policy statement are locked.")
    if pitch.recommended_policy_id and any(bullet.policy_id and bullet.policy_id != pitch.recommended_policy_id and bullet.kind == "recommendation" for bullet in proposed.bullets):
        problems.append("The recommendation cannot be changed in the pitch studio.")
    return problems


def _intent(text: str) -> str:
    for phrase, intent in _INTENT_WORDS:
        if phrase in text:
            return intent
    return "simplify"


def _structural(slide: Slide, intent: str) -> Slide:
    """Change presentation structure only. Bullet wording and citations stay byte-for-byte."""
    proposed = slide.model_copy(deep=True)
    proposed.layout = intent if intent in INTENTS else slide.layout
    note = {
        "simplify": "Simplified for scanning. Facts unchanged.",
        "executive_summary": "Executive summary layout. Facts unchanged.",
        "comparison": "Comparison layout. Facts unchanged.",
        "process_flow": "Process flow. Facts unchanged.",
        "timeline": "Timeline layout. Facts unchanged.",
        "two_column": "Two-column layout. Facts unchanged.",
        "key_takeaways": "Key takeaways. Facts unchanged.",
        "risk_to_coverage_mapping": "Risk-to-coverage layout. Facts unchanged.",
    }[proposed.layout if proposed.layout in INTENTS else "simplify"]
    proposed.subtitle = note
    return proposed


def _writer(llm):
    if llm is not None:
        return llm if getattr(llm, "available", False) else None
    try:
        from app.services.llm import get_llm

        service = get_llm()
    except Exception:
        return None
    return service if service.available else None


def _wording_prompt(slide: Slide, instruction: str, policy_names: list[str]) -> str:
    lines = [
        f"Instruction: {instruction}",
        f"Title: {slide.title}",
        f"Subtitle: {slide.subtitle or ''}",
        "Bullets:",
    ]
    for index, bullet in enumerate(slide.bullets, start=1):
        lines.append(f"{index}. {bullet.text}")
    if policy_names:
        lines.append("Policy names that must stay verbatim if they already appear: " + "; ".join(policy_names))
    lines.append("Rewrite only the language. Keep every number and every policy name that already appears.")
    return "\n".join(lines)


def _apply_wording(base: Slide, original: Slide, draft: WordingDraft) -> tuple[Slide, str | None]:
    if len(draft.bullets) != len(original.bullets):
        return base, "The proposal did not keep every bullet, so it was not applied."
    proposed = base.model_copy(deep=True)
    if draft.title.strip():
        proposed.title = " ".join(draft.title.split())
    if draft.subtitle and draft.subtitle.strip():
        proposed.subtitle = " ".join(draft.subtitle.split())
    for bullet, text, source in zip(proposed.bullets, draft.bullets, original.bullets):
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return base, "The proposal removed a bullet, so it was not applied."
        bullet.text = cleaned
        bullet.source_chunk_ids = list(source.source_chunk_ids)
        bullet.source_urls = list(source.source_urls)
        bullet.policy_id = source.policy_id
        bullet.kind = source.kind
    return proposed, None


def _preservation_problems(original: Slide, proposed: Slide, pitch: Pitch, policy_names: list[str]) -> list[str]:
    problems: list[str] = []
    for before, after in zip(original.bullets, proposed.bullets):
        if before.kind == "recommendation" and before.policy_id == pitch.recommended_policy_id and after.policy_id != pitch.recommended_policy_id:
            problems.append(_FACT_MESSAGE)
        if _numbers(before.text) != _numbers(after.text):
            problems.append(_FACT_MESSAGE)
        if _excluded(before.text) != _excluded(after.text):
            problems.append(_FACT_MESSAGE)
        if "waiting" in before.text.lower() and "wait" not in after.text.lower():
            problems.append(_FACT_MESSAGE)
        for name in policy_names:
            if name.lower() in before.text.lower() and name.lower() not in after.text.lower():
                problems.append(_FACT_MESSAGE)
        before_features = set(map_claim_to_features(before.text, limit=6))
        after_features = set(map_claim_to_features(after.text, limit=6))
        if after_features - before_features:
            problems.append(_FACT_MESSAGE)
    if _numbers(original.title) != _numbers(proposed.title):
        problems.append(_FACT_MESSAGE)
    return problems


def _excluded(text: str) -> bool:
    lowered = text.lower()
    return any(mark in lowered for mark in _EXCLUSION)


def _numbers(text: str) -> set[str]:
    return set(_NUMBERS.findall(text))
