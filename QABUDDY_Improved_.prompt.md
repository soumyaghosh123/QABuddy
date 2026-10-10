# QABuddy.ai — Build Prompt (Advanced Hybrid RAG for QA Engineers)

> **Updated 2026-10-10** to describe QABuddy as it is built and deployed today: https://qabuddy-mauve.vercel.app.
> The original brief, which asked for a DigitalOcean droplet and live JIRA MCP and left the stack open, is in git history (commit `d512ca9`).
> Detailed designs: `docs/superpowers/specs/2026-10-05-qabuddy-phase1-local-design.md` and `docs/superpowers/specs/2026-10-08-qabuddy-vercel-deploy-design.md`.

## 1. Context & Role

You are acting as a senior AI engineer. Build **QABuddy.ai**: a multi-source **Hybrid RAG** (Retrieval-Augmented Generation) system for the QA engineers at my company.

A QA engineer asks one question and gets a single **cited answer**, grounded in our Selenium framework, Playwright framework, test case repository, JIRA tickets, company docs, meeting notes, Lucid charts, PRDs and Jenkins results.

## 2. Objective

Build an end-to-end system that:

1. Ingests code and documents from a fixed folder structure, on my Windows laptop (CPU only, no Docker)
2. Chunks, embeds and indexes them in **Qdrant**, with hybrid retrieval (semantic + BM25 keyword + exact-ID match) and a cross-encoder rerank
3. Streams grounded answers **with `[n]` citations** through a web chatbot
4. Runs 24x7 on **Vercel** at a public URL, with hosted inference and the index mirrored from the laptop
5. Keeps token usage low: at most 6 sources and 2,800 source tokens per answer, no LLM call when nothing relevant is found, and an answer cache

## 3. Use Cases to Support

- **Onboarding:** new team members can self-serve answers
- **QA knowledge base:** a "KB brain" connected to our code repos — a one-stop resource system
- **Test failure analysis** and root cause analysis (RCA)
- **Test design:** test case creation, test plan drafting, test case review, and identifying missing test cases (gaps)
- **Process artifacts:** RTM building, bug triage, and test analyst workflows
- **Flaky tests:** feed in build/run data and retrieve flaky test history later
- **Framework coding help:** best practices, scripts, coding exercises, and doubt-busting at our framework level

**Target impact on test coverage:**

| Setup | Expected test coverage |
|---|---|
| GitHub Copilot + JIRA ID (MCP) | ~30–40% |
| GitHub Copilot + RAG (LLM) + JIRA ID | ~70–80% |

## 4. Data Sources (10)

Sources are declared in `config/sources.yaml`. Folder paths are relative to `data/`, which is git-ignored and never uploaded.

