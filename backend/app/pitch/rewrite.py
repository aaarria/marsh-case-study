"""Inline AI edit of one slide bullet (the deck's ⌘K), under the same evidence rules as the generator.

The advisor points at a bullet and says what should change; Gemini rewrites that bullet only, citing
evidence ids from the run's evidence pack. A policy claim that the pack cannot support is refused rather
than returned uncited, so the edit can never introduce a statement the audit would have to fail.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.pitch import Pitch, SlideBullet
from app.pitch.evidence_pack import EvidencePack
from app.pitch.generator import _pack_text
from app.services.llm import LLMService, LLMUnavailable, get_llm

REWRITE_SYSTEM = """You edit ONE bullet of an insurance pitch slide exactly as a Marsh Client Advisor instructs.
HARD RULES:
1. Change only what the instruction asks; keep the rest of the bullet's meaning.
2. A bullet about the policy (kind=policy) must say only what the cited EVIDENCE PACK items say; cite their ids in evidence_ids. Keep numbers, limits and waiting periods exactly as written there. Never add a benefit or figure that is not in the pack.
3. If the instruction asks for a policy fact the pack does not contain, do NOT add it: keep the bullet faithful and explain what was missing in `note` (one sentence).
4. Company statements may only use COMPANY FACTS (kind=company) or be framed as inferences (kind=assumption). Never invent company figures.
5. <= 30 words, plain professional English, no marketing hyperbole, no superlatives.
Return the rewritten bullet, its kind, the evidence ids that still support it, and an optional note."""


class RewriteOut(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    kind: Literal["policy", "company", "recommendation", "assumption", "marsh"]
    evidence_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class RewriteRefused(ValueError):
    """The rewrite would produce an uncited policy claim; nothing is applied."""


def rewrite_bullet(pitch: Pitch, pack: EvidencePack | None, slide_number: int, bullet: SlideBullet, instruction: str, llm: LLMService | None = None) -> tuple[SlideBullet, str | None]:
    """Returns (rewritten bullet, note). Raises LLMUnavailable without a key, RewriteRefused for uncited policy claims."""
    llm = llm or get_llm()
    if not llm.available:
        raise LLMUnavailable("GEMINI_API_KEY is not configured")
    slide = next((s for s in pitch.slides if s.slide_number == slide_number), None)
    by_id = pack.by_id() if pack else {}
    # Which evidence ids the bullet currently rests on, so "keep the citation" is expressible.
    current_ids = [eid for eid, it in by_id.items() if any(c in bullet.source_chunk_ids for c in it.chunk_ids)]
    context = "\n".join(f"- {b.text}" for b in (slide.bullets if slide else []) if b is not bullet and b.text != bullet.text)
    user = (
        f"COMPANY: {pitch.company_name}\nSLIDE {slide_number}: {slide.title if slide else ''}\nOTHER BULLETS ON THE SLIDE (for context, do not edit):\n{context or '- none'}\n\n"
        f"BULLET TO EDIT (kind={bullet.kind}; currently cites {', '.join(current_ids) or 'nothing'}):\n{bullet.text}\n\n"
        f"ADVISOR INSTRUCTION:\n{instruction.strip()}\n\n"
        f"COMPANY FACTS (verified; cite the id):\n" + (("\n".join(f"[{c.evidence_id}] {c.text}" for c in pack.company_evidence) or "- none") if pack else "- none") + "\n\n"
        f"EVIDENCE PACK:\n{_pack_text(pack) if pack and pack.items else '- none'}"
    )
    out = llm.structured(REWRITE_SYSTEM, user, RewriteOut, purpose="bullet_rewrite", temperature=0.2)
    company_by_id = pack.company_by_id() if pack else {}
    chunk_ids: list[str] = []
    urls: list[str] = []
    for eid in out.evidence_ids:
        if eid in by_id:
            chunk_ids.extend(by_id[eid].chunk_ids)
        elif eid in company_by_id:
            urls.extend(company_by_id[eid].urls)
    chunk_ids = list(dict.fromkeys(chunk_ids))
    if out.kind == "policy" and not chunk_ids:
        raise RewriteRefused(out.note or "The rewrite would state a policy fact that no brochure evidence in this run supports; nothing was changed.")
    # A company bullet keeps its web sources unless the rewrite cited different facts.
    urls = list(dict.fromkeys(urls)) or (bullet.source_urls if out.kind == "company" else [])
    new = SlideBullet(text=out.text.strip(), source_chunk_ids=chunk_ids, source_urls=urls, policy_id=(pack.recommended_policy_id if pack and chunk_ids else None), kind=out.kind)
    return new, (out.note.strip() if out.note else None)
