# QABuddy Phase 1 (Local) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

- **Goal:** A local, free hybrid-RAG web app that answers QA questions with citations, built from the files in `data/`.
- **Architecture:**
  - A Python package (`backend/qabuddy`) has three stages:
    1. **Ingest.** Parses each source kind into `Chunk` records, embeds them (dense through Ollama, BM25 through FastEmbed) and upserts them into a Qdrant collection, with SQLite for the manifest, test runs and entities.
    2. **Retrieve.** Qdrant hybrid query with RRF, then a cross-encoder rerank.
    3. **Answer.** Ollama chat, streamed over Server-Sent Events by FastAPI.
  - A React (Vite + TypeScript) single-page app talks to `/api`.
- **Tech stack:**
  - Python 3.12 (uv), FastAPI, uvicorn, qdrant-client, fastembed, ollama, tree-sitter (Java/TS/JS), docling, pypdf, openpyxl, PyYAML, Typer, pytest
  - Qdrant 1.19.1 (Windows binary)
  - Ollama models `qwen3-embedding:0.6b` and `qwen3:4b-instruct-2507-q4_K_M`
  - React 18 + Vite + TypeScript + react-markdown + Vitest
- **Spec:** `docs/superpowers/specs/2026-10-05-qabuddy-phase1-local-design.md`

## Global constraints

- Python `>=3.12,<3.13`, managed by `uv`. Run commands from `backend/` with `uv run …`.
- Every model and library is free and open source. No paid APIs.
- No git commands. The owner commits.
- Paths in chunk payloads are relative to `data/` and use forward slashes.
- Tokens are estimated as `ceil(len(text) / 4)`.
- Chunk sizes: target 400, max 800, min 40, prose overlap 60 tokens (spec §6.4).
- Qdrant collection `qabuddy_chunks`: named dense vector `dense` (cosine), sparse vector `bm25` (IDF modifier).
- Default ports: API 8000, Qdrant 6333, Ollama 11434, Vite dev server 5173.
- Settings live in `config/settings.yaml` and can be overridden by `QABUDDY_<SECTION>__<KEY>` environment variables.

## Review focus

These are the five failure modes most likely to bite users. Each has a pinned test:

1. **File names with spaces and parentheses**, e.g. `Product Requirements Document (PRD) VWO.com (1).pdf`. Discovery, citation and the chunk API must handle them. *Tests:* Task 3 `test_discover_handles_spaces_in_names`; Task 10 `test_chunk_endpoint_returns_pdf_chunk`.
2. **Files that aren't UTF-8** (e.g. Excel CSVs saved as cp1252). These are read with a fallback, never crash. *Test:* Task 2 `test_read_text_falls_back_to_cp1252`.
3. **Huge single-line files** (minified JS, big JSON). The splitter must hard-cut long lines so no chunk exceeds 800 tokens. *Test:* Task 1 `test_split_by_size_hard_cuts_long_lines`.
4. **ID written differently in the question** (`vwo-26`, `VWO 26`, `build 142`, `#142`). It must still hit the exact-ID prefetch. *Test:* Task 9 `test_extract_query_ids_variants`.
5. **LLM missing or Ollama down while answering.** Sources still stream, followed by `error: llm_unavailable` and then `done`. *Test:* Task 10 `test_chat_streams_sources_then_llm_unavailable`.

---

### Task 1: Backend scaffold, settings, Chunk model, chunking utilities

**Files:**
- Create:
  - `backend/pyproject.toml`
  - `backend/qabuddy/__init__.py`
  - `backend/qabuddy/config.py`
  - `backend/qabuddy/ingest/__init__.py`
  - `backend/qabuddy/ingest/models.py`
  - `backend/qabuddy/ingest/chunking.py`
  - `config/settings.yaml`
  - `config/sources.yaml`
  - `config/glossary.yaml`
  - `.gitignore`
- Delete: `data/sources.yaml` (it uses stale folder names)
- Test: `backend/tests/test_chunking.py`, `backend/tests/conftest.py`

**Interfaces (produced):**
- `config.py`:
  - `load_settings() -> Settings`, where `Settings` is a pydantic model holding the spec §12 keys.
  - `Settings.data_path` and `Settings.storage_path` are absolute `Path`s resolved from the repo root.
  - `REPO_ROOT: Path`.
