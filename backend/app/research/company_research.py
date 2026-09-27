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
from app.research.search import SearchUnavailable, WebSearch, get_search, mentions
from app.services.llm import LLMQuotaExceeded, LLMService, LLMUnavailable, get_llm
from app.utils.ids import stable_id
from app.utils.logging import get_logger

log = get_logger(__name__)

RESEARCH_DISABLED = "RESEARCH_DISABLED"
RESEARCH_COMPLETE = "RESEARCH_COMPLETE"
RESEARCH_PARTIAL = "RESEARCH_PARTIAL"
NO_VERIFIED_SOURCE = "NO_VERIFIED_SOURCE"
RESEARCH_FAILED = "RESEARCH_FAILED"

DISABLED_NOTE = "Web research is off (WEB_RESEARCH_ENABLED=false); company statements are assumptions, not verified facts."
EXHAUSTED_NOTE = "Web research was attempted, but no sufficiently reliable public source was found for this company."
PARTIAL_NOTE = "Research partially completed. Public sources were found, but they do not verify the full company profile."
COMPLETE_NOTE = "Company research complete."

RESEARCH_SYSTEM = """You are a corporate research analyst supporting an insurance advisor.
You are given web-sourced snippets about a company, each with a source id [S#] and URL.
Produce a structured company profile for employee health-insurance advisory.
Rules:
- A statement supported by a snippet is kind=FACT and MUST list the supporting source ids.
- A statement you derive logically from facts is kind=INFERENCE (list the facts' source ids it builds on).
- Do not use general knowledge, the company name, or industry stereotypes to fill industry, size, geography, revenue, or business model.
- If a field is not supported by a snippet or by advisor input, emit kind=UNKNOWN and text "Unknown". Do not guess a number.
- If snippets describe different organisations, do not merge them. Use only the company in the request.
- Keep each fact to one sentence. Fields: overview, industry, size, geography, workforce, business, risk.
- key_risks are health/benefits-relevant workforce risks supported by the snippets, not generic business risks."""

# Used only when snippets exist but none are readable enough to cite. Still forbids prior-knowledge facts.
NO_SEARCH_SYSTEM = """You are a corporate research analyst. No reliable public source was retrieved for this company.
Do not use general knowledge, the company name, or industry stereotypes.
Emit kind=UNKNOWN and text "Unknown" for every field that advisor input does not already answer.
Do not invent industry, employee count, geography, revenue, or business model. Never invent numbers or dates."""

_LEGAL_SWAPS = (
    (re.compile(r"\bprivate\s+limited$", re.I), "Pvt Ltd"),
    (re.compile(r"\bpvt\.?\s*ltd\.?$", re.I), "Private Limited"),
    (re.compile(r"\blimited$", re.I), "Ltd"),
    (re.compile(r"\bltd\.?$", re.I), "Limited"),
)
_INDUSTRY_HINTS = (
    "software", "manufactur", "hospital", "logistics", "retail", "bank", "textile", "restaurant",
    "construction", "pharma", "education", "consulting", "steel", "hotel", "food",
)
_KIND_WORDS = frozenset({
    "bank", "university", "hospital", "cafe", "airline", "steel", "software", "insurance", "school",
    "college", "restaurant", "records", "motors", "pharma", "textile", "logistics", "hotel", "foundry", "cement",
})
_LOW_HOSTS = ("facebook.com", "instagram.com", "youtube.com", "quikr.com", "twitter.com", "x.com", "pinterest.com", "linkedin.com")
_PROFILE_FIELDS = ("overview", "industry", "size", "geography", "workforce", "business", "risk")


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


def alternate_name(name: str) -> str | None:
    """One legal-form or punctuation variant. A one-word residue of a longer name is not searched on its own."""
    raw = re.sub(r"\s+", " ", name.strip())
    for pattern, replacement in _LEGAL_SWAPS:
        if pattern.search(raw):
            base = pattern.sub("", raw).strip(" ,.-")
            if base:
                return f"{base} {replacement}".strip()
    if "&" in raw:
        return re.sub(r"\s*&\s*", " and ", raw)
    return None


