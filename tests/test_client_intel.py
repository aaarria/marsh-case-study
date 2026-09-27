"""Company research and exposure mapping (offline + mock LLM)."""
from __future__ import annotations

import httpx

from app.exposure.mapping import map_exposures
from app.models.client import ClientIntake, CompanyProfile, FactKind
from app.research.company_research import FactOut, ProfileOut, research_company
from app.research.search import WebSearch
from app.services.llm import LLMService


def _no_llm() -> LLMService:
    return LLMService(mock_handler=None)


def test_offline_research_uses_only_advisor_inputs():
    intake = ClientIntake(company_name="Acme Steel", industry="Steel manufacturing", employee_count=12000)
    p = research_company(intake, llm=_no_llm(), search=WebSearch(enabled=False))
    assert p.research_status == "RESEARCH_DISABLED"
    assert "WEB_RESEARCH_ENABLED" in (p.research_note or "")
    kinds = {f.kind for f in p.facts}
    assert FactKind.UNKNOWN in kinds
    adv = [f for f in p.facts if f.sources and f.sources[0].url.startswith("advisor://")]
    assert len(adv) == 2 and all(f.kind == FactKind.FACT for f in adv)


def test_fact_without_source_is_not_kept_as_company_fact():
    """A FACT the model cannot cite is dropped. It is not relabelled into an industry we will show."""
    from app.models.client import WebSource

    def handler(system, user, schema):
        return ProfileOut(overview="o", industry="Software", size=None, geography="India", workforce=None,
                          facts=[FactOut(field="industry", text="Acme is a software company", kind="FACT", source_ids=[], confidence=0.9),
                                 FactOut(field="size", text="Unknown", kind="UNKNOWN", source_ids=[], confidence=0.1)])

    class _Search:
        available = True
        calls = 0

        def cached(self, company):
            return None

        def reference_sources(self, company):
            return []

        def search(self, query, max_results=5, about=None):
            self.calls += 1
            return [WebSource(url="https://acme.example/about", title="Acme", snippet="Acme publishes an about page.", accessible=True)]

        def read(self, sources, company):
            return sources

        def remember(self, company, sources):
            return None

    p = research_company(ClientIntake(company_name="Acme"), llm=LLMService(mock_handler=handler), search=_Search())
    assert p.research_status == "RESEARCH_PARTIAL"
    assert not any("software company" in f.text for f in p.facts)
    assert any(f.field == "industry" and f.kind == FactKind.UNKNOWN for f in p.facts)


def test_advisor_answered_fields_are_not_duplicated_by_model():
    from app.models.client import WebSource

    def handler(system, user, schema):
        return ProfileOut(overview="o", industry="Software", size=None, geography=None, workforce=None,
                          facts=[FactOut(field="industry", text="Software", kind="ASSUMPTION", source_ids=[], confidence=0.5),
                                 FactOut(field="size", text="Unknown", kind="UNKNOWN", source_ids=[], confidence=0.1),
                                 FactOut(field="workforce", text="500 employees", kind="ASSUMPTION", source_ids=[], confidence=0.4),
                                 FactOut(field="business", text="Unknown", kind="UNKNOWN", source_ids=[], confidence=0.1)])

    class _Search:
        available = True

        def cached(self, company):
            return None

        def reference_sources(self, company):
            return []

        def search(self, query, max_results=5, about=None):
            return [WebSource(url="https://acme.example/about", title="Acme", snippet="Acme describes its business on this page.", accessible=True)]

        def read(self, sources, company):
            return sources

        def remember(self, company, sources):
            return None

    intake = ClientIntake(company_name="Acme", industry="Software", employee_count=500)
    p = research_company(intake, llm=LLMService(mock_handler=handler), search=_Search())
    by_field = {}
    for f in p.facts:
        by_field.setdefault(f.field, []).append(f)
    assert [f.kind for f in by_field["industry"]] == [FactKind.FACT]
    assert [f.kind for f in by_field["size"]] == [FactKind.FACT]
    assert not any("500" in f.text for f in by_field.get("workforce", []))
    assert [f.kind for f in by_field["workforce"]] == [FactKind.UNKNOWN]
    assert [f.kind for f in by_field["business"]] == [FactKind.UNKNOWN]