- `models.py`:
  - `@dataclass Chunk` with the fields `chunk_id, source, kind, path, citation, location: dict, title, breadcrumb, parent_id, module, ids_norm: list[str], date, header, text`, plus the property `content_hash`.
  - `make_chunk_id(path: str, locator: str) -> str` returns uuid5 under a fixed namespace.
  - `@dataclass SourceFile(abs_path: Path, rel_path: str, source: str, parser: str, file_hash: str)`.
- `chunking.py`:
  - `estimate_tokens(text) -> int`
  - `@dataclass Section(breadcrumb: list[str], text: str, line_start: int, line_end: int)`
  - `split_markdown(text: str, title: str) -> list[Section]`: splits on H1–H3 and keeps 1-based line numbers.
  - `split_by_size(text: str, target: int, max_tokens: int, overlap: int) -> list[tuple[str, int, int]]`: returns `(piece, line_start, line_end)`. It splits on paragraph, then line, then hard character cut.
  - `merge_small(items, min_tokens, target)`.

**Steps:**
- [ ] Write the tests:
  - `test_estimate_tokens` (`"abcd"*10` → 10)
  - `test_split_markdown_breadcrumbs`:
    - input `# A\n## B\ntext\n### C\nmore` gives breadcrumbs `[A]`, `[A,B]`, `[A,B,C]`;
    - line numbers are correct.
  - `test_split_by_size_overlap`:
    - pieces are ≤ max;
    - consecutive pieces share about 60 tokens of text.
  - `test_split_by_size_hard_cuts_long_lines`: a 20,000-character single line gives every piece ≤ 800 tokens.
  - `test_settings_env_override`: `QABUDDY_RETRIEVAL__TOP_K=4` gives `settings.retrieval.top_k == 4`.
- [ ] `uv run pytest tests/test_chunking.py`. Expect FAIL (module missing).
- [ ] Implement the files above. `pyproject.toml` defines the console script `qabuddy = "qabuddy.cli:app"`, pytest config with `testpaths=["tests"]`, and the dependencies listed in the tech stack plus `pydantic`, `httpx` and `sse-starlette`. The config YAMLs carry the spec §5 mapping and exclusions, and the spec §12 settings. `.gitignore` covers `data/`, `storage/`, `tools/`, `.venv/`, `node_modules/`, `frontend/dist/` and `__pycache__/`.
- [ ] `uv sync` then `uv run pytest tests/test_chunking.py`. Expect PASS.

### Task 2: Normalization (cleaning, secrets, identifiers, IDs, glossary)

**Files:**
- Create: `backend/qabuddy/ingest/normalize.py`
- Test: `backend/tests/test_normalize.py`

**Interfaces (produced):**
- `read_text(path: Path) -> str`: tries utf-8-sig, then cp1252, then latin-1.
- `clean_text(text: str) -> str`: NFC normalization; strips BOM, ANSI and `\r`; collapses more than 2 blank lines; strips trailing whitespace.
- `mask_secrets(text: str) -> str`: uses the spec §6.3 key regex and handles `k=v`, `k: v` and `"k": "v"` forms.
- `split_identifiers(text: str) -> str`: returns the space-joined parts of every camelCase, PascalCase, snake_case or dotted token.
- `TICKET_RE`
- `extract_ids(text: str, known: set[str]) -> list[str]`: returns lowercased ticket-like keys, known identifiers found as tokens, and build refs written as `#142` / `build 142`, normalized to `build-142`.
- `load_glossary(path) -> dict[str, list[str]]`
- `expand_with_glossary(text, glossary) -> str`

**Steps:**
- [ ] Write the tests:
  - `test_mask_secrets_properties_file`: reading `data/07_Source_Codes/01_selenium_framework/ATB13xSeleniumAdvanceFramework-main/src/main/resources/data.properties` and masking it leaves none of the original values of the password/username keys.
  - `test_mask_secrets_env_file`: same for the Playwright `.env`.
  - `test_split_identifiers`: `"testLoginPositiveVWO DashBoardPage.loggedInUserName"` contains `"test login positive vwo"` and `"dash board page logged in user name"`.
  - `test_extract_ids`: `"VWO-26 and QAB-101 in build #142"` gives `{"vwo-26","qab-101","build-142"}`.
  - `test_read_text_falls_back_to_cp1252`: bytes `b"caf\xe9"` give `"café"`.
  - `test_clean_text_strips_ansi_and_bom`
