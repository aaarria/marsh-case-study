"""Company Research agent: reasoning + external information.

FACT requires a web source; INFERENCE is derived from facts; ASSUMPTION is model/world knowledge
without a source; UNKNOWN when nothing confident can be said. Advisor-provided intake fields are
FACTS attributed to the advisor.
"""
from __future__ import annotations

import re
from typing import Literal
from urllib.parse import unquote

from pydantic import BaseModel, Field

from app.models.client import ClientFact, ClientIntake, CompanyProfile, FactKind, WebSource
from app.research.search import SearchUnavailable, WebSearch, get_search
from app.services.llm import LLMQuotaExceeded, LLMService, LLMUnavailable, get_llm
from app.utils.ids import stable_id
from app.utils.logging import get_logger

log = get_logger(__name__)

RESEARCH_SYSTEM = """You are a corporate research analyst supporting an insurance advisor.
You are given web-sourced snippets about a company, each with a source id [S#] and URL.
Produce a structured company profile for employee health-insurance advisory.
Rules:
- A statement supported by a snippet is kind=FACT and MUST list the supporting source ids.
- A statement you derive logically from facts is kind=INFERENCE (list the facts' source ids it builds on).
- A statement from general knowledge without a snippet is kind=ASSUMPTION (source_ids empty) and must say so plainly.
- If you cannot say anything confident about a field, emit one fact with kind=UNKNOWN and text "Unknown".
- Never invent numbers. Quote figures exactly as in snippets.
- Keep each fact to one sentence. Fields: overview, industry, size, geography, workforce, business, risk.
- key_risks are health/benefits-relevant workforce risks (e.g. shift work, field operations, ageing workforce), not generic business risks."""

NO_SEARCH_SYSTEM = """You are a corporate research analyst. No web search is available.
Provide a company profile from general knowledge ONLY where you are confident, and label EVERY statement kind=ASSUMPTION (source_ids empty).
If you are not confident about a field, emit kind=UNKNOWN with text "Unknown". Never invent numbers or dates."""


class FactOut(BaseModel):
    field: Literal["overview", "industry", "size", "geography", "workforce", "business", "risk"]
    text: str
    kind: Literal["FACT", "INFERENCE", "ASSUMPTION", "UNKNOWN"]
    source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class ProfileOut(BaseModel):
    overview: str | None
    industry: str | None
    size: str | None
    geography: str | None
    workforce: str | None
    business_characteristics: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    facts: list[FactOut] = Field(default_factory=list)


def _queries(intake: ClientIntake) -> list[str]:
    name = f'"{intake.company_name.strip()}"'  # quoted: a phrase, so engines do not answer about the first word only
    q = [
        f"{name} company overview industry headquarters",
        f"{name} number of employees workforce offices",
        f"{name} business operations India",
    ]
    if intake.industry:
        q[0] = f"{name} {intake.industry} company overview"
    return q


def _url_key(url: str) -> str:
    """Same page, same key: percent-decoding, scheme, "www." and a trailing slash do not make a new source."""
    return re.sub(r"^https?://(www\.)?", "", unquote(url)).rstrip("/").lower()


def _norm(v: object) -> str:
    """Comparison key for 'did the model just echo the advisor's input' (case, commas, whitespace)."""
    return str(v).strip().lower().replace(",", "").replace(" employees", "")


def _advisor_facts(intake: ClientIntake) -> list[ClientFact]:
    facts: list[ClientFact] = []
    src = WebSource(url="advisor://intake", title="Advisor intake form", accessible=True)
    if intake.industry:
        facts.append(ClientFact(fact_id=stable_id(intake.company_name, "adv_industry"), field="industry", text=f"Industry (advisor input): {intake.industry}", kind=FactKind.FACT, sources=[src], confidence=0.9))
    if intake.geography:
        facts.append(ClientFact(fact_id=stable_id(intake.company_name, "adv_geo"), field="geography", text=f"Geography (advisor input): {intake.geography}", kind=FactKind.FACT, sources=[src], confidence=0.9))
    if intake.employee_count:
        facts.append(ClientFact(fact_id=stable_id(intake.company_name, "adv_size"), field="size", text=f"Employee count (advisor input): {intake.employee_count:,}", kind=FactKind.FACT, sources=[src], confidence=0.9))
    if intake.advisor_notes:
        facts.append(ClientFact(fact_id=stable_id(intake.company_name, "adv_notes"), field="business", text=f"Advisor notes: {intake.advisor_notes.strip()}", kind=FactKind.FACT, sources=[src], confidence=0.8))
    return facts


def _offline_profile(profile: CompanyProfile, facts: list[ClientFact], intake: ClientIntake, note: str) -> CompanyProfile:
    """Advisor-only profile used when no LLM can be called. Every non-advisor field is explicitly UNKNOWN."""
    name = profile.company_name
    profile.research_status = "UNAVAILABLE"
    profile.research_note = note
    profile.industry = intake.industry
    profile.geography = intake.geography
    profile.size = f"{intake.employee_count:,} employees" if intake.employee_count else None
    for fld in ("overview", "workforce", "business", "risk"):
        facts.append(ClientFact(fact_id=stable_id(name, fld, "unknown"), field=fld, text="Unknown", kind=FactKind.UNKNOWN))
    profile.facts = facts
    return profile


