# How a run works

One company name goes in. One recommendation and a short deck come out. Nothing is chosen by insurer name. The steps below run in this order, as one LangGraph thread. The app pauses only when it needs you.

## 1. You start a run

On the home page you type a company name. You can add industry, geography, headcount, notes, and priorities (maternity, accident cover, and so on). If you do not pick policies, all four brochures are in scope:

- ABHI Activ One
- Care Supreme
- HDFC ERGO Optima Secure+
- Niva Bupa ReAssure 2.0

`POST /api/client/analyze` starts the graph. The run page is a thread on the left and the deck on the right.

## 2. Company profile

If web research is on, the backend fetches Wikipedia, the company site, then Brave, DuckDuckGo, and Bing. Gemini writes a profile from that text plus anything you typed. Every statement is labelled:

- **FACT** — you typed it, or a fetched page says it, and the page is cited
- **INFERENCE** — derived from those facts
- **ASSUMPTION** — not sourced

If research runs and still finds no reliable public source, and you gave only a name, the thread says research was attempted and asks you to add what you know or to continue. Continuing means later company lines stay labelled as assumptions. The thread says “web research is off” only when web research is actually disabled. One search provider failing does not stop the others, and a missing Wikipedia page is not treated as a skipped search.

## 3. Exposures

The profile becomes a short list of health-cover needs. Three baseline needs are always there: employee hospitalisation, waiting periods, and out-of-pocket costs. Each priority you typed is added on top at a higher weight, and is marked as something you asked for. Industry text can add a few more (for example accident cover for a plant workforce).

## 4. The same test for every brochure

Each exposure becomes the same scenarios for every policy (room rent, maternity, air ambulance, and so on). The brochures were ingested earlier: PDF pages become chunks, stored in FAISS and BM25. For each feature, retrieval runs inside that policy only, then a structured fact is extracted: covered, conditional, partial, add-on, excluded, or not found. Not found is not treated as excluded.

## 5. Score, then recommend

Each policy gets the same score:

`100 × (0.60 × coverage + 0.15 × evidence strength − 0.15 × exclusion risk − 0.10 × uncertainty)`

- Covered = 1, conditional ≈ 0.75, partial = 0.5, add-on = 0.35, excluded = 0.
- A scenario the brochure does not mention counts as 0.5 inside coverage, and also raises uncertainty. It is never scored as an exclusion.
- A baseline scenario is used only when at least half the policies actually document it. A benefit that only the longest brochure mentions does not decide the winner.
- A scenario from a priority you typed always counts. Silence on that one hurts that policy.

The highest score is the recommendation. An exact tie breaks on policy id, not on brochure length or on “Policy A”. If two scores are within 5 points, the thread asks you which one to pitch.

The score is decision support. Gemini does not pick the winner.

## 6. Evidence pack, then the deck

Only the chosen policy’s cited facts go into the evidence pack, plus gaps and assumptions. The writer drafts four content slides: the client, exposure to benefit, why Marsh and what to watch, and one recommendation. A policy sentence with no evidence id is dropped. The cover is added only when the PowerPoint file is built, so the file has five slides and the on-screen deck shows the four content slides in the same frames.

## 7. Audit, then you

Every claim is checked against the text it cites (the chunk exists, the numbers match, an exclusion was not described as cover). The gate fails if a material policy claim is contradicted or unsupported.

You then approve and export, edit, regenerate, or reject. A failed gate can be exported only if you override it and put your name on it. Approval writes the `.pptx` and the audit `.md` / `.json`. Nothing is exported before that.
