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
    "PERSPECTIVE|See the client's risks from more than one angle.",
    "EXPERTISE|Specialized risk and insurance advice.",
    "CONNECTED CAPABILITIES|Broader Marsh capabilities, where the brief needs them.",
]

SYSTEM = """You write concise, client-specific insurance pitch slides for a Marsh Client Advisor.
HARD RULES:
1. Every bullet about the policy (kind=policy) must cite one or more evidence ids from the EVIDENCE PACK, and must say only what that evidence says. Do not add benefits, numbers, limits or waiting periods that are not in the cited evidence. Keep numbers exactly as written.
2. If evidence carries conditions or a variant scope, the bullet must mention them briefly (e.g. "subject to a 36-month waiting period", "on the VIP+ variant").
3. Statements about the company must only use COMPANY FACTS (kind=company, cite their C-ids in evidence_ids so the source pages can be linked) or be clearly framed as inferences (kind=assumption). Never invent company figures.
4. Comparison statements against other insurers must come verbatim in meaning from COMPARISON NOTES (kind=policy, cite the evidence id of the feature if given, else leave evidence empty and use kind=recommendation).
5. Produce exactly 4 content slides. The cover is added separately. Use the pipe form LABEL|text. Each text is one short sentence, never a paragraph.
   Slide 1 layout is the client at a glance. Title like "[Client] at a glance". Subtitle is one sentence that says what kind of employer this is and why the medical programme matters, using only the facts given. Exactly three profile bullets, kind=company if a COMPANY FACT supports them else kind=assumption: INDUSTRY|…, SCALE|…, FOOTPRINT|…. Then up to four exposure cards. The label is the exposure name, never the word TITLE. The text is one sentence on what that exposure means for this client's health cover. kind=assumption unless the exposure status is FACT.
   Slide 2 is the journey. Up to three exposures as the exposure name, then a pipe, then the client need (kind=assumption or company). Never use the word TITLE as the label. Up to three benefits as the benefit name, then a pipe, then what the brochure states (kind=policy, cite evidence ids, keep figures exact). Never use the word BENEFIT as the label. One kind=recommendation bullet: WHY|one sentence on why this policy fits this client, from RATIONALE only, naming the exposure it answers. Never say "best".
   Slide 3: the three MARSH POSITIONING lines verbatim, kind=marsh. Then up to four watch items. Tag must be WATCH, LIMIT, GAP or CONDITION. kind=policy with an evidence id when the pack states the condition; otherwise kind=recommendation and do not invent a page. A missing passage is GAP, not an exclusion.
   Slide 4 title "One policy. Clear rationale." Subtitle is the policy name only. Bullets: SCORE|{fit}/100 as kind=recommendation; up to three reasons as REASON|one short line (kind=policy with evidence ids when the reason is a benefit, else kind=recommendation); one TRADEOFF|one limitation.
6. No marketing hyperbole, no superlatives like "best" or "guaranteed", no AI jargon.
7. The fit score is decision-support only. Never present it as proof of suitability."""


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
    def _clip(text: str, limit: int = 110) -> str:
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"

    industry = profile.industry or "Not established"
    scale = profile.size or "Not established"
    footprint = profile.geography or "Not established"
    s1 = Slide(slide_number=1, title=f"{profile.company_name} at a glance", subtitle=_clip(profile.overview or f"What we can say about {profile.company_name}, and what is still an assumption.", 140), layout="glance", bullets=[])
    for label, value in (("INDUSTRY", industry), ("SCALE", scale), ("FOOTPRINT", footprint)):
        known = value != "Not established"
        s1.bullets.append(SlideBullet(text=f"{label}|{value}", kind="company" if known else "assumption"))
    for e in sorted(exposures, key=lambda x: -x.priority)[:4]:
        kind = "company" if e.status.value == "FACT" else "assumption"
        s1.bullets.append(SlideBullet(text=f"{e.title}|{_clip(e.description, 90)}", kind=kind))

    s2 = Slide(slide_number=2, title="From exposure to benefit", subtitle=None, layout="map", bullets=[])
    ranked = sorted(exposures, key=lambda x: -x.priority)[:3]
    for e in ranked:
        kind = "company" if e.status.value == "FACT" else "assumption"
        s2.bullets.append(SlideBullet(text=f"{e.title}|{_clip(e.description, 90)}", kind=kind))
    for it in pack.items[:3]:
        s2.bullets.append(SlideBullet(text=f"{it.feature_label}|{_clip(it.statement, 90)}", source_chunk_ids=it.chunk_ids, policy_id=pid, kind="policy"))
    why = recommendation.rationale[0] if recommendation.rationale else f"Selected because the brochure evidence lines up with the exposures identified for {profile.company_name}."
    s2.bullets.append(SlideBullet(text=f"WHY|{_clip(why, 150)}", kind="recommendation"))

    s3 = Slide(slide_number=3, title="Why Marsh", subtitle=None, layout="perspective", bullets=[])
    for line in MARSH_POSITIONING:
        s3.bullets.append(SlideBullet(text=line, kind="marsh"))
    for it in pack.items:
        if not it.conditions:
            continue
        s3.bullets.append(SlideBullet(text=f"CONDITION|{it.feature_label}: {_clip(it.conditions[0], 80)}", source_chunk_ids=it.chunk_ids[:1], policy_id=pid, kind="policy"))
        if sum(1 for b in s3.bullets if b.kind != "marsh") >= 4:
            break
    for g in pack.gaps:
        if sum(1 for b in s3.bullets if b.kind != "marsh") >= 4:
            break
        low = g.lower()
        tag = "GAP" if any(w in low for w in ("does not address", "cannot be confirmed", "not found", "unknown")) else "LIMIT" if any(w in low for w in ("capped", "limit")) else "CONDITION" if "condition" in low or "subject to" in low else "WATCH"
        s3.bullets.append(SlideBullet(text=f"{tag}|{_clip(g, 90)}", kind="recommendation"))

    score = int(round(recommendation.fit_score))
    s4 = Slide(slide_number=4, title="One policy. Clear rationale.", subtitle=name, layout="recommendation", bullets=[])
    s4.bullets.append(SlideBullet(text=f"SCORE|{score}/100", kind="recommendation"))
    for reason in recommendation.rationale[:3]:
        s4.bullets.append(SlideBullet(text=f"REASON|{_clip(reason, 100)}", kind="recommendation"))
    for it in pack.items[: max(0, 3 - len(recommendation.rationale))]:
        s4.bullets.append(SlideBullet(text=f"REASON|{it.feature_label}: {_clip(it.statement, 80)}", source_chunk_ids=it.chunk_ids[:1], policy_id=pid, kind="policy"))
    trade = next((a for a in pack.assumptions), None) or (pack.gaps[0] if pack.gaps else "Group terms are not in these brochures. Confirm wording before a client meeting.")
    s4.bullets.append(SlideBullet(text=f"TRADEOFF|{_clip(trade, 120)}", kind="assumption"))
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
    for slide, layout in zip(slides, ("glance", "map", "perspective", "recommendation")):
        slide.layout = layout
    return _finalise(slides, profile.company_name, pack.recommended_policy_id, version), warnings
