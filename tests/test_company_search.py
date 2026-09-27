"""Company research must search every name. One provider failing is not a skipped search."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.graph.nodes import context_prompt
from app.models.client import ClientFact, ClientIntake, CompanyProfile, FactKind, WebSource
from app.research.company_research import (
    FactOut,
    ProfileOut,
    alternate_name,
    conflicting_sources,
    followup_query,
    initial_queries,
    research_company,
)
from app.research.search import RETRY_BACKOFF_SECONDS, SearchReport, SearchUnavailable, WebSearch
from app.services.llm import LLMService


def _html(text, status=200):
    return httpx.Response(status, text=text, headers={"content-type": "text/html; charset=utf-8"})


DDG_PAGE = """<div class="result results_links results_links_deep web-result">
<div class="links_main"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Facme&amp;rut=x">Acme Ltd</a>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">Acme Ltd employs people in Pune.</a></div>
</div>"""

BING_PAGE = """<ol id="b_results">
<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&amp;p=x&amp;u=a1aHR0cHM6Ly9hY21lLmV4YW1wbGUvYWJvdXQ&amp;ntb=1">Acme Ltd about</a></h2><div><p>Acme Ltd is a private manufacturer.</p></div></li>
</ol>"""


class _Search:
    def __init__(self, sources=None, wiki=None, failures=None, cached_sources=None):
        self.available = True
        self.sources = list(sources or [])
        self.wiki = list(wiki or [])
        self.failures = list(failures or [])
        self.cached_sources = cached_sources
        self.queries: list[str] = []
        self.remembered = None

    def cached(self, company):
        return self.cached_sources

    def reference_sources(self, company):
        return list(self.wiki)

    def search_providers(self, query, max_results=5, about=None, skip=None):
        self.queries.append(query)
        return SearchReport(sources=list(self.sources), failures=list(self.failures))

    def read(self, sources, company):
        return sources

    def remember(self, company, sources):
        self.remembered = sources


def _profile(**kwargs):
    def handler(system, user, schema):
        handler.user = user
        return ProfileOut(overview="o", industry=None, size=None, geography=None, workforce=None, facts=kwargs.get("facts", []))

    handler.user = ""
    return LLMService(mock_handler=handler), handler


def test_name_variants_are_bounded_and_do_not_search_a_one_word_residue():
    assert alternate_name("ABC Pvt Ltd") == "ABC Private Limited"
    assert alternate_name("ABC Private Limited") == "ABC Pvt Ltd"
    assert alternate_name("ABC Ltd") == "ABC Limited"
    assert alternate_name("Smith & Sons") == "Smith and Sons"
    intake = ClientIntake(company_name="SmallTech Solutions Pvt Ltd")
    queries = initial_queries(intake)
    assert len(queries) <= 3
    assert any("SmallTech Solutions Pvt Ltd" in q for q in queries)
    assert any("Private Limited" in q for q in queries)
    assert all(q.strip() != '"SmallTech Solutions"' for q in queries)
    assert "LinkedIn" in followup_query(intake, None)
    assert "software" in followup_query(intake, "software")


def test_large_company_with_wikipedia_still_queries_a_search_engine():
    llm, _handler = _profile(facts=[FactOut(field="industry", text="Infosys is an IT services company.", kind="FACT", source_ids=["S1"], confidence=0.9)])
    wiki = [WebSource(url="https://en.wikipedia.org/wiki/Example_Co", title="Example Co - Wikipedia", snippet="Example Co is an IT services company headquartered in Bengaluru.", accessible=True)]
    search = _Search(wiki=wiki, sources=[WebSource(url="https://example.co/about", title="Example Co", snippet="Example Co employs 300,000 people.", accessible=True)])
    profile = research_company(ClientIntake(company_name="Example Co"), llm=llm, search=search)
    assert search.queries, "Wikipedia must not replace the search-engine query"
    assert profile.research_status == "RESEARCH_COMPLETE"
    assert any(f.kind == FactKind.FACT and f.sources for f in profile.facts)


def test_company_without_wikipedia_is_still_searched():
    llm, _handler = _profile(facts=[FactOut(field="overview", text="Northwind Labs builds software in Pune.", kind="FACT", source_ids=["S1"], confidence=0.8)])
    page = WebSource(url="https://northwind.example/about", title="Northwind Labs", snippet="Northwind Labs builds software in Pune.", accessible=True)
    search = _Search(sources=[page])
    profile = research_company(ClientIntake(company_name="Northwind Labs"), llm=llm, search=search)
    assert search.wiki == [] or search.queries
    assert search.queries
    assert profile.research_status == "RESEARCH_COMPLETE"
    assert any(f.sources and f.sources[0].url == page.url for f in profile.facts)


def test_small_private_company_is_searched_with_legal_variants():
    llm, _handler = _profile(facts=[FactOut(field="overview", text="SmallTech Solutions provides software services.", kind="FACT", source_ids=["S1"], confidence=0.7)])
    page = WebSource(url="https://smalltech.example/", title="SmallTech Solutions", snippet="SmallTech Solutions Private Limited provides software services in Indore.", accessible=True)
    search = _Search(sources=[page])
    profile = research_company(ClientIntake(company_name="SmallTech Solutions Pvt Ltd"), llm=llm, search=search)
    assert search.queries, "a small company must not skip search"
    assert any("Private Limited" in q for q in search.queries)
    assert profile.research_status == "RESEARCH_COMPLETE"


def test_limited_web_presence_stays_partial_and_does_not_invent_numbers():
    llm, handler = _profile(facts=[
        FactOut(field="size", text="SmallTech Solutions has 12 employees.", kind="FACT", source_ids=[], confidence=0.4),
        FactOut(field="industry", text="Unknown", kind="UNKNOWN", source_ids=[], confidence=0.1),
    ])
    page = WebSource(url="https://smalltech.example/contact", title="SmallTech Solutions", snippet="SmallTech Solutions lists a contact form.", accessible=False)
    search = _Search(sources=[page])
    profile = research_company(ClientIntake(company_name="SmallTech Solutions"), llm=llm, search=search)
    assert search.queries
    assert profile.research_status == "RESEARCH_PARTIAL"
    assert not any("12" in f.text for f in profile.facts)
    assert context_prompt(ClientIntake(company_name="SmallTech Solutions"), profile) is None
    assert "SEARCH SNIPPETS" in handler.user


def test_exhausted_search_reports_no_verified_source_and_does_not_say_research_is_off():
    def handler(*_args):
        raise AssertionError("no sources means the model must not invent a profile")

    search = _Search(failures=["brave: HTTP 429", "duckduckgo: HTTP 202", "bing: nothing about Obscure Widgets"])
    profile = research_company(ClientIntake(company_name="Obscure Widgets"), llm=LLMService(mock_handler=handler), search=search)
    assert search.queries
    assert profile.research_status == "NO_VERIFIED_SOURCE"
    assert "Web research was attempted" in (profile.research_note or "")
    assert "web research is off" not in (profile.research_note or "").lower()
    assert all(f.kind != FactKind.ASSUMPTION for f in profile.facts)
    prompt = context_prompt(ClientIntake(company_name="Obscure Widgets"), profile)
    assert prompt is not None
    assert "Web research was attempted" in prompt["message"]
    assert "web research is off" not in prompt["message"].lower()


def test_ambiguous_name_does_not_merge_unrelated_companies():
    def handler(*_args):
        raise AssertionError("tied ambiguous sources must not be sent to the model together")

    sources = [
        WebSource(url="https://bank.example/", title="Acme Bank", snippet="Acme Bank serves customers in Mumbai.", accessible=True),
        WebSource(url="https://steel.example/", title="Acme Steel", snippet="Acme Steel runs a plant in Pune.", accessible=True),
    ]
    kept, note = conflicting_sources(sources, "Acme")
    assert kept == [] and note and "not merged" in note
    search = _Search(sources=sources)
    profile = research_company(ClientIntake(company_name="Acme"), llm=LLMService(mock_handler=handler), search=search)
    assert profile.research_status == "NO_VERIFIED_SOURCE"
    assert "not merged" in (profile.research_note or "")
    assert not any("Mumbai" in f.text or "Pune" in f.text for f in profile.facts)


def test_larger_cluster_is_kept_and_the_other_entity_is_not_in_the_prompt():
    llm, handler = _profile(facts=[FactOut(field="industry", text="Acme Bank is a bank.", kind="FACT", source_ids=["S1"], confidence=0.8)])
    sources = [
        WebSource(url="https://bank.example/a", title="Acme Bank", snippet="Acme Bank is a bank in Mumbai.", accessible=True),
        WebSource(url="https://bank.example/b", title="Acme Bank care", snippet="Acme Bank employs tellers.", accessible=True),
        WebSource(url="https://steel.example/", title="Acme Steel", snippet="Acme Steel runs a plant in Pune.", accessible=True),
    ]
    search = _Search(sources=sources)
    profile = research_company(ClientIntake(company_name="Acme"), llm=llm, search=search)
    assert "Acme Steel" not in handler.user
    assert "Acme Bank" in handler.user
    assert "not merged" in (profile.research_note or "")
    assert profile.research_status == "RESEARCH_COMPLETE"


def test_disabled_research_is_the_only_off_message():
    profile = CompanyProfile(company_name="Nameless", research_status="RESEARCH_DISABLED", facts=[])
    prompt = context_prompt(ClientIntake(company_name="Nameless"), profile)
    assert prompt and "web research is off" in prompt["message"]
    failed = CompanyProfile(company_name="Nameless", research_status="RESEARCH_FAILED", facts=[])
    failed_prompt = context_prompt(ClientIntake(company_name="Nameless"), failed)
    assert failed_prompt and "web research is off" not in failed_prompt["message"].lower()


def test_provider_429_is_retried_once_and_does_not_block_the_next_provider(monkeypatch):
    monkeypatch.setattr("app.research.search.RETRY_BACKOFF_SECONDS", 0)
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "search.brave.com":
            return _html("limited", 429)
        if request.url.host == "html.duckduckgo.com":
            return _html(DDG_PAGE)
        return _html("<html></html>")

    ws = WebSearch(enabled=True, transport=httpx.MockTransport(handler))
    report = ws.search_providers('"Acme Ltd" company', about="Acme Ltd")
    assert hosts.count("search.brave.com") == 2
    assert any("429" in item for item in report.failures)
    assert any(s.url == "https://example.com/acme" for s in report.sources)
    assert RETRY_BACKOFF_SECONDS >= 0


def test_challenge_page_is_skipped_and_another_provider_is_kept(monkeypatch):
    monkeypatch.setattr("app.research.search.RETRY_BACKOFF_SECONDS", 0)
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "search.brave.com":
            return _html("missing", 404)
        if request.url.host == "html.duckduckgo.com":
            return _html("challenge", 202)
        return _html(BING_PAGE)

    ws = WebSearch(enabled=True, transport=httpx.MockTransport(handler))
    skip: set[str] = set()
    first = ws.search_providers("acme", about="Acme", skip=skip)
    assert any(s.url == "https://acme.example/about" for s in first.sources)
    assert "duckduckgo" in skip
    assert any("202" in item for item in first.failures)
    ws.search_providers("acme about", about="Acme", skip=skip)
    assert hosts.count("html.duckduckgo.com") == 1


def test_one_provider_failure_does_not_abort_remaining_queries():
    class _Flaky:
        available = True
        calls = 0

        def cached(self, company):
            return None

        def reference_sources(self, company):
            return []

        def search(self, query, max_results=5, about=None):
            self.calls += 1
            if self.calls == 1:
                raise SearchUnavailable("no search engine answered (brave: HTTP 429)")
            return [WebSource(url="https://later.example/about", title="Later Co", snippet="Later Co operates in Kochi.", accessible=True)]

        def read(self, sources, company):
            return sources

        def remember(self, company, sources):
            return None

    llm, _handler = _profile(facts=[FactOut(field="geography", text="Later Co operates in Kochi.", kind="FACT", source_ids=["S1"], confidence=0.8)])
    search = _Flaky()
    profile = research_company(ClientIntake(company_name="Later Co"), llm=llm, search=search)
    assert search.calls >= 2
    assert profile.research_status == "RESEARCH_COMPLETE"


def test_all_providers_failing_is_exhausted_not_disabled():
    def handler(request: httpx.Request) -> httpx.Response:
        return _html("no", 429 if request.url.host == "search.brave.com" else 404)

    ws = WebSearch(enabled=True, transport=httpx.MockTransport(handler))
    with pytest.raises(SearchUnavailable):
        ws.search("nobody", about="Nobody")


def test_empty_provider_report_does_not_crash_research():
    def handler(*_args):
        raise AssertionError("empty results must not be sent to the model as facts")

    search = _Search(sources=[], failures=["brave: no results on the page (bot challenge?)"])
    profile = research_company(ClientIntake(company_name="Empty Results Ltd"), llm=LLMService(mock_handler=handler), search=search)
    assert profile.research_status == "NO_VERIFIED_SOURCE"
    assert search.queries


def test_unusable_cache_does_not_block_a_later_success(tmp_path, monkeypatch):
    monkeypatch.setattr("app.research.search.RETRY_BACKOFF_SECONDS", 0)
    ws = WebSearch(enabled=True, transport=httpx.MockTransport(lambda request: _html("")), cache_dir=tmp_path)
    useless = [WebSource(url="https://example.com/gone", title="Gone", snippet=None, accessible=False)]
    ws.remember("Obscure Co", useless)
    assert ws.cached("Obscure Co") is None
    assert list(tmp_path.glob("*.json")) == []
    poisoned = tmp_path / "obscure-co.json"
    poisoned.write_text(json.dumps({
        "company": "Obscure Co",
        "normalized": "obscure-co",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sources": [useless[0].model_dump()],
    }))
    assert ws.cached("Obscure Co") is None
    good = [WebSource(url="https://obscure.example/", title="Obscure Co", snippet="Obscure Co employs 12 people in Pune.", accessible=True, retrieved_at="2026-09-27T00:00:00+00:00")]
    ws.remember("Obscure Co", good)
    assert ws.cached("obscure co")[0].url == good[0].url

    class _Cached:
        available = True
        searched = False

        def cached(self, company):
            return good

        def reference_sources(self, company):
            raise AssertionError("a usable cache should not fetch Wikipedia again")

        def search(self, query, max_results=5, about=None):
            self.searched = True
            raise AssertionError("a usable cache should not search again")

        def read(self, sources, company):
            return sources

        def remember(self, company, sources):
            return None

    llm, _handler = _profile(facts=[FactOut(field="size", text="Obscure Co employs 12 people in Pune.", kind="FACT", source_ids=["S1"], confidence=0.9)])
    profile = research_company(ClientIntake(company_name="Obscure Co"), llm=llm, search=_Cached())
    assert profile.research_status == "RESEARCH_COMPLETE"
    assert "cache" in (profile.research_note or "").lower()


def test_mentions_does_not_match_a_longer_unrelated_word():
    from app.research.search import mentions

    assert mentions("Apple Inc employs people", "Apple")
    assert not mentions("pineapple jam", "Apple")
    assert not mentions("https://www.pineapple.com/", "Apple")
    assert mentions("https://www.asianpaints.com/", "Asian Paints")
    assert mentions("https://www.bombaysweetshop.com/about", "Bombay Sweet Shop")


def test_named_domain_is_read_when_engines_find_nothing(monkeypatch):
    monkeypatch.setattr("app.research.search.RETRY_BACKOFF_SECONDS", 0)
    page = "<html><head><title>SmallTech Solutions</title></head><body><p>SmallTech Solutions builds clinic software in Indore and employs a local team of engineers.</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "smalltechsolutions.com":
            return _html(page)
        if request.url.host == "search.brave.com":
            return _html("limited", 429)
        return _html("no", 404)

    llm, _handler = _profile(facts=[FactOut(field="geography", text="SmallTech Solutions employs a local team in Indore.", kind="FACT", source_ids=["S1"], confidence=0.8)])
    search = WebSearch(enabled=True, transport=httpx.MockTransport(handler))
    profile = research_company(ClientIntake(company_name="SmallTech Solutions"), llm=llm, search=search)
    assert profile.research_status == "RESEARCH_COMPLETE"
    assert any("smalltechsolutions.com" in (f.sources[0].url if f.sources else "") for f in profile.facts)
    assert not any(f.kind == FactKind.ASSUMPTION for f in profile.facts)


def test_disabled_prompt_is_not_used_for_a_partial_profile():
    partial = CompanyProfile(
        company_name="Later Co",
        research_status="RESEARCH_PARTIAL",
        facts=[ClientFact(fact_id="1", field="overview", text="Unknown", kind=FactKind.UNKNOWN)],
    )
    assert context_prompt(ClientIntake(company_name="Later Co"), partial) is None
