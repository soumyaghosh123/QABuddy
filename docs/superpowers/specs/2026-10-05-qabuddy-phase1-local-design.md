# QABuddy.ai — Phase 1 (Local) Design

- **Date:** 2026-10-05
- **Status:** Design approved in chat; this written spec is awaiting review
- **Source brief:** [QABUDDY_Improved_.prompt.md](../../../QABUDDY_Improved_.prompt.md)

## 1. Goal

QA engineers ask one question in a web page and get one answer, with citations to the exact file and line range, test case ID, JIRA key, build, or document page it came from. The answers draw on the Phase 1 data in `data/`: both automation frameworks, test cases, JIRA tickets, company docs, meeting notes, Lucid charts, the PRD and Jenkins results.

Phase 1 runs locally on the owner's laptop. Every component is free and open source. The same code is later packaged for a DigitalOcean droplet.

### Success criteria

1. `qabuddy ingest` indexes every Phase 1 source. Files that fail to parse are reported and skipped, and they never abort the run.
2. On the eval set (§13), **≥ 85 %** of answerable questions have a correct source in the top 6, and **all** deliberately unanswerable questions return "not found".
3. In the browser, cited sources appear before the answer starts, the answer streams in, and every `[n]` citation opens the source text.
4. No secret value from `data.properties`, `.env` or similar files appears in any indexed chunk.

## 2. Scope

**In scope:**
- ingestion of the 9 Phase 1 sources from files;
- hybrid retrieval with reranking;
- a streaming chat answer from a local LLM, with citations;
- the React web UI;
- CLI commands for ingest, search, eval and serve;
- incremental re-indexing by file hash;
- the eval harness;
- Windows setup and start scripts.

**Out of scope (later):**
- Live JIRA MCP + JQL. The JIRA loader sits behind an interface so it can be added.
- Hourly auto-ingestion (Phase 2). Ingestion is already incremental, so the scheduler can be added later as a thin wrapper.
- Figma (Phase 2).
- Authentication.
- Docker / droplet packaging. That's the next step after local works, because Docker isn't installed on this laptop.
- LLM-written chunk context.
- Dedicated RTM and gap-analysis pages.

## 3. Environment and constraints

| Item | Value |
|---|---|
| Machine | Windows 11, i7-8665U (4 cores / 8 threads), 16 GB RAM, no GPU, ~24 GB free disk |
| Installed | Python 3.14 + `uv`, Node 24, git, Ollama 0.35 |
| Runtime model | Native processes, no Docker: Qdrant Windows binary, Ollama, FastAPI (uvicorn), static React build |
| Python | 3.12, installed and pinned by `uv` for library-wheel compatibility |
| Licences | Every model and library is open source. Models: Apache 2.0 or MIT. |

## 4. Architecture

```
INDEXING  (qabuddy ingest — full first run, then changed files only)
data/ ─► discover ─► parse (per source) ─► normalize ─► chunk ─► embed ─────────► Qdrant (dense + BM25)
         include/     code: tree-sitter      clean,       + context  dense: qwen3-embedding:0.6b (Ollama)
         exclude,     CSV rows, JIRA .md,    mask         header     sparse: BM25 (FastEmbed Qdrant/bm25)
         hash dedupe  PDF: Docling, VTT,     secrets,
                      Lucid, JUnit/logs      tag IDs  ─► SQLite: file manifest, test runs, entities, cache

ASKING  (React UI ─► FastAPI POST /api/chat, Server-Sent Events)
question ─► query prep ─► Qdrant hybrid query ─────────────► rerank (30 → 6) ─► SSE "sources" (instant)
            IDs, glossary   dense 30 + BM25 30 + exact-ID 10,                    │
                            fused with RRF                                       ▼
                                          qwen3:4b-instruct-2507 (Ollama) ◄─ prompt (≤ ~3K tokens)
                                          ─► SSE "token" stream ─► SSE "done" (citation check, timings)
```

### Components

