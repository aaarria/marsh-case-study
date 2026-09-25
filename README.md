# Marsh Evidence-First Insurance Advisory

A Marsh Client Advisor types a company name. The system builds a company profile with every statement labelled fact / inference / assumption, maps health-benefit exposures, tests four insurer brochures against the same client scenarios, drafts a 4-slide pitch in which every policy statement is footnoted to a brochure page, audits every claim against the cited clauses, and then waits for the advisor to approve, edit, regenerate or reject before an editable PPTX deck and a claim-level audit report are exported. The whole run is a single chat thread beside the live deck; when the system needs a decision (missing context, a close call between policies, the review itself) it asks in the thread instead of guessing.

Scope is deliberately limited to the brief: intake → profile → deck → audit → human approval → export. Nothing that the advisor does not need on that path is built.

Built for the Marsh internship case study. Monorepo: `frontend/` (Next.js 16, React 19, TypeScript, shadcn/ui, Tailwind v4, deploy on Vercel) and `backend/` (Python 3.12, FastAPI, LangGraph, Pydantic v2, deploy on Railway). All storage is local: FAISS + BM25 + SQLite/JSON under `storage/`. No cloud or managed vector database.

Sample outputs (generated end-to-end, offline mode): [`outputs/presentations/sample_Infosys_pitch.pptx`](outputs/presentations/sample_Infosys_pitch.pptx), [`outputs/audit_reports/sample_Infosys_audit.md`](outputs/audit_reports/sample_Infosys_audit.md) (+ `.json`).

---

## 1. Running it

