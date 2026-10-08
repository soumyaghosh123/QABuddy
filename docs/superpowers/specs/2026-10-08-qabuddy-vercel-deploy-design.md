# QABuddy.ai — Vercel Deployment Design

- **Date:** 2026-10-08
- **Status:** Design approved in chat (3 sections); this written spec is awaiting review
- **Builds on:** [Phase 1 (Local) design](2026-10-05-qabuddy-phase1-local-design.md)
- **Target:** Vercel team `qa-explorer` (https://vercel.com/qa-explorer), project `qabuddy`

## 1. Goal

Run the whole QABuddy app — UI, API and retrieval — on Vercel, so QA engineers can use it 24x7 from a `*.vercel.app` URL without the owner's laptop being on. Answers must keep today's behaviour: sources first, a streamed answer, and `[n]` citations that open the source text.

### Success criteria

1. A production URL on the `qa-explorer` team serves the UI and streams cited answers.
2. On the existing eval set (Phase 1 spec §13), the cloud stack meets the Phase 1 gate: **≥ 85 %** of answerable questions have a correct source in the top 6, and **all** unanswerable questions return "not found". Expected result: no worse than the local run by more than one question (local today: hit@6 100 %, MRR 0.888, 3/3).
3. The stack strip shows the live cloud stack (vector DB, embeddings, reranker, LLM) with per-component health.
4. A new local ingest goes live in the cloud after `qabuddy publish`, with **no redeploy**.
5. The local app (`scripts/start.ps1`, http://localhost:8000) keeps working unchanged.
6. No file from `data/` or `storage/` is uploaded to Vercel.

## 2. Scope

**In scope:**
- a settings profile mechanism (`local` default, `cloud`);
- Cloudflare Workers AI clients for query/document embeddings and reranking;
- a pure-Python BM25 query encoder that matches FastEmbed `Qdrant/bm25`;
- Qdrant Cloud support (API key) and an index manifest stored in Qdrant;
- the `qabuddy publish` command (local index → Qdrant Cloud);
- profile-neutral `/api/health` and the backend-driven stack strip (folds in today's uncommitted StackBar work);
- Vercel packaging (Services), a daily keep-alive cron, `.vercelignore`;
- cloud eval and threshold calibration;
- README section for the cloud setup and the ingest → publish routine.

**Out of scope:**
- Authentication or rate limiting. The owner chose a public URL (see §12 for cost guards).
- Running ingest on Vercel. Ingest stays a laptop job (Docling is far beyond the function size limit, and `data/` is not in git).
- Phase 2 hourly ingest, Figma, JIRA MCP.
- A shared (cross-instance) answer cache.

## 3. Decisions made in chat

| Decision | Choice | Why |
|---|---|---|
| Where the app runs | Everything on Vercel | Owner's choice over "UI on Vercel + droplet" and "UI + laptop tunnel" |
| Who can use it | Public, anyone with the link | Owner's choice; data is dummy today |
| Where inference runs | Hosted (Cloudflare Workers AI) | Small function, fast cold starts; owner's choice over bundling FastEmbed models |
| BM25 | Pure-Python query encoder, parity-tested against FastEmbed | Qdrant docs conflict on server-side BM25 for free clusters; this also lets existing sparse vectors be copied as-is |
| Index sync | `qabuddy publish` mirrors local → cloud | Keeps the local stack untouched; incremental; no redeploy after ingest |

## 4. Architecture

```
Browser ──► Vercel project "qabuddy" (team qa-explorer)
             ├─ service "web"  : frontend/ (Vite build) ............ /
             └─ service "api"  : repo root, vercel_app.py (FastAPI) .. /api/*
                   ├─ Qdrant Cloud (free) ── collection qabuddy_chunks + qabuddy_meta (manifest)
                   ├─ Cloudflare Workers AI ── @cf/qwen/qwen3-embedding-0.6b, @cf/baai/bge-reranker-base
                   └─ DeepSeek ── answer stream (unchanged)

Laptop (unchanged): ingest → local Qdrant + Ollama ──(qabuddy publish)──► Qdrant Cloud
```

### Components by profile

| Part | `local` profile (default, unchanged) | `cloud` profile |
|---|---|---|
| Vector DB | Qdrant on `localhost:6333` | Qdrant Cloud URL + `QDRANT_API_KEY` |
| Query embedding | Ollama `qwen3-embedding:0.6b` | Cloudflare `@cf/qwen/qwen3-embedding-0.6b` |
| Query BM25 | pure encoder (§6.2) | pure encoder (§6.2) |
| Reranker | FastEmbed `Xenova/ms-marco-MiniLM-L-12-v2` | Cloudflare `@cf/baai/bge-reranker-base` |
| `min_rerank_score` | `settings.yaml` (−3.6574) | `settings.cloud.yaml` (calibrated by cloud eval) |
| Index catalog (version, entities, stats) | SQLite `storage/qabuddy.db` | manifest in Qdrant `qabuddy_meta` |
| Answer cache | SQLite `storage/qabuddy.db` | SQLite `/tmp/qabuddy-cache.db` (per instance) |
| LLM | DeepSeek (or Ollama) | DeepSeek |

The query BM25 encoder replaces FastEmbed at query time in **both** profiles, so one code path is tested everywhere. Ingest still produces document BM25 vectors with FastEmbed.

## 5. Settings profiles and secrets

`load_settings()` reads `config/settings.yaml`, then, when `QABUDDY_PROFILE` is set, deep-merges `config/settings.<profile>.yaml` on top, then applies the existing `QABUDDY_<SECTION>__<KEY>` env overrides. `Settings` gains a `profile` field (default `local`).

New settings keys (defaults keep today's local behaviour):

```yaml
qdrant:
  url_env: ""                      # cloud: QDRANT_URL — overrides url when that env var is set
  api_key_env: ""                  # cloud: QDRANT_API_KEY; empty → no key (local)
  meta_collection: qabuddy_meta
embeddings:
  provider: ollama                 # ollama | cloudflare
reranker:
  provider: fastembed              # fastembed | cloudflare
catalog:
  provider: sqlite                 # sqlite | manifest
cache:
  db_path: ""                      # empty → storage/qabuddy.db
cloudflare:
  account_id_env: CLOUDFLARE_ACCOUNT_ID
  api_token_env: CLOUDFLARE_API_TOKEN
  embed_model: "@cf/qwen/qwen3-embedding-0.6b"
  rerank_model: "@cf/baai/bge-reranker-base"
  timeout_s: 30
  embed_batch_size: 32
```

`config/settings.cloud.yaml` sets `embeddings.provider: cloudflare`, `reranker.provider: cloudflare`, `catalog.provider: manifest`, `cache.db_path: /tmp/qabuddy-cache.db`, `retrieval.min_rerank_score: <calibrated>`. The Qdrant cluster URL is **not** committed (public repo); the cloud overlay sets `qdrant.url_env: QDRANT_URL`. A dedicated variable is used instead of `QABUDDY_QDRANT__URL`, because the generic override would also redirect the **local** profile on the laptop to the cloud cluster.

Environment values, set by the owner (never in files or chat):

| Name | Where | Secret |
|---|---|---|
| `DEEPSEEK_API_KEY` | Vercel | yes |
| `QDRANT_API_KEY` | Vercel + laptop | yes |
| `CLOUDFLARE_API_TOKEN` | Vercel + laptop | yes |
| `CLOUDFLARE_ACCOUNT_ID` | Vercel + laptop | no, but kept out of the repo |
| `QDRANT_URL` | Vercel + laptop (for publish/eval) | no, but kept out of the repo |

`vercel_app.py` sets `QABUDDY_PROFILE=cloud` itself, so it is not an env value.

## 6. Components

### 6.1 Cloudflare Workers AI client (`qabuddy/inference/cloudflare.py`)

One small `httpx` client: `POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}` with `Authorization: Bearer <token>`.

- **`CloudflareEmbedder`** implements the existing embedder interface (`embed_query`, `embed_documents`, `dimension`). Queries are sent with the QA instruction from `QUERY_INSTRUCTION`; documents are sent without one, matching `dense_text()`. Batches of `embed_batch_size`. The exact input field names (`queries` / `documents` / `instruction` vs `text`) are confirmed against the model's published `schema-input.json` and pinned by a test fixture.
- **`CloudflareReranker`** implements `score(query, texts) -> list[float]`: sends `{query, contexts: [{text}], top_k: len(texts)}` and maps each response item back to its input position by `id`. The raw score is used as-is; the threshold is calibrated on the same scale.
- Retries: up to 3 attempts with exponential backoff on network errors, 429 and 5xx. Other 4xx fail at once.
- Errors raise `InferenceUnavailable` (new base class; `OllamaUnavailable` becomes a subclass) with the service, model and HTTP status in the message, e.g. "Cloudflare Workers AI returned 429 for qwen3-embedding-0.6b — the daily free quota may be used up."

### 6.2 BM25 query encoder (`qabuddy/index/bm25.py`)

Reproduces FastEmbed `Qdrant/bm25` **query** embeddings: same tokenizer, lowercasing, punctuation handling, English stop-word list (vendored as a text file), Snowball English stemmer (`py-rust-stemmers`), token id = `abs(mmh3.hash(token))`, weight 1.0 per unique token. Output: `qdrant_client.models.SparseVector`. Dependencies: `mmh3`, `py-rust-stemmers` (both small wheels). `SparseEmbedder.embed_query` delegates to it; `embed_documents` keeps FastEmbed for ingest.

### 6.3 Vector store

`VectorStore(url, collection, api_key=None)` passes the key to `QdrantClient`. `IndexUnavailable` is also raised for 401/403, with "check QDRANT_API_KEY" in the message. `ensure_collection`, `upsert`, `hybrid` and `get` are otherwise unchanged. New helpers used by publish: `scroll_all(with_vectors: bool)` yielding `(id, payload, vectors)`, and `upsert_raw(points)`.

### 6.4 Index manifest and catalog

**Manifest:** one point (fixed UUID) in collection `qabuddy_meta` (created with an empty vectors config). Payload:

```json
{"index_version": 3, "published_at": 1791300000.0, "last_ingest_at": 1791216332.18,
 "embed_model": "@cf/qwen/qwen3-embedding-0.6b", "chunks": 1162, "files": 146,
 "by_source": {"jira": 120, "...": 0}, "failed_files": [{"path": "...", "error": "..."}],
 "entities": ["vwo-26", "testloginpositivevwo", "..."]}
```

**Catalog interface** (`qabuddy/catalog.py`): `index_version()`, `known_entities()`, `stats()`, `last_ingest_at()`.
- `SqliteCatalog` wraps today's `Database` methods (local; no behaviour change).
- `ManifestCatalog` reads the manifest from Qdrant and caches it in memory for 5 minutes. A missing manifest is reported as "index is empty — run qabuddy publish".

`Searcher.known_entities`, `/api/stats`, `/api/health` and the answer-cache key use the catalog instead of calling `Database` directly. The answer cache stays in `Database`, opened at `cache.db_path`.

### 6.5 `qabuddy publish`

```
qabuddy publish [--dry-run]
```

Source: the local profile (local Qdrant + SQLite). Target: the cloud profile (Qdrant Cloud + Cloudflare).

1. Ensure the cloud collection exists (same vector config, payload indexes) and the meta collection exists.
2. Scroll all local points (payload + `bm25` vector) and all cloud points (payload only).
3. Diff by point ID: **new** (local only), **changed** (payload differs), **deleted** (cloud only), **unchanged**.
4. For new + changed: embed `dense_text` via `CloudflareEmbedder` in batches, then upsert with the local `bm25` vector copied unchanged.
5. Delete the deleted IDs.
6. Write the manifest **last**, built from the local SQLite catalog. `index_version` = previous cloud version + 1 if anything changed, else unchanged.
7. Print a summary (counts per bucket, tokens sent, seconds). `--dry-run` stops after step 3.

The first run re-embeds all ~1,162 chunks (≈ 0.5 M tokens, ≈ 550 of the 10 k free daily neurons).

### 6.6 API and health

`create_app` builds components from the profile (embedder, reranker, catalog, cache path). `/api/chat`, `/api/chunks`, `/api/examples` and the SSE protocol are unchanged.

`/api/health` becomes profile-neutral and costs nothing (one Qdrant collection GET; key-presence checks for Cloudflare and DeepSeek; an Ollama `/api/tags` GET only in the local profile):

```json
{"status": {"level": "ok", "text": "Ready · 1,162 chunks indexed · deepseek-flash"},
 "stack": [
   {"key": "vector", "label": "Vector DB", "value": "Qdrant Cloud · hybrid RRF", "ok": true},
   {"key": "embed", "label": "Embeddings", "value": "qwen3-embedding-0.6b · Cloudflare", "ok": true},
   {"key": "rerank", "label": "Reranker", "value": "bge-reranker-base · Cloudflare", "ok": true},
   {"key": "llm", "label": "LLM", "value": "DeepSeek · deepseek-flash", "ok": true}],
 "index": {"chunks": 1162, "last_ingest_at": 1791216332.18, "index_version": 3}}
```

A failing pill carries `"ok": false, "error": "<reason>"`. `status.level` is `ok` / `warn` / `down`, computed server-side with today's `healthState` rules, generalised to the stack entries.

### 6.7 Frontend

`StackBar` renders `health.status` and `health.stack` as returned (no client-side stack logic). If `/api/health` fails, it shows "✗ API offline" and the last known pills greyed out with "Last known:" tooltips; before any successful response it shows only the status pill. `TopBar` keeps the source filters and "New chat" only. Types in `types.ts` follow the new health shape.

## 7. Packaging (Vercel)

- `vercel.json` (repo root):
  - `services.web` → `root: "frontend/"`;
  - `services.api` → `root: "./"`, `entrypoint: "vercel_app:app"`, with `excludeFiles` for tests;
  - rewrites `/api/(.*)` → `api`, `/(.*)` → `web`;
  - a daily cron on `/api/health`;
  - function region near the Qdrant cluster (Qdrant AWS `us-east-1` ↔ Vercel `iad1`).
  
  Whether the backend receives `/api/...` or a stripped path is checked against the Services routing docs and the first preview deploy.
- `requirements.txt` (repo root): `fastapi`, `qdrant-client`, `httpx`, `pydantic`, `pyyaml`, `mmh3`, `py-rust-stemmers`. Heavy local packages (Docling, FastEmbed, Ollama, tree-sitter, openpyxl, pypdf) stay only in `backend/pyproject.toml`. The cloud import path must not import them (checked by a test that imports `vercel_app` with those modules blocked).
- `vercel_app.py` (repo root): sets `QABUDDY_PROFILE=cloud`, puts `backend/` on `sys.path`, exposes `app = create_app(load_settings())`. `config/` and `eval/questions.yaml` are read from the repo root (bundled).
- `.vercelignore`: `data/`, `storage/`, `tools/`, `.superpowers/`, `docs/`, `backend/.venv/`, `backend/tests/`, `frontend/node_modules/`, `frontend/dist/`, `.env*`, `*.log`.
- **Fallback** if Services (beta) misbehaves: `framework: vite` with `buildCommand`/`outputDirectory` for `frontend/`, plus a file-based function `api/index.py` and a rewrite `/api/(.*)` → `/api/index`.
- Deploys use the Vercel CLI from the laptop (`vercel deploy`, then `vercel deploy --prod`). No Git integration is required.

## 8. Error handling

| Failure | Behaviour |
|---|---|
| Cloudflare network error / timeout / 429 / 5xx after retries | SSE `error` `llm_unavailable` naming Cloudflare, the model and the status; `done` follows |
| Cloudflare 401/403 | Same, with "check CLOUDFLARE_API_TOKEN / account ID" |
| Qdrant Cloud unreachable / 401 | SSE `index_unavailable` with the reason; the Vector DB pill turns red |
| Secret missing on Vercel | `/api/health` names it ("QDRANT_API_KEY is not set"); that pill turns red; chat returns the matching error |
| Manifest missing | status "index is empty — run qabuddy publish"; chat returns `index_unavailable` |
| DeepSeek errors | unchanged |
| `publish` fails mid-way | manifest not written, so the cloud version is unchanged; re-running is safe (idempotent upserts and deletes) |

## 9. Testing and acceptance

**Unit (no network, run in CI):**
- Cloudflare embedder/reranker with `httpx.MockTransport`: request URL/headers/body, batching, id→position mapping, retry on 429/5xx, no retry on 400, error messages.
- BM25 parity: for ~50 real chunk texts and edge cases (identifiers, punctuation, non-ASCII, empty), the pure encoder's query vectors equal FastEmbed's `query_embed` output exactly.
- Settings: profile overlay merge order (yaml → profile yaml → env).
- Catalogs: `SqliteCatalog` equals today's behaviour; `ManifestCatalog` TTL and missing-manifest handling (fake store).
- Publish diff: new / changed / deleted / unchanged buckets, manifest written last and version bumped only on change, `--dry-run` writes nothing (fake stores and embedder).
- Health: status + stack for both profiles, including missing-secret cases.
- Import guard: `vercel_app` imports without Docling / FastEmbed / Ollama / tree-sitter installed.
- Frontend: `StackBar` renders backend pills, offline/stale state, first-load state.

**Cloud integration (marker `cloud`; needs the owner's env values):**
- `qabuddy publish` against the real cluster; a second run reports 0 changes.
- `QABUDDY_PROFILE=cloud qabuddy eval` passes the gate in §1 and writes the calibrated `min_rerank_score` to `config/settings.cloud.yaml`.

**Deployment acceptance:**
1. Preview deploy: `/api/health` is `ok`; three eval questions stream sources, tokens and citations; a citation opens `/api/chunks/{id}`; cold start and warm latency recorded.
2. The preview deployment's source file list (`vercel inspect` / the dashboard's Source tab) contains nothing under `data/` or `storage/`.
3. Production deploy, the same checks on the production URL.
4. The local app still passes its tests and the existing local smoke test.

## 10. Rollout

| Step | Who | What |
|---|---|---|
| 1 | Owner | Create a Qdrant Cloud free cluster (AWS `us-east-1`) and API key; create a Cloudflare API token with Workers AI permission; set the env values from §5 on the laptop |
| 2 | Claude | Implement §5–§7 with tests |
| 3 | Claude | Run `qabuddy publish`, then the cloud eval; commit the calibrated threshold |
| 4 | Claude | `vercel link` the project `qabuddy` to team `qa-explorer` |
| 5 | Owner | Add the §5 values with `vercel env add <NAME> production` (and `preview`) — Claude provides the exact commands |
| 6 | Claude | Preview deploy and acceptance checks, then production deploy |
| 7 | Claude | README: cloud setup, env values, the "ingest → publish" routine, redeploy steps |

## 11. Repository changes

```
vercel.json, vercel_app.py, requirements.txt, .vercelignore     (new, repo root)
config/settings.cloud.yaml                                       (new)
backend/qabuddy/config.py               profiles, new settings sections
backend/qabuddy/catalog.py              (new) SqliteCatalog, ManifestCatalog, manifest build/read/write
backend/qabuddy/inference/cloudflare.py (new) CloudflareEmbedder, CloudflareReranker
backend/qabuddy/inference/errors.py     (new) InferenceUnavailable
backend/qabuddy/index/bm25.py           (new) query encoder + english stop words file
backend/qabuddy/index/embedder.py       OllamaUnavailable → subclass; SparseEmbedder.embed_query → bm25
backend/qabuddy/index/store.py          api_key, auth errors, scroll_all, upsert_raw
backend/qabuddy/publish.py              (new) diff + sync + manifest
backend/qabuddy/retrieval/search.py     build_searcher by profile; catalog for entities
backend/qabuddy/api.py                  catalog, cache path, profile-neutral health
backend/qabuddy/cli.py                  `publish`; eval writes to the active profile's settings file
backend/pyproject.toml                  + mmh3, py-rust-stemmers
frontend/src/components/StackBar.tsx    renders backend status/stack
frontend/src/types.ts, App.tsx, TopBar.tsx, styles.css, stackbar.test.ts
README.md                               cloud section
```

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Public URL lets anyone spend DeepSeek credit | Owner keeps a small prepaid DeepSeek balance; on the Workers **Free** plan Cloudflare stops at 10 k neurons/day instead of billing (keep the account on Free); rate limiting can be added later |
| Qdrant free cluster suspended after 1 week idle, deleted after 4 | Daily Vercel cron hits `/api/health`, which queries the cluster |
| Vercel Services is beta | Documented fallback layout (§7) |
| Cloudflare input schema differs from docs | Field names pinned by a fixture taken from the published schema; first `publish` exercises it before deploy |
| BM25 encoder drifts from FastEmbed | Exact-equality parity test on real chunks |
| bge-reranker-base ranks differently from MiniLM | Cloud eval gate + threshold recalibration before any deploy |
| Ollama (Q8) vs Cloudflare qwen3 vectors differ | Cloud collection is re-embedded entirely by Cloudflare, so query and document vectors always come from the same provider |
| Cold start latency | Small bundle (no ONNX/torch); lazy client creation as today |

## 13. Decision log

- 2026-10-08 — Owner asked to deploy the whole app on Vercel (`qa-explorer`); chose everything-on-Vercel, public access, hosted inference.
- 2026-10-08 — Server-side BM25 dropped in favour of a parity-tested pure encoder (conflicting Qdrant docs on free-cluster support).
- 2026-10-08 — Ingest stays local; `qabuddy publish` mirrors the index and writes a manifest to Qdrant so ingest goes live without a redeploy.
- 2026-10-08 — Cloud Qdrant URL read from `QDRANT_URL` (not `QABUDDY_QDRANT__URL`) so the laptop's local profile is unaffected.
- 2026-10-08 — `/api/health` returns a server-computed status and stack list; the UI's stack strip renders it.