def research_company(intake: ClientIntake, llm: LLMService | None = None, search: WebSearch | None = None) -> CompanyProfile:
    llm = llm or get_llm()
    search = search or get_search()
    name = intake.company_name.strip()
    sources: list[WebSource] = []
    status, note = "OK", None

    if search.available:
        cached = search.cached(name)
        if cached:
            sources = cached
            note = f"Web sources re-used from this machine's research cache (fetched {(cached[0].retrieved_at or '')[:16].replace('T', ' ')} UTC; refreshed after 24 h)."
        else:
            sources = search.reference_sources(name)  # Wikipedia + official site: already read, engine-independent
            engine_error: SearchUnavailable | None = None
            for q in _queries(intake):
                try:
                    sources.extend(search.search(q, max_results=4, about=name))
                except SearchUnavailable as exc:
                    log.warning("Search failed: %s", exc)
                    engine_error = exc
                    break  # every engine refused: they are rate-limiting us, do not hammer them with the other queries
            # de-duplicate by URL, keeping order (reference sources, then the engine's ranking), then read the pages
            seen: set[str] = set()
            sources = [s for s in sources if s.url and not (_url_key(s.url) in seen or seen.add(_url_key(s.url)))][:10]
            if not sources:
                status, note = "PARTIAL", f"No web source found: no Wikipedia article for this name and {engine_error or 'no search results'}. Profile relies on assumptions; retry later or add details on the intake form."
            else:
                sources = search.read(sources, name)
                if engine_error:
                    status, note = "PARTIAL", f"Search engines did not answer this request ({engine_error}); profile built from Wikipedia and the company website only. Retry later for broader coverage."
                elif not any(s.accessible for s in sources):
                    status, note = "PARTIAL", "No result page could be read; facts rely on search-result snippets only."
                search.remember(name, sources)
    else:
        status, note = "PARTIAL", "Web research is off (WEB_RESEARCH_ENABLED=false); company statements are assumptions, not verified facts."

    profile = CompanyProfile(company_name=name, research_status=status, research_note=note)
    facts: list[ClientFact] = _advisor_facts(intake)

    if not llm.available:
        return _offline_profile(profile, facts, intake, "LLM not configured: only advisor-provided inputs are used; all other fields are UNKNOWN.")

    src_block = "\n\n".join(
        f"[S{i+1}] {s.title or ''} ({s.url}{', ' + s.published_date if s.published_date else ''})"
        f"{'' if s.accessible else ' [search-result snippet only; page not read]'}\n{s.snippet or ''}"
        for i, s in enumerate(sources)
    )
    intake_block = "\n".join(
        f"- {k}: {v}" for k, v in {"industry": intake.industry, "geography": intake.geography, "employee_count": intake.employee_count, "advisor_notes": intake.advisor_notes, "priorities": ", ".join(intake.client_priorities) or None}.items() if v
    )
    user = f"COMPANY: {name}\nADVISOR INPUTS (treat as facts from the advisor):\n{intake_block or '- none'}\n\nSEARCH SNIPPETS:\n{src_block or '(none)'}"
    try:
        out = llm.structured(RESEARCH_SYSTEM if sources else NO_SEARCH_SYSTEM, user, ProfileOut, purpose="company_research")
    except LLMUnavailable as exc:
        # Do not build another client here: fall straight back to the advisor-only profile.
        return _offline_profile(profile, facts, intake, f"LLM unavailable ({exc}): only advisor-provided inputs are used; all other fields are UNKNOWN.")
    except LLMQuotaExceeded:
        raise  # stop the run with a clear quota error rather than labelling everything UNKNOWN
    except Exception as exc:
        log.error("Company research LLM failed: %s", exc)
        profile.research_status = "UNAVAILABLE"
        profile.research_note = f"Research failed: {exc}"
        profile.facts = facts
        return profile

    by_id = {f"S{i+1}": s for i, s in enumerate(sources)}
    advisor_fields = {f.field for f in facts}
    advisor_values = {_norm(v) for v in (intake.industry, intake.geography, intake.employee_count) if v}
    for i, f in enumerate(out.facts):
        srcs = [by_id[s] for s in f.source_ids if s in by_id]
        kind = FactKind(f.kind)
        if kind == FactKind.FACT and not srcs:
            kind = FactKind.ASSUMPTION  # a FACT without a source is not a fact
        # The advisor already answered this: an "Unknown" on their field, or a restatement of their input, adds nothing.
        if (f.field in advisor_fields and kind == FactKind.UNKNOWN) or _norm(f.text) in advisor_values:
            continue
        facts.append(ClientFact(fact_id=stable_id(name, f.field, f.text, str(i)), field=f.field, text=f.text.strip(), kind=kind, sources=srcs, confidence=f.confidence))

    profile.overview = out.overview
    profile.industry = intake.industry or out.industry
    profile.size = (f"{intake.employee_count:,} employees (advisor input)" if intake.employee_count else out.size)
    profile.geography = intake.geography or out.geography
    profile.workforce = out.workforce
    profile.business_characteristics = out.business_characteristics
    profile.key_risks = out.key_risks
    profile.facts = facts
    return profile
