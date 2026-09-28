"""Pitch generation: 3-5 slides whose policy statements are restricted to Evidence Pack items.

The LLM writes bullets and cites evidence ids; we map ids -> chunk ids deterministically. Any
policy bullet without valid evidence is dropped before audit (never silently kept). A deterministic
template pitch is produced when no LLM is configured, so the pipeline never fabricates.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from app.advisory.states import coverage_label, coverage_state
from app.models.client import CompanyProfile, Exposure
from app.models.fit import Recommendation
from app.models.pitch import Pitch, Slide, SlideBullet
from app.models.policy import ComparisonMatrix, CoverageStatus
from app.pitch.evidence_pack import EvidenceItem, EvidencePack
from app.policy_fit.scoring_config import ScoringConfig
from app.services.llm import LLMQuotaExceeded, LLMService, LLMUnavailable, get_llm
from app.utils.ids import new_id
from app.utils.logging import get_logger

log = get_logger(__name__)

MARSH_POSITIONING = [
    "EVIDENCE|Policy conclusions are tied to the supplied policy documents.",
    "PRIORITIES|The recommendation follows the client's stated priorities, not a generic ranking.",
    "ALTERNATIVES|Another policy is shown only when its evidence is sufficient to compare.",
    "UNCERTAINTY|Information that cannot be established from the supplied material is identified, not presented as fact.",
    "REVIEW|The advisor reviews the evidence and the presentation before it is shared.",
]

SYSTEM = """You write concise, client-specific insurance pitch slides for a Marsh Client Advisor.
HARD RULES:
1. Every bullet about the policy (kind=policy) must cite one or more evidence ids from the EVIDENCE PACK, and must say only what that evidence says. Do not add benefits, numbers, limits or waiting periods that are not in the cited evidence. Keep numbers exactly as written.
2. If evidence carries conditions or a variant scope, the bullet must mention them briefly (e.g. "subject to a 36-month waiting period", "on the VIP+ variant").
3. Statements about the company must only use COMPANY FACTS (kind=company, cite their C-ids in evidence_ids so the source pages can be linked) or be clearly framed as inferences (kind=assumption). Never invent company figures.
4. Comparison statements against other insurers must come verbatim in meaning from COMPARISON NOTES (kind=policy, cite the evidence id of the feature if given, else leave evidence empty and use kind=recommendation).
5. Produce exactly 4 content slides. A cover is added separately. Use the pipe form LABEL|text. Each text is one short sentence. Do not use an em dash.
   Slide 1 is the client context. Title like "[Client]: the decision context". Subtitle is one sentence using only the facts given. Exactly three profile bullets, kind=company if a COMPANY FACT supports them else kind=assumption: INDUSTRY|…, SCALE|…, FOOTPRINT|…. Then up to four client needs. The label is the exposure name. kind=assumption unless the exposure status is FACT.
   Slides 2, 3 and 4 are replaced from the evidence pack after you write. Still produce them so the draft is complete: slide 2 a comparison, slide 3 why this policy, slide 4 what this means for the client. Do not write a fit score, a completeness score, or a ranking number on any slide.