- [ ] Run → FAIL. Implement. Run → PASS.

### Task 3: Discovery

**Files:**
- Create: `backend/qabuddy/ingest/discover.py`
- Test: `backend/tests/test_discover.py`

**Interfaces:**
- Consumes `Settings` and `config/sources.yaml`.
- Produces:
  - `load_sources(path) -> list[SourceConfig]`, where `SourceConfig` has `id, folder, parser, enabled, include, exclude_dirs, exclude_files, label`.
  - `discover(settings) -> list[SourceFile]`:
    - only enabled sources, in config order;
    - glob includes;
    - excluded dirs and files, `_README*` and binary extensions skipped;
    - sha256 `file_hash`;
    - byte-identical duplicates kept only once (first wins).

**Steps:**
- [ ] Write the tests against the real `data/`:
  - `test_discover_skips_readme_and_binaries`: no `_README` file, no `.zip`, `.webm` or `.png`.
  - `test_discover_skips_excluded_dirs`: nothing under `.idea`, `tta-report` or `node_modules`.
  - `test_discover_no_duplicate_hashes`
  - `test_discover_handles_spaces_in_names`: a `SourceFile` exists with `rel_path` ending `Product Requirements Document (PRD) VWO.com (1).pdf` and source `requirements`.
  - `test_discover_figma_disabled`: no file with source `figma`.
- [ ] Run → FAIL. Implement. Run → PASS.

### Task 4: Parsers — test cases and JIRA

**Files:**
- Create:
  - `backend/qabuddy/ingest/parsers/__init__.py` (registry `PARSERS: dict[str, Callable[[SourceFile, ParseContext], list[Chunk]]]`)
  - `parsers/testcases.py`
  - `parsers/jira.py`
- Create: `backend/qabuddy/ingest/context.py`, holding `@dataclass ParseContext(settings, source_label: str, known_entities: set[str], glossary)`
- Test: `backend/tests/test_parsers_testcases_jira.py`

**Interfaces (produced):**
- `parse_testcases(sf, ctx) -> list[Chunk]`:
  - `kind="testcase"`, `citation=<ID>`, `parent_id=<ID>`, `location={"row": n}`;
  - `module` from the ID prefix, title-cased.
- `testcase_entities(chunks) -> set[str]`
- `JiraLoader` protocol: `load() -> list[Chunk]`.
- `parse_jira_markdown(sf, ctx) -> list[Chunk]`: `kind="ticket"`, `citation=<KEY>`, `parent_id=<KEY>`.

**Steps:**
- [ ] Write the tests:
  - `test_testcases_count`: 500 chunks.
  - `test_testcase_login_001`:
    - citation `LOGIN-001`, module `Login`;
    - text contains `"Steps:\n1. Open the VWO Login area"`;
    - "Expected:" appears exactly once (the duplicate column collapsed).
  - `test_jira_vwo_26`:
    - citation `VWO-26`;
    - text contains "Login failure on VWO app" and "Status: To Do";
    - no "Back to previous view" and no "Generated at".
  - `test_jira_qab_102_comments`: text contains "Comment by Pramod" and "Labels: flaky, selenium, wait".
- [ ] Run → FAIL. Implement. Run → PASS.

### Task 5: Parsers — documents, meetings, Lucid

**Files:**
- Create: `parsers/documents.py`, `parsers/meetings.py`, `parsers/lucid.py`
- Test: `backend/tests/test_parsers_docs.py`

**Interfaces (produced):**
- `parse_document(sf, ctx)`:
  - Markdown goes through `split_markdown` + `split_by_size`.
  - PDF goes through `pdf_to_sections(path) -> list[Section]`: Docling with page provenance in `Section`, using `line_start` for the page; `pypdf` per-page fallback on any exception.
  - `kind="doc_section"`; `citation=f"{title} › {section}"` plus ` p.{page}` for PDFs.