def initial_queries(intake: ClientIntake) -> list[str]:
    """At most three queries. The exact name is always first. Wikipedia is not one of them."""
    exact = intake.company_name.strip()
    quoted = f'"{exact}"'
    country = (intake.geography or "India").strip()
    variant = alternate_name(exact)
    second = f'"{variant}" official website' if variant and variant.lower() != exact.lower() else f"{quoted} official website"
    queries = [f"{quoted} company", second, f"{quoted} about {country}"]
    out: list[str] = []
    for query in queries:
        if query not in out:
            out.append(query)
    return out[:3]


def followup_query(intake: ClientIntake, industry_hint: str | None) -> str:
    """Fourth query only: an industry discovered in earlier snippets, else a company-profile search."""
    quoted = f'"{intake.company_name.strip()}"'
    if industry_hint:
        return f"{quoted} {industry_hint}"
    if intake.industry:
        return f"{quoted} {intake.industry.strip()}"
    return f"{quoted} LinkedIn company"


def _queries(intake: ClientIntake) -> list[str]:
    """Bounded plan before any snippet is known. The follow-up query is added only if recall is still thin."""
    return initial_queries(intake) + [followup_query(intake, None)]


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


def _usable(sources: list[WebSource]) -> list[WebSource]:
    return [s for s in sources if (s.snippet or "").strip()]


def _fill_unknowns(name: str, facts: list[ClientFact]) -> list[ClientFact]:
    covered = {f.field for f in facts}
    for field in _PROFILE_FIELDS:
        if field not in covered:
            facts.append(ClientFact(fact_id=stable_id(name, field, "unknown"), field=field, text="Unknown", kind=FactKind.UNKNOWN))
    return facts


def _advisor_profile(name: str, intake: ClientIntake, status: str, note: str) -> CompanyProfile:
    """Advisor inputs plus explicit unknowns. Nothing is filled in from the company name."""
    facts = _fill_unknowns(name, _advisor_facts(intake))
    return CompanyProfile(
        company_name=name,
        industry=intake.industry,
        geography=intake.geography,
        size=f"{intake.employee_count:,} employees (advisor input)" if intake.employee_count else None,
        research_status=status,
        research_note=note,
        facts=facts,
    )


def _provider_note(failures: list[str]) -> str:
    text = " ".join(failures).lower()
    bits: list[str] = []
    if "429" in text:
        bits.append("a search provider was rate-limited")
    if "202" in text:
        bits.append("a search provider did not return a results page")
    if not bits:
        return ""
    return "Provider limits: " + "; ".join(bits) + "."


def _status_note(usable: list[WebSource], failures: list[str], ambiguity: str | None, web_facts: list[ClientFact]) -> tuple[str, str]:
    extra = " ".join(part for part in (_provider_note(failures), ambiguity) if part)
    if web_facts:
        note = COMPLETE_NOTE
        status = RESEARCH_COMPLETE
    elif usable:
        note = PARTIAL_NOTE
        status = RESEARCH_PARTIAL
    else:
        note = EXHAUSTED_NOTE
        status = NO_VERIFIED_SOURCE
    if extra:
        note = f"{note} {extra}"
    return status, note


def _industry_hint(sources: list[WebSource], company: str) -> str | None:
    for source in sources:
        text = f"{source.title or ''} {source.snippet or ''}"
        if not mentions(text, company):
            continue
        low = text.lower()
        for hint in _INDUSTRY_HINTS:
            if hint in low:
                return hint
    return None


def _source_rank(source: WebSource) -> tuple[int, str]:
    host = source.url.lower()
    if "wikipedia.org" in host:
        band = 0
    elif any(host.find(item) != -1 for item in _LOW_HOSTS):
        band = 2
    else:
        band = 1
    return (band, source.url)