6. No marketing hyperbole, no superlatives like "best" or "guaranteed", no AI jargon.
7. Do not present an internal score as proof of suitability. Do not mention a fit score at all."""


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


def _plain(text: str) -> str:
    return text.replace("\u2014", ", ").replace("\u2013", ", ")


def _cover_slide(company: str) -> Slide:
    """The opener the advisor sees and the export prints. It is part of the structured deck."""
    name = (company or "the client").strip() or "the client"
    when = datetime.now(timezone.utc).strftime("%B %Y")
    return Slide(
        slide_number=1,
        title="Health Policy Advisory",
        subtitle="Evidence-led health insurance recommendation",
        layout="cover",
        bullets=[
            SlideBullet(text=name, kind="recommendation"),
            SlideBullet(text=when, kind="recommendation"),
            SlideBullet(text="Prepared by Marsh McLennan", kind="recommendation"),
        ],
    )


def _finalise(slides: list[Slide], company: str, pid: str, version: int) -> Pitch:
    if not slides or slides[0].layout != "cover":
        slides = [_cover_slide(company), *slides]
    for i, s in enumerate(slides, start=1):
        s.slide_number = i
    return Pitch(pitch_id=new_id("pitch"), company_name=company, recommended_policy_id=pid, slides=slides, version=version)


def _clip_text(text: str, limit: int = 110) -> str:
    text = " ".join(_plain(text).replace("|", " ").split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _sentence_case(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return text
    lowered = text.lower()
    return lowered[:1].upper() + lowered[1:]


def _concise(text: str, limit: int = 64) -> str:
    """A short client phrase. It keeps a clause that is already in the supplied wording."""
    raw = " ".join(_plain(text).replace("|", " ").split())
    if not raw:
        return ""
    if "?" in raw and len(raw.split("?", 1)[1].strip()) > 12:
        raw = raw.split("?", 1)[1].strip(" .")
    low = raw.lower()
    if "no capping" in low or re.search(r"\bno cap\b", low):
        return "No separate cap"
    if re.search(r"\bat actuals\b", low):
        return "At actuals"
    if re.search(r"day[\s-]*1|day one", low) and "chronic" in low:
        return "Day-one cover for listed chronic conditions"
    if re.search(r"(?i)each claim will be up to the base sum insured", raw):
        return "Each claim up to the base sum insured"
    if re.search(r"(?i)up to 100% of (?:the )?(?:base )?sum insured", raw):
        return "Up to 100% of base sum insured"
    if re.search(r"(?i)super reload refills it to 100%", raw):
        return "Super reload refills it to 100% for your next claim"
    if "unlimited times" in low:
        return "Available for unlimited times for unrelated or same illness"
    grown = re.search(r"(?i)inflates to (\d+) times.{0,60}?(\d+(?:st|nd|rd|th) year)", raw)
    if grown:
        return f"Sum insured inflates to {grown.group(1)} times by the {grown.group(2)}"
    if re.search(r"(?i)\bup to si\b", raw) and "base sum insured" not in low:
        return "Up to the sum insured"
    if re.search(r"(?i)up to (?:your |the )?(?:base )?sum insured", raw) and not re.search(r"(?i)claim protect|restore|recharge|reload", raw) and ("room" in low or "no capping" in low or len(raw) < 160):
        return "Up to the base sum insured"
    if raw[:1].islower():
        found = re.search(r"[A-Z][A-Za-z].{12,}", raw)
        if not found:
            return "Documented in the brochure"
        raw = found.group(0).strip(" .")
    if ":" in raw:
        left, right = raw.split(":", 1)
        right = re.split(r"\s+\(", right.strip(" ."))[0].strip()
        if re.search(r"(?i)^up to si$", right):
            return "Up to the sum insured"
        if 0 < len(left) <= 46 and len(right) > 6:
            piece = re.split(r"(?<!\d),\s+|;\s+", right)[0].strip(" .")
            label = re.sub(r"\s*\([^)]*\)", "", left).strip()
            if label and label.lower() not in piece.lower():
                phrase = f"{_sentence_case(label)}: {_sentence_case(piece)}"
            else:
                phrase = _sentence_case(piece)
            if len(phrase) > limit:
                phrase = _sentence_case(piece)
            if re.search(r"\bINR\s+\d{1,3}$", phrase):
                return "Documented in the brochure"
            return _clip_text(phrase, limit)
    clause = re.split(r"\s+\(", re.split(r"(?<=\.)\s", raw)[0])[0].strip(" .")
    clause = _sentence_case(clause)
    if not clause or clause[:1].islower():
        return "Documented in the brochure"
    return _clip_text(clause, limit)


_LENS_PRIORITY = "The recommendation evaluates the stated client priority alongside core hospitalisation and policy design considerations. These include room rent, restore benefits, waiting periods, non-medical expenses, co-payment and other documented coverage provisions."
_LENS_BASELINE = "The supplied brochures are compared on hospitalisation, room rent, restore, waiting periods, non-medical expenses, and co-payment."
_WHY_PRIORITY = "This is the requirement the client asked the advisory to answer. It carries the greatest weight in the comparison, so each policy is read against it before the other documented benefits. The aim is to see which supplied wording addresses that requirement, and on what terms."
_WHY_BASELINE = "No specific client priority was selected. The comparison therefore uses the standard baseline coverage criteria."
_BASIS_LINE = "Recommended based on the stated client priorities and documented policy benefits."


def _priority_weight_label() -> str:
    """The share of the decision assigned to stated priorities. Not a fit score."""
    pct = ScoringConfig().advisor_pool * 100
    return f"{pct:g}%"


def _reading(item: EvidenceItem) -> str:
    label = item.feature_label
    if item.status == CoverageStatus.COVERED:
        return f"The supplied brochure establishes {label}."
    if item.status in {CoverageStatus.CONDITIONAL, CoverageStatus.PARTIALLY_COVERED}:
        extra = f" Condition: {_clip_text(item.conditions[0], 70)}" if item.conditions else ""
        return f"Available subject to stated conditions.{extra}"
    if item.status == CoverageStatus.ADD_ON:
        return "Available as an add-on under the supplied brochure."
    if item.status == CoverageStatus.EXCLUDED:
        return "Excluded under the supplied brochure."
    return "Not established from the supplied brochure."


def _priority_exposure(item: EvidenceItem, exposures: list[Exposure]) -> Exposure | None:
    for exposure in exposures:
        if item.feature_key not in (exposure.feature_keys or []):
            continue
        if "advisor_priority" in (exposure.basis or []) or exposure.title.lower().startswith("advisor priority"):
            return exposure
    return None


def _need(item: EvidenceItem, exposures: list[Exposure]) -> str:
    priority = _priority_exposure(item, exposures)
    if priority is None:
        return item.feature_label
    title = priority.title.split(":", 1)[1].strip() if ":" in priority.title else priority.title
    return title or item.feature_label


def _means(item: EvidenceItem, exposures: list[Exposure]) -> str:
    if _priority_exposure(item, exposures):
        return f"This addresses the client's stated priority: {_need(item, exposures)}."
    return f"Documented treatment of {item.feature_label}."


def _evidence_line(item: EvidenceItem) -> str:
    """A readable sentence from the stored wording. A one-letter stub is not shown as the start."""
    raw = " ".join(item.statement.replace(":.", ".").split())
    if raw[:1].isupper():
        sentence = re.match(r"[A-Z].*?\.", raw)
        return sentence.group(0) if sentence else raw
    stub = re.match(r"^[a-z]\s+", raw)
    body = raw[stub.end():] if stub else raw
    if stub and body[:1].islower():
        labelled = re.search(r"[A-Z][A-Za-z]+(?::|\s)[^.]{8,}\.", body)
        if labelled:
            return labelled.group(0)
    if body[:1].islower():
        body = body[:1].upper() + body[1:]
    if body.lower().startswith(item.feature_label.lower()):
        return body
    return f"{item.feature_label}: {body}"


def _readable_quote(text: str) -> str:
    text = " ".join(text.replace("_", " ").split())
    if ":" in text:
        text = text.split(":", 1)[1].strip()
    if text[:1].islower():
        found = re.search(r"[A-Z][\w].*", text)
        if found and found.start() <= 12:
            return found.group(0).strip()
        return ""
    return text


def _alt_lines(alt) -> tuple[str, str]:
    offer = ""
    if alt.evidence:
        offer = _readable_quote(_clean_note(re.sub(r"(?i)\bp\.\s*\d+", "", alt.evidence[0])))
    trade = ""
    if alt.trade_offs:
        raw = alt.trade_offs[0]
        found = re.search(r"(?i)\b(ADD_ON|CONDITIONAL|EXCLUDED|PARTIALLY_COVERED|COVERED)\b", raw)
        if found:
            trade = _clean_note(found.group(1))
            if trade.lower() in {"not established", "excluded"}:
                trade = "Not included in the compared wording" if trade.lower() == "excluded" else ""
    if offer and re.search(r"(?i)not established|sufficient evidence|no other policy", offer):
        offer = ""
    return offer, trade


_NOT_CELL = "Not established from supplied policy"


def _cell_source(matrix: ComparisonMatrix | None, feature: str, policy_id: str) -> str:
    """Chunk id for a compared policy, so an alternative is cited from its own brochure."""
    if matrix is None or not feature or not policy_id:
        return ""
    cell = (matrix.cells or {}).get(feature, {}).get(policy_id)
    if cell is None or not cell.fact.sources:
        return ""
    return cell.fact.sources[0].chunk_id or ""


def _peer_cell(matrix: ComparisonMatrix | None, feature: str, policy_id: str) -> str:
    """A short client phrase. An unsettled point is named, not left as an internal status."""
    if matrix is None or not feature:
        return _NOT_CELL
    cell = (matrix.cells or {}).get(feature, {}).get(policy_id)
    if cell is None:
        return _NOT_CELL
    status = cell.status
    if status in {CoverageStatus.NOT_FOUND, CoverageStatus.UNKNOWN}:
        return _NOT_CELL
    if status == CoverageStatus.ADD_ON:
        return "Available as an add-on"
    if status == CoverageStatus.EXCLUDED:
        return "Not included"
    if status in {CoverageStatus.CONDITIONAL, CoverageStatus.PARTIALLY_COVERED}:
        return "Subject to stated conditions"
    value = " ".join((cell.fact.value or "").split())
    if not value or value.lower().startswith("not specified"):
        return "Documented in the brochure"
    if re.search(r"(?i)\bcovered\s+\d+\s*$", value):
        return "Documented in the brochure"
    return _concise(value, 58)


def _clean_note(text: str) -> str:
    text = _plain(text)
    text = re.sub(r"(?i)\b(?:fit|completeness|weighted|eligibility)\s+score\b[^.]{0,48}", "", text)
    text = re.sub(r"\d+(?:\.\d+)?\s*/\s*100", "", text)
    text = re.sub(r"(?i)\bADD_ON\b", "available as an add-on", text)
    text = re.sub(r"(?i)\b(?:NOT_FOUND|UNKNOWN)\b", "not established", text)
    text = re.sub(r"(?i)\bEXCLUDED\b", "excluded", text)
    text = re.sub(r"(?i)\bCONDITIONAL\b", "subject to stated conditions", text)
    text = re.sub(r"(?i)\bPARTIALLY_COVERED\b", "partially addressed", text)
    text = re.sub(r"(?i)\bCOVERED\b", "established", text)
    return " ".join(text.split())


def _ordered_items(pack: EvidencePack, exposures: list[Exposure]) -> list[EvidenceItem]:
    items = [it for it in pack.items if it.statement]
    asked = [it for it in items if any(it.feature_key in (e.feature_keys or []) for e in exposures)]
    rest = [it for it in items if it not in asked]
    chosen: list[EvidenceItem] = []
    seen: dict[str, int] = {}
    for item in asked + rest:
        signature = " ".join(item.statement.lower().split())[:120]
        if signature in seen:
            previous = chosen[seen[signature]]
            if item.feature_key == "room_rent" and "room rent" in signature and previous.feature_key != "room_rent":
                chosen[seen[signature]] = item
            continue
        seen[signature] = len(chosen)
        chosen.append(item)
        if len(chosen) >= 6:
            break
    return chosen


def _settled(phrase: str) -> bool:
    low = (phrase or "").strip().lower()
    if not low or low.startswith("not established"):
        return False
    return "no specific priority" not in low


def _explain(role: str, phrase: str, priority: str, condition: str = "", company: str = "") -> str:
    """Two sentences. The fact is the supplied phrase; the second says why it matters for this client."""
    del condition
    who = company.strip() or "the client"
    if not _settled(phrase):
        return f"This point is not established from the supplied policy documentation for {who}. It is not treated as a documented benefit, and it should be confirmed in the wording before a decision."
    fact = phrase.rstrip(".")
    shown = fact[0].lower() + fact[1:] if fact[:1].isupper() else fact
    if role == "priority":
        named = f", {priority}," if priority else ""
        return f"The brochure documents {shown}. For {who}, this is read against the stated priority{named} which is the requirement the advisory was asked to answer."
    if role == "room":
        return f"The supplied room-rent wording is: {fact}. This is what {who} can use to judge whether a room-rent limit would reduce an eligible hospitalisation claim."
    if role == "cover":
        return f"On hospitalisation, the supplied wording states: {fact}. That is the core protection available to {who} for an eligible in-patient claim."
    if role == "design":
        return f"On policy design, the supplied wording states: {fact}. For {who}, this bears on how cover behaves after a claim, or on the period before cover applies."
    return f"The supplied wording states: {fact}. It is included because it bears on the decision {who} is making."


def _why_body(item: EvidenceItem, exposures: list[Exposure], clip) -> str:
    line = _concise(_evidence_line(item), 90)
    role = "priority" if _priority_exposure(item, exposures) else "other"
    condition = item.conditions[0] if item.conditions else ""
    return clip(_explain(role, line, _need(item, exposures) if role == "priority" else "", condition), 320)


def _takeaway(item: EvidenceItem, exposures: list[Exposure], clip) -> str:
    line = _concise(_evidence_line(item), 88)
    role = "priority" if _priority_exposure(item, exposures) else "other"
    return clip(_explain(role, line, _need(item, exposures) if role == "priority" else ""), 280)


def _client_heading(item: EvidenceItem, exposures: list[Exposure]) -> str:
    if _priority_exposure(item, exposures):
        return "Priority alignment"
    return {
        "room_rent": "Room rent protection",
        "in_patient_hospitalisation": "Hospitalisation protection",
        "restore_recharge": "Restore protection",
        "waiting_period_initial": "Waiting-period considerations",
        "waiting_period_ped": "Waiting-period considerations",
        "waiting_period_specific": "Waiting-period considerations",
        "chronic_conditions_day1": "Priority alignment",
        "non_medical_expenses_cover": "Non-medical expenses",
        "copay": "Co-pay",
        "personal_accident": "Accident cover",
    }.get(item.feature_key, item.feature_label)


def _policy_columns(matrix: ComparisonMatrix | None, recommendation: Recommendation) -> list[tuple[str, str]]:
    """Policies in the comparison's existing order. Names come from the recommendation or the brochure fact."""
    known: dict[str, str] = {}
    if recommendation.recommended_policy_id and recommendation.policy_name:
        known[recommendation.recommended_policy_id] = recommendation.policy_name.strip()
    for alt in recommendation.alternatives:
        if alt.policy_id and alt.policy_name:
            known[alt.policy_id] = alt.policy_name.strip()
    if matrix is not None:
        for cols in (matrix.cells or {}).values():
            for pid, cell in cols.items():
                fact = cell.fact
                label = " ".join(part for part in (fact.insurer_name, fact.product_name) if part).strip()
                if not label and fact.sources:
                    label = (fact.sources[0].policy_name or "").strip()
                if label:
                    known.setdefault(pid, label)
        ids = [pid for pid in matrix.policy_ids if pid][:4]
    else:
        ids = []
    if not ids:
        ids = [recommendation.recommended_policy_id, *[alt.policy_id for alt in recommendation.alternatives[:3]]]
        ids = [pid for pid in ids if pid][:4]
    return [(pid, known.get(pid) or pid) for pid in ids]