- `parse_meeting(sf, ctx)`:
  - `.vtt` / `.txt` turns go through `group_turns(turns, target=350, overlap_turns=1)`;
  - `kind="transcript"`, `date` from the file name;
  - citation `f"{title} {date} @{start}"`.
  - `.md` uses the Markdown path with the meeting header.
- `parse_lucid(sf, ctx)`: JSON / CSV / TXT become `kind="diagram"` with "A → B (label)" flow lines.

**Steps:**
- [ ] Write the tests:
  - `test_markdown_doc_waits_section`: the coding-standards doc has a chunk whose breadcrumb includes "1.3 Waits" and whose text includes "waitJVM".
  - `test_pdf_prd_has_pages`: the PRD yields more than 0 chunks, all with an integer `location["page"]`.
  - `test_vtt_turns`: the stand-up yields ≥ 1 chunk containing "Ananya Rao" and a time citation starting `@00:00:01`.
  - `test_txt_meeting_date`: the triage meeting chunks have date `2026-04-07`.
  - `test_lucid_json_flow`: the AB test lifecycle text contains `"→"` and "approved".
  - `test_lucid_csv_flow`: the login flow text contains "Click Sign in".
- [ ] Run → FAIL. Implement. Run → PASS. The first Docling run downloads its models; allow time for it.

### Task 6: Parser — code (tree-sitter)

**Files:**
- Create: `parsers/code.py`
- Test: `backend/tests/test_parser_code.py`

**Interfaces (produced):**
- `parse_code(sf, ctx) -> list[Chunk]`, dispatching by extension:
  - `.java` → Java;
  - `.ts` / `.js` → TypeScript / JavaScript;
  - `.md` / `.mdx` → the Markdown path, `kind="md_section"`;
  - config / json / yml / properties / env → masked whole file, `kind="config"`.
- Java: `method` and `class_context` chunks.
- TS: `function`, `method`, top-level arrow const, `test(...)` / `test.describe(...)`.
- `citation=f"{file_name}:{start}-{end}"`; `parent_id=rel_path`.
- `header`: `f"{source_label} › {short_path} › class {Cls} › {signature}"`.
- `code_entities(chunks) -> set[str]`: class and method names.

**Steps:**
- [ ] Write the tests:
  - `test_java_method_chunk`: `DashBoardPage.java` (improved_POM) has a chunk with header containing `loggedInUserName` and line range covering line 20.
  - `test_java_class_context`: one `class_context` chunk per class, containing the `By` locator fields.
  - `test_playwright_test_blocks`: some `.spec.ts` chunk is a `test(...)` unit whose header includes its title.
  - `test_code_max_tokens`: across all code chunks, `estimate_tokens(text) <= 800`.
  - `test_properties_masked`: the `data.properties` chunk text contains `***`.
- [ ] Run → FAIL. Implement. Run → PASS.

### Task 7: Parser — Jenkins (JUnit, console logs, test history)

**Files:**
- Create: `parsers/jenkins.py`
- Test: `backend/tests/test_parser_jenkins.py`

**Interfaces (produced):**
- `@dataclass TestRun(job, build: int, test_id, class_name, method, title, file, status, attempt: int, duration_s: float|None, error_type, error_message, source_path)`
- `parse_junit(sf, ctx) -> tuple[list[Chunk], list[TestRun]]`
- `parse_console_log(sf, ctx) -> tuple[list[Chunk], list[TestRun]]`
- `merge_runs(runs) -> list[TestRun]`
- `build_test_history(runs, ctx) -> list[Chunk]`: `kind="test_history"`; fields `is_flaky` and `flip_rate` appear in the text.
- `parse_jenkins(sf, ctx)` returns chunks and stores runs in the context.
- `project_frames(trace: str, limit=5) -> list[str]`

**Steps:**
- [ ] Write the tests:
  - `test_junit_142`: 4 runs, 2 failed; the failure chunk includes `DashBoardPage.java:20` and not `org.openqa` frames.
  - `test_selenium_log_143_retry`: the runs for `testLoginPositiveVWO` include attempt 1 skipped/failed and a later pass.
  - `test_playwright_88`: the checkout test's final status is `failed` with 3 attempts; "create a booking" passed on retry #1.
  - `test_history_flaky`: the history chunk for `testLoginPositiveVWO` says `Flaky: yes`.
  - `test_build_summary_chunk`: build 143 summary includes "Started by user Rahul Verma" and the commit message.