| Unit | Responsibility | Depends on |
|---|---|---|
| `ingest/discover.py` | Walk `data/` using `config/sources.yaml`, apply include/exclude, hash files, skip byte-identical duplicates | config |
| `ingest/parsers/*.py` | Turn one file into `Chunk` records (text + location + metadata). One module per source kind. Each parser is a pure function `parse(path, ctx) -> list[Chunk]`. | normalize, chunking |
| `ingest/normalize.py` | Text cleanup, secret masking, identifier splitting, ID extraction | glossary, entity set |
| `ingest/chunking.py` | Shared token estimate, heading-aware Markdown splitter, size-limited splitter with overlap | — |
| `ingest/pipeline.py` | Order the work, two-pass entity tagging, call the embedder and store, update the manifest | all of the above |
| `index/embedder.py` | Dense vectors (Ollama `/api/embed`, batched), sparse BM25 vectors (FastEmbed) | Ollama, FastEmbed |
| `index/store.py` | Qdrant collection schema; upsert, delete and hybrid query | Qdrant |
| `retrieval/query.py` | Extract IDs, glossary expansion, follow-up handling | entity set, glossary |
| `retrieval/reranker.py` | Cross-encoder scoring (FastEmbed `TextCrossEncoder`) | FastEmbed |
| `retrieval/search.py` | Hybrid query → rerank → diversity cap → top-k | store, reranker, query |
| `answer/prompt.py` | System prompt, numbered sources, token budget | — |
| `answer/llm.py` | Streams chat from Ollama. Swappable interface (`LLMClient.stream(messages)`). | Ollama |
| `db.py` | SQLite access (manifest, test runs, entities, answer cache, meta) | — |
| `api.py` | FastAPI app and SSE; serves the built UI | search, answer, db |
| `cli.py` | `ingest`, `search`, `eval`, `serve` (Typer) | pipeline, search, api |
| `frontend/` | React chat UI | `/api/*` |

## 5. Data sources and folder mapping

`config/sources.yaml` replaces the old `data/sources.yaml`, which used folder names that no longer exist and is deleted. All paths below are relative to `data/`.

| # | Source id | Folder | Parser | Phase |
|---|---|---|---|---|
| 1 | `selenium` | `07_Source_Codes/01_selenium_framework` | code | 1 |
| 2 | `playwright` | `07_Source_Codes/02_playwright_framework` | code | 1 |
| 3 | `testcases` | `00_TestCases` | testcases | 1 |
| 4 | `jira` | `01_JIRA_Tickets` | jira (Markdown exports) | 1 |
| 5 | `company_docs` | `02_Company_Docs` | documents | 1 |
| 6 | `figma` | `06_Figma_Desings` | — (disabled) | 2 |
| 7 | `meetings` | `03_Meeting_Notes` | meetings | 1 |
| 8 | `lucid` | `04_Lucid_charts` | lucid | 1 |
| 9 | `requirements` | `05_PRD_SRS_BRD_FRDs` | documents | 1 |
| 10 | `jenkins` | `06_Jenkins_Logs` | jenkins | 1 |

### Exclusions

- **Everywhere:**
  - files named `_README*`
  - binaries: `.zip`, `.webm`, `.png`, `.jpg`, `.gif`, `.ico`, `.pdf` inside repos, `.jar`, `.class`
  - lockfiles: `package-lock.json`, `*.lock`
- **Code sources, excluded folders:** `.idea/`, `.git/`, `node_modules/`, `target/`, `tta-report/`, `test-results/`, `playwright-report/`, `allure-results/`, `.husky/`
- **Code sources, also excluded:** `*.html` (generated reports)
- **Code sources, included extensions:** `.java`, `.ts`, `.js`, `.md`, `.mdx`, `.xml`, `.json`, `.yml`, `.yaml`, `.properties`, `.env` (secret-masked)
- **Not configured as sources:** `07_Source_Codes/Advance-Playwright-Framework-main`, which is byte-identical to source 2, and `07_Source_Codes/ATB13xSeleniumAdvanceFramework`, which is empty. Hash dedupe would also catch the first.

## 6. Ingestion

### 6.1 The `Chunk` record