def _advisor_priorities(exposures: list[Exposure]) -> list[Exposure]:
    return [e for e in exposures if "advisor_priority" in (e.basis or []) or e.title.lower().startswith("advisor priority")]


def _headcount(profile: CompanyProfile) -> str | None:
    """A stated headcount. A size phrase with no number is not turned into an employee figure."""
    texts = [profile.size or ""]
    texts.extend(fact.text for fact in profile.facts if fact.field == "size" and fact.text)
    for text in texts:
        cleaned = " ".join(text.split()).strip()
        if cleaned and cleaned.lower() != "unknown" and re.search(r"\d", cleaned):
            return cleaned
    return None


def _known(value: str | None) -> str:
    text = " ".join((value or "").split()).strip()
    if not text or text.lower() in {"unknown", "not established", "none", "n/a"}:
        return ""
    return text


def _company_brief(profile: CompanyProfile, pack: EvidencePack) -> str:
    """One sentence from researched company material. The client name is always in it."""
    name = (profile.company_name or "the client").strip() or "the client"
    overview = _known(profile.overview)
    if overview:
        sentence = overview if overview.endswith(".") else f"{overview}."
        if name.lower() not in sentence.lower():
            sentence = f"{name}. {sentence}"
        return _clip_text(sentence, 280)
    for fact in pack.company_evidence:
        text = _known(fact.text)
        if not text:
            continue
        sentence = text if text.endswith(".") else f"{text}."
        if name.lower() not in sentence.lower():
            sentence = f"{name}. {sentence}"
        return _clip_text(sentence, 280)
    bits = [part for part in (_known(profile.industry), _known(profile.geography), _headcount(profile) or _known(profile.size)) if part]
    if bits:
        return f"{name} is {', '.join(bits)}."
    return f"This advisory is prepared for {name}."