- [ ] Run → FAIL. Implement. Run → PASS.

### Task 8: Storage, embedding, indexing pipeline, `qabuddy ingest`

**Files:**
- Create:
  - `backend/qabuddy/db.py`
  - `backend/qabuddy/index/__init__.py`
  - `index/embedder.py`
  - `index/store.py`
  - `ingest/pipeline.py`
  - `backend/qabuddy/cli.py`
- Test: `backend/tests/test_db.py`, `backend/tests/test_pipeline_integration.py`

**Interfaces (produced):**
- `db.py`:
  - `Database(path)` with `init()`
  - `upsert_file`, `get_file`, `all_files`, `delete_file`
  - `replace_test_runs(runs)`, `all_test_runs()`
  - `replace_entities(rows)`, `known_entities() -> set[str]`, `entities_in(text) -> list[str]`
  - `get_meta(k)`, `set_meta(k, v)`, `bump_index_version() -> int`
  - `cache_get(key)`, `cache_put(...)`
- `embedder.py`:
  - `DenseEmbedder(url, model)` with `.embed_documents(texts) -> list[list[float]]` (batch 16), `.embed_query(q)` (adds the spec §6.5 instruction prefix), and `.dimension`
  - `SparseEmbedder()` with `.embed_documents(texts)` and `.embed_query(q)`, returning `qdrant_client.models.SparseVector`
  - `OllamaUnavailable` exception
- `store.py`:
  - `VectorStore(url, collection)` with:
    - `.ensure_collection(dim)` (payload indexes from spec §6.5)
    - `.upsert(chunks, dense, sparse)`, `.delete(chunk_ids)`, `.drop()`, `.count() -> int`
    - `.hybrid(dense_q, sparse_q, ids: list[str], sources: list[str]|None, dense_k, sparse_k, id_k, limit) -> list[ScoredChunk]`
    - `.get(chunk_id) -> dict|None`
  - `IndexUnavailable` exception
- `pipeline.py`: `run_ingest(settings, full: bool=False, sources: list[str]|None=None) -> IngestReport(files_total, files_changed, files_failed: list[(path, error)], chunks_upserted, chunks_deleted, seconds)`. Parse order: code, testcases, jira, then the rest (two-pass entity tagging), then Jenkins history.
- `cli.py`: Typer `app` with commands `ingest [--full] [--source]`, `search`, `eval`, `serve`. `search`, `eval` and `serve` are wired in later tasks.

**Steps:**
- [ ] Write the tests:
  - `test_db_roundtrip`: a temp file; meta bump goes 1 → 2; the cache put/get round-trips; `entities_in("see VWO-26")` gives `["vwo-26"]` once seeded.
  - `test_ingest_and_find_ticket`:
    - marked `integration`;
    - skipped unless Qdrant on 6333 and the Ollama embed model are available;
    - ingests only sources `jira` and `testcases` into collection `qabuddy_test`;
    - `store.hybrid` for "VWO-26 login failure" returns citation `VWO-26` first;
    - drops the collection afterwards.
- [ ] Run → FAIL. Implement. Run → PASS. This needs Qdrant running: download it via Task 13's setup script logic or manually now.
- [ ] Run a full `uv run qabuddy ingest --full`. Expect a report with 0 failed files and a chunk count printed. Record the time.

### Task 9: Retrieval (query prep, rerank, search) and `qabuddy search`

**Files:**
- Create: `backend/qabuddy/retrieval/__init__.py`, `retrieval/query.py`, `retrieval/reranker.py`, `retrieval/search.py`
- Modify: `cli.py` (the `search` command)
- Test: `backend/tests/test_retrieval.py`

**Interfaces (produced):**
- `query.py`:
  - `prepare_query(question, history: list[dict], known: set[str], glossary) -> PreparedQuery(retrieval_text, bm25_text, ids: list[str])`
  - `extract_query_ids(text, known) -> list[str]`: normalizes `VWO 26` → `vwo-26` and `build 142` / `#142` → `build-142`, and also includes known identifiers.
