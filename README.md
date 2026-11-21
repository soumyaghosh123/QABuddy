# QABuddy.ai — local hybrid RAG for QA engineers

Ask one question, get one answer with citations. Answers draw on:
- the Selenium and Playwright frameworks;
- test cases and JIRA tickets;
- company docs and meeting notes;
- Lucid charts, requirements, and Jenkins results.

Search runs locally on free, open-source parts. The answer is written by DeepSeek through its API, or by a local Ollama model if you switch `llm.provider` to `ollama`.

| Part | Choice |
|---|---|
| Vector DB | Qdrant 1.19.1 (Windows binary). Dense vectors plus BM25 sparse vectors, fused with RRF in one query |
| Embeddings | `qwen3-embedding:0.6b` via Ollama (dense) + FastEmbed `Qdrant/bm25` (keyword) |
| Reranker | FastEmbed cross-encoder (`config/settings.yaml → reranker.model`) |
| LLM | `deepseek-flash` via the DeepSeek API, non-thinking mode (`config/settings.yaml → llm`). Local fallback: `qwen3:4b-instruct-2507-q4_K_M` via Ollama |
| Parsing | tree-sitter (Java/TS/JS), Docling (PDF), custom parsers for CSV, JIRA, VTT, Lucid and Jenkins |
| App | FastAPI (Server-Sent Events) + React/Vite |

- Design: [docs/superpowers/specs/2026-10-05-qabuddy-phase1-local-design.md](docs/superpowers/specs/2026-10-05-qabuddy-phase1-local-design.md)
- Build plan: [docs/superpowers/plans/2026-10-05-qabuddy-phase1-local.md](docs/superpowers/plans/2026-10-05-qabuddy-phase1-local.md)
- Desktop app: [docs/desktop.md](docs/desktop.md)

## Status

| Part | State |
|---|---|
| Web app (this README) | Working: indexing, hybrid search, reranking, cited answers, web UI |
| Desktop app (Windows, GitHub/Zephyr/Confluence sync, traceability matrix) | Being rebuilt. Part of its source was lost in a damaged copy in Oct 2026. Routes, shared state, the GitHub/Confluence/Jira connectors, traceability and the Matches table are restored; `desktop/wiring`, `secrets`, `sources`, `paths`, the Zephyr connector, the Confluence parser and the Settings/Traceability panels are still to do |

The desktop pieces only load in the `desktop` profile, so the web app is unaffected by the missing parts.

## Prerequisites (Windows)

