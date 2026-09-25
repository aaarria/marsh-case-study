"""Web research: a small native crawler. No search API, no key, no AI call.

Google Search grounding is not free on the current Gemini models, and having Gemini fetch a search
engine's results page (the previous approach) failed whenever the engine served that fetcher a bot
challenge. So the backend does the web work itself, over plain HTTP:

  reference_sources():  the company's Wikipedia article (summary + infobox: industry, headquarters,
                        employees, revenue, subsidiaries…) and its official website (home + about
                        page) found from that infobox. Public APIs and the company's own pages; they
                        do not rate-limit a polite client, so research works even when engines do.
  search():             organic results from public search engines' HTML result pages (Brave, then
                        DuckDuckGo, then Bing). Anonymous engines rate-limit bursts, so these are
                        best-effort enrichment on top of the reference sources.
  read():               fetch the result pages (identified user agent, robots.txt honoured, one
                        fetch per page, size and time capped), strip them to text and keep the
                        sentences that are about the company.
  cached()/remember():  a 24-hour on-disk cache of the fetched sources per company, so re-running
                        the same client does not hit the engines again.

Every FACT the research agent emits therefore cites text that appears on the cited page. Gemini is
used only afterwards, to write the profile from that text. When a page cannot be fetched it is
marked unreadable and its search snippet is kept as what the engine showed; when nothing can be
fetched the agent labels company statements as assumptions. Nothing is invented and nothing
switches to a paid tier.
"""
from __future__ import annotations

import base64
import html as html_lib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, quote_plus, unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.config import get_settings
from app.models.client import WebSource
from app.utils.logging import get_logger

log = get_logger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36 MarshAdvisoryResearch/1.0"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9", "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5"}
TIMEOUT = httpx.Timeout(12.0, connect=6.0)
MAX_PAGE_BYTES = 1_500_000
MAX_READ_PAGES = 8
SNIPPET_CHARS = 1200
CACHE_TTL = timedelta(hours=24)
WIKI = "https://en.wikipedia.org"
# Infobox rows worth carrying into the profile, in display order.
INFOBOX_ROWS = ("Type", "Traded as", "Industry", "Founded", "Headquarters", "Area served", "Number of locations", "Key people", "Products", "Services", "Revenue", "Number of employees", "Parent", "Owner", "Subsidiaries", "Divisions", "Website")
COMPANY_WORDS = ("company", "corporation", "manufacturer", "conglomerate", "bank", "firm", "brand", "business", "enterprise", "group", "operator", "provider", "chain", "hospital", "airline", "retailer", "developer", "insurer", "subsidiary", "startup")
# Sentences about these matter for a benefits advisor; the extractor ranks page text by them.
RELEVANCE_TERMS = ("employee", "workforce", "staff", "headcount", "headquarter", "based in", "founded", "revenue", "turnover", "industry", "manufactur", "operat", "offices", "plants", "facilities", "hospitals", "subsidiar", "listed", "sector", "services", "customers", "locations", "countries", "india")


class SearchUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Result-page parsers (one per engine). Each returns [(title, destination url, snippet)].
# ---------------------------------------------------------------------------