| Field | Example | Notes |
|---|---|---|
| `chunk_id` | UUIDv5 of `path#locator` | Stays the same across runs, so re-ingesting updates the chunk in place |
| `source` | `selenium` | Source id from §5 |
| `kind` | `method`, `class_context`, `md_section`, `config`, `testcase`, `ticket`, `doc_section`, `transcript`, `diagram`, `test_failure`, `error_block`, `build_summary`, `test_history` | |
| `path` | `07_Source_Codes/01_selenium_framework/.../DashBoardPage.java` | Relative to `data/` |
| `citation` | `DashBoardPage.java:18-24`, `LOGIN-001`, `VWO-26`, `PRD p.4 › Login`, `vwo-selenium-regression #142 › testLoginPositiveVWO`, `Daily Standup 2026-04-14 @00:00:01` | Label shown in the UI |
| `location` | `{line_start, line_end}` / `{page}` / `{row}` / `{build}` / `{time}` | |
| `title`, `breadcrumb` | `Automation Coding Standards › 1. Selenium › 1.3 Waits` | |
| `parent_id` | file path; ticket key; `job#build`; test case ID | Used for the diversity cap |
| `module` | `Login` | From test-case ID prefix, Java package, or folder name |
| `ids_norm` | `["vwo-26","testloginpositivevwo","dashboardpage"]` | IDs found in the chunk, lowercased |
| `date` | `2026-04-07` | When known |
| `header` | one-line context (§6.4) | Embedded together with the text |
| `text` | chunk body shown to users | |
| `content_hash` | sha256 of header + text | |

### 6.2 Parsers

**Code (`code.py`):** tree-sitter, using the Java, TypeScript and JavaScript grammars.
- **Java:** one chunk per method or constructor. A class's fields, locators and annotations are merged into one `class_context` chunk with the package and imports.
- **TypeScript / JavaScript:**
  - Functions, class methods and top-level `const x = (...) =>` become `method` chunks.
  - **Playwright `test(...)` and `test.describe(...)` calls** are units too. Each test's chunk carries its describe title.
- **Small neighbours:** consecutive pieces under 40 tokens are merged into one chunk of up to 400 tokens.
- **Long methods:** methods over 800 tokens are split at statement boundaries, overlapping by 5 lines.
- **Other repo files:**
  - `.md` / `.mdx` use the Markdown splitter.
  - `pom.xml`, `testng*.xml`, `package.json`, `tsconfig.json`, workflow YAML, `.properties` and `.env` are one `config` chunk per file, split by lines above 800 tokens.
  - Other `.json` files under `src/` (test data) are handled the same way.
- **Citation:** file name plus line range.

**Test cases (`testcases.py`):** `.csv` (read as utf-8-sig) and `.xlsx` (openpyxl).
- **One row = one chunk.**
- **Columns** are mapped by header name: ID, description/title, precondition, steps, expected, actual, status, priority, automated, comments.
- **Duplicate column names:** if the second column with the same name has identical content, it's dropped. Otherwise it's kept, labelled "(2)".
- **Steps:** `" | "`-separated steps become numbered lines.
- **Module** comes from the ID prefix (`LOGIN-001` → `Login`).
- **Text template:**

  ```
  Test case LOGIN-001 — Verify Login - page load
  Module: Login | Priority: Medium | Automated: Yes | Status: Not Executed
  Precondition: …
  Steps:
  1. …
  Expected: …
  ```

**JIRA (`jira.py`):** reads Jira "printable" Markdown exports.
- **Header line** `[KEY] Summary Created: … Updated: …` gives the key and summary.
- **Fields** come from tab-separated `Name:\tvalue` lines: Status, Project, Components, Type, Priority, Reporter, Assignee, Resolution, Labels.
- **Sections:** Description and Comments (`Comment by X [ date ]`).
- **Removed:** "Back to previous view", the "Generated at …" footer, Rank, Votes, estimates.
- **Chunking:** one ticket = one chunk. Over 800 tokens, it's split into Description and Comments chunks with the header repeated and 1 paragraph of overlap.
- **Fallback:** if the header line is missing, the key comes from the file name.
- **Interface:** `JiraLoader` (with `MarkdownExportLoader` now), so `McpJqlLoader` can be added later.

**Documents (`documents.py`):** used for company docs and requirements.
- `.md` uses the Markdown splitter.
- `.pdf` goes through Docling → Markdown, keeping per-element page numbers, then the same splitter. If Docling fails on a file, it falls back to `pypdf` page text with page numbers.
- **Citation:** document title plus page (PDF) or section.