def conflicting_sources(sources: list[WebSource], company: str) -> tuple[list[WebSource], str | None]:
    """Drop sources that name a different kind of organisation. A tie keeps none of them."""
    grouped: dict[frozenset[str], list[WebSource]] = {}
    for source in sources:
        words = set(re.findall(r"[a-z0-9]+", (source.title or "").lower()))
        grouped.setdefault(frozenset(words & _KIND_WORDS), []).append(source)
    labeled = [key for key in grouped if key]
    if len(labeled) < 2:
        return sources, None
    shared = labeled[0]
    for key in labeled[1:]:
        shared = shared & key
    if shared:
        return sources, None
    ranked = sorted(labeled, key=lambda key: (-len(grouped[key]), sorted(key)[0]))
    if len(grouped[ranked[0]]) == len(grouped[ranked[1]]):
        kinds = ", ".join(sorted(set().union(*labeled)))
        return [], f"The name matches more than one organisation ({kinds}). Their facts were not merged."
    dropped = ", ".join(sorted(set().union(*ranked[1:])))
    kept = list(grouped[ranked[0]]) + list(grouped.get(frozenset(), []))
    return kept, f"The name also matches a different organisation ({dropped}). Those sources were not merged."


def _run_query(search: WebSearch, query: str, company: str, skip: set[str]) -> tuple[list[WebSource], list[str]]:
    providers = getattr(search, "search_providers", None)
    if providers is not None:
        report = providers(query, max_results=6, about=company, skip=skip)
        return list(report.sources), list(report.failures)
    try:
        return list(search.search(query, max_results=6, about=company)), []
    except SearchUnavailable as exc:
        log.info("Search query failed, continuing: %s", exc)
        return [], [str(exc)]


def _cache_note(sources: list[WebSource]) -> str:
    stamp = (sources[0].retrieved_at or "")[:16].replace("T", " ")
    if not stamp:
        return "Web sources re-used from this machine's research cache (refreshed after 24 h)."
    return f"Web sources re-used from this machine's research cache (fetched {stamp} UTC; refreshed after 24 h)."


def _collect(search: WebSearch, intake: ClientIntake) -> tuple[list[WebSource], list[str], str | None, bool]:
    """Wikipedia, then bounded engine queries. A miss or a provider error never aborts the rest."""
    name = intake.company_name.strip()
    cached = search.cached(name)
    if cached:
        kept, ambiguity = conflicting_sources(cached, name)
        if kept:
            return kept, [], ambiguity, True
    sources = list(search.reference_sources(name) or [])
    failures: list[str] = []
    skip: set[str] = set()
    for query in initial_queries(intake):
        found, failed = _run_query(search, query, name, skip)
        sources.extend(found)
        failures.extend(failed)
        # The first engine query always runs, because the check is after it. Later variants run only while recall is thin.
        if len(_usable(sources)) >= 3:
            break
    if len(_usable(sources)) < 3:
        hint = intake.industry or _industry_hint(sources, name)
        found, failed = _run_query(search, followup_query(intake, hint), name, skip)
        sources.extend(found)
        failures.extend(failed)
    if len(_usable(sources)) < 3:
        discover = getattr(search, "discover_named_site", None)
        if discover is not None:
            try:
                sources.extend(discover(name) or [])
            except Exception as exc:
                log.info("Domain discovery failed, continuing: %s", exc)
                failures.append(f"domain: {exc}")
    seen: set[str] = set()
    sources = [s for s in sources if s.url and not (_url_key(s.url) in seen or seen.add(_url_key(s.url)))]
    sources = sorted(sources, key=_source_rank)[:10]
    sources = search.read(sources, name)
    sources, ambiguity = conflicting_sources(sources, name)
    if _usable(sources):
        search.remember(name, sources)
    return sources, failures, ambiguity, False


def _web_facts(facts: list[ClientFact]) -> list[ClientFact]:
    return [f for f in facts if f.kind == FactKind.FACT and f.sources and not f.sources[0].url.startswith("advisor://")]


def _supported_text(facts: list[ClientFact], field: str) -> str | None:
    for fact in facts:
        if fact.field == field and fact.kind in {FactKind.FACT, FactKind.INFERENCE} and fact.text.strip().lower() != "unknown":
            return fact.text.strip()
    return None


