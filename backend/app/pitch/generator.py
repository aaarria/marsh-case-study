"""Pitch generation: 3-5 slides whose policy statements are restricted to Evidence Pack items.

The LLM writes bullets and cites evidence ids; we map ids -> chunk ids deterministically. Any
policy bullet without valid evidence is dropped before audit (never silently kept). A deterministic
template pitch is produced when no LLM is configured, so the pipeline never fabricates.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.client import CompanyProfile, Exposure
from app.models.fit import Recommendation
from app.models.pitch import Pitch, Slide, SlideBullet
from app.pitch.evidence_pack import EvidencePack
from app.services.llm import LLMQuotaExceeded, LLMService, LLMUnavailable, get_llm
from app.utils.ids import new_id
from app.utils.logging import get_logger

log = get_logger(__name__)

MARSH_POSITIONING = [
    "Marsh advises on programme design, insurer negotiation and employee communication, and supports claims advocacy through the policy year.",
    "Recommendation is evidence-first: every policy statement in this deck is traceable to the insurer brochure page cited in the footnotes.",
]

SYSTEM = """You write concise, client-specific insurance pitch slides for a Marsh Client Advisor.
HARD RULES:
1. Every bullet about the policy (kind=policy) must cite one or more evidence ids from the EVIDENCE PACK, and must say only what that evidence says. Do not add benefits, numbers, limits or waiting periods that are not in the cited evidence. Keep numbers exactly as written.
2. If evidence carries conditions or a variant scope, the bullet must mention them briefly (e.g. "subject to a 36-month waiting period", "on the VIP+ variant").
3. Statements about the company must only use COMPANY FACTS (kind=company, cite their C-ids in evidence_ids so the source pages can be linked) or be clearly framed as inferences (kind=assumption). Never invent company figures.
4. Comparison statements against other insurers must come verbatim in meaning from COMPARISON NOTES (kind=policy, cite the evidence id of the feature if given, else leave evidence empty and use kind=recommendation).
5. Produce exactly 4 slides, in this order: (1) Company overview: industry, size, geography and key risks, using COMPANY FACTS or kind=assumption. (2) Exposure map: up to 3 client exposures (kind=assumption or kind=company) paired with up to 3 brochure benefits (kind=policy, each citing evidence ids). End slide 2 with exactly one kind=recommendation bullet that starts with "Why this policy:" and uses only RATIONALE and COMPARISON NOTES. Do not invent figures. (3) Why Marsh and what to watch: use the MARSH POSITIONING lines verbatim as kind=marsh, then watch-outs and gaps as kind=recommendation, and assumptions as kind=assumption. (4) The one recommended policy: name it, mention the fit score only as a decision-support score, restate the labelled assumptions, and the next step of confirming policy wording.
6. 3-5 bullets per slide, each <= 30 words, plain professional English, no marketing hyperbole, no superlatives like "best" or "guaranteed".
7. Do not mention scores as truth; if you mention the fit score, call it a decision-support score."""


class BulletOut(BaseModel):
    text: str
    kind: Literal["policy", "company", "recommendation", "assumption", "marsh"]
    evidence_ids: list[str] = Field(default_factory=list)


class SlideOut(BaseModel):
    title: str
    subtitle: str | None = None
    bullets: list[BulletOut]


class PitchOut(BaseModel):
    slides: list[SlideOut] = Field(min_length=4, max_length=4)


def _pack_text(pack: EvidencePack) -> str:
    lines = []
    for it in pack.items:
        cond = f" Conditions: {'; '.join(it.conditions)}." if it.conditions else ""
        exc = f" Exclusions: {'; '.join(it.exclusions)}." if it.exclusions else ""
        scope = f" Variant scope: {it.variant_scope}." if it.variant_scope else ""
        lines.append(f"[{it.evidence_id}] ({it.feature_label}, {it.status.value}) {it.statement}{cond}{exc}{scope}")
    return "\n".join(lines)


def _finalise(slides: list[Slide], company: str, pid: str, version: int) -> Pitch:
    for i, s in enumerate(slides, start=1):
        s.slide_number = i
    return Pitch(pitch_id=new_id("pitch"), company_name=company, recommended_policy_id=pid, slides=slides, version=version)


def template_pitch(profile: CompanyProfile, exposures: list[Exposure], recommendation: Recommendation, pack: EvidencePack, version: int = 1) -> Pitch:
    """Deterministic pitch from the evidence pack (used offline and as a safe fallback)."""
    pid = pack.recommended_policy_id
    name = recommendation.policy_name
    s1 = Slide(slide_number=1, title=f"{profile.company_name}: company overview", subtitle="Industry, size and risks. Anything without a source is labelled an assumption.", bullets=[])
    for c in pack.company_evidence[:4]:
        s1.bullets.append(SlideBullet(text=c.text, source_urls=c.urls, kind="company"))
    if not s1.bullets:
        s1.bullets.append(SlideBullet(text="Company research unavailable; context is limited to advisor inputs and is an assumption.", kind="assumption"))

    s2 = Slide(
        slide_number=2,
        title=f"How {name} maps to the exposures",
        subtitle="Benefits below are taken from the insurer brochure. The arrow is the mapping, not a coverage guarantee.",
        layout="map",
        bullets=[],
    )
    for e in sorted(exposures, key=lambda x: -x.priority)[:3]:
        kind = "company" if e.status.value == "FACT" else "assumption"
        s2.bullets.append(SlideBullet(text=f"{e.title}: {e.description}", kind=kind))
    for it in pack.items[:3]:
        cond = f" Subject to: {it.conditions[0]}" if it.conditions else ""
        scope = f" ({it.variant_scope})" if it.variant_scope else ""
        s2.bullets.append(SlideBullet(text=f"{it.feature_label}: {it.statement}{scope}{cond}", source_chunk_ids=it.chunk_ids, policy_id=pid, kind="policy"))
    if not any(b.kind == "policy" for b in s2.bullets):
        s2.bullets.append(SlideBullet(text="No evidence-backed benefit statements could be established from the brochure.", kind="assumption"))
    why = recommendation.rationale[0] if recommendation.rationale else f"{name} leads the decision-support comparison of the brochures in scope."
    s2.bullets.append(SlideBullet(text=f"Why this policy: {why}", kind="recommendation"))

    s3 = Slide(slide_number=3, title="Why Marsh, and what to watch", subtitle="Positioning is Marsh's. Watch-outs come from the brochure comparison.", bullets=[])
    for line in MARSH_POSITIONING:
        s3.bullets.append(SlideBullet(text=line, kind="marsh"))
    for n in pack.comparison_notes[:2]:
        s3.bullets.append(SlideBullet(text=n, kind="recommendation"))
    for g in pack.gaps[:2]:
        s3.bullets.append(SlideBullet(text=f"Watch-out: {g}", kind="recommendation"))

    s4 = Slide(slide_number=4, title=f"Recommended policy: {name}", subtitle="One recommendation, for advisor review before anything reaches the client.", bullets=[])
    s4.bullets.append(SlideBullet(text=f"Recommend {name}. Fit score {recommendation.fit_score}/100 is decision-support only, not a measure of coverage.", kind="recommendation"))
    for a in pack.assumptions[:2]:
        s4.bullets.append(SlideBullet(text=f"Assumption: {a}", kind="assumption"))
    s4.bullets.append(SlideBullet(text="Next step: confirm group terms and policy wording with the insurer before client presentation.", kind="recommendation"))
    return _finalise([s1, s2, s3, s4], profile.company_name, pid, version)


def generate_pitch(profile: CompanyProfile, exposures: list[Exposure], recommendation: Recommendation, pack: EvidencePack, llm: LLMService | None = None, version: int = 1, feedback: str | None = None) -> tuple[Pitch, list[str]]:
    """Returns (pitch, warnings). Warnings list dropped bullets and fallbacks."""
    llm = llm or get_llm()
    warnings: list[str] = []
    if not llm.available or not pack.items:
        if not pack.items:
            warnings.append("Evidence pack is empty; template pitch generated without policy claims.")
        else:
            warnings.append("LLM not configured; deterministic template pitch generated.")
        return template_pitch(profile, exposures, recommendation, pack, version), warnings

    by_id = pack.by_id()
    company_by_id = pack.company_by_id()
    exp_lines = "\n".join(f"- {e.title} [{e.status.value}, priority {e.priority}]: {e.description}" for e in sorted(exposures, key=lambda x: -x.priority)[:8])
    user = (
        f"COMPANY: {profile.company_name}\nINDUSTRY: {profile.industry or 'Unknown'} | SIZE: {profile.size or 'Unknown'} | GEOGRAPHY: {profile.geography or 'Unknown'}\n"
        f"COMPANY FACTS (verified; cite the id):\n" + ("\n".join(f"[{c.evidence_id}] {c.text}" for c in pack.company_evidence) or "- none") + "\n"
        "COMPANY INFERENCES/ASSUMPTIONS:\n" + ("\n".join(f"- {f}" for f in pack.company_inferences) or "- none") + "\n\n"
        f"EXPOSURES:\n{exp_lines}\n\nRECOMMENDED POLICY: {recommendation.policy_name} (id {pack.recommended_policy_id}); fit score {recommendation.fit_score}/100 (decision-support)\n"
        f"RATIONALE: {' '.join(recommendation.rationale[:3])}\n\nEVIDENCE PACK:\n{_pack_text(pack)}\n\n"
        f"COMPARISON NOTES:\n" + ("\n".join(f"- {n}" for n in pack.comparison_notes) or "- none") + "\n\n"
        "GAPS / WATCH-OUTS:\n" + ("\n".join(f"- {g}" for g in pack.gaps) or "- none") + "\n\n"
        "ASSUMPTIONS:\n" + ("\n".join(f"- {a}" for a in pack.assumptions) or "- none") + "\n\n"
        "MARSH POSITIONING (use verbatim, kind=marsh):\n" + "\n".join(f"- {m}" for m in MARSH_POSITIONING)
        + (f"\n\nAUDITOR FEEDBACK TO ADDRESS:\n{feedback}" if feedback else "")
    )
    try:
        out = llm.structured(SYSTEM.replace("{policy}", recommendation.policy_name), user, PitchOut, purpose="pitch_generation", temperature=0.2)
    except LLMUnavailable:
        warnings.append("LLM unavailable; template pitch generated.")
        return template_pitch(profile, exposures, recommendation, pack, version), warnings
    except LLMQuotaExceeded:
        raise  # quota is a stop-the-run error, not something to paper over
    except Exception as exc:
        log.error("Pitch generation failed: %s", exc)
        warnings.append(f"Pitch generation failed ({exc}); template pitch generated.")
        return template_pitch(profile, exposures, recommendation, pack, version), warnings

    slides: list[Slide] = []
    for i, s in enumerate(out.slides, start=1):
        bullets: list[SlideBullet] = []
        for b in s.bullets:
            ids = [e for e in b.evidence_ids if e in by_id]
            chunk_ids: list[str] = []
            for e in ids:
                chunk_ids.extend(by_id[e].chunk_ids)
            urls = [u for e in b.evidence_ids if e in company_by_id for u in company_by_id[e].urls]
            if b.kind == "policy" and not chunk_ids:
                warnings.append(f"Dropped uncited policy bullet on slide {i}: '{b.text[:80]}'")
                continue
            bullets.append(SlideBullet(text=b.text.strip(), source_chunk_ids=list(dict.fromkeys(chunk_ids)), source_urls=list(dict.fromkeys(urls)), policy_id=pack.recommended_policy_id if chunk_ids else None, kind=b.kind))
        if bullets:
            slides.append(Slide(slide_number=i, title=s.title.strip(), subtitle=s.subtitle, bullets=bullets))
    if len(slides) < 4:
        warnings.append("LLM pitch had too few valid slides; template pitch used.")
        return template_pitch(profile, exposures, recommendation, pack, version), warnings
    slides = slides[:4]
    slides[1].layout = "map"
    return _finalise(slides, profile.company_name, pack.recommended_policy_id, version), warnings