**Meetings (`meetings.py`):**
- `.vtt`: cues become `(time, speaker, text)` from `<v Speaker>`.
- `.txt`: lines `[hh:mm:ss] Speaker: text` become turns.
- `.md`: Markdown splitter, with the meeting header.
- **Date** comes from the `YYYY-MM-DD_` file-name prefix or a `Date:` line. Attendees come from an `Attendees:` line or the set of speakers.
- **Chunking:** consecutive turns until about 350 tokens, overlapping by 1 turn.
- **Citation:** `<title> <date> @<start time>`.

**Lucid (`lucid.py`):**
- **`.json` `{title, nodes[], edges[]}`** becomes text like this:

  ```
  Diagram: <title>
  Steps:
  - <label>
  Flow:
  - <from label> → <to label> (<edge label>)
  ```

- **`.csv` (Lucid shape export):**
  - Rows with `Text Area 1` become nodes.
  - Rows with `Line Source` and `Line Destination` become edges.
  - The `Page` row gives the title.
- **`.txt`:** prose, split on blank-line sections.
- **Size:** one chunk per diagram up to 800 tokens; above that, split per page or flow.

**Jenkins (`jenkins.py`):**
- **JUnit XML** (`<testsuite>`):
  - Job and build come from the `jenkins.build` property (`vwo-selenium-regression #142`) or the file-name suffix.
  - Every `<testcase>` becomes a `test_runs` row.
  - Every failure becomes a `test_failure` chunk: header, exception type, message, and up to 5 project stack frames.
  - **Project frames** are frames whose class doesn't start with `java.`, `jdk.`, `sun.`, `org.openqa.`, `org.testng.`, `org.junit.` or `node:`.
- **Console logs** (`<job>_<build>.log`):
  - **Build summary chunk:** started by, agent, revision, commit message, the stages in order, and the final result.
  - **Test-result lines become `test_runs` rows:**
    - TestNG: `PASSED|FAILED|SKIPPED: fqcn.method`, plus `RetryListener … attempt n`.
    - Playwright: `✓|✘ <n> [project] › file:line:col › title [(retry #k)] (dur)`.
  - **Error blocks** (`error_block` chunks) start at an exception or `Error:` line and include the following stack lines (up to 40), plus 3 lines of context before. Identical blocks within one build appear once, with a count.
- **Merging:** rows describing the same run, from the XML and the log for the same build, are merged on `(job, build, test, attempt)`.
- **Test history:**
  - Built after all Jenkins files are parsed. There's one `test_history` chunk per test across builds: each build's final status, retries and first error line.
  - **Flaky** if the test passed after a retry in any build, or its status differs between builds.
  - The **flip rate** is status changes ÷ (builds − 1).

### 6.3 Normalization

1. **Clean.**
   - Decode as UTF-8 and normalize to Unicode NFC.
   - Strip the byte-order mark, ANSI escape codes and `\r`.
   - Collapse runs of more than 2 blank lines.
   - Remove trailing whitespace.
   - Remove the boilerplate listed in §6.2.
2. **Remove duplicates.**
   - Byte-identical files are indexed once, in the first source in config order.
   - Repeated CSV columns and repeated error blocks are handled as in §6.2.
3. **Mask secrets.**
   - Applies to `.env`, `.properties`, YAML and JSON config, and `key=value` / `key: value` / `"key": "value"` lines anywhere.
   - Keys matching `(?i)(pass(word)?|pwd|secret|token|api[_-]?key|auth|credential|username|user(name)?|email)` get `***` as their value.
   - Masking happens before chunking, so masked values never reach the index.
4. **Terminology.**
   - For BM25 only, code names are expanded: camelCase, PascalCase, snake_case and dotted names also add their parts (`testLoginPositiveVWO` → `test login positive vwo`).
   - At query time, abbreviations in `config/glossary.yaml` are expanded: TC, POM, PW, RTM, RCA, SUT, E2E, CI, PR, flaky ↔ flaky test, and so on.
5. **Entity tagging (two passes).**
   - **Pass 1** collects the known entities:
     - JIRA keys from ticket files;
     - test-case IDs from the CSV;
     - class and method names from code;
     - job + build pairs from Jenkins.
   - **Pass 2** tags every chunk's `ids_norm`:
     - ticket-like keys found by the regex `\b[A-Z][A-Z0-9]{1,9}-\d+\b`;
     - known identifiers found by token match;
     - build references (`#142`, `build 142`).
   - Entities are stored in SQLite.
   - Incremental ingests re-tag only changed files. `--full` re-tags everything.