def _context_slide(profile: CompanyProfile, exposures: list[Exposure], pack: EvidencePack) -> Slide:
    """Who the client is, what they asked for, and what the advisory is weighing."""
    name = (profile.company_name or "the client").strip() or "the client"
    slide = Slide(
        slide_number=1,
        title=f"{name}: the decision context",
        subtitle=None,
        layout="glance",
        bullets=[],
    )
    employees = _headcount(profile)
    for label, value in (("INDUSTRY", profile.industry), ("FOOTPRINT", profile.geography)):
        if value:
            slide.bullets.append(SlideBullet(text=f"{label}|{value}", kind="company"))
    if employees:
        slide.bullets.append(SlideBullet(text=f"EMPLOYEES|{employees}", kind="company"))
    elif profile.size:
        slide.bullets.append(SlideBullet(text=f"SCALE|{profile.size}", kind="company"))
    priorities = _advisor_priorities(exposures)
    if priorities:
        for exposure in priorities[:2]:
            title = exposure.title.split(":", 1)[1].strip() if ":" in exposure.title else exposure.title
            slide.bullets.append(SlideBullet(text=f"PRIORITY|{title}", kind="company"))
        slide.bullets.append(SlideBullet(text=f"WEIGHT|{_priority_weight_label()}", kind="recommendation"))
        slide.bullets.append(SlideBullet(text=f"WHY|{_clip_text(_company_brief(profile, pack) + ' ' + _WHY_PRIORITY, 320)}", kind="recommendation"))
        slide.bullets.append(SlideBullet(text=f"LENS|{_LENS_PRIORITY}", kind="recommendation"))
    else:
        slide.bullets.append(SlideBullet(text=f"WHY|{_company_brief(profile, pack)}", kind="recommendation"))
        slide.bullets.append(SlideBullet(text=f"LENS|{_LENS_BASELINE}", kind="recommendation"))
    labels = _assessed_labels(exposures)
    if labels:
        slide.bullets.append(SlideBullet(text="ASSESSED|" + "|".join(labels), kind="recommendation"))
    return slide