def research_company(intake: ClientIntake, llm: LLMService | None = None, search: WebSearch | None = None) -> CompanyProfile:
    llm = llm or get_llm()
    search = search or get_search()
    name = intake.company_name.strip()

    if not search.available:
        return _advisor_profile(name, intake, RESEARCH_DISABLED, DISABLED_NOTE)

    try:
        sources, failures, ambiguity, from_cache = _collect(search, intake)
    except Exception as exc:
        log.error("Company research failed before search finished: %s", exc)
        return _advisor_profile(name, intake, RESEARCH_FAILED, "Company research could not be completed.")

    usable = _usable(sources)
    cache_prefix = _cache_note(sources) + " " if from_cache and usable else ""

    if not usable or not llm.available:
        status, note = _status_note(usable, failures, ambiguity, [])
        if not llm.available and usable:
            status = RESEARCH_PARTIAL
            note = "Sources were found but the language model is unavailable, so unverified company fields stay unknown. " + note
        elif not llm.available and not usable:
            note = note if status == NO_VERIFIED_SOURCE else note
            if status == NO_VERIFIED_SOURCE:
                note = EXHAUSTED_NOTE + " The language model is also unavailable, so nothing was inferred from the name."
                extra = " ".join(part for part in (_provider_note(failures), ambiguity) if part)
                if extra:
                    note = f"{note} {extra}"
        return _advisor_profile(name, intake, status, (cache_prefix + note).strip())

    src_block = "\n\n".join(
        f"[S{i+1}] {s.title or ''} ({s.url}{', ' + s.published_date if s.published_date else ''})"
        f"{'' if s.accessible else ' [search-result snippet only; page not read]'}\n{s.snippet or ''}"
        for i, s in enumerate(usable)
    )
    intake_block = "\n".join(
        f"- {k}: {v}" for k, v in {"industry": intake.industry, "geography": intake.geography, "employee_count": intake.employee_count, "advisor_notes": intake.advisor_notes, "priorities": ", ".join(intake.client_priorities) or None}.items() if v
    )
    caution = f"\nAMBIGUITY: {ambiguity}\n" if ambiguity else ""
    user = f"COMPANY: {name}\nADVISOR INPUTS (treat as facts from the advisor):\n{intake_block or '- none'}\n{caution}\nSEARCH SNIPPETS:\n{src_block}"
    try:
        out = llm.structured(RESEARCH_SYSTEM, user, ProfileOut, purpose="company_research")
    except LLMUnavailable as exc:
        return _advisor_profile(name, intake, RESEARCH_PARTIAL if usable else NO_VERIFIED_SOURCE, f"Sources were retrieved but the language model is unavailable ({exc}). Unverified fields stay unknown.")
    except LLMQuotaExceeded:
        raise
    except Exception as exc:
        log.error("Company research LLM failed: %s", exc)
        return _advisor_profile(name, intake, RESEARCH_PARTIAL, f"{PARTIAL_NOTE} The profile could not be written from the sources.")

    facts: list[ClientFact] = _advisor_facts(intake)
    by_id = {f"S{i+1}": s for i, s in enumerate(usable)}
    advisor_fields = {f.field for f in facts}
    advisor_values = {_norm(v) for v in (intake.industry, intake.geography, intake.employee_count) if v}
    for i, fact in enumerate(out.facts):
        srcs = [by_id[s] for s in fact.source_ids if s in by_id]
        kind = FactKind(fact.kind)
        if kind in {FactKind.FACT, FactKind.INFERENCE} and not srcs:
            continue  # prior knowledge is not a company fact, and a figure without a source is not kept
        if kind == FactKind.ASSUMPTION:
            continue
        if (fact.field in advisor_fields and kind == FactKind.UNKNOWN) or _norm(fact.text) in advisor_values:
            continue
        facts.append(ClientFact(fact_id=stable_id(name, fact.field, fact.text, str(i)), field=fact.field, text=fact.text.strip(), kind=kind, sources=srcs, confidence=fact.confidence))
    facts = _fill_unknowns(name, facts)
    web = _web_facts(facts)
    status, note = _status_note(usable, failures, ambiguity, web)
    return CompanyProfile(
        company_name=name,
        overview=out.overview if web else _supported_text(facts, "overview"),
        industry=intake.industry or _supported_text(facts, "industry"),
        size=(f"{intake.employee_count:,} employees (advisor input)" if intake.employee_count else _supported_text(facts, "size")),
        geography=intake.geography or _supported_text(facts, "geography"),
        workforce=_supported_text(facts, "workforce") if web else None,
        business_characteristics=out.business_characteristics if web else [],
        key_risks=out.key_risks if web else [],
        facts=facts,
        research_status=status,
        research_note=(cache_prefix + note).strip(),
    )