- `reranker.py`: `Reranker(model_name)` with `.score(query, texts) -> list[float]`.
- `search.py`:
  - `Searcher(settings, store, dense, sparse, reranker, db, glossary)` with `.search(question, history=[], sources=None) -> SearchResult(hits: list[Hit], no_answer: bool, timings: dict)`
  - `Hit(n, chunk_id, citation, source, kind, path, location, header, text, snippet, score)`
  - `apply_diversity(hits, max_per_parent) -> list`

**Steps:**
- [ ] Write the tests:
  - `test_extract_query_ids_variants`: `"vwo 26"`, `"VWO-26"` and `"vwo-26"` all give `vwo-26`; `"build 142"` and `"#142"` give `build-142`.
  - `test_followup_prepends_previous_question`: history `[{"role":"user","content":"Why did build 142 fail?"}]` plus "and in 143?" gives `retrieval_text` containing both.
  - `test_diversity_cap`: 5 hits with the same `parent_id` and `max_per_parent=3` gives 3.
  - `test_no_answer_below_threshold`: a fake store and reranker returning scores below `min_rerank_score` give `no_answer=True`.
  - `test_search_build_142`: an integration test; "Why did testLoginPositiveVWO fail in build 142?" returns a Jenkins hit in the top 6.
- [ ] Run → FAIL. Implement. Run → PASS.

### Task 10: Answer generation, API with SSE, `qabuddy serve`

**Files:**
- Create: `backend/qabuddy/answer/__init__.py`, `answer/prompt.py`, `answer/llm.py`, `backend/qabuddy/api.py`
- Modify: `cli.py` (the `serve` command)
- Test: `backend/tests/test_answer.py`, `backend/tests/test_api.py`

**Interfaces (produced):**
- `prompt.py`:
  - `SYSTEM_PROMPT`, following spec §8
  - `build_messages(question, hits, history, max_source_tokens, history_turns) -> tuple[list[dict], list[Hit]]`
  - `check_citations(answer: str, k: int) -> tuple[list[int], bool]`
- `llm.py`:
  - `LLMClient` protocol with `.stream(messages) -> Iterator[str]`
  - `OllamaLLM(url, model, num_ctx, temperature, max_tokens, timeout_s)`
  - `LLMUnavailable` exception
- `api.py`: `create_app(settings, searcher=None, llm=None, db=None) -> FastAPI`. Arguments can be injected for tests. Endpoints and SSE events follow spec §9. It serves `frontend/dist` if present.

**Steps:**
- [ ] Write the tests:
  - `test_build_messages_budget`: 10 hits of 600 tokens give a source block of ≤ 2,800 tokens, numbering from 1, with the lowest-ranked dropped first.
  - `test_check_citations`: `"a [1] b [3] c [9]"` with k=6 gives `([1,3], False)`; `"no cites"` gives `([], True)`.
  - `test_chat_streams_sources_then_tokens`: a fake searcher and a fake LLM yielding "Hello [1]" give the event order `sources`, `token`…, `done`, with `citations_used == [1]`.
  - `test_chat_streams_sources_then_llm_unavailable`: a fake LLM raising `LLMUnavailable` gives `sources`, `error(llm_unavailable)`, `done`.
  - `test_chat_no_answer_skips_llm`: when the searcher returns `no_answer`, the LLM is never called and `done.no_answer` is true.
  - `test_chat_rejects_empty_and_long`: a 400 response for `""` and for 2,001 characters.
  - `test_chunk_endpoint_returns_pdf_chunk`: an integration test; the id of a PRD chunk returns a path containing a space.
- [ ] Run → FAIL. Implement. Run → PASS.

### Task 11: Eval set, reranker benchmark, threshold calibration, `qabuddy eval`

**Files:**
- Create: `eval/questions.yaml` (about 20 answerable and 3 unanswerable), `backend/qabuddy/evaluate.py`
- Modify: `cli.py` (`eval [--reranker NAME]`), `config/settings.yaml` (the chosen reranker and `min_rerank_score`)
- Test: `backend/tests/test_evaluate.py`