### 6.4 Chunk sizes and overlap

Tokens are estimated as characters ÷ 4.

**General rule:**
- Aim for 300–500 tokens. The maximum is 800. Pieces under 40 tokens are merged.
- The embedding model's 32K window isn't the limit. Retrieval precision and the CPU LLM's prompt budget (6 × ~400 ≈ 2.4K tokens) are.

| Source | One chunk = | Size | Overlap |
|---|---|---|---|
| Code | method / function / Playwright test; `class_context` per class | ≤ 800 | 0 (5 lines when a long method is split) |
| Repo Markdown | heading section | ≤ 800 | 15 % |
| Repo config | whole file | ≤ 800 | 0 |
| Test cases | 1 row | 150–300 | 0 |
| JIRA | 1 ticket | ≤ 800 | 1 paragraph if split |
| Docs / PRD | heading section → 400-token pieces | 400 | 60 tokens |
| Meetings | consecutive turns | ~350 | 1 turn |
| Lucid | 1 diagram | ≤ 800 | 0 |
| Jenkins failure / error block | 1 failure | 200–400 | 0 |
| Build summary | 1 build | ~200 | 0 |
| Test history | 1 test | ~200 | 0 |

**Context header** (embedded and BM25-indexed together with the text; shown in the source viewer):
`<Source label> › <path or title> › <class/section/ticket> › <method/row/turn>`. For example:
`Selenium framework › pages/pageObjectModel/vwo/improved_POM/DashBoardPage.java › class DashBoardPage › loggedInUserName()`.

### 6.5 Embedding and the Qdrant collection

- **Dense embeddings:** Ollama `qwen3-embedding:0.6b`, using `/api/embed` in batches of 16. Documents are embedded as `header + "\n" + text`.
  - **Questions** get the instruction prefix `Instruct: Given a QA engineer's question, retrieve the code, test cases, tickets, logs or documents that answer it\nQuery: `.
  - The vector **dimension is read from the first response** (expected 1024) and stored in `meta`.
- **Sparse embeddings:** FastEmbed `Qdrant/bm25`. Documents are indexed as `header + text + identifier expansions`, and questions as the query text with glossary expansions.
- **Collection** `qabuddy_chunks`:
  - named dense vector `dense` (cosine);
  - sparse vector `bm25` with `modifier = IDF`;
  - payload holds the `Chunk` fields except `content_hash`;
  - keyword payload indexes on `source`, `kind`, `ids_norm`, `path`, `module`, `parent_id`.
- **Storage:** the Qdrant process stores its data in `storage/qdrant/`.

### 6.6 Incremental re-index

- **SQLite `files` table:** `path, source, file_hash, size, mtime, chunk_ids(json), status, error, indexed_at`.
- **Changed or new files:** re-parse, upsert the chunks, and delete chunk ids that are no longer produced.
- **Removed files:** delete their chunks.
- **Jenkins:** any change rebuilds all `test_history` chunks.
- **`--full`:** drops the collection and tables, then rebuilds everything.
- **Index version:** each ingest that changed anything increments `meta.index_version`, which invalidates the answer cache.

### 6.7 SQLite schema (`storage/qabuddy.db`)

- `files` (§6.6)
- `test_runs(job, build, suite, test_id, class_name, method, title, file, status, attempt, duration_s, error_type, error_message, source_path)` — unique on `(job, build, test_id, attempt)`
- `entities(id_norm, display, kind, source_path)` — `kind` is one of `jira_key`, `testcase_id`, `class`, `method`, `build`
- `answer_cache(key, index_version, question, filters, answer, sources_json, created_at)`
- `meta(key, value)` — `index_version`, `embed_dim`, `last_ingest_at`

## 7. Retrieval

1. **Query prep** (no LLM):
   - Find IDs in the question by regex and by known entities, and expand them with the glossary for the BM25 query.
   - **Follow-ups:** if there's history and the question is under 6 words or starts with or contains it/this/that/they/those, the previous user question is put in front of it **for retrieval only**.
2. **Hybrid query.** One Qdrant `query_points` call:
   - `prefetch` dense, limit 30, plus `prefetch` BM25, limit 30;
   - plus, when IDs were found, a dense prefetch with a `ids_norm` match-any filter, limit 10;
   - merged with `Fusion.RRF`, limit 30.
   - The user's source filter (UI buttons) is applied to every prefetch.