| # | Source id | Folder | Format | Parser | Phase |
|---|---|---|---|---|---|
| 1 | `selenium` | `07_Source_Codes/01_selenium_framework` | Java repo ([ATB13xSeleniumAdvanceFramework](https://github.com/PramodDutta/ATB13xSeleniumAdvanceFramework)) | code | 1 |
| 2 | `playwright` | `07_Source_Codes/02_playwright_framework` | TS/JS repo ([Advance-Playwright-Framework](https://github.com/PramodDutta/Advance-Playwright-Framework)) | code | 1 |
| 3 | `testcases` | `00_TestCases` | CSV / XLSX | testcases | 1 |
| 4 | `jira` | `01_JIRA_Tickets` | Jira "printable" Markdown exports | jira | 1 |
| 5 | `company_docs` | `02_Company_Docs` | PDF, MD | documents | 1 |
| 6 | `figma` | `06_Figma_Desings` | Figma exports | — (disabled) | **2** |
| 7 | `meetings` | `03_Meeting_Notes` | TXT, MD, VTT, SRT transcripts | meetings | 1 |
| 8 | `lucid` | `04_Lucid_charts` | JSON, CSV, TXT exports | lucid | 1 |
| 9 | `requirements` | `05_PRD_SRS_BRD_FRDs` | PDF, MD | documents | 1 |
| 10 | `jenkins` | `06_Jenkins_Logs` | console logs, JUnit XML | jenkins | 1 |

- **JIRA:** tickets come from file exports today. The loader sits behind a `JiraLoader` interface, so a live MCP + JQL loader can be added later without touching the rest of the pipeline.
- **Excluded everywhere:** `_README*` files, binaries, lockfiles, and generated folders such as `.git/`, `node_modules/`, `target/` and report folders.
- **Generated HTML reports** are also excluded from the code sources.
- **Duplicates:** byte-identical files are indexed once.

## 5. Fixed Technical Decisions

These are settled. Do not reopen them unless something is broken.

| Area | Choice | Why |
|---|---|---|
| Vector DB | **Qdrant** (Apache 2.0). Local server for development; **Qdrant Cloud** free tier in production. Collection `qabuddy_chunks`: named dense vector `dense` (cosine) + sparse vector `bm25` (IDF modifier), keyword payload indexes on `source`, `kind`, `ids_norm`, `path`, `module`, `parent_id`. | Native hybrid query with RRF fusion and payload filters in one call |
| Dense embeddings | **qwen3-embedding 0.6B** (Apache 2.0, 1024 dims). Ollama on the laptop for ingest; Cloudflare Workers AI `@cf/qwen/qwen3-embedding-0.6b` for cloud queries. Questions get an instruction prefix; documents are embedded as `header + text`. | Strong open model that runs on CPU; the same model is hosted for the cloud |
| Sparse / keyword | **BM25**. FastEmbed `Qdrant/bm25` for documents; a pure-Python query encoder, parity-tested against FastEmbed, in both profiles. | Exact matches on IDs, class names and error text |
| Reranker | Local: `Xenova/ms-marco-MiniLM-L-12-v2` (picked by the eval benchmark). Cloud: `@cf/baai/bge-reranker-base` (scores are 0–1 probabilities). | Cuts 30 candidates down to the best 6 |
| Answer LLM | **DeepSeek** `deepseek-flash` via its OpenAI-compatible API, non-thinking mode, temperature 0.2, max 700 answer tokens. Switchable to local Ollama `qwen3:4b-instruct-2507-q4_K_M` with `llm.provider`. | Local 4B on CPU was too slow; the data is dummy sample data, so a hosted LLM is acceptable |
| Parsing | tree-sitter (Java, TypeScript, JavaScript), Docling for PDF (pypdf fallback), openpyxl / CSV, and custom parsers for JIRA, meetings, Lucid and Jenkins | Structure-aware chunks with accurate citations |
| Backend | Python, FastAPI with Server-Sent Events, Typer CLI (`ingest`, `search`, `eval`, `serve`, `publish`), SQLite for the manifest, test runs, entities and answer cache | |
| Frontend | React 18, Vite, TypeScript; `react-markdown` + `remark-gfm` + `rehype-highlight` | |
| Hosting | Vercel Services: `web` (the Vite build) and `api` (`vercel_app.py`, FastAPI, 60 s max duration), plus a daily cron on `/api/health` | One public URL; a push to `main` deploys |

## 6. Chunk Size and Overlap (per source type)

Tokens are estimated as characters ÷ 4. Aim for 300–500 tokens, cap at 800, and merge pieces under 40 tokens.

| Source | One chunk = | Size (tokens) | Overlap |
|---|---|---|---|
| Code | one method / function / Playwright `test(...)`; one `class_context` per class (fields, locators, imports) | ≤ 800 | 0 (5 lines when a long method is split) |
| Repo Markdown | heading section | ≤ 800 | 15 % |
| Repo config (`pom.xml`, `testng*.xml`, `package.json`, `.properties`, …) | whole file | ≤ 800 | 0 |
| Test cases | 1 row, rendered as a readable template | 150–300 | 0 |
| JIRA | 1 ticket; split into Description and Comments only if over 800 | ≤ 800 | 1 paragraph if split |
| Docs / PRD | heading section → 400-token pieces | 400 | 60 |
| Meetings | consecutive speaker turns | ~350 | 1 turn |
| Lucid | 1 diagram, as steps and `A → B` flow text | ≤ 800 | 0 |
| Jenkins failure / error block | 1 failure: exception, message, up to 5 project stack frames | 200–400 | 0 |
| Build summary | 1 build: trigger, agent, revision, stages, result | ~200 | 0 |
| Test history | 1 test across builds: statuses, retries, first error, flaky flag and flip rate | ~200 | 0 |

Every chunk carries a one-line **context header**, which is embedded and BM25-indexed together with the text. For example:
`Selenium framework › pages/.../DashBoardPage.java › class DashBoardPage › loggedInUserName()`.

## 7. Preprocessing and Normalization

1. **Clean:** UTF-8, Unicode NFC, strip BOM / ANSI codes / `\r`, collapse blank lines, and remove export boilerplate (Jira footers, Rank, Votes and so on).
2. **Deduplicate:** identical files, repeated CSV columns, and identical error blocks within one build.
3. **Mask secrets before chunking:** values of keys such as password, token, api_key, secret, username or email in `.env`, `.properties`, YAML, JSON and `key=value` lines become `***`. They never reach the index.
4. **Terminology:**
   - For BM25, code names also index their parts (`testLoginPositiveVWO` → `test login positive vwo`).
   - At query time, `config/glossary.yaml` expands abbreviations (TC, POM, PW, RTM, RCA, CI, PRD, flaky, locator and so on).
5. **Entity tagging (two passes):** collect the known JIRA keys, test-case IDs, class and method names, and job + build pairs. Then tag each chunk's `ids_norm` so exact-ID questions hit directly.
6. **Incremental re-index:** a file hash manifest drives the work. Only changed files are re-parsed, and removed files lose their chunks. Any change bumps `index_version`, which invalidates the answer cache.

## 8. Retrieval and Answer Pipeline

1. **Query prep (no LLM):**
   - Find IDs and expand glossary terms.
   - Short or pronoun follow-ups ("it", "this", …) borrow the previous question, for retrieval only.
2. **Hybrid query:** one Qdrant call that fuses three prefetches with **RRF** into 30 candidates:
   - dense, top 30;
   - BM25, top 30;
   - exact-ID dense, top 10, only when IDs were found.

   The user's source filter applies to every prefetch.
3. **Rerank** the 30 candidates with the cross-encoder.
4. **Cap and cut:** at most 3 chunks per parent (file, ticket or build), then keep the top 6.
5. **No-answer gate:** if the top rerank score is below `min_rerank_score` (calibrated by `qabuddy eval --write`), reply "not found" without calling the LLM, and still show the closest matches.
6. **Prompt** (≤ ~3K tokens):
   - system rules;
   - up to 2 earlier exchanges;
   - numbered sources (`[n] citation — source`, header, text), lowest-ranked trimmed first, never cut mid-line;
   - the question.
7. **Rules for the LLM:**
   - Answer only from the sources and cite every claim with `[n]`.
   - Say so when the sources don't answer it.
   - Never invent IDs, file names or results.
   - Label any new test cases as **proposed**.
8. **After the answer:** drop citations outside 1..k and flag answers with none as `uncited`. Cache answers to questions without history, keyed on question + filter + `index_version`.
9. **API:**
   - `POST /api/chat` streams SSE events in this order: `sources` (sent instantly), `token` (repeated), `done` (citations used, timings, cached / no-answer flags), and `error` when needed.
   - Other endpoints: `GET /api/chunks/{id}`, `/api/health` (status plus stack pills), `/api/stats` (enabled sources) and `/api/examples`.

## 9. Web UI

The approved design is a warm, light chat app with a left sidebar. Mock-up: https://claude.ai/artifact/WLHt1DTSxpmypLMipM4VrE.

**Left sidebar (264 px)**
- Brand "🧪 QABuddy.ai", the tagline "Hybrid RAG · Selenium · Playwright · Jira", and a **New chat** button.
- **Mode** presets. Each one ticks a set of knowledge sources:

  | Mode | Sources |
  |---|---|
  | General QA | all |
  | Selenium | selenium |
  | Playwright | playwright |
  | Jira Tickets | jira |
  | CI / Jenkins | jenkins |
  | Docs & PRDs | company docs, requirements, meetings, Lucid |

- **Knowledge sources:** one checkbox per enabled source, loaded from `/api/stats`, each with an emoji icon.
  - All ticked means no filter; otherwise only the ticked sources are sent with the question.
  - The last ticked box can't be unticked.
  - Editing the boxes by hand clears the mode highlight, unless the result matches a preset.
  - A mode whose sources are all missing is hidden.

**Main column**
- **Stack strip** across the top, from `/api/health`: a status pill (Ready · N chunks / Degraded / API offline) and pills for Vector DB, Embeddings, Reranker and LLM. Failing pills turn red; when offline, they show the last known values in grey.
- **Empty state:**
  - Title "🧪 QABuddy.ai" and a one-line description.
  - The **example questions laid out as a spider web:** the first 6 `example: true` questions in `eval/questions.yaml`, served by `/api/examples`.
- **Spider web:**
  - An SVG of 4 rings that sag slightly between 12 radials, around a round 🧪 "Try asking" hub.
  - One spoke per question, starting top-left and running clockwise, with a white card at its end.
  - Each card shows a source tag guessed from the question (CI, TC, SE, JIRA, PW, …).
  - Hovering or keyboard-focusing a card lights its spoke in the accent colour and lifts the card. Clicking it asks the question.
  - When the container is narrower than 680 px, the cards stack along one vertical thread instead.
- **Conversation:**
  - Questions in terracotta bubbles; answers in white cards with Markdown and `[n]` citation chips.
  - A meta line with retrieval / first-word / total timings and badges for cached and not found.
  - Notices for LLM offline, an interrupted answer, and an uncited answer.
- **Composer:** an auto-growing textarea (Enter sends, Shift+Enter adds a new line) and **Ask** / **Stop**. The placeholder shows how many sources are selected.

**Sources panel (380 px, right)**
- Appears only once a conversation starts.
- Each card shows: number, source tag, citation, path + location, snippet and relevance. Cited cards are highlighted.
- Clicking a card or a chip opens the **source viewer** drawer: header, breadcrumb, and the full chunk with line numbers.

**Look**
- **Colours:**

  | Use | Colour |
  |---|---|
  | Background | cream `#FAF9F5` |
  | Surfaces | white |
  | Hub, spokes, focus rings | terracotta `#D97757` |
  | Filled buttons and bubbles | `#B5532F` |
  | Text on the tint | `#A84A2A` |
  | Tint | `#F6E7E0` |

- **Font:** system font (Segoe UI).
- **Dark mode:** a warm dark palette via `prefers-color-scheme`.
- **AI watermark** behind the conversation area:
  - a faint terracotta neural-network mesh (nodes, links, dashed circuit traces) that fades out toward the centre;
  - soft four-point sparkles in the corners.
  - Cards stay solid, so text contrast is unchanged.

**Phone (< 900 px)**
- The sidebar becomes a slide-in drawer, opened from a ☰ button in a mobile top bar that also has the brand and New chat.
- The stack strip scrolls sideways.
- The sources panel stacks under the chat (max 40 % of the height).
- The backdrop or Escape closes the drawer.

**Accessibility**
- Real buttons and checkboxes, and `aria-pressed` on modes.
- Keyboard focus mirrors hover.
- Reduced motion is respected.
- The conversation lives only in browser memory.

## 10. Deployment and Operations

```
Browser ──► Vercel project "qabuddy" (team qa-explorer)
             ├─ web : frontend/ (Vite build) ......... /
             └─ api : vercel_app.py (FastAPI, SSE) ... /api/*
                   ├─ Qdrant Cloud ── qabuddy_chunks + qabuddy_meta (index manifest)
                   ├─ Cloudflare Workers AI ── query embeddings + reranker
                   └─ DeepSeek ── answer stream

Laptop: qabuddy ingest → local Qdrant + Ollama ──(qabuddy publish)──► Qdrant Cloud
```

- **Settings profiles:** `config/settings.yaml` is the local default. `QABUDDY_PROFILE=cloud` overlays `config/settings.cloud.yaml`. Any key can be overridden with `QABUDDY_<SECTION>__<KEY>`.
- **Secrets live only in environment variables:**
  - `DEEPSEEK_API_KEY`
  - `QDRANT_URL`
  - `QDRANT_API_KEY`
  - `CLOUDFLARE_ACCOUNT_ID`
  - `CLOUDFLARE_API_TOKEN`

  I add them on Vercel myself with `vercel env add`. They never go in the repo or in chat.
- **Ingest stays on the laptop:**
  - Run `qabuddy ingest` (incremental; `--full` rebuilds), then `qabuddy publish` to mirror the changes to Qdrant Cloud. No redeploy is needed.
  - `data/` and `storage/` are never uploaded.
- **Releases:** a push to `main` on GitHub auto-deploys production. Work on a branch, and merge only after tests pass.
- **Access:** public URL, no login (my choice while the data is dummy sample data).
- **Local app:** `scripts/setup.ps1`, then `scripts/start.ps1`, serves http://localhost:8000 and keeps working unchanged.

## 11. Testing and Acceptance

- **Backend (pytest):**
  - Each parser is tested against the real files in `data/`: build #142 failures, the build #143 retry marked flaky, Playwright build #88, `VWO-26`, secret masking, and code line numbers.
  - Chunking tests cover size limits, overlap and Markdown heading paths.
  - The integration test is skipped when Qdrant or Ollama isn't running.
- **Eval (`qabuddy eval`):**
  - `eval/questions.yaml` holds answerable questions written from the real data, plus unanswerable ones.
  - **Gate:** hit@6 ≥ 85 % and every unanswerable question rejected. MRR is reported.
  - Run it locally and against the cloud profile; `--write` calibrates `min_rerank_score`.
  - **Known exception (accepted):** the salary question is refused by DeepSeek rather than by retrieval.
- **Frontend (Vitest + Testing Library):**
  - Existing: SSE parser, citation chips, stack strip.
  - New with the redesign: mode and filter rules, sidebar, spider web (spoke per question, hover/focus highlight, source tags) and app shell (sources panel hidden on the empty screen, drawer open/close).
- **Before every release:** the full test suites and `npm run build` must pass, then a manual smoke test asks 3 eval questions in the browser and checks streaming, citations and the source viewer.

## 12. Phase 2 — Plan Only (do not build yet)

- **Hourly auto-ingestion:** detect new test cases, repo commits or documents, re-index, then publish to the cloud automatically.
- **Figma** design ingestion (ER diagrams, user guides, wireframes).
- **Live JIRA** via MCP + JQL, as a new `JiraLoader` implementation.
- **Login and rate limiting** before any real company data goes on the public URL.

## 13. Constraints

- Embedding model, reranker and vector database are **open source**. Cloud queries may call the same open models on Cloudflare Workers AI.
- The answer LLM may be hosted (DeepSeek) **only while the data is dummy sample data**. With real confidential data, switch `llm.provider` to local Ollama, or agree on another option first.
- Available 24x7 on Vercel, and token-efficient (see §2).
- The laptop is Windows, CPU-only, with no Docker. Ingest must run there.

## 14. How to Respond

1. Read this prompt and the two design specs.
2. Present a short plan that follows §5–§11, show the folder structure for the 10 sources, and call out anything that conflicts or is missing. Don't reopen settled decisions without a concrete reason.
3. Wait for my approval.
4. Build Phase 1 end-to-end with tests first.
5. Verify against §11, then deploy as described in §10.