**Interfaces (produced):**
- `load_questions(path) -> list[EvalQuestion(q, expect: list[str], answerable: bool)]`. `expect` holds substrings matched against hit `citation` or `path`.
- `run_eval(searcher, questions, k=6) -> EvalReport(hit_at_k, mrr, no_answer_acc, per_question, top_scores)`
- `calibrate_threshold(report) -> float|None`, following the spec §7 rule.

**Steps:**
- [ ] Write the tests: `test_hit_and_mrr_math` (a fake per-question rank list [1, None, 2] gives hit@6 = 2/3 and MRR = 0.5) and `test_calibrate_threshold` (answerable top scores [3.1, 5.0] and unanswerable [-2.0, 0.5] give a threshold in (0.5, 3.1]).
- [ ] Run → FAIL. Implement. Run → PASS.
- [ ] Write `eval/questions.yaml` from the real data. Every expected value must exist in the indexed citations or paths.
- [ ] Run `qabuddy eval --reranker Xenova/ms-marco-MiniLM-L-12-v2` and `--reranker BAAI/bge-reranker-base`. Choose per spec §7, write it to `settings.yaml`, and set `min_rerank_score` from the calibration. **Gate:** hit@6 ≥ 0.85 and 3/3 unanswerable rejected. If it misses, tune glossary or chunk headers and re-run.

### Task 12: Frontend (React + Vite + TypeScript)

**Files:**
- Create in `frontend/`:
  - `package.json`, `vite.config.ts` (proxy `/api` → 8000; Vitest config), `tsconfig.json`, `index.html`
  - `src/main.tsx`, `src/App.tsx`, `src/api.ts`, `src/sse.ts`, `src/types.ts`
  - components: `TopBar.tsx`, `ChatPanel.tsx`, `Message.tsx`, `SourcesPanel.tsx`, `SourceViewer.tsx`, `EmptyState.tsx`
  - `src/styles.css`
- Test: `frontend/src/sse.test.ts`, `frontend/src/citations.test.tsx`

**Interfaces:**
- **Consumes:** the spec §9 contract.
- **Produces:**
  - `parseSSE(chunk: string, buffer: {rest: string}) -> {event, data}[]`
  - `streamChat(req, handlers: {onSources, onToken, onDone, onError}, signal)`
  - `renderWithCitations(text, onCite) -> ReactNode`, which turns `[n]` into buttons

**Steps:**
- [ ] Write the tests:
  - `parseSSE` handles events split across reads and multiple events in one read;
  - `renderWithCitations("see [2]")` renders a button labelled 2 that calls `onCite(2)`.
- [ ] `npm test` → FAIL. Implement the layout and behaviour from spec §10: filter buttons, health dot polling `/api/health` every 15 s, example questions taken from `eval/questions.yaml` (copied into `src/examples.ts`), streaming answer, uncited badge, "LLM offline" notice, source drawer. Light and dark themes come from CSS variables.
- [ ] `npm test` → PASS; `npm run build` succeeds.

### Task 13: Setup and start scripts, README, end-to-end smoke test

**Files:**
- Create: `scripts/setup.ps1`, `scripts/start.ps1`, `README.md`

**Steps:**
- [ ] `setup.ps1`, idempotent, as in spec §14:
  1. Run `uv sync`.
  2. Download and expand Qdrant v1.19.1 into `tools/qdrant` if missing.
  3. `ollama pull` both models.
  4. `npm ci` and `npm run build`.
- [ ] `start.ps1`:
  1. Start Qdrant with `QDRANT__STORAGE__STORAGE_PATH=storage/qdrant`, unless port 6333 is already answering.
  2. Check Ollama on 11434.
  3. Run `uv run qabuddy serve`.
  4. Open the browser.
- [ ] README covers prerequisites, setup, ingest, eval, run, configuration and troubleshooting, plus the measured ingest time and answer latency.
- [ ] Smoke test:
  1. Run `start.ps1`.
  2. `curl /api/health`: all ok.
  3. POST 3 eval questions to `/api/chat` and check for `sources` + `token` + `done`, with `citations_used` non-empty.
  4. Load the UI in a browser if one is available.
  5. Record the timings in the README.
- [ ] Run the full `uv run pytest` and `npm test`. Everything passes, with integration tests running rather than skipped.
