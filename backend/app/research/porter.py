"""Porter context for the company. It can suggest exposure hypotheses. It cannot score a policy."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.client import CompanyProfile
from app.services.llm import LLMService, LLMUnavailable, get_llm
from app.utils.logging import get_logger

log = get_logger(__name__)

PORTER_SYSTEM = """You summarise industry context for an insurance advisor using Porter's Five Forces.
Use only the company profile text supplied. Do not mention insurance products, premiums, or a preferred insurer.
Label each force VERIFIED only when the profile cites a source. Otherwise ASSUMED or UNKNOWN.
Do not invent numbers. This output is context, not a buying recommendation."""


class PorterForce(BaseModel):
    force: Literal["competitive_intensity", "supplier_dependency", "buyer_dynamics", "substitutes", "entry_barriers"]
    assessment: str
    status: Literal["VERIFIED", "ASSUMED", "UNKNOWN"] = "UNKNOWN"
    sources: list[str] = Field(default_factory=list)


class MarketContext(BaseModel):
    industry: str | None = None
    forces: list[PorterForce] = Field(default_factory=list)
    industry_context: str = ""
    status: str = "LIMITED"  # OK | LIMITED | UNAVAILABLE
    note: str = ""
    hypotheses: list[str] = Field(default_factory=list)


class _PorterOut(BaseModel):
    industry_context: str
    forces: list[PorterForce]
    hypotheses: list[str] = Field(default_factory=list)


_FORCES = (
    "competitive_intensity",
    "supplier_dependency",
    "buyer_dynamics",
    "substitutes",
    "entry_barriers",
)


def limited_context(profile: CompanyProfile, note: str) -> MarketContext:
    industry = profile.industry or "Unknown"
    return MarketContext(
        industry=industry,
        forces=[PorterForce(force=name, assessment="Unknown", status="UNKNOWN") for name in _FORCES],
        industry_context="Unknown",
        status="LIMITED",
        note=note,
        hypotheses=[],
    )


def analyse_market(profile: CompanyProfile, llm: LLMService | None = None) -> MarketContext:
    """One structured pass. Failure stays LIMITED/UNKNOWN and does not invent industry facts."""
    llm = llm or get_llm()
    if not llm.available:
        return limited_context(profile, "Market context unavailable: no language model. Forces are UNKNOWN.")
    user = "\n".join(
        [
            f"Company: {profile.company_name}",
            f"Industry: {profile.industry or 'Unknown'}",
            f"Size: {profile.size or 'Unknown'}",
            f"Geography: {profile.geography or 'Unknown'}",
            f"Overview: {profile.overview or 'Unknown'}",
            "Profile facts:",
            *[f"- [{fact.kind.value}] {fact.text}" for fact in profile.facts[:12]],
        ]
    )
    try:
        out = llm.structured(PORTER_SYSTEM, user, _PorterOut, purpose="market_context")
    except LLMUnavailable as exc:
        return limited_context(profile, f"Market context unavailable: {exc}")
    except Exception as exc:
        log.warning("Porter pass failed: %s", exc)
        return limited_context(profile, f"Market context failed: {exc}. Forces are UNKNOWN.")
    forces = list(out.forces) or [PorterForce(force=name, assessment="Unknown", status="UNKNOWN") for name in _FORCES]
    return MarketContext(
        industry=profile.industry,
        forces=forces,
        industry_context=out.industry_context or "Unknown",
        status="OK",
        note="Porter context is industry interpretation. It is not a policy score.",
        hypotheses=[item.strip() for item in out.hypotheses if item.strip()][:6],
    )