3. **Rerank** the 30 candidates with the cross-encoder on `header + text`.
4. **Diversity cap:** at most 3 chunks per `parent_id`. Test-case rows have their own ID as `parent_id`, so the cap doesn't limit them.
5. **Cut-off:**
   - Keep the top 6.
   - If the top rerank score is below `min_rerank_score`, return **no answer** and don't call the LLM. Any results are still shown as "closest matches".
   - **Setting `min_rerank_score`:** at implementation time, it's set to the highest value that passes all answerable eval questions while rejecting all unanswerable ones. If no such value exists, it's set by the answerable questions and the misses are reported.

**Reranker:** `Xenova/ms-marco-MiniLM-L-12-v2` (Apache 2.0, 120 MB) and `BAAI/bge-reranker-base` (MIT, 1 GB) are both run on the eval set. The one with the higher hit@6 becomes the default; if they're within 2 points, the faster one. The result goes in `settings.yaml`.

## 8. Answer generation

- **Model:** Ollama `qwen3:4b-instruct-2507-q4_K_M`, called through `/api/chat` with `stream=true`.
  - **Options:** `num_ctx 8192`, `temperature 0.2`, `num_predict 700`.
- **Prompt layout:**
  1. system rules;
  2. up to 2 earlier exchanges, with each previous answer cut to 300 tokens;
  3. a numbered source block;
  4. the question.
- **Source budget:** 2,800 tokens. The lowest-ranked sources are trimmed first. A source is never cut mid-line.
- **Source block format:**

  ```
  [n] <citation> — <source label>
  <header>
  <text>
  ```

- **System rules:**
  - Answer only from the sources, citing each claim with `[n]`.
  - If the sources don't answer it, say that and suggest where to look.
  - Be concise: bullets for steps, code blocks copied from sources.
  - Never invent IDs, file names or results.
  - For test design requests, base new test cases on the cited requirements and existing tests, and label them as **proposed**.
- **Citation check after the answer:**
  - Collect the `[n]` markers and drop any outside 1..k.
  - If none remain, mark the answer `uncited`. The UI shows a warning badge.
- **Cache:**
  - **Key:** sha256 of the normalized question, the source filter and `index_version`.
  - Only answers to questions without history are cached.
  - A cache hit replays the stored sources and answer.

## 9. API

Base URL `http://localhost:8000`. The FastAPI app also serves `frontend/dist` at `/`.

| Endpoint | Request | Response |
|---|---|---|
| `POST /api/chat` | `{question: str, sources?: str[], history?: [{role, content}]}` | `text/event-stream` (see below) |
| `GET /api/chunks/{chunk_id}` | — | `{chunk_id, citation, source, kind, path, location, title, breadcrumb, header, text}` |
| `GET /api/health` | — | `{qdrant: {ok, error?}, ollama: {ok, error?}, models: {embed: bool, llm: bool}, index: {chunks, last_ingest_at, index_version}}` |
| `GET /api/stats` | — | `{by_source: {source: count}, by_kind: {...}, files: n, failed_files: [{path, error}]}` |

**SSE events, in order:**
1. `sources`: `[{n, chunk_id, citation, source, kind, path, location, snippet (≤ 300 chars), score}]`
2. `token`: `{t}`, repeated
3. `done`: `{citations_used: int[], uncited: bool, no_answer: bool, cached: bool, timings_ms: {retrieve, rerank, llm_first_token, total}}`
4. `error`: `{code: "llm_unavailable" | "index_unavailable" | "bad_request" | "internal", message}`

**Failure behaviour:**
- **LLM unavailable:** `sources` is sent as usual, then `error` with `llm_unavailable`, then `done`. The UI shows "LLM offline: showing matching sources".
- **Qdrant down:** `error` with `index_unavailable`.

## 10. Frontend

React 18, Vite and TypeScript. Markdown is rendered with `react-markdown` and `remark-gfm`; code blocks are highlighted with `rehype-highlight`.

- **Layout:**
  - a top bar with the name, source filter buttons (All, Selenium, Playwright, Test cases, JIRA, Docs, Meetings, Lucid, Requirements, Jenkins) and a health dot;
  - the chat panel on the left;
  - the sources panel on the right.