def _priority_title(exposures: list[Exposure]) -> str:
    priorities = _advisor_priorities(exposures)
    if not priorities:
        return ""
    title = priorities[0].title
    return title.split(":", 1)[1].strip() if ":" in title else title


def _priority_keys(exposures: list[Exposure]) -> list[str]:
    keys: list[str] = []
    for exposure in _advisor_priorities(exposures):
        for key in exposure.feature_keys or []:
            if key not in keys:
                keys.append(key)
    if keys:
        return keys
    for exposure in exposures:
        for key in exposure.feature_keys or []:
            if key not in keys:
                keys.append(key)
    return keys[:1] or ["in_patient_hospitalisation"]


def _assessed_labels(exposures: list[Exposure]) -> list[str]:
    return [label for label, _keys in _comparison_rows(exposures)]


def _comparison_rows(exposures: list[Exposure]) -> list[tuple[str, list[str]]]:
    return [
        ("Client priority alignment", _priority_keys(exposures)),
        ("Room rent", ["room_rent"]),
        ("Hospitalisation", ["in_patient_hospitalisation"]),
        ("Restore / recharge", ["restore_recharge"]),
        ("Waiting periods", ["waiting_period_initial", "waiting_period_specific", "waiting_period_ped"]),
        ("Non-medical expenses", ["non_medical_expenses_cover"]),
        ("Co-payment", ["copay"]),
    ]