def test_exposures_never_upgrade_inference_to_fact_without_verified_basis():
    from app.exposure.mapping import ExposureOut, ExposuresOut

    def handler(system, user, schema):
        if schema is ExposuresOut:
            return ExposuresOut(exposures=[ExposureOut(title="Frequent travel", description="Staff travel abroad", basis_fact_ids=["nope"], reasoning="r", status="FACT", confidence=0.8, priority=1.0, feature_keys=["global_cover"])])
        raise AssertionError("unexpected schema")

    profile = CompanyProfile(company_name="Acme", industry="Consulting", facts=[])
    intake = ClientIntake(company_name="Acme", client_priorities=["maternity benefits"])
    exps = map_exposures(profile, intake, llm=LLMService(mock_handler=handler))
    travel = next(e for e in exps if e.title == "Frequent travel")
    assert travel.status == FactKind.INFERENCE
    prio = next(e for e in exps if e.title.startswith("Advisor priority"))
    assert prio.priority > 1.5 and "maternity" in prio.feature_keys
    assert all(e.feature_keys for e in exps)


BING_PAGE = """<ol id="b_results">
<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&amp;p=x&amp;u=a1aHR0cHM6Ly9lbi53aWtpcGVkaWEub3JnL3dpa2kvQWNtZQ&amp;ntb=1">Acme - <strong>Wikipedia</strong></a></h2><div><p>Acme is a maker of anvils &amp; rockets …Read more</p></div></li>
<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&amp;p=y&amp;u=a1aHR0cHM6Ly9hY21lLmV4YW1wbGUv&amp;ntb=1">Acme</a></h2><div><p>Official site</p></div></li>
<li class="b_algo"><h2><a href="https://www.bing.com/search?q=acme+more">More results</a></h2></li>
</ol>"""

DDG_PAGE = """<div class="result results_links results_links_deep web-result">
<div class="links_main"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FAcme&amp;rut=x">Acme - Wikipedia</a>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">Acme is a maker of <b>anvils</b></a></div>
</div>"""

BRAVE_PAGE = """<section id="mixed-main">
<div class="snippet svelte-x" data-pos="0" data-type="web"><a href="https://www.reveliolabs.com/companies/acme/employees/" class="l1"><div class="title search-snippet-title" title="Acme Number of Employees 2026 | Headcount Data">Acme Number of Employees 2026 | Headcount Data</div></a><div class="generic-snippet"><div class="content t-primary"><span class="t-secondary">June 30, 2026 -</span> Acme Ltd. has <strong>approximately 9,104</strong> total employees worldwide.</div></div></div>
<div class="snippet svelte-x" data-pos="1" data-type="web"><a href="https://www.youtube.com/watch?v=abc"><div class="title" title="Anvil unboxing">Anvil unboxing</div></a><div class="generic-snippet"><div class="content">A video about anvils.</div></div></div>
<div class="snippet svelte-x" data-pos="2" data-type="videos"><a href="https://example.com/video">ignored non-web snippet</a></div>
</section>"""

ACME_ARTICLE = """<html><head><title>Acme Corporation - Wikipedia</title>
<meta property="article:published_time" content="2026-01-15T00:00:00Z"></head>
<body><nav><a href="/">Home</a> Menu with many links that should be ignored by the extractor</nav>
<p>Acme Corporation is a fictional manufacturer of anvils, rockets and other equipment for coyotes.</p>
<p>The company employs 1,234 people across 12 plants in the United States and India, with headquarters in Fairfield.</p>
<p>The weather was mild and the film was received warmly by critics at the festival that year.</p>
<footer>Text is available under the Creative Commons License; additional terms may apply.</footer></body></html>"""