### Prerequisites
- Python 3.12 (managed with [uv](https://docs.astral.sh/uv/)), Node 20+, npm.
- Optional but recommended: a free `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/app/apikey). **Google Gemini is the only external AI API** — no OpenAI, OpenRouter, Tavily or other paid service is called. One key drives structured extraction, pitch writing and the coverage audit. Without it the system runs in a deterministic offline mode: heuristics only, more cells read `NOT_FOUND`, company facts are labelled `ASSUMPTION`/`UNKNOWN`. It never fabricates to fill the gap.

### Gemini free tier: model choice, quota and retry
- **Configurable model, no hard-coding.** Every call reads `GEMINI_MODEL` from the environment (`app/config.py` is the only place the default lives). Default: `gemini-3.5-flash-lite`.
- **Eligible free-tier models.** Google's [pricing page](https://ai.google.dev/gemini-api/docs/pricing) and [rate-limits page](https://ai.google.dev/gemini-api/docs/rate-limits) (checked 2026-09-24) list these text models as "Free of charge" on the Free Tier: `gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`. Google recommends 3.5 Flash-Lite or 3.8 Flash for new projects; Flash-Lite has the most free-tier headroom, which matters because a full run makes ~40–60 LLM calls. The list is mirrored in `FREE_TIER_TEXT_MODELS` (`app/config.py`) only to **warn** at startup and on the dashboard if `GEMINI_MODEL` is not on it; it is never used to pick a model.
- **Never switches to a paid model.** There is no fallback list and no "audit model": one configured model is used for every purpose. The SDK's automatic retries are disabled so requests are visible and bounded.
- **Quota / rate limit reached.** A `429 RESOURCE_EXHAUSTED` is classified from Google's `QuotaFailure`/`RetryInfo` details. Per-minute limits are waited out briefly (≤ 3 attempts, honouring `retryDelay`); anything beyond that, and any per-day limit, stops the run with a typed `LLMQuotaExceeded`. The run is stored as `failed` with `error_kind: "quota"` and `retry_after` (seconds; daily quotas point at midnight Pacific). The UI shows an amber "free-tier limit reached" banner with the expected reset time and a **Retry now** button; `POST /api/runs/{id}/retry` resumes the run from its LangGraph checkpoint so completed steps are not re-billed against quota. Quota errors are never swallowed into heuristics — they propagate through every agent so nothing is silently degraded.
- **Server restarts don't lose work.** Runs execute in a background thread, so a restart mid-run would otherwise leave them `running` forever. On startup `reconcile_orphaned_runs()` marks any `running` run without a live thread as `failed` with `error_kind: "interrupted"` and `retryable: true`; the run header shows an "Interrupted" banner with a **Resume** button that continues from the last LangGraph checkpoint. A run whose last event is older than 8 minutes shows a stall notice while it is still `running`.
- **Web research is a native crawler, not an API.** Google Search grounding is not free on current Gemini models, and letting Gemini fetch a search engine's results page proved unreliable (engines serve its fetcher bot challenges). So the backend does the web work itself over plain HTTP (`research/search.py`): the company's **Wikipedia article** (summary plus infobox: industry, headquarters, employees, revenue, subsidiaries) and its **official website** (home and about page, found from that infobox) are the backbone; **public search engines** (Brave, then DuckDuckGo, then Bing, parsed from their HTML result pages) add more pages when they answer. Result pages are fetched politely (identified user agent, robots.txt honoured, size and time caps) and reduced to the sentences that name the company, so every company FACT cites text that is on the cited page. Sources are cached on disk for 24 h per company. Gemini's only role is writing the profile from that text. No search API, no key, no AI call for the crawling. `WEB_RESEARCH_ENABLED=false` turns it off; company statements are then labelled assumptions.
- **Embeddings stay local by default** (`EMBEDDING_PROVIDER=local`, fastembed ONNX) so retrieval never consumes Gemini quota. `EMBEDDING_PROVIDER=gemini` uses `GEMINI_EMBEDDING_MODEL` (default `gemini-embedding-001`; re-run `npm run ingest -- --force` after switching).

### Backend
```bash
cp .env.example .env            # add GEMINI_API_KEY if you have one
npm run setup:backend           # uv venv + requirements
npm run ingest                  # parse PDFs -> chunks -> FAISS/BM25/SQLite -> structured policy facts
npm run dev:backend             # http://localhost:8000  (docs at /docs)
```
`npm run ingest` is idempotent. With `EMBEDDING_PROVIDER=local` it uses a small ONNX embedder and needs no key. The committed `storage/` already contains a local-embedding index, so the API also boots without ingesting: on startup it restores the SQLite chunk table from `storage/metadata/` and only re-embeds if the indexes are missing.

### Frontend
```bash
npm run setup:frontend
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev:frontend    # http://localhost:3000
```

### Tests
```bash
npm run test:backend            # 62 tests: ingestion, retrieval, numerals, extraction, policy fit, client intel, audit, Gemini quota handling, API/graph, restart recovery
npm run lint:frontend
```
Tests run against a temporary copy of `storage/` (`tests/conftest.py` sets `STORAGE_DIR`/`OUTPUTS_DIR`), so they never write runs or decks into the working directory you demo from.

CORS: `CORS_ORIGINS` lists production origins; any `http://localhost:<port>` and `*.vercel.app` origin is also accepted, so a dev frontend on 3000 or another port needs no `.env` change.

### Deploy
- **Backend (Railway):** repo root as project root; `railway.json` points at `backend/Dockerfile`. Set `GEMINI_API_KEY`, `GEMINI_MODEL`, `CORS_ORIGINS=https://<your-vercel-app>.vercel.app`. The image ships the pre-built indexes; `/api/health` reports `bootstrap.status`.
- **Frontend (Vercel):** project root `frontend/`; set `NEXT_PUBLIC_API_URL=https://<railway-service>.up.railway.app`.
- The API container runs as a non-root user and writes only to `storage/` and `outputs/`. `PUBLIC_API_URL` should be the public API origin so the hyperlinks written into exported decks resolve.
- The workspace is desktop-only: below 1024 px the app is replaced by a notice asking the reader to open the link on a desktop browser (CSS media query in the root layout, no mobile layout is shipped).

---

## 2. What the advisor sees (one thread, one deck)

Two screens. The home page is a composer: type the company name, optionally add priorities and context, pick the policies to compare. The run page is a Cursor-style split: a **thread** on the left, the **deck** on the right, always visible.

The thread is the run narrated as an agent transcript. Each step is a collapsible **tool row** — one line stating what was found ("Researched Apollo Hospitals · 6 web-sourced facts", "Audited draft v2: 9 claims · Gate PASS") that expands to the full result (the profile with every statement typed FACT / INFERENCE / ASSUMPTION, the ranked exposures, the comparison, the fit scores, the evidence pack, the draft, the audit). The step in progress shows a scanning dot matrix (the Transitions.dev loader, 4×4 dots sharing one colour-pulse cycle), a shimmering label and its elapsed time; the same mark sits in the status bar and on the home page, animating only while something is actually running — still when idle or done, red when failed. Text the system has just written streams in word by word (the Transitions.dev streaming-text effect: each word resolves through opacity and a 1px blur, 60 ms apart, compressed so a long paragraph never takes more than ~2.4 s) — new tool rows, the questions it asks, a ⌘K proposal, and the bullets of a draft that has just landed on the slide. Text you wrote, history loaded on page open, and bodies you expand by hand simply appear; `prefers-reduced-motion` disables it. A pinned **composer** at the bottom of the thread answers whatever is pending — advisor context, a close-call choice by policy name, or regeneration feedback — so the run reads and drives like a chat. The system **asks a question in the thread** whenever it needs the advisor rather than making a silent choice:

| Question | When | Choices |
|---|---|---|
| Context check | Only a company name was given and web research is off, so the profile would be assumptions | Add industry / geography / headcount / notes / priorities and re-profile, or continue with assumptions (labelled as such) |
| Recommendation check | Two policies land within five fit points | Pitch either one (score, confidence and rationale shown side by side) or let the score decide |
| Your review | Every audited draft | Approve & export, edit slides, regenerate with feedback, reject. A FAIL gate lists each unsupported claim inline with **Evidence Passport / Edit / Remove bullet** actions; approving over a FAIL needs an explicit override with the reviewer's name |

Answers are posted back into the thread and the run resumes from its checkpoint. Editing happens on the deck: the slide canvas renders each slide in the same Marsh template as the exported PPTX (`slide-canvas.tsx` mirrors `pptx_builder.py` geometry, column planning and typography), with numbered footnotes that open their source. Select a bullet and press **⌘K** for an inline AI edit: type an instruction ("shorter", "mention the waiting period"), and the proposal comes back as a word diff with **Accept / Refine / Reject**. The rewrite is constrained the same way as the writer — a policy statement may only cite this run's evidence pack, and an instruction the evidence cannot support is refused with a note rather than invented; a proposal that drops a citation says so. Accepted edits stay local until **Preview audit** / **Save & re-audit** creates the next version. After approval the thread ends with the download links (client deck `.pptx`, audit `.md` and `.json`). The left rail lists recent pitches and which ones need you; the status bar at the bottom shows the API state, the configured Gemini model (flagged if it is not on the free-tier list), whether web research is on, and the size of the local index.

---

## 3. Architecture

```
frontend (Next.js) ──HTTP──> FastAPI ──> LangGraph StateGraph (SqliteSaver checkpoints, interrupt() for human review)
                                          │
   research_company → confirm_context (interrupt if name-only) → map_exposures → compare_policies
   → policy_fit_arena → confirm_recommendation (interrupt on close call) → evidence_pack
   → generate_pitch → audit_pitch → human_review (interrupt)
       approve → export_outputs      edit → audit_pitch      regenerate → generate_pitch      reject → end
```

Backend layout (`backend/app/`):
- `policies/` — `parser.py` (PyMuPDF + pymupdf4llm two-pass parsing, per-page table detection), `chunker.py` (structure-aware chunks with footnote/condition linking), `profiles/*.yaml` (per-brochure layout hints), `features.py` (39-feature comparison schema), `extraction.py` (Pydantic structured extraction → `FeatureFact`), `comparison.py`, `ingest.py`.
- `rag/` — `embeddings.py` (local ONNX by default, or Gemini embeddings), `vector_store.py` (FAISS, cosine), `lexical.py` (bm25s), `fusion.py` (RRF), `reranker.py` (ONNX cross-encoder), `retriever.py` (per-policy hybrid retrieval), `metadata_store.py` (SQLite: chunks, cache, runs, events, artifacts).
- `research/` — `search.py` (native crawler: Wikipedia summary + infobox and official site as reference sources; Brave → DuckDuckGo → Bing result-page parsers; polite page reader that keeps the sentences naming the company, each page marked readable or not; 24 h source cache) and company research (`generateCompanyProfile`). `exposure/` — exposure mapping and the exposure → comparison-feature mapping.
- `policy_fit/` — scenarios, arena, gap analysis, deterministic scoring and recommendation.
- `pitch/` — evidence pack, pitch generator, single-bullet rewriter (`rewrite.py`, behind the deck's ⌘K, same evidence constraint as the writer), python-pptx builder. The deck uses Marsh's own template as measured from the case-study brief (sky cover with the wordmark and ocean image; navy header band, Georgia titles, Calibri body, en-dash bullets, two-column "statements | assumptions" layout, footer rule with wordmark, copyright and page number; `pitch/assets/` holds the MarshMcLennan wordmark in navy and white plus the cover image extracted from the brief; the app sidebar uses the Marsh McLennan symbol from `frontend/public/`). Body text is sized to the space left above the numbered sources block, which is itself sized from its line count, so references never overlap content. Every source is a live hyperlink: brochure citations (and the superscript markers in the bullets) open the PDF at the cited page through `GET /api/policies/{id}/document#page=N` (`PUBLIC_API_URL` names the API host written into the file), and company statements link to the web pages the fact was read from (the evidence pack carries them as `C1..Cn` ids that the writer cites alongside the brochure `E` ids).
- `auditing/` — claim extraction, five auditors, aggregation, evidence passports, evidence gate (`auditPitchContent`).
- `graph/` — LangGraph state, nodes, builder. `services/` — LLM wrapper, run manager, bootstrap. `api/` — routes and request schemas.

API: `POST /api/client/analyze` (start a run), `GET /api/runs[/{id}[/events|/artifacts/{kind}]]` (`GET /api/runs/{id}` includes the pending `question`, if any), `POST /api/runs/{id}/answer` (answer whichever question is pending: context, close call or review — the `action` must be one of the offered options, otherwise 409), `POST /api/runs/{id}/audit-preview` (audit edited slides without saving), `POST /api/runs/{id}/rewrite` (the deck's ⌘K: rewrite one bullet under the evidence-pack constraint; nothing saved — 422 when the instruction cannot be supported, 429 on free-tier quota, 502 on a transient Gemini failure), `POST /api/runs/{id}/retry`, `GET /api/policies`, `GET /api/downloads/{run_id}/{kind}`, `GET /api/health`.

---

## 4. Design write-up

### 4.1 What the documents actually are
All four PDFs are retail individual/family-floater product brochures (HDFC ERGO Optima Secure+, Care Supreme, Aditya Birla Activ One, Niva Bupa ReAssure 2.0). None states group or corporate terms; two are two-page marketing sheets and one Niva page is image-only. So `is_group_policy` is `NOT_FOUND` for every policy and the recommendation carries an explicit assumption that the client would be offered a retail / employee-choice plan facilitated by Marsh. This is stated on the recommendation slide rather than hidden.

### 4.2 Structure-aware ingestion
Brochure meaning lives in tables, footnotes and pipe-separated lists, not paragraphs. Parsing runs twice per page (plain PyMuPDF for rows and `find_tables`, pymupdf4llm for markdown), guided by a small YAML profile per brochure (section hints, table pages, variant scope patterns). The chunker emits `section`, `clause`, `table`, `table_row`, `list_item`, `exclusion`, `waiting_period`, `eligibility`, `condition`, `add_on`, `discount`, `pricing`, `marketing_stat` chunks; footnote markers (`*`, `^`, `(7)`, superscripts) are normalised and each benefit chunk carries `footnote_refs` to its condition chunks, so "covered*" is retrieved together with the asterisk text. Result: 383 chunks (HDFC 178, Care 81, ABHI 81, Niva 43), 60 of them conditions and 10 exclusions. Every chunk keeps `policy_id, page, section, clause, content_type, chunk_id, parent_chunk_id, source_text`.

### 4.3 Balanced hybrid retrieval
Retrieval is always per policy: the same query runs independently inside each policy's chunk set (BM25 with Indian-unit synonyms + FAISS cosine over the same chunks → RRF → cross-encoder rerank blended with the fused prior), then linked conditions are attached. A 16-page brochure therefore cannot crowd out a 2-page one, and "no evidence" for a policy is an explicit outcome instead of a silent omission. Retrieval scores are exposed as `retrieval_relevance` and labelled as a ranking signal, never as accuracy.

### 4.4 Structured facts, not prose
The comparison engine works on `FeatureFact` records (status, value, limit, waiting period, co-pay, deductible, conditions, exclusions, add-on flag, variant scope, sources, confidence) produced by structured extraction against the retrieved evidence for each of 39 features × 4 policies. The model may only cite evidence ids it was shown; a fact without a cited source is downgraded to `NOT_FOUND`. `NOT_FOUND` is never collapsed into `EXCLUDED`. An offline heuristic extractor exists for keyless runs and is deliberately conservative.

### 4.5 Deterministic decision logic
Exposure → feature mapping, matrix building, scenario generation, arena evaluation, gap analysis, fit scoring and recommendation are plain Python. The fit score is `100 × clip(0.60·coverage + 0.15·evidence strength − 0.15·exclusion risk − 0.10·uncertainty)` over exposure-weighted scenarios (a scenario the brochure does not address counts a neutral 0.5 in coverage and is penalised again under uncertainty, so a thin brochure cannot win by silence); every component and its explanation is returned, close calls within five points are flagged, and the UI labels the score as decision support. The LLM never assigns a score.

### 4.6 Evidence Pack → pitch → audit
The pitch writer only sees an Evidence Pack (facts with ids and sources, deterministic comparison notes, gaps, assumptions, company statements labelled FACT). Policy bullets must cite pack ids; uncited policy bullets are dropped before audit and reported. Each bullet becomes an atomic claim typed POLICY / COMPANY / MARSH_POSITIONING / RECOMMENDATION / ASSUMPTION and runs through five auditors: citation (does the cited chunk exist, belong to this policy, and overlap the claim), numerical (every money / percent / duration / multiplier in the claim must appear in the cited text, with lakh and month/year normalisation), exclusion (claim asserts coverage of something extracted as excluded, or omits a stated condition or add-on status), contradiction (against other claims and against the matrix), and coverage (audit model, offline heuristic fallback). Statuses are `SUPPORTED / PARTIALLY_SUPPORTED / CONTRADICTED / NOT_FOUND / UNCERTAIN`; each claim gets an Evidence Passport. The gate fails on any contradicted policy claim or unsupported material claim. There is no automatic self-correction: every audited pitch goes to the advisor, who edits, regenerates (the auditor findings are fed back to the writer) or rejects. Claims the audit cannot trace are flagged for human review rather than rewritten by the machine.

### 4.7 Human in the loop
Every question is a LangGraph `interrupt()` with a typed payload (`context`, `close_call`, `review`; message + allowed options); state is checkpointed in SQLite so the advisor can come back later, and answers are validated against the offered options before the graph resumes. Edits are re-audited (a preview audit is available before saving), regeneration feeds the auditor findings back to the writer, approval triggers export, rejection ends the run. Progress events per node are written to SQLite and polled by the UI.

### 4.8 Token and cost discipline
LLM calls are limited to research synthesis, exposure mapping, structured extraction (cached per policy/feature/evidence hash and persisted), pitch writing and the coverage audit for material policy claims only. Everything else is deterministic. Token counts are logged per call.

### 4.9 Evaluation hooks
Each audit reports supported / partial / contradicted / not-found counts and a confidence score defined as the share of material claims fully supported; `audit_history` tracks the gate across pitch versions. The test suite checks real brochure values (e.g. Niva air ambulance INR 2,50,000, Care PED 36 months, HDFC maternity excluded), balanced retrieval, numerical and exclusion auditing, NOT_FOUND ≠ EXCLUDED, and the API flow through review → edit → approve → download.

### 4.10 Limitations and next steps
- Brochures are summaries; the audit verifies the pitch against the brochure, not against policy wording. The disclaimer says so on every slide.
- Offline mode is intentionally sparse. With keys, extraction and audits are materially richer.
- Company research (when enabled) depends on Gemini being able to fetch the search-results page and the result pages; pages that block the fetcher keep only their search snippet and are flagged as such to the model. Statements without a sourced snippet are labelled INFERENCE / ASSUMPTION, never presented as fact.
- The local stack (FAISS + BM25 + SQLite) is a design choice for this exercise; `vector_store.py` / `lexical.py` / `metadata_store.py` are the seams where pgvector or a managed store would plug in.

---

## 5. Repository map
```
backend/            FastAPI + LangGraph service (see app/ layout above), scripts/ingest_policies.py, Dockerfile
frontend/           Next.js app (src/app: composer + run page; src/components/thread = tool rows + composer, deck = slide canvas + ⌘K inline-edit, matrix.tsx = dot-matrix presence mark, stream.tsx = streaming text; src/lib/thread.ts builds the thread from run events, diff.ts = word diff)
data/policies/      The four insurer brochures
docs/               Case study brief
storage/            Local knowledge base: metadata/, vector_index/, bm25_index/ (committed); sqlite/, cache/ (runtime)
outputs/            presentations/ and audit_reports/ (sample_* committed)
tests/              pytest suite (run from backend/)
```

Secrets live only in `.env` (see `.env.example`); logs redact API keys; model outputs are structured schemas without chain-of-thought.