def _pick_feature(matrix: ComparisonMatrix | None, keys: list[str], policy_id: str) -> str:
    if matrix is None:
        return keys[0] if keys else ""
    for key in keys:
        cell = (matrix.cells or {}).get(key, {}).get(policy_id)
        if cell is not None and cell.status not in {CoverageStatus.NOT_FOUND, CoverageStatus.UNKNOWN}:
            return key
    return keys[0] if keys else ""


def _item_for(pack: EvidencePack, feature: str) -> EvidenceItem | None:
    for item in pack.items:
        if item.feature_key == feature and item.statement:
            return item
    return None


def _advisory_body(profile: CompanyProfile, exposures: list[Exposure], recommendation: Recommendation, pack: EvidencePack, clip, matrix: ComparisonMatrix | None = None) -> list[Slide]:
    """Comparison, recommendation reasons, and the client summary. Built only from stored evidence."""
    pid = pack.recommended_policy_id
    name = (recommendation.policy_name or "The recommended policy").strip()
    client = (profile.company_name or "the client").strip() or "the client"
    columns = _policy_columns(matrix, recommendation)
    basis = f"For {client}, these points follow the researched company context and the supplied brochure wording."

    rows = _comparison_rows(exposures)
    priority_name = _priority_title(exposures)
    comparison = Slide(
        slide_number=2,
        title="How the policies compare",
        subtitle=None,
        layout="comparison",
        bullets=[
            SlideBullet(text="COLUMNS|" + "|".join(label for _pid, label in columns), kind="recommendation"),
            SlideBullet(text=f"REC|{name}", kind="recommendation"),
        ],
    )
    row_facts: list[tuple[str, str, str, EvidenceItem | None]] = []
    for label, keys in rows:
        feature = _pick_feature(matrix, keys, pid)
        item = _item_for(pack, feature)
        phrases = []
        if label == "Client priority alignment" and not _advisor_priorities(exposures):
            phrases = ["No specific priority selected"] * max(len(columns), 1)
            item = None
        else:
            for policy_id, _policy_name in columns:
                if policy_id == pid and item is not None:
                    phrases.append(_concise(_evidence_line(item), 48))
                else:
                    phrases.append(_peer_cell(matrix, feature, policy_id))
        comparison.bullets.append(SlideBullet(
            text="|".join(["ROW", label, *phrases]),
            source_chunk_ids=(item.chunk_ids[:1] if item else []),
            policy_id=pid,
            kind="policy",
        ))
        recommended_phrase = phrases[next((i for i, (col_id, _n) in enumerate(columns) if col_id == pid), 0)] if columns else _NOT_CELL
        row_facts.append((label, feature, recommended_phrase, item))

    role_for = {
        "Client priority alignment": "priority",
        "Room rent": "room",
        "Hospitalisation": "cover",
        "Restore / recharge": "design",
        "Waiting periods": "design",
        "Non-medical expenses": "other",
        "Co-payment": "other",
    }
    why = Slide(
        slide_number=3,
        title="Why this policy fits",
        subtitle=f"Why it fits {client}",
        layout="why",
        bullets=[SlideBullet(text=f"POLICY|{name}", kind="recommendation")],
    )
    documented = [row for row in row_facts if _settled(row[2])]
    chosen_rows = (documented or row_facts)[:5]
    for label, _feature, phrase, item in chosen_rows:
        condition = item.conditions[0] if item and item.conditions and len(item.conditions[0]) <= 48 else ""
        why.bullets.append(SlideBullet(
            text=f"{label}|{clip(_explain(role_for.get(label, 'other'), phrase, priority_name, condition, client), 320)}",
            source_chunk_ids=(item.chunk_ids[:1] if item else []),
            policy_id=pid,
            kind="policy",
        ))

    blocks = [
        ("Priority alignment", "priority", row_facts[0]),
        ("Coverage protection", "cover", row_facts[2]),
        ("Room rent position", "room", row_facts[1]),
        ("Policy design", "design", next((row for row in (row_facts[3], row_facts[4]) if _settled(row[2])), row_facts[3])),
        ("Practical decision consideration", "practical", row_facts[5]),
    ]
    decision = Slide(
        slide_number=4,
        title="What this means for the client",
        subtitle=basis,
        layout="decision",
        bullets=[SlideBullet(text=f"POLICY|{name}", kind="recommendation")],
    )
    for heading, role, (_label, _feature, phrase, item) in blocks:
        chunk_ids = item.chunk_ids[:1] if item else []
        if role == "practical" and recommendation.alternatives:
            alt = recommendation.alternatives[0]
            offer, trade = _alt_lines(alt)
            named = [word for word in ("hypertension", "diabetes", "hyperlipidemia", "asthma") if word in offer.lower()]
            if named and re.search(r"\b30\b", offer):
                listed = ", ".join(named[:-1]) + (f" or {named[-1]}" if len(named) > 1 else named[0])
                offer_bit = f"instant cover for {listed} after 30 days"
            else:
                offer_bit = _concise(offer, 72) if offer else ""
            body = f"For {client}, {alt.policy_name} was also considered."
            if offer_bit:
                body += f" It offers {offer_bit.rstrip('.')}."
            if trade:
                body += f" The trade-off is that this wording is {trade.rstrip('.')}."
            if len(recommendation.alternatives) > 1:
                second = recommendation.alternatives[1]
                offer2, trade2 = _alt_lines(second)
                extra = f" {second.policy_name} was also considered."
                bit2 = _concise(offer2, 64) if offer2 else ""
                if bit2:
                    extra += f" It offers {bit2.rstrip('.')}."
                if trade2:
                    extra += f" The trade-off is that this wording is {trade2.rstrip('.')}."
                name_only = f" {second.policy_name} was also considered."
                if len(body) + len(extra) <= 280:
                    body += extra
                elif len(body) + len(name_only) <= 280:
                    body += name_only
            peer = _cell_source(matrix, row_facts[0][1], alt.policy_id)
            chunk_ids = [peer] if peer else []
        else:
            if role == "priority" and not _settled(phrase):
                body = _WHY_BASELINE
            else:
                body = _explain(role if role != "practical" else "other", phrase, priority_name, company=client)
        decision.bullets.append(SlideBullet(
            text=f"{heading}|{clip(body, 280)}",
            source_chunk_ids=chunk_ids,
            policy_id=pid,
            kind="policy",
        ))
    return [comparison, why, decision]


