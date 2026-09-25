"""Deterministic parsing of Indian insurance numerals.

Handles: INR / Rs / ₹ / ` (rupee glyph in these brochures), lakh/lac/L, crore/Cr, Indian digit
grouping (5,00,000), percentages, durations (days/months/years), multipliers (2X), and
"unlimited"/"up to sum insured" markers. Used by the numerical auditor and comparison engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_RUPEE_TOKENS = r"(?:INR|Rs\.?|₹|`|Rupees)"
_NUM = r"\d[\d,]*(?:\.\d+)?"

MONEY_RE = re.compile(
    rf"(?:{_RUPEE_TOKENS}\s*)?({_NUM})\s*(lakhs?|lacs?|lakh|lac|L|crores?|cr\.?|Cr)?(?!\s*%)",
    re.IGNORECASE,
)
PERCENT_RE = re.compile(rf"({_NUM})\s*%")
DURATION_RE = re.compile(rf"({_NUM})\s*(days?|months?|years?|yrs?|hrs?|hours?)", re.IGNORECASE)
MULTIPLIER_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[xX×](?![A-Za-z])")
UNLIMITED_RE = re.compile(r"\b(unlimited|infinite|no (?:sub-)?limits?|no capping|at actuals)\b", re.IGNORECASE)
# "up to (the|your|full|base|opted|chosen) sum insured / SI": brochures vary the determiner and qualifier.
UPTO_SI_RE = re.compile(r"up ?to (?:(?:the|your|full|base|opted|chosen) ){0,3}(?:sum insured|SI\b)|100% of (?:(?:the|your|base) ){0,2}(?:sum insured|SI\b)", re.IGNORECASE)


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


def _scale(unit: str | None) -> float:
    if not unit:
        return 1.0
    u = unit.lower().rstrip(".")
    if u in {"lakh", "lakhs", "lac", "lacs", "l"}:
        return 100_000.0
    if u in {"crore", "crores", "cr"}:
        return 10_000_000.0
    return 1.0


@dataclass
class ExtractedNumbers:
    money: list[float] = field(default_factory=list)  # in INR
    percents: list[float] = field(default_factory=list)
    durations: list[tuple[float, str]] = field(default_factory=list)  # (value, unit normalized)
    multipliers: list[float] = field(default_factory=list)
    unlimited: bool = False
    up_to_sum_insured: bool = False


def _norm_unit(u: str) -> str:
    u = u.lower()
    if u.startswith("day"):
        return "day"
    if u.startswith("month"):
        return "month"
    if u.startswith("y"):
        return "year"
    if u.startswith("h"):
        return "hour"
    return u


def extract_numbers(text: str) -> ExtractedNumbers:
    out = ExtractedNumbers()
    if not text:
        return out
    t = text.replace("\u20b9", "INR ")
    # percents first, then mask them so money regex doesn't pick them up
    for m in PERCENT_RE.finditer(t):
        out.percents.append(_to_float(m.group(1)))
    masked = PERCENT_RE.sub(" ", t)
    for m in DURATION_RE.finditer(masked):
        out.durations.append((_to_float(m.group(1)), _norm_unit(m.group(2))))
    masked = DURATION_RE.sub(" ", masked)
    for m in MULTIPLIER_RE.finditer(masked):
        out.multipliers.append(float(m.group(1)))
    masked = MULTIPLIER_RE.sub(" ", masked)
    for m in MONEY_RE.finditer(masked):
        raw, unit = m.group(1), m.group(2)
        # Only treat as money if there's a currency marker, a scale unit, or Indian grouping / >= 4 digits
        span_start = max(0, m.start() - 12)
        prefix = masked[span_start : m.start()]
        has_currency = re.search(_RUPEE_TOKENS + r"\s*$", prefix, re.IGNORECASE) is not None or m.group(0).strip().upper().startswith(("INR", "RS", "₹", "`"))
        if unit or has_currency or "," in raw or (raw.isdigit() and len(raw) >= 4):
            # Avoid treating UIN / years / page numbers as money when no unit or currency
            if not unit and not has_currency and "," not in raw:
                continue
            out.money.append(round(_to_float(raw) * _scale(unit), 2))
    out.unlimited = UNLIMITED_RE.search(t) is not None
    out.up_to_sum_insured = UPTO_SI_RE.search(t) is not None
    return out


def format_inr(value: float) -> str:
    """Human-readable INR (lakh/crore aware)."""
    if value >= 10_000_000:
        v = value / 10_000_000
        return f"INR {v:g} crore"
    if value >= 100_000:
        v = value / 100_000
        return f"INR {v:g} lakh"
    return f"INR {value:,.0f}"


def durations_to_days(durations: list[tuple[float, str]]) -> list[float]:
    factors = {"day": 1, "month": 30, "year": 365, "hour": 1 / 24}
    return [v * factors.get(u, 1) for v, u in durations]