- **Empty state:** 6 example questions taken from the eval set.
- **While an answer streams:**
  - Source cards appear as soon as `sources` arrives: type icon, citation, path, snippet and score.
  - The answer streams in, with each `[n]` rendered as a clickable chip that highlights its card.
  - Clicking a card or chip opens the source viewer: a drawer with the header, breadcrumb and full chunk text, with line numbers for code.
- **State:** the conversation lives in browser memory only. "New chat" clears it.
- **Requests:** the chat request is a POST with SSE read from the stream via `fetch` + `ReadableStream` (EventSource can't POST).
- **Dev server:** Vite proxies `/api` to `localhost:8000`.

## 11. Error handling

| Situation | Behaviour |
|---|---|
| File fails to parse | Logged, recorded in `files.status = failed` with the error, ingest continues, listed by `/api/stats` and the ingest summary |
| Ollama not running / model missing during ingest | Ingest stops before writing anything, with the exact `ollama pull` / start command to run |
| Qdrant not running | CLI and API report `index_unavailable` with the start command |
| LLM error or timeout (300 s) mid-stream | The partial answer is kept, followed by `error` and `done`. The UI shows "answer interrupted". |
| Empty question or > 2,000 characters | `400 bad_request` |
| No results / below threshold | `no_answer` response without an LLM call |

## 12. Configuration (`config/settings.yaml`)

```yaml
data_dir: data
storage_dir: storage
qdrant:   {url: http://localhost:6333, collection: qabuddy_chunks}
ollama:   {url: http://localhost:11434, embed_model: "qwen3-embedding:0.6b",
           llm_model: "qwen3:4b-instruct-2507-q4_K_M", num_ctx: 8192,
           temperature: 0.2, max_answer_tokens: 700, timeout_s: 300}
reranker: {model: "Xenova/ms-marco-MiniLM-L-12-v2"}   # final value set by §7 benchmark
retrieval: {dense_k: 30, sparse_k: 30, id_k: 10, rerank_candidates: 30,
            top_k: 6, max_per_parent: 3, min_rerank_score: 0.0}  # calibrated per §7
chunking: {target_tokens: 400, max_tokens: 800, min_tokens: 40, prose_overlap_tokens: 60}
prompt:   {max_source_tokens: 2800, history_turns: 2}
api:      {host: 127.0.0.1, port: 8000}
```

Any key can be overridden by an environment variable with the `QABUDDY_` prefix and `__` as the nesting separator.

## 13. Testing and acceptance

- **Unit tests (pytest).** Each parser is tested against the real files in `data/`.
  - **Test cases:** 500 records; the duplicate columns collapse; `LOGIN-001` maps to module Login.
  - **JUnit file for build #142:** 4 runs, 2 failures, with project frames such as `DashBoardPage.java:20`.
  - **Build #143 log:** a retry is detected, and the test history marks `testLoginPositiveVWO` as flaky.
  - **Playwright build #88:** retries are parsed, and the checkout test ends as `failed`.
  - **JIRA:** `VWO-26` gets its summary and status; `QAB-102` keeps its comments.
  - **VTT:** 3+ speaker turns, with times.
  - **Lucid:** JSON and CSV come out as flow text with arrows.
  - **Secrets:** no value from `data.properties` or `.env` survives masking.
  - **Code:** `DashBoardPage.loggedInUserName` becomes one chunk with correct line numbers, and Playwright `test(...)` blocks are chunked.
- **Chunking tests:** size limits, overlap and Markdown heading paths.
- **Integration test.** Uses Qdrant and Ollama, and is skipped with a message if they aren't running. It ingests a small fixture subset into a temporary collection, then searches for `VWO-26` and expects that ticket first.
- **Eval (`qabuddy eval`):** `eval/questions.yaml` holds about 20 answerable questions written from the real data, each with expected `path` or ID substrings, plus 3 unanswerable ones.
  - **Answerable questions cover:**
    - RCA for build #142;
    - login test cases;
    - the wait standard;
    - `VWO-26`;
    - Playwright checkout flakiness;
    - `DriverManager`;
    - the CI pipeline stages;
    - the triage meeting decision;
    - the PRD;
    - writing a Playwright API test;
    - the gaps mentioned in the stand-up.
  - **Reported:** hit@6, MRR, and no-answer accuracy.
  - **Gate:** hit@6 ≥ 85 % and 3/3 unanswerable questions rejected.
- **Frontend:** Vitest tests for the SSE parser and citation-chip rendering.
- **Manual smoke test:** start the app, ask 3 eval questions in the browser, and confirm streaming, citations and the source viewer work.

## 14. Setup and run (Windows, no Docker)

- **`scripts/setup.ps1`** (safe to run more than once):
  1. `uv python install 3.12` and `uv sync` in `backend/`.
  2. Download the Qdrant v1.19.1 `qdrant-x86_64-pc-windows-msvc.zip` into `tools/qdrant/`.
  3. `ollama pull qwen3-embedding:0.6b` and `ollama pull qwen3:4b-instruct-2507-q4_K_M`.
  4. `npm ci` and `npm run build` in `frontend/`.
- **`scripts/start.ps1`:**
  1. Start `tools/qdrant/qdrant.exe` with its storage in `storage/qdrant`, unless it's already running.
  2. Check that Ollama is up.
  3. Run `uv run qabuddy serve`.
  4. Open `http://localhost:8000`.
- **First use:** `uv run qabuddy ingest`, then `uv run qabuddy eval`.
- **Disk:** estimated ≈ 6 GB in total (models ~3.1 GB, Python dependencies including Docling ~2 GB, Qdrant + Node tooling ~0.5 GB).
- **Time:** the first full ingest is estimated at 10–20 minutes on this laptop. Both numbers are measured and recorded in the README.

## 15. Repository layout

```
QaBuddy/
├── QABUDDY_Improved_.prompt.md
├── README.md
├── config/          sources.yaml · glossary.yaml · settings.yaml
├── data/            (owner's data; git-ignored)
├── backend/
│   ├── pyproject.toml
│   ├── qabuddy/     config.py · db.py · api.py · cli.py
│   │   ├── ingest/  discover.py · normalize.py · chunking.py · pipeline.py · models.py
│   │   │   └── parsers/  code.py · testcases.py · jira.py · documents.py · meetings.py · lucid.py · jenkins.py
│   │   ├── index/   embedder.py · store.py
│   │   ├── retrieval/ query.py · reranker.py · search.py
│   │   └── answer/  prompt.py · llm.py
│   └── tests/
├── frontend/        (Vite React TS app)
├── eval/            questions.yaml
├── scripts/         setup.ps1 · start.ps1
├── docs/superpowers/specs/  (this file)
├── storage/         (git-ignored: Qdrant data, SQLite)
└── tools/           (git-ignored: Qdrant binary)
```

## 16. Decision log

| Decision | Choice | Main reason | Rejected alternatives |
|---|---|---|---|
| Embedding model | `qwen3-embedding:0.6b` (Ollama) | Apache 2.0, 32K context, from the family that leads the multilingual and code benchmarks, CPU-sized, no PyTorch | bge-m3 (lower MTEB), nomic-embed-text (weaker), CodeRankEmbed (code only), jina v3/v4 (non-commercial) |
| Vector DB | Qdrant 1.19.1 | Native hybrid search (dense + BM25 + RRF) in one call, payload filters, single process, Windows binary | Milvus (3 services, ≥ 8 GB), pgvector (no native hybrid), Weaviate (heavier) |
| Keyword search | FastEmbed BM25 + Qdrant IDF | Exact IDs and error strings | Learned sparse (SPLADE) — weaker on identifiers |
| Reranker | MiniLM-L-12 vs bge-reranker-base, chosen by eval | CPU budget | bge-reranker-v2-m3 (not in FastEmbed, slow on this CPU — droplet option) |
| LLM | `qwen3:4b-instruct-2507` Q4 | Free, Apache 2.0, no thinking delay, fits in 16 GB | Gemma 4 E4B (6–9.6 GB), 7B+ models (too slow on CPU), paid APIs (cost, data leaves the company) |
| Chunk context | Metadata header | Free and instant | LLM-written context (hours on this CPU) |
| PDF parsing | Docling, pypdf fallback | Layout and tables | PyMuPDF (AGPL) |
| Backend | Python FastAPI | Best free RAG libraries | All-TypeScript, Open WebUI |
| Runtime | Native processes | No Docker or WSL on this laptop | Docker Desktop (admin + reboot + RAM) |