def _transport(routes):
    """httpx.MockTransport keyed by host (+ optional path prefix); anything else is a 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        for key, resp in routes.items():
            host, _, path = key.partition("/")
            if request.url.host == host and request.url.path.startswith("/" + path):
                return resp() if callable(resp) else resp
        return httpx.Response(404, text="")

    return httpx.MockTransport(handler)


def _html(text, status=200):
    return httpx.Response(status, text=text, headers={"content-type": "text/html; charset=utf-8"})


def test_crawler_search_parses_brave_and_filters_results_about_the_company():
    ws = WebSearch(enabled=True, transport=_transport({"search.brave.com/search": _html(BRAVE_PAGE)}))
    out = ws.search("acme employees", max_results=5)
    assert [s.url for s in out] == ["https://www.reveliolabs.com/companies/acme/employees/", "https://www.youtube.com/watch?v=abc"]
    assert out[0].title == "Acme Number of Employees 2026 | Headcount Data" and out[0].snippet == "Acme Ltd. has approximately 9,104 total employees worldwide." and out[0].accessible is None
    # about= drops results that never name the company (an engine answering a different question)
    assert [s.url for s in ws.search("acme employees", about="Acme")] == ["https://www.reveliolabs.com/companies/acme/employees/"]


def test_crawler_search_parses_bing_unwraps_redirects_and_drops_engine_links():
    ws = WebSearch(enabled=True, transport=_transport({"www.bing.com/search": _html(BING_PAGE)}))
    out = ws.search("acme", max_results=5)
    assert [s.url for s in out] == ["https://en.wikipedia.org/wiki/Acme", "https://acme.example/"]
    assert out[0].title == "Acme - Wikipedia" and out[0].snippet == "Acme is a maker of anvils & rockets" and out[0].accessible is None


def test_crawler_search_falls_back_across_engines_and_fails_loudly():
    import pytest

    from app.research.search import SearchUnavailable

    # Brave unreachable, Bing serves a challenge page (no organic results) -> DuckDuckGo answers
    ws = WebSearch(enabled=True, transport=_transport({"www.bing.com/search": _html("<html>challenge</html>", 202), "html.duckduckgo.com/html": _html(DDG_PAGE)}))
    out = ws.search("acme")
    assert [s.url for s in out] == ["https://en.wikipedia.org/wiki/Acme"] and out[0].snippet == "Acme is a maker of anvils"

    # every engine unreachable -> explicit failure, never an empty "no results" that reads as fact
    with pytest.raises(SearchUnavailable):
        WebSearch(enabled=True, transport=_transport({})).search("acme")
    with pytest.raises(SearchUnavailable):
        WebSearch(enabled=False, transport=_transport({})).search("acme")


def test_crawler_read_extracts_company_passages_and_marks_unreadable_pages():
    from app.models.client import WebSource

    ws = WebSearch(enabled=True, transport=_transport({"en.wikipedia.org/wiki": _html(ACME_ARTICLE), "en.wikipedia.org/robots.txt": _html("User-agent: *\nAllow: /", 200),
                                                        "blocked.example/robots.txt": _html("User-agent: *\nDisallow: /private", 200), "blocked.example/private": _html("<p>Acme secret headcount 99</p>"),
                                                        "wiki.example/robots.txt": httpx.Response(404)}))
    srcs = [WebSource(url="https://en.wikipedia.org/wiki/Acme", title="Acme - Wikipedia", snippet="engine blurb"),
            WebSource(url="https://blocked.example/private/about", title="About", snippet="engine blurb 2"),
            WebSource(url="https://down.example/about", title="Down", snippet="engine blurb 3"),
            WebSource(url="https://wiki.example/summary", title="Already read", snippet="kept as is", accessible=True)]
    out = ws.read(srcs, "Acme Corporation")
    assert out[0].accessible is True and out[0].title == "Acme Corporation - Wikipedia" and out[0].published_date == "2026-01-15T00:00:00Z"
    assert "employs 1,234 people" in out[0].snippet and "Acme Corporation is a fictional manufacturer" in out[0].snippet
    assert "weather was mild" not in out[0].snippet and "Creative Commons" not in out[0].snippet  # off-topic sentence and footer chrome dropped
    assert out[1].accessible is False and out[1].snippet == "engine blurb 2"  # robots.txt disallows -> not fetched
    assert out[2].accessible is False and out[2].snippet == "engine blurb 3"  # unreachable -> engine snippet kept, flagged
    assert out[3].snippet == "kept as is" and out[3].accessible is True  # pre-read source passes through


WIKI_ARTICLE = """<html><body><table class="infobox"><tbody>
<tr><th class="infobox-label">Industry</th><td class="infobox-data">Forging<sup>[1]</sup>; Automotive</td></tr>
<tr><th class="infobox-label">Number of employees</th><td class="infobox-data">3,970 (2025)<style>.x{}</style></td></tr>
<tr><th class="infobox-label">Website</th><td class="infobox-data"><a href="https://acme.example/">acme.example</a></td></tr>
<tr><th class="infobox-label">Footnotes</th><td class="infobox-data">ignored row</td></tr>
</tbody></table><p>Article prose.</p></body></html>"""

ACME_HOME = """<html><head><title>Acme Ltd</title></head><body><nav><a href="/about-us">About</a></nav>
<p>Acme Ltd forges components for 1,200 customers in 14 countries.</p></body></html>"""
ACME_ABOUT = """<html><head><title>About Acme</title></head><body><p>Acme Ltd was founded in 1961 and employs about 4,000 people at plants in Pune and Baramati.</p></body></html>"""


def test_crawler_reference_sources_wikipedia_infobox_and_official_site(tmp_path):
    routes = {
        "en.wikipedia.org/w/api.php": httpx.Response(200, json={"query": {"search": [{"title": "Acme (person)"}, {"title": "Acme Ltd"}]}}),
        "en.wikipedia.org/api/rest_v1/page/summary/Acme_(person)": httpx.Response(200, json={"type": "standard", "description": "Inventor", "extract": "Acme was an inventor."}),
        "en.wikipedia.org/api/rest_v1/page/summary/Acme_Ltd": httpx.Response(200, json={"type": "standard", "description": "Indian forging company", "extract": "Acme Ltd is an Indian forging company.", "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Acme_Ltd"}}}),
        "en.wikipedia.org/api/rest_v1/page/html/Acme_Ltd": _html(WIKI_ARTICLE),
        "acme.example/robots.txt": httpx.Response(404),
        "acme.example/about-us": _html(ACME_ABOUT),
        "acme.example/": _html(ACME_HOME),
    }
    ws = WebSearch(enabled=True, transport=_transport(routes))
    out = ws.reference_sources("Acme")  # the person article is skipped: its description is not company-like
    assert [s.url for s in out] == ["https://en.wikipedia.org/wiki/Acme_Ltd", "https://acme.example/", "https://acme.example/about-us"]
    assert all(s.accessible for s in out)
    assert out[0].snippet == "Acme Ltd is an Indian forging company.\nInfobox: Industry: Forging; Automotive; Number of employees: 3,970 (2025)"
    assert "1,200 customers in 14 countries" in out[1].snippet and out[2].title == "About Acme" and "employs about 4,000 people" in out[2].snippet
    # already-read sources pass through read() untouched, and no Wikipedia article means no reference sources (not an error)
    assert ws.read(out, "Acme") == out
    assert WebSearch(enabled=True, transport=_transport({})).reference_sources("Nobody Ltd") == []


def test_crawler_cache_round_trips_sources_and_expires(tmp_path, monkeypatch):
    from app.models.client import WebSource
    from app.research import search as mod

    ws = WebSearch(enabled=True, transport=_transport({}), cache_dir=tmp_path)
    src = [WebSource(url="https://acme.example/", title="Acme", snippet="Acme employs 4,000 people.", accessible=True, retrieved_at="2026-09-25T10:00:00+00:00")]
    assert ws.cached("Acme Ltd") is None
    ws.remember("Acme Ltd", src)
    assert ws.cached("Acme Ltd") == src and ws.cached("acme ltd") == src  # key is case/punctuation-insensitive
    monkeypatch.setattr(mod, "CACHE_TTL", mod.timedelta(seconds=0))
    assert ws.cached("Acme Ltd") is None
    assert WebSearch(enabled=True, transport=_transport({})).cached("Acme Ltd") is None  # no cache dir -> never cached


def test_research_uses_reference_sources_when_engines_are_rate_limited():
    from app.models.client import WebSource

    class _Search:
        available = True
        calls = 0

        def cached(self, company):
            return None

        def reference_sources(self, company):
            return [WebSource(url="https://en.wikipedia.org/wiki/Acme_Ltd", title="Acme Ltd - Wikipedia", snippet="Acme Ltd is an Indian forging company.\nInfobox: Number of employees: 3,970 (2025)", accessible=True)]

        def search(self, query, max_results=5, about=None):
            from app.research.search import SearchUnavailable

            self.calls += 1
            raise SearchUnavailable("no search engine answered (brave: HTTP 429; duckduckgo: HTTP 202; bing: nothing about Acme Ltd)")

        def read(self, sources, company):
            return sources

        def remember(self, company, sources):
            self.remembered = sources

    def handler(system, user, schema):
        assert "[S1] Acme Ltd - Wikipedia" in user and "3,970" in user
        return ProfileOut(overview="Indian forging company", industry="Forging", size="3,970 employees (2025)", geography="India", workforce=None,
                          facts=[FactOut(field="size", text="Acme Ltd had 3,970 employees in 2025.", kind="FACT", source_ids=["S1"], confidence=0.9)])

    search = _Search()
    p = research_company(ClientIntake(company_name="Acme Ltd"), llm=LLMService(mock_handler=handler), search=search)
    assert p.research_status == "RESEARCH_COMPLETE"
    assert "rate-limited" in (p.research_note or "")
    assert "web research is off" not in (p.research_note or "").lower()
    assert search.calls >= 2  # the first provider failure does not abort the remaining queries
    size = [f for f in p.facts if f.field == "size"][0]
    assert size.kind == FactKind.FACT and size.sources[0].url == "https://en.wikipedia.org/wiki/Acme_Ltd"
    assert search.remembered and search.remembered[0].url.endswith("Acme_Ltd")


def test_offline_exposures_from_industry_keywords():
    profile = CompanyProfile(company_name="Acme", industry="Steel manufacturing", facts=[])
    intake = ClientIntake(company_name="Acme")
    exps = map_exposures(profile, intake, llm=_no_llm())
    titles = {e.title for e in exps}
    assert "Employee hospitalisation" in titles and "Industrial and field workforce" in titles