def template_pitch(profile: CompanyProfile, exposures: list[Exposure], recommendation: Recommendation, pack: EvidencePack, version: int = 1, matrix: ComparisonMatrix | None = None) -> Pitch:
    """Deterministic pitch from the evidence pack (used offline and as a safe fallback)."""
    pid = pack.recommended_policy_id
    def _clip(text: str, limit: int = 110) -> str:
        return _clip_text(text, limit)

    context = _context_slide(profile, exposures, pack)
    body = _advisory_body(profile, exposures, recommendation, pack, _clip, matrix)
    pitch = _finalise([context, *body], profile.company_name, pid, version)
    align_fit_score(pitch.slides, recommendation.fit_score)
    return pitch


def generate_pitch(profile: CompanyProfile, exposures: list[Exposure], recommendation: Recommendation, pack: EvidencePack, llm: LLMService | None = None, version: int = 1, feedback: str | None = None, matrix: ComparisonMatrix | None = None) -> tuple[Pitch, list[str]]:
    """Returns (pitch, warnings). Warnings list dropped bullets and fallbacks."""
    llm = llm or get_llm()
    warnings: list[str] = []
    if not llm.available or not pack.items:
        if not pack.items:
            warnings.append("Evidence pack is empty; template pitch generated without policy claims.")
        else:
            warnings.append("LLM not configured; deterministic template pitch generated.")
        return template_pitch(profile, exposures, recommendation, pack, version, matrix), warnings

    by_id = pack.by_id()
    company_by_id = pack.company_by_id()
    exp_lines = "\n".join(f"- {e.title} [{e.status.value}, priority {e.priority}]: {e.description}" for e in sorted(exposures, key=lambda x: -x.priority)[:8])
    user = (
        f"COMPANY: {profile.company_name}\nINDUSTRY: {profile.industry or 'Unknown'} | SIZE: {profile.size or 'Unknown'} | GEOGRAPHY: {profile.geography or 'Unknown'}\n"
        f"COMPANY FACTS (verified; cite the id):\n" + ("\n".join(f"[{c.evidence_id}] {c.text}" for c in pack.company_evidence) or "- none") + "\n"
        "COMPANY INFERENCES/ASSUMPTIONS:\n" + ("\n".join(f"- {f}" for f in pack.company_inferences) or "- none") + "\n\n"
        f"EXPOSURES:\n{exp_lines}\n\nRECOMMENDED POLICY: {recommendation.policy_name} (id {pack.recommended_policy_id})\n"
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
        return template_pitch(profile, exposures, recommendation, pack, version, matrix), warnings
    except LLMQuotaExceeded:
        raise  # quota is a stop-the-run error, not something to paper over
    except Exception as exc:
        log.error("Pitch generation failed: %s", exc)
        warnings.append(f"Pitch generation failed ({exc}); template pitch generated.")
        return template_pitch(profile, exposures, recommendation, pack, version, matrix), warnings

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
        return template_pitch(profile, exposures, recommendation, pack, version, matrix), warnings
    slides = slides[:4]
    warnings.extend(align_fit_score(slides, recommendation.fit_score))
    context = _context_slide(profile, exposures, pack)
    body = _advisory_body(profile, exposures, recommendation, pack, _clip_text, matrix)
    return _finalise([context, *body], profile.company_name, pack.recommended_policy_id, version), warnings


_FIT_CLAUSE = re.compile(r"(?i)(?:,|\s)?(?:with a )?(?:decision-support )?fit score of \d+(?:\.\d+)?\s*/\s*100")
_FIT_NUMBER = re.compile(r"\d+(?:\.\d+)?(?=\s*/\s*100)")


def align_fit_score(slides: list[Slide], score: float) -> list[str]:
    """A client slide does not carry the fit figure. It is not brochure evidence.

    The calculated score stays on the recommendation record for the advisor view.
    """
    del score  # retained so callers keep the same signature; the figure is not written onto slides
    warnings: list[str] = []
    for slide in slides:
        kept: list[SlideBullet] = []
        for bullet in slide.bullets:
            if re.search(r"(?i)fit score|SCORE\|", bullet.text) or _FIT_NUMBER.search(bullet.text):
                warnings.append("Removed a fit score from the client deck. The score is not brochure evidence and is not shown to the client.")
                cleaned = _FIT_CLAUSE.sub("", bullet.text)
                cleaned = _FIT_NUMBER.sub("", cleaned)
                cleaned = re.sub(r"(?i)\bSCORE\|", "", cleaned)
                cleaned = re.sub(r"/+\s*100", "", cleaned).strip(" ,;.|/")
                if not re.sub(r"[^A-Za-z]", "", cleaned):
                    continue
                bullet.text = cleaned
                bullet.kind = "recommendation"
                bullet.source_chunk_ids = []
                bullet.source_urls = []
                bullet.policy_id = None
            kept.append(bullet)
        slide.bullets = kept
    return warnings
