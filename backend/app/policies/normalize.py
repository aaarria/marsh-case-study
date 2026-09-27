"""Map brochure wording onto canonical features without scoring by insurer.

A hit is kept only when the quote is a substring of that policy's own chunk.
Product names in the quote are evidence. They are not a score.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.policy import (
    AvailabilityMode,
    Chunk,
    CoverageStatus,
    FeatureFact,
    PolicyExtractionResult,
    SourceRef,
)

_MISSING = {CoverageStatus.NOT_FOUND, CoverageStatus.UNKNOWN, CoverageStatus.REVIEW_REQUIRED}
_MARKER = "semantic_normalization=1"


@dataclass
class _Hit:
    feature: str
    status: CoverageStatus
    value: str
    quote: str
    chunks: list[Chunk]
    is_add_on: bool = False
    waiting_period: str | None = None
    waiting_period_days: float | None = None
    copay: str | None = None
    copay_percent: float | None = None
    deductible: str | None = None
    limit: str | None = None
    variant_scope: str | None = None
    exclusions: list[str] = field(default_factory=list)
    terminology: str = ""
    rank: int = 0

    def to_fact(self, policy_id: str) -> FeatureFact:
        sources = [SourceRef.from_chunk(chunk) for chunk in self.chunks]
        mode = AvailabilityMode.OPTIONAL_ADD_ON if self.is_add_on or self.status == CoverageStatus.ADD_ON else AvailabilityMode.BASE_POLICY
        if self.status == CoverageStatus.CONDITIONAL:
            mode = AvailabilityMode.CONDITIONAL
        if self.status == CoverageStatus.EXCLUDED:
            mode = AvailabilityMode.EXCLUDED
        note = f"{_MARKER} terminology={self.terminology}."
        return FeatureFact(
            policy_id=policy_id,
            feature=self.feature,
            coverage_status=self.status,
            availability_mode=mode,
            value=self.value,
            limit=self.limit,
            waiting_period=self.waiting_period,
            waiting_period_days=self.waiting_period_days,
            copay=self.copay,
            copay_percent=self.copay_percent,
            deductible=self.deductible,
            exclusions=list(self.exclusions),
            is_add_on=self.is_add_on or self.status == CoverageStatus.ADD_ON,
            add_on_required=self.is_add_on or self.status == CoverageStatus.ADD_ON,
            variant_scope=self.variant_scope,
            original_quote=self.quote,
            source_page=self.chunks[0].page_number if self.chunks else None,
            source_section=self.chunks[0].section if self.chunks else None,
            source_chunk_id=self.chunks[0].chunk_id if self.chunks else None,
            sources=sources,
            notes=note,
        )


def apply_normalization(results: dict[str, PolicyExtractionResult], chunks_for) -> dict[str, PolicyExtractionResult]:
    """Return a copy of each policy book with evidence-backed terminology filled in.

    `chunks_for(policy_id)` returns that policy's chunks. Other policies are never read.
    """
    out: dict[str, PolicyExtractionResult] = {}
    for pid, result in results.items():
        chunks = list(chunks_for(pid) or [])
        facts = {key: fact.model_copy(deep=True) for key, fact in result.facts.items()}
        for hit in _hits(chunks):
            if not _quote_ok(hit):
                continue
            current = facts.get(hit.feature)
            if current is None or current.coverage_status in _MISSING or _MARKER not in (current.notes or ""):
                if current is not None and current.coverage_status not in _MISSING and not _should_replace(current, hit, chunks):
                    current = _patch(current, hit)
                    facts[hit.feature] = current
                    continue
                facts[hit.feature] = hit.to_fact(pid)
        out[pid] = result.model_copy(update={"facts": facts})
    return out


def _patch(fact: FeatureFact, hit: _Hit) -> FeatureFact:
    """Keep a stronger existing fact, but copy a quantified field the heuristic left empty."""
    updates: dict = {}
    if fact.copay_percent is None and hit.copay_percent is not None:
        updates["copay_percent"] = hit.copay_percent
        updates["copay"] = hit.copay
    if fact.waiting_period_days is None and hit.waiting_period_days is not None and fact.feature == hit.feature:
        if fact.coverage_status == CoverageStatus.ADD_ON or hit.is_add_on:
            updates["waiting_period_days"] = hit.waiting_period_days
            updates["waiting_period"] = hit.waiting_period
    if updates and _MARKER not in (fact.notes or ""):
        updates["notes"] = ((fact.notes or "") + f" {_MARKER} terminology={hit.terminology}.").strip()
    return fact.model_copy(update=updates) if updates else fact


def _should_replace(current: FeatureFact, hit: _Hit, chunks: list[Chunk]) -> bool:
    """Replace a status that treats an add-on, a variant, or an unstated cap as settled base cover."""
    if current.coverage_status == CoverageStatus.EXCLUDED and hit.status == CoverageStatus.ADD_ON:
        return True
    if hit.feature == "room_rent" and hit.limit:
        blob = f"{current.value or ''} {current.original_quote or ''} {current.limit or ''}".lower()
        if "at actual" not in blob and "up to si" not in blob and "up to sum insured" not in blob and "up to your base sum insured" not in blob:
            return True
    if current.coverage_status == CoverageStatus.COVERED and hit.status in {CoverageStatus.ADD_ON, CoverageStatus.CONDITIONAL}:
        if hit.is_add_on or hit.variant_scope:
            return True
        if _on_addon_page(current, chunks) or _on_addon_page_hit(hit, chunks):
            return True
    return False


def _on_addon_page(fact: FeatureFact, chunks: list[Chunk]) -> bool:
    pages = {src.page for src in fact.sources}
    return any(chunk.page_number in pages and _addon_marker(chunk) for chunk in chunks)


def _on_addon_page_hit(hit: _Hit, chunks: list[Chunk]) -> bool:
    pages = {chunk.page_number for chunk in hit.chunks}
    return any(chunk.page_number in pages and _addon_marker(chunk) for chunk in chunks)


def _addon_marker(chunk: Chunk) -> bool:
    blob = f"{chunk.section or ''} {chunk.source_text}".lower()
    return "add-ons to choose" in blob or "optional benefits" in blob or "optional benefit" in blob


def _quote_ok(hit: _Hit) -> bool:
    if not hit.quote or not hit.chunks:
        return False
    return any(hit.quote in chunk.source_text for chunk in hit.chunks)


def _hits(chunks: list[Chunk]) -> list[_Hit]:
    found: dict[str, _Hit] = {}

    def keep(hit: _Hit) -> None:
        if not _quote_ok(hit):
            return
        current = found.get(hit.feature)
        if current is None or hit.rank > current.rank:
            found[hit.feature] = hit

    addon_pages = {chunk.page_number for chunk in chunks if _addon_marker(chunk)}
    for chunk in chunks:
        if chunk.content_type.value == "marketing_stat":
            continue
        text = chunk.source_text
        low = text.lower()
        on_addon = chunk.page_number in addon_pages or chunk.content_type.value == "add_on" or _addon_marker(chunk)
        variant = "chosen ones" in f"{chunk.section or ''} {text}".lower()
        rank = 3 if chunk.content_type.value == "table_row" else 2 if chunk.content_type.value in {"add_on", "table"} else 1

        if "room rent" in low and "at actual" in low:
            quote = _window(text, "room rent")
            keep(_Hit("room_rent", CoverageStatus.COVERED, quote, quote, [chunk], limit="At actuals", terminology="Room rent at actuals", rank=rank + 2))
        elif "room rent" in low and ("up to si" in low or "up to sum insured" in low or "up to your base sum insured" in low or ("up to" in low and "base sum insured" in low)):
            quote = _window(text, "room rent")
            keep(_Hit("room_rent", CoverageStatus.COVERED, quote, quote, [chunk], limit="Up to sum insured", terminology="Room rent up to sum insured", rank=rank + 3))

        if "super reload" in low and "100%" in low:
            quote = _sentence(text, "super reload")
            keep(_Hit("restore_recharge", CoverageStatus.COVERED, quote, quote, [chunk], terminology="Super Reload", rank=rank))
        if "unlimited automatic recharge" in low:
            quote = _sentence(text, "unlimited automatic recharge")
            keep(_Hit("restore_recharge", CoverageStatus.COVERED, quote, quote, [chunk], terminology="Unlimited Automatic Recharge", rank=rank + 1))
        if "automatic restore" in low and "unlimited" in low:
            quote = _sentence(text, "automatic restore")
            keep(_Hit("restore_recharge", CoverageStatus.COVERED, quote, quote, [chunk], terminology="Automatic Restore Benefit", rank=rank + 1))
        if "reassure+" in low and "unlimited" in low:
            quote = _sentence(text, "reassure+")
            keep(_Hit("restore_recharge", CoverageStatus.COVERED, quote, quote, [chunk], terminology="ReAssure+", rank=rank + 1))

        if "super credit" in low and "sum insured" in low:
            quote = _sentence(text, "super credit")
            keep(_Hit("sum_insured_growth_bonus", CoverageStatus.COVERED, quote, quote, [chunk], terminology="Super Credit", rank=rank))
        if "infinite benefit" in low and "100%" in low:
            quote = _sentence(text, "infinite benefit")
            keep(_Hit("sum_insured_growth_bonus", CoverageStatus.COVERED, quote, quote, [chunk], terminology="Infinite Benefit", rank=rank))
        if "cumulative bonus" in low and "si" in low and not on_addon:
            quote = _sentence(text, "cumulative bonus")
            keep(_Hit("sum_insured_growth_bonus", CoverageStatus.COVERED, quote, quote, [chunk], terminology="Cumulative Bonus", rank=rank))
        if "booster+" in low and "base sum insured" in low and not on_addon:
            quote = _sentence(text, "booster+")
            keep(_Hit("sum_insured_growth_bonus", CoverageStatus.COVERED, quote, quote, [chunk], terminology="Booster+", rank=rank))

        if "day 1 cover" in low and "zero waiting" in low and "chronic" in low:
            quote = _sentence(text, "day 1")
            keep(_Hit(
                "chronic_conditions_day1", CoverageStatus.CONDITIONAL, quote, quote, [chunk],
                waiting_period="0 days", waiting_period_days=0, variant_scope="plan variant" if variant else None,
                terminology="Day 1 chronic cover, zero waiting period", rank=rank + 2,
            ))
        if "instant cover" in low and ("30 days" in low or "30 day" in low):
            quote = _sentence(text, "instant cover")
            keep(_Hit(
                "chronic_conditions_day1", CoverageStatus.ADD_ON, quote, quote, [chunk],
                is_add_on=True, waiting_period="30 days", waiting_period_days=30,
                terminology="Instant Cover after 30 days", rank=rank + 2,
            ))
        if "from the 31" in low and any(word in low for word in ("asthma", "diabetes", "cholesterol")):
            quote = _sentence(text, "from the 31")
            keep(_Hit(
                "chronic_conditions_day1", CoverageStatus.ADD_ON if on_addon else CoverageStatus.CONDITIONAL, quote, quote, [chunk],
                is_add_on=on_addon, waiting_period="from the 31st day", waiting_period_days=31,
                terminology="ABCD Chronic Care from the 31st day", rank=rank + 2,
            ))

        if "parenthood" in low and "maternity" in low:
            quote = _sentence(text, "parenthood")
            keep(_Hit(
                "maternity", CoverageStatus.ADD_ON, quote, quote, [chunk], is_add_on=True,
                exclusions=["maternity"], terminology="Parenthood add-on", rank=rank + 2,
            ))
        if "international" in low and "domestic maternity" in low:
            quote = _sentence(text, "maternity")
            keep(_Hit(
                "maternity", CoverageStatus.CONDITIONAL, quote, quote, [chunk],
                variant_scope="plan variant" if variant else None, terminology="International and domestic maternity", rank=rank,
            ))

        if "claim protect" in low and "non-medical" in low:
            quote = _sentence(text, "claim protect")
            keep(_Hit("non_medical_expenses_cover", CoverageStatus.COVERED, quote, quote, [chunk], terminology="Claim Protect", rank=rank))
        if "protect benefit" in low and ("non-medical" in low or "up to sum insured" in low) and "claim protect" not in low:
            quote = _sentence(text, "protect benefit")
            keep(_Hit("non_medical_expenses_cover", CoverageStatus.COVERED, quote, quote, [chunk], terminology="Protect Benefit", rank=rank + 1))
        if "claim shield" in low and "non-payable" in low:
            quote = _sentence(text, "claim shield")
            keep(_Hit("non_medical_expenses_cover", CoverageStatus.ADD_ON, quote, quote, [chunk], is_add_on=True, terminology="Claim Shield", rank=rank + 1))
        if "claim safeguard" in low and "non-payable" in low:
            quote = _sentence(text, "safeguard")
            keep(_Hit("non_medical_expenses_cover", CoverageStatus.ADD_ON, quote, quote, [chunk], is_add_on=True, terminology="Safeguard", rank=rank + 1))

        if "global cover" in low and "abroad" in low:
            quote = _sentence(text, "global cover")
            keep(_Hit(
                "global_cover", CoverageStatus.CONDITIONAL if variant else CoverageStatus.COVERED, quote, quote, [chunk],
                variant_scope="plan variant" if variant else None, terminology="Global Cover", rank=rank + 1,
            ))

        if "personal accident" in low and ("rider" in low or "optional" in low or on_addon or "1x" in low):
            quote = _sentence(text, "personal accident")
            keep(_Hit("personal_accident", CoverageStatus.ADD_ON, quote, quote, [chunk], is_add_on=True, terminology="Personal accident option", rank=rank + 1))

        if "care opd" in low or ("optima wellbeing" in low and "outpatient" in low):
            quote = _sentence(text, "opd" if "opd" in low else "wellbeing")
            keep(_Hit("teleconsultation_opd", CoverageStatus.ADD_ON, quote, quote, [chunk], is_add_on=True, terminology="OPD / wellbeing add-on", rank=rank))
        if "e-consultation" in low and "unlimited" in low and not on_addon:
            quote = _sentence(text, "e-consultation")
            keep(_Hit("teleconsultation_opd", CoverageStatus.COVERED, quote, quote, [chunk], terminology="Unlimited e-consultation", rank=rank + 1))

        if "healthreturns" in low and ("steps" in low or "earn" in low):
            quote = _sentence(text, "healthreturns")
            keep(_Hit("wellness_renewal_discount", CoverageStatus.COVERED, quote, quote, [chunk], terminology="HealthReturns", rank=rank))
        if "wellness benefit" in low and on_addon:
            quote = _sentence(text, "wellness")
            keep(_Hit("wellness_renewal_discount", CoverageStatus.ADD_ON, quote, quote, [chunk], is_add_on=True, terminology="Wellness Benefit", rank=rank))
        if "live healthy" in low and "discount" in low:
            quote = _sentence(text, "live healthy")
            keep(_Hit("wellness_renewal_discount", CoverageStatus.COVERED, quote, quote, [chunk], terminology="Live Healthy", rank=rank))

        if "no geography-based co-payment" in low or "without any co-payment" in low:
            quote = _sentence(text, "co-payment")
            keep(_Hit("copay", CoverageStatus.COVERED, quote, quote, [chunk], copay="0%", copay_percent=0, terminology="No geography-based co-payment", rank=rank + 2))
        if "co-payment of 20%" in low or "co-payment of 20 %" in low:
            quote = _sentence(text, "co-payment")
            keep(_Hit(
                "copay", CoverageStatus.ADD_ON if on_addon else CoverageStatus.CONDITIONAL, quote, quote, [chunk],
                is_add_on=on_addon, copay="20% outside the tiered network", copay_percent=20,
                terminology="Tiered network co-payment", rank=rank + 1,
            ))

        if "aggregate deductible" in low:
            needle = "aggregate deductible of" if "aggregate deductible of" in low else "aggregate deductible"
            quote = _window(text, needle)
            if not any(token in quote.lower() for token in ("25,000", "3 lakh", "22.5")):
                pass
            else:
                keep(_Hit(
                    "deductible_options", CoverageStatus.ADD_ON if on_addon or "discount" in quote.lower() else CoverageStatus.CONDITIONAL,
                    quote, quote, [chunk], is_add_on=True, deductible=quote,
                    terminology="Aggregate deductible options", rank=rank + 2,
                ))

        if chunk.content_type.value != "condition" and (
            "in-patient" in low or "in-patient care" in low or "hospitalisation expenses" in low or "hospitalization expenses" in low
        ) and ("sum insured" in low or "up to si" in low or "base sum insured" in low) and "may not cover" not in low:
            quote = _sentence(text, "hospital")
            keep(_Hit("in_patient_hospitalisation", CoverageStatus.COVERED, quote, quote, [chunk], terminology="In-patient hospitalisation", rank=rank))

        if "pre-hospital" in low and "60" in low:
            quote = _sentence(text, "pre-hospital")
            keep(_Hit("pre_post_hospitalisation", CoverageStatus.COVERED, quote, quote, [chunk], limit="60 days pre / stated post", terminology="Pre-hospitalisation 60 days", rank=rank))

    _pair_room_rent(chunks, keep)
    _pair_inpatient(chunks, keep)
    return list(found.values())


def _pair_room_rent(chunks: list[Chunk], keep) -> None:
    named = [chunk for chunk in chunks if "room rent" in chunk.source_text.lower() and chunk.content_type.value != "marketing_stat"]
    if any("at actual" in chunk.source_text.lower() or "up to si" in chunk.source_text.lower() or "base sum insured" in chunk.source_text.lower() and "room rent" in chunk.source_text.lower() for chunk in named):
        return
    limitless = [
        chunk for chunk in chunks
        if "no sub-limits" in chunk.source_text.lower() and "sum insured" in chunk.source_text.lower() and len(chunk.source_text) < 240
    ]
    if not named or not limitless:
        return
    quote = limitless[0].source_text.strip()
    keep(_Hit(
        "room_rent", CoverageStatus.COVERED, quote, quote, [named[0], limitless[0]],
        limit="Up to sum insured", terminology="No separate sub-limit; base benefits up to sum insured", rank=2,
    ))


def _pair_inpatient(chunks: list[Chunk], keep) -> None:
    named = [
        chunk for chunk in chunks
        if any(token in chunk.source_text.lower() for token in ("hospitalisation", "hospitalization expenses", "in-patient"))
        and "may not cover" not in chunk.source_text.lower()
        and "not paid" not in chunk.source_text.lower()
        and chunk.content_type.value != "marketing_stat"
    ]
    if not named:
        return
    if any("sum insured" in chunk.source_text.lower() or "up to si" in chunk.source_text.lower() for chunk in named):
        return
    limitless = [chunk for chunk in chunks if "every base benefit is covered up to sum insured" in chunk.source_text.lower() and len(chunk.source_text) < 240]
    if not limitless:
        return
    quote = _window(named[0].source_text, "hospital")
    if "not cover" in quote.lower():
        return
    keep(_Hit(
        "in_patient_hospitalisation", CoverageStatus.COVERED, quote, quote, [named[0]],
        limit="Up to sum insured", terminology="Hospitalisation named; base benefits up to sum insured", rank=1,
    ))


def _window(text: str, needle: str) -> str:
    low = text.lower()
    at = low.find(needle.lower())
    if at < 0:
        return text.strip()[:400]
    start = max(0, at - 40)
    end = min(len(text), at + len(needle) + 220)
    return text[start:end].strip()


def _sentence(text: str, needle: str) -> str:
    return _window(text, needle)