def _clean(fragment: str) -> str:
    text = html_lib.unescape(re.sub(r"<[^>]+>", "", fragment or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s*…?\s*Read more$", "", text)


def _bing_destination(href: str) -> str:
    """Bing wraps results as /ck/a?...&u=a1<urlsafe-base64 of the destination>."""
    p = urlparse(href)
    if p.netloc.endswith("bing.com") and p.path.startswith("/ck/"):
        u = parse_qs(p.query).get("u", [""])[0]
        if u.startswith("a1"):
            raw = u[2:] + "=" * (-len(u[2:]) % 4)
            try:
                return base64.urlsafe_b64decode(raw).decode("utf-8", "ignore")
            except (ValueError, UnicodeDecodeError):
                return href
    return href


def parse_bing(page: str) -> list[tuple[str, str, str]]:
    out = []
    for block in re.findall(r'<li class="b_algo".*?</li>', page, re.S):
        h = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not h:
            continue
        p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        out.append((_clean(h.group(2)), _bing_destination(html_lib.unescape(h.group(1))), _clean(p.group(1)) if p else ""))
    return out


def _ddg_destination(href: str) -> str:
    """DuckDuckGo wraps results as //duckduckgo.com/l/?uddg=<encoded url>."""
    if href.startswith("//"):
        href = "https:" + href
    p = urlparse(href)
    if p.netloc.endswith("duckduckgo.com") and p.path.startswith("/l/"):
        target = parse_qs(p.query).get("uddg", [""])[0]
        return unquote(target) if target else href
    return href


def parse_ddg(page: str) -> list[tuple[str, str, str]]:
    out = []
    for block in re.findall(r'<div class="result results_links.*?</div>\s*</div>', page, re.S):
        a = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not a:
            continue
        s = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        out.append((_clean(a.group(2)), _ddg_destination(html_lib.unescape(a.group(1))), _clean(s.group(1)) if s else ""))
    return out


def parse_brave(page: str) -> list[tuple[str, str, str]]:
    """Brave Search: <div class="snippet …" data-type="web"> with a link, a titled div and a .content blurb."""
    out = []
    for block in re.split(r'<div class="snippet [^"]*"', page)[1:]:
        if 'data-type="web"' not in block[:120]:
            continue
        a = re.search(r'<a href="(https?://[^"]+)"', block)
        t = re.search(r'<div class="title[^"]*"[^>]*title="([^"]+)"', block) or re.search(r'<div class="title[^"]*"[^>]*>(.*?)</div>', block, re.S)
        c = re.search(r'<div class="content[^"]*"[^>]*>(.*?)</div>', block, re.S)
        if not a:
            continue
        snippet = re.sub(r'<span class="t-secondary">.*?</span>', "", c.group(1), flags=re.S) if c else ""
        out.append((_clean(t.group(1)) if t else "", html_lib.unescape(a.group(1)), _clean(snippet)))
    return out


# Order matters. Brave answers the full query for a plain HTTP client. DuckDuckGo's HTML endpoint
# rate-limits to a challenge page quickly. Bing without a browser session silently truncates multi-
# word queries to their first word (so "Bharat Forge …" returns pages about Bharat); its results are
# only useful after the `about` filter below, hence last.
ENGINES = (
    ("brave", "https://search.brave.com/search?q={q}&source=web", parse_brave),
    ("duckduckgo", "https://html.duckduckgo.com/html/?q={q}", parse_ddg),
    ("bing", "https://www.bing.com/search?q={q}&setlang=en", parse_bing),  # no setmkt: Bing answers that with a decoy page
)
ENGINE_HOSTS = ("search.brave.com", "duckduckgo.com", "bing.com")


# ---------------------------------------------------------------------------
# Page text extraction
# ---------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "button", "iframe", "template"}
_BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "td", "th", "section", "article", "blockquote", "dd", "dt"}


class _TextExtractor(HTMLParser):
    """Readable paragraphs of an HTML document, skipping chrome (nav, footer, scripts…)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._buf: list[str] = []
        self._skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._buf.append(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if len(text) >= 40:
            self.parts.append(text)

    def close(self):
        super().close()
        self._flush()


def html_to_text(page: str) -> tuple[str, str]:
    """(title, text) of an HTML page. Tolerates broken markup; never raises."""
    ex = _TextExtractor()
    try:
        ex.feed(page)
        ex.close()
    except Exception:  # pragma: no cover - html.parser is lenient, but never let a page kill research
        pass
    return re.sub(r"\s+", " ", ex.title).strip(), "\n".join(ex.parts)


def parse_infobox(page: str) -> dict[str, str]:
    """Label -> value of a Wikipedia infobox (only INFOBOX_ROWS), footnote markers and styling removed."""
    rows: dict[str, str] = {}
    for label, value in re.findall(r'<th[^>]*class="[^"]*infobox-label[^"]*"[^>]*>(.*?)</th>\s*<td[^>]*class="[^"]*infobox-data[^"]*"[^>]*>(.*?)</td>', page, re.S):
        key = _clean(re.sub(r"<(style|sup)[^>]*>.*?</\1>", "", label, flags=re.S))
        if key not in INFOBOX_ROWS:
            continue
        value = re.sub(r"<(style|sup)[^>]*>.*?</\1>", "", value, flags=re.S)
        value = re.sub(r"<br\s*/?>|<li[^>]*>", "; ", value)
        rows[key] = _clean(value).strip("; ")[:300]
    return {k: rows[k] for k in INFOBOX_ROWS if k in rows}


def about_link(page: str, base: str) -> str | None:
    """The first same-site link that looks like an "about us" page."""
    host = urlparse(base).netloc.removeprefix("www.")
    for href in re.findall(r'<a[^>]+href="([^"#]+)"', page, re.I):
        url = urljoin(base, html_lib.unescape(href))
        if urlparse(url).netloc.removeprefix("www.") == host and re.search(r"about|who-we-are|company|overview", urlparse(url).path, re.I):
            return url
    return None


def published_date(page: str) -> str | None:
    m = re.search(r'(?:property|name|itemprop)="(?:article:published_time|datePublished|date|pubdate|publish_date)"[^>]*content="([^"]+)"', page, re.I) or re.search(
        r'content="([^"]+)"[^>]*(?:property|name|itemprop)="(?:article:published_time|datePublished)"', page, re.I
    )
    return m.group(1)[:32] if m else None


_GENERIC = {"the", "and", "ltd", "limited", "inc", "plc", "corp", "corporation", "company", "group", "pvt", "private", "llp", "enterprise", "enterprises", "holdings", "industries", "international"}


def name_tokens(company: str) -> list[str]:
    """Distinctive lower-case words of a company name ("Bharat Forge Ltd" -> ["bharat", "forge"])."""
    return [t.lower() for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9&.-]+", company) if len(t) > 1 and t.lower() not in _GENERIC] or [company.strip().lower()]


def mentions(text: str, company: str) -> bool:
    """Does `text` name the company? All distinctive tokens for short names, most of them for long ones."""
    low = text.lower()
    tokens = name_tokens(company)
    need = len(tokens) if len(tokens) <= 2 else len(tokens) - 1
    return company.strip().lower() in low or sum(1 for t in tokens if t in low) >= need


def relevant_passage(text: str, company: str, limit: int = SNIPPET_CHARS) -> str:
    """Sentences from `text` that are about `company`, verbatim, in document order, up to `limit` chars.

    The opening sentences of a page carry a small bonus so a "who they are" line survives alongside the
    figure-heavy ones; a page that never names the company yields nothing.
    """
    if not mentions(text, company):
        return ""
    tokens = name_tokens(company)
    scored: list[tuple[int, int, str]] = []
    for i, s in enumerate(re.split(r"(?<=[.!?])\s+|\n+", text)):
        s = s.strip()
        if not 40 <= len(s) <= 420:
            continue
        low = s.lower()
        score = 2 * sum(1 for t in tokens if t in low) + sum(1 for k in RELEVANCE_TERMS if k in low) + (1 if re.search(r"\d", s) else 0)
        if score and i < 3:
            score += 2  # opening lines say who the company is; keep them when they carry any signal
        if score >= 2:
            scored.append((score, i, s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    chosen, used = [], 0
    for _score, i, s in scored:
        if used + len(s) + 1 > limit:
            continue
        chosen.append((i, s))
        used += len(s) + 1
    return " ".join(s for _, s in sorted(chosen))


# ---------------------------------------------------------------------------
# The crawler
# ---------------------------------------------------------------------------


class WebSearch:
    """Native web research. `available` is False only when WEB_RESEARCH_ENABLED=false."""

    def __init__(self, enabled: bool | None = None, transport: httpx.BaseTransport | None = None, cache_dir: Path | None = None):
        settings = get_settings()
        self._enabled = settings.web_research_enabled if enabled is None else enabled
        self._transport = transport  # tests inject httpx.MockTransport (and then get no disk cache)
        self._cache_dir = cache_dir if cache_dir or transport else settings.storage_path / "cache" / "research"
        self._robots: dict[str, RobotFileParser] = {}

    @property
    def available(self) -> bool:
        return self._enabled

    def _client(self) -> httpx.Client:
        return httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True, transport=self._transport)

    def _get(self, client: httpx.Client, url: str) -> httpx.Response | str:
        """The 200 response for `url` (body capped at MAX_PAGE_BYTES), or a short reason string."""
        try:
            with client.stream("GET", url) as r:
                if r.status_code != 200:
                    log.info("fetch %s -> HTTP %s", urlparse(url).netloc, r.status_code)
                    return f"HTTP {r.status_code}"
                ctype = r.headers.get("content-type", "")
                if not any(t in ctype for t in ("text/html", "text/plain", "application/xhtml", "application/json")):
                    return f"not a page ({ctype.split(';')[0] or 'unknown type'})"
                body = bytearray()
                for chunk in r.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_PAGE_BYTES:
                        break
                r._content = bytes(body)
                return r
        except httpx.HTTPError as exc:
            log.info("fetch %s failed: %s", urlparse(url).netloc, type(exc).__name__)
            return type(exc).__name__

    def _json(self, client: httpx.Client, url: str) -> dict:
        r = self._get(client, url)
        try:
            d = r.json() if not isinstance(r, str) else {}
        except ValueError:
            d = {}
        return d if isinstance(d, dict) else {}

    def _allowed(self, client: httpx.Client, url: str) -> bool:
        """robots.txt check, cached per host; an unreachable robots.txt means allowed."""
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = RobotFileParser()
            try:
                r = client.get(f"{base}/robots.txt", timeout=5.0)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except httpx.HTTPError:
                rp.parse([])
            self._robots[base] = rp
        return self._robots[base].can_fetch(USER_AGENT, url)

    # -- search engines -----------------------------------------------------

    def search(self, query: str, max_results: int = 5, about: str | None = None) -> list[WebSource]:
        """Organic results for `query` from the first engine that answers with usable results.

        With `about=<company>`, results whose title, snippet and URL never name the company are
        dropped (an engine that answered a different question is treated as not having answered).
        """
        if not self._enabled:
            raise SearchUnavailable("Web research is disabled (WEB_RESEARCH_ENABLED=false)")
        now = datetime.now(timezone.utc).isoformat()
        failures = []
        with self._client() as client:
            for name, template, parse in ENGINES:
                r = self._get(client, template.format(q=quote_plus(query)))
                items = parse(r.text) if not isinstance(r, str) else []
                out: list[WebSource] = []
                seen: set[str] = set()
                for title, url, snippet in items:
                    host = urlparse(url).netloc
                    if not url.startswith(("http://", "https://")) or any(host.endswith(h) for h in ENGINE_HOSTS) or url in seen:
                        continue
                    if about and not mentions(f"{title} {snippet} {unquote(url)}", about):
                        continue
                    seen.add(url)
                    out.append(WebSource(url=url, title=title or None, snippet=snippet[:600] or None, retrieved_at=now))
                if not out:
                    failures.append(f"{name}: {r if isinstance(r, str) else 'nothing about ' + about if items and about else 'no results on the page (bot challenge?)'}")
                    continue
                log.info("web_search[%s] %r -> %s results", name, query[:60], len(out))
                return out[:max_results]
        raise SearchUnavailable("no search engine answered (" + "; ".join(failures) + ")")

    # -- reference sources: Wikipedia and the company's own site ----------------

    def _wikipedia_article(self, client: httpx.Client, company: str) -> tuple[str, str, dict] | None:
        """(title, url, summary json) of the Wikipedia article about the company, or None."""
        d = self._json(client, f"{WIKI}/w/api.php?action=query&list=search&srsearch={quote_plus(company + ' company')}&srlimit=4&format=json")
        hits = [h.get("title", "") for h in (d.get("query") or {}).get("search", [])]
        # "Tesla" finds Nikola Tesla first: prefer the article whose short description reads like a company.
        for title in [t for t in hits if mentions(t, company)][:3]:
            summary = self._json(client, f"{WIKI}/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}")
            extract = (summary.get("extract") or "").strip()
            blurb = f"{summary.get('description') or ''} {extract[:300]}".lower()
            if extract and summary.get("type") != "disambiguation" and any(k in blurb for k in COMPANY_WORDS):
                url = (summary.get("content_urls") or {}).get("desktop", {}).get("page") or f"{WIKI}/wiki/{quote(title.replace(' ', '_'))}"
                return title, unquote(url), summary  # "Tesla,_Inc." not "Tesla%2C_Inc.", so engine results for the same page de-duplicate
        return None

    def reference_sources(self, company: str) -> list[WebSource]:
        """Wikipedia (summary + infobox) and the official website (home + about page), already read.

        Returns [] when the company has no Wikipedia article; never raises.
        """
        if not self._enabled:
            return []
        out: list[WebSource] = []
        now = datetime.now(timezone.utc).isoformat()
        with self._client() as client:
            found = self._wikipedia_article(client, company)
            if not found:
                log.info("wikipedia: no company article for %r", company)
                return out
            title, url, summary = found
            article = self._get(client, f"{WIKI}/api/rest_v1/page/html/{quote(title.replace(' ', '_'))}")
            infobox = parse_infobox(article.text) if not isinstance(article, str) else {}
            facts = "; ".join(f"{k}: {v}" for k, v in infobox.items() if k != "Website")
            snippet = (summary.get("extract") or "").strip()[:SNIPPET_CHARS] + (f"\nInfobox: {facts}" if facts else "")
            out.append(WebSource(url=url, title=f"{title} - Wikipedia", snippet=snippet[:2 * SNIPPET_CHARS], accessible=True, retrieved_at=now))
            log.info("wikipedia %r -> %s (%s infobox rows)", company, title, len(infobox))

            site = infobox.get("Website", "").split(";")[0].strip()
            if site and " " not in site:
                home = site if site.startswith("http") else f"https://{site}"
                home = home if urlparse(home).path else home + "/"
                if self._allowed(client, home):
                    r = self._get(client, home)
                    if not isinstance(r, str):
                        pages = [(str(r.url), r.text)]
                        about = about_link(r.text, str(r.url))
                        if about and about != str(r.url) and self._allowed(client, about):
                            a = self._get(client, about)
                            if not isinstance(a, str):
                                pages.append((str(a.url), a.text))
                        for page_url, page in pages:
                            page_title, text = html_to_text(page)
                            passage = relevant_passage(text, company)
                            if passage:
                                out.append(WebSource(url=page_url, title=page_title[:200] or f"{company} official website", published_date=published_date(page), snippet=passage, accessible=True, retrieved_at=now))
                    log.info("official site %s -> %s readable pages", urlparse(home).netloc, len(out) - 1)
        return out

    # -- cache --------------------------------------------------------------

    def _cache_file(self, company: str) -> Path | None:
        if not self._cache_dir:
            return None
        return self._cache_dir / ((re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-") or "company")[:80] + ".json")

    def cached(self, company: str) -> list[WebSource] | None:
        """Sources remembered for `company` within CACHE_TTL, else None."""
        f = self._cache_file(company)
        if not f or not f.exists():
            return None
        try:
            d = json.loads(f.read_text())
            at = datetime.fromisoformat(d["fetched_at"])
        except (ValueError, KeyError, OSError):
            return None
        if datetime.now(timezone.utc) - at > CACHE_TTL:
            return None
        return [WebSource.model_validate(s) for s in d.get("sources", [])]

    def remember(self, company: str, sources: list[WebSource]) -> None:
        f = self._cache_file(company)
        if not f or not sources:
            return
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"company": company, "fetched_at": datetime.now(timezone.utc).isoformat(), "sources": [s.model_dump() for s in sources]}, indent=1))
        except OSError as exc:
            log.warning("research cache not written: %s", exc)

    # -- read ---------------------------------------------------------------

    def _read_one(self, client: httpx.Client, s: WebSource, company: str) -> WebSource:
        if not self._allowed(client, s.url):
            log.info("robots.txt disallows %s", urlparse(s.url).netloc)
            return s.model_copy(update={"accessible": False})
        r = self._get(client, s.url)
        if isinstance(r, str):
            return s.model_copy(update={"accessible": False})
        title, text = html_to_text(r.text)
        passage = relevant_passage(text, company)
        if not passage:
            # Reachable but says nothing specific; keep the engine's snippet, flagged as such.
            return s.model_copy(update={"accessible": False})
        return WebSource(url=str(r.url), title=title[:200] or s.title, published_date=published_date(r.text), snippet=passage, accessible=True, retrieved_at=datetime.now(timezone.utc).isoformat())

    def read(self, sources: list[WebSource], company: str) -> list[WebSource]:
        """Fetch the pages behind `sources` and replace search blurbs with passages from the pages.

        Pages that cannot be fetched (or say nothing about the company) keep their search snippet and
        are marked accessible=False, so the research step cites them as what the engine showed.
        Sources already read (the reference sources) pass through untouched.
        """
        todo = [s for s in sources if not s.accessible][:MAX_READ_PAGES]
        if not todo:
            return sources
        with self._client() as client, ThreadPoolExecutor(max_workers=4) as pool:
            read = {s.url: r for s, r in zip(todo, pool.map(lambda s: self._read_one(client, s, company), todo))}
        out = [read.get(s.url, s) for s in sources]
        log.info("web_read %s pages -> %s readable", len(todo), sum(1 for s in out if s.accessible))
        return out


_search: WebSearch | None = None


def get_search() -> WebSearch:
    global _search
    if _search is None:
        _search = WebSearch()
    return _search


__all__ = ["SearchUnavailable", "WebSearch", "get_search", "html_to_text", "parse_bing", "parse_brave", "parse_ddg", "parse_infobox", "relevant_passage"]