- [uv](https://docs.astral.sh/uv/). It installs Python 3.12 by itself.
- [Ollama](https://ollama.com/download)
- Node.js 20+
- About 6 GB of free disk space
- A DeepSeek API key, stored in an environment variable rather than in any file:

  ```powershell
  setx DEEPSEEK_API_KEY "sk-..."
  ```

  `setx` only affects windows opened afterwards, so open a new terminal before running `start.ps1`. Each question sends the retrieved snippets (about 3K tokens) to DeepSeek. To keep everything on this machine instead, set `llm.provider: ollama` in `config/settings.yaml`.

## Setup

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

The setup script does four things:
1. Installs the Python dependencies.
2. Downloads Qdrant into `tools\qdrant\`.
3. Pulls the two Ollama models. With `llm.provider: deepseek` (the default) only the embedding model is used, so you can skip the 2.5 GB chat model: `ollama pull qwen3-embedding:0.6b`.
4. Builds the web UI.

## Sample data

`data/` is not stored in this repo (it is in `.gitignore`). To get the sample data the tests and the eval set expect:

| Folder | Source |
|---|---|
| `data/00_TestCases` … `data/06_Jenkins_Logs` | [`chapter_12_RAG_QA_BuddyAI/data`](https://github.com/PramodDutta/AITesterBlueprint4x/tree/main/chapter_12_RAG_QA_BuddyAI/data) in PramodDutta/AITesterBlueprint4x |
| `data/07_Source_Codes/01_selenium_framework/ATB13xSeleniumAdvanceFramework-main` | [PramodDutta/ATB13xSeleniumAdvanceFramework](https://github.com/PramodDutta/ATB13xSeleniumAdvanceFramework) |
| `data/07_Source_Codes/02_playwright_framework/Advance-Playwright-Framework-main` | [PramodDutta/Advance-Playwright-Framework](https://github.com/PramodDutta/Advance-Playwright-Framework) |

Copy the files in with the folder names shown, then index them (below).

## Run

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

This starts Qdrant in the background, checks that Ollama is running, starts the app, and opens http://localhost:8000.

## Index your data

The data folders live under `data/`; `config/sources.yaml` maps each folder to a parser. In a second terminal:

```powershell
cd backend
uv run qabuddy ingest            # only new or changed files
uv run qabuddy ingest --full     # drop the index and rebuild everything
uv run qabuddy ingest --source jira --source testcases   # only some sources
```

- **First run is slow on a laptop CPU.** Each chunk is embedded by the local model; the measured times are below.
- **Later runs are fast.** They only re-embed files whose content changed.
- **Safe to interrupt.** Each file is saved as soon as it's indexed, so re-running continues where it stopped.

## Check retrieval quality

```powershell
cd backend
uv run qabuddy search "Why did testLoginPositiveVWO fail in build 142?"   # ranked sources, no LLM
uv run qabuddy eval                       # hit@6, MRR, no-answer accuracy on eval/questions.yaml
uv run qabuddy eval --reranker BAAI/bge-reranker-base --write   # benchmark another reranker and save it
```

`eval --write` stores the chosen reranker in `config/settings.yaml`, together with the `min_rerank_score` threshold. Questions whose best match scores below that threshold get the answer "not found in the knowledge base" without calling the LLM.

## Tests

```powershell
cd backend;  uv run pytest                     # unit tests + integration tests (they skip if Qdrant/Ollama are down)
cd frontend; npm test
```

## Configuration

- **`config/settings.yaml`:** models, retrieval sizes, chunk sizes and ports. Any key can be overridden with an environment variable, e.g. `QABUDDY_RETRIEVAL__TOP_K=4`.
- **`config/sources.yaml`:** data folders, file patterns, exclusions, and which parser handles each source. Figma is listed but disabled until Phase 2.
- **`config/glossary.yaml`:** abbreviations that are expanded in keyword search (TC, POM, RCA, …).

Runtime state is stored in `storage/`: Qdrant data, the SQLite manifest, test runs, and the answer cache. Delete it to start from scratch.

## Measured on this laptop

Hardware: i7-8665U, 4 cores, 16 GB RAM, no GPU.

Measured on 2026-10-05 with Chrome, Zoom and other apps open.

| Step | Time |
|---|---|
| Full first index (146 files → 1,162 chunks) | 94 min (`ingest --full`, CPU embedding, 2026-10-05) |
| Full first index (144 files → 1,160 chunks) | 24 min (`ingest`, CPU embedding, re-measured 2026-11-21) |
| Question → sources shown | 7.5–8 s; 15 s for the first question after Ollama unloads the embedding model |
| Question → first answer word (`deepseek-flash`) | +0.6–0.8 s after the sources |
| Question → full answer (`deepseek-flash`) | 9–10 s; 17 s for the first question |

Where a warm question's time goes: embedding and hybrid search take about 2.3 s, and reranking 30 candidates on the CPU takes about 5–5.5 s. DeepSeek returns the whole answer in 1–2 s. The local `qwen3:4b` LLM was not benchmarked; on this CPU it is far slower than DeepSeek.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Health dot red, "Server not reachable" | Run `scripts\start.ps1` |
| "Qdrant is not reachable" | `scripts\start.ps1` starts it. Logs are in `storage\qdrant.log` |
| "model … is not available" | `ollama pull <model>` (names are in `config/settings.yaml`) |
| "DEEPSEEK_API_KEY is not set" | `setx DEEPSEEK_API_KEY "sk-..."`, open a new terminal, re-run `scripts\start.ps1` |
| "LLM API key rejected (401)" / "insufficient balance (402)" | Check the key, or top up the DeepSeek account |
| "index is empty" | `cd backend; uv run qabuddy ingest` |
| Answers are slow | With `llm.provider: ollama` this is normal on CPU; switch to `deepseek`. Retrieval (embedding + rerank) stays on the CPU either way, so close heavy apps if sources take long to appear |
| A file failed to parse | `uv run qabuddy ingest` prints it; `/api/stats` lists failed files |

## Layout

```
backend/qabuddy/   ingest/ (discover, normalize, chunking, parsers/*, connectors/*, pipeline)
                   index/ (embedder, store) · retrieval/ (query, reranker, search, matches)
                   answer/ (prompt, llm) · api.py · cli.py · db.py · evaluate.py
                   desktop/ (app, routes, meta, inputs, sync) · traceability.py
frontend/src/      React UI (chat, sources panel, source viewer; desktop.ts + Settings/Traceability panels in the desktop app)
config/            settings (local, cloud, desktop), sources, glossary
eval/              questions.yaml (retrieval eval set)
packaging/         PyInstaller spec and Inno Setup installer for the desktop app
scripts/           setup.ps1, start.ps1
```
