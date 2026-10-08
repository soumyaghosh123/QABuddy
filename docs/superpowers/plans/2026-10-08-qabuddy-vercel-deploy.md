# QABuddy Vercel Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the whole QABuddy app on Vercel (team `qa-explorer`) with Qdrant Cloud, Cloudflare Workers AI and DeepSeek, while the local stack keeps working unchanged.

**Architecture:** A settings profile (`local` default, `cloud`) picks the vector store, embedder, reranker, index catalog and cache location. The laptop keeps ingesting locally; a new `qabuddy publish` command mirrors the local index into Qdrant Cloud and writes a manifest there. Vercel Services serves the Vite UI and a slim FastAPI function that runs the cloud profile.

**Tech Stack:** Python 3.12, FastAPI, qdrant-client 1.19, httpx 0.28, mmh3 + py-rust-stemmers (BM25), Cloudflare Workers AI REST, React 18 + Vite + Vitest, Vercel CLI 59 (Services).

**Spec:** [docs/superpowers/specs/2026-10-08-qabuddy-vercel-deploy-design.md](../specs/2026-10-08-qabuddy-vercel-deploy-design.md)

## Global Constraints

- Python `>=3.12,<3.13` (backend); the Vercel function runs Python 3.12 (`.python-version`).
- The cloud import path (`vercel_app.py` → `qabuddy.api`) must not import `docling`, `fastembed`, `onnxruntime`, `ollama`, `tree_sitter`, `openpyxl`, `pypdf` or `torch`. The function bundle limit is 500 MB uncompressed.
- Secrets come only from env vars: `DEEPSEEK_API_KEY`, `QDRANT_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, plus the cluster URL in `QDRANT_URL`. Never write their values to files, logs, test fixtures or chat. Claude never runs `vercel env add` with values. The owner does.
- `QABUDDY_QDRANT__URL` is **not** used for the cloud. The cloud overlay reads `QDRANT_URL` via `qdrant.url_env`, so the laptop's local profile is unaffected.
- Local profile behaviour is unchanged. Every existing backend and frontend test keeps passing.
- Cloudflare endpoint: `POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}`, `Authorization: Bearer <token>`.
  - Models: `@cf/qwen/qwen3-embedding-0.6b` and `@cf/baai/bge-reranker-base`.
  - Embedding input: `queries` / `documents` (≤ 32 items, each non-empty) plus optional `instruction`. Output: `result.data` and `result.shape`.
  - Rerank input: `query`, `contexts: [{text}]`, `top_k`. Output: `result.response: [{id, score}]`.
- Query instruction text (unchanged meaning): `Given a QA engineer's question, retrieve the code, test cases, tickets, logs or documents that answer it`.
- `/api/health` never calls an embedding, rerank or LLM API.
- Eval gate (Phase 1 §13): hit@6 ≥ 0.85 **and** 3/3 unanswerable rejected. Cloud must also be no worse than local by more than one question (local today: 100 %, MRR 0.888).
- Nothing under `data/` or `storage/` is uploaded to Vercel.
- Work on branch `feat/vercel-deploy`. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- `README.md` has uncommitted edits by the owner. Never stage README without asking the owner first.
- Backend commands run from `backend/`: `uv run pytest ...`. Frontend commands run from `frontend/`: `npx vitest run`, `npm run build`.

## Review Focus

1. Cloudflare answers HTTP 200 with `{"success": false, "errors": [...]}` → raise `InferenceUnavailable` carrying Cloudflare's message, not a `KeyError`. (Task 4, `test_success_false_envelope_raises`)
2. A rerank response that is out of order or missing some ids → each score lands on its own input position; missing ones get `-inf`. (Task 4, `test_rerank_maps_scores_by_id`)
3. An empty string sent for embedding (Cloudflare requires `minLength: 1`) → it is sent as `" "` and keeps its position. (Task 4, `test_empty_document_text_is_sent_as_space`)
4. `qabuddy publish` while the local index is empty (Qdrant restarted with fresh storage, or ingest never ran) → refuse, and **never** delete the cloud index. (Task 8, `test_refuses_when_local_index_empty`)
5. Cloud profile with `QDRANT_URL` unset → the health pill says "QDRANT_URL is not set" with no HTTP request to an empty URL. (Task 9, `test_cloud_missing_qdrant_url`)

---

### Task 1: Branch and baseline

Commits the already-working stack-strip change (uncommitted in the tree) so later tasks have a clean base.

**Files:**
- Commit as-is: `backend/qabuddy/api.py`, `backend/tests/test_api.py`, `frontend/src/components/StackBar.tsx`, `frontend/src/components/TopBar.tsx`, `frontend/src/App.tsx`, `frontend/src/types.ts`, `frontend/src/styles.css`, `frontend/src/stackbar.test.ts` (renamed from `topbar.test.ts`), the spec and this plan.
- Do **not** stage `README.md`.

- [ ] **Step 1: Create the branch**

Run: `git checkout -b feat/vercel-deploy`
Expected: `Switched to a new branch 'feat/vercel-deploy'`

- [ ] **Step 2: Run the existing suites**

Run: `cd backend && uv run pytest -q` then `cd frontend && npx vitest run`
Expected: backend all pass (integration tests may skip); frontend `15 passed`.

- [ ] **Step 3: Commit**

```bash
git add backend/qabuddy/api.py backend/tests/test_api.py frontend/src docs/superpowers/specs/2026-10-08-qabuddy-vercel-deploy-design.md docs/superpowers/plans/2026-10-08-qabuddy-vercel-deploy.md
git commit -m "feat(ui): backend stack strip; add Vercel deployment spec and plan

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Settings profiles

**Files:**
- Modify: `backend/qabuddy/config.py`
- Create: `config/settings.cloud.yaml`
- Test: `backend/tests/test_config.py` (new)

**Interfaces:**
- Produces:
  - `load_settings(path: Path | None = None, env: Mapping[str, str] | None = None, profile: str | None = None) -> Settings`. Merge order: `settings.yaml` → deep-merge `settings.<profile>.yaml` (skipped for `local`) → `QABUDDY_<SECTION>__<KEY>` env overrides → if `qdrant.url_env` is set and present in env, `qdrant.url = env[url_env]`. Profile precedence: `profile` argument > `env["QABUDDY_PROFILE"]` > `"local"`. A missing profile file raises `ValueError` naming `settings.<profile>.yaml`.
  - `Settings.profile: str = "local"`.
  - `QdrantSettings.url_env: str = ""`, `api_key_env: str = ""`, `meta_collection: str = "qabuddy_meta"`, and `QdrantSettings.api_key(env: Mapping[str, str] | None = None) -> str | None` (None when `api_key_env` is empty or unset in env).
  - `EmbeddingsSettings.provider: str = "ollama"` → `Settings.embeddings`.
  - `RerankerSettings.provider: str = "fastembed"` (new field).
  - `CatalogSettings.provider: str = "sqlite"` → `Settings.catalog`.
  - `CacheSettings.db_path: str = ""` → `Settings.cache`; property `Settings.cache_db_path -> Path` (`db_path` when empty, else `_resolve(cache.db_path)`).
  - `CloudflareSettings`: `account_id_env="CLOUDFLARE_ACCOUNT_ID"`, `api_token_env="CLOUDFLARE_API_TOKEN"`, `embed_model="@cf/qwen/qwen3-embedding-0.6b"`, `rerank_model="@cf/baai/bge-reranker-base"`, `timeout_s: float = 30`, `embed_batch_size: int = 32` → `Settings.cloudflare`.
  - Property `Settings.settings_file -> Path`: `config/settings.yaml` for `local`, else `config/settings.<profile>.yaml`.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_config.py`

```python
from pathlib import Path
import pytest
from qabuddy.config import load_settings

CLOUD_ENV = {"QABUDDY_PROFILE": "cloud", "QDRANT_URL": "https://x.cloud.qdrant.io", "QDRANT_API_KEY": "k"}

def test_local_profile_is_default():
    s = load_settings(env={})
    assert s.profile == "local"
    assert (s.embeddings.provider, s.reranker.provider, s.catalog.provider) == ("ollama", "fastembed", "sqlite")
    assert s.cache_db_path == s.db_path
    assert s.qdrant.api_key({"QDRANT_API_KEY": "k"}) is None

def test_cloud_profile_overlays_settings():
    s = load_settings(env=CLOUD_ENV)
    assert s.profile == "cloud"
    assert s.qdrant.url == "https://x.cloud.qdrant.io"
    assert s.qdrant.api_key(CLOUD_ENV) == "k"
    assert (s.embeddings.provider, s.reranker.provider, s.catalog.provider) == ("cloudflare", "cloudflare", "manifest")
    assert s.cache_db_path == Path("/tmp/qabuddy-cache.db")
    assert s.retrieval.top_k == 6 and s.llm.provider == "deepseek"   # untouched keys survive the merge

def test_profile_argument_beats_env():
    assert load_settings(env=CLOUD_ENV, profile="local").profile == "local"

def test_env_override_applies_after_profile():
    s = load_settings(env={**CLOUD_ENV, "QABUDDY_RETRIEVAL__TOP_K": "4"})
    assert s.retrieval.top_k == 4 and s.embeddings.provider == "cloudflare"

def test_local_profile_ignores_qdrant_url_env():
    assert load_settings(env={"QDRANT_URL": "https://x"}).qdrant.url == "http://localhost:6333"

def test_unknown_profile_raises():
    with pytest.raises(ValueError, match=r"settings\.nope\.yaml"):
        load_settings(env={}, profile="nope")

def test_settings_file_per_profile():
    assert load_settings(env={}).settings_file.name == "settings.yaml"
    assert load_settings(env=CLOUD_ENV).settings_file.name == "settings.cloud.yaml"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_config.py -q`
Expected: FAIL (`profile` keyword / attributes missing).

- [ ] **Step 3: Create `config/settings.cloud.yaml`** with exactly:

```yaml
# Vercel profile (QABUDDY_PROFILE=cloud). Secrets and the cluster URL come from env vars, never this file.
qdrant:
  url: ""
  url_env: QDRANT_URL
  api_key_env: QDRANT_API_KEY
embeddings:
  provider: cloudflare
reranker:
  provider: cloudflare
catalog:
  provider: manifest
cache:
  db_path: /tmp/qabuddy-cache.db
retrieval:
  min_rerank_score: 0.0   # placeholder until `QABUDDY_PROFILE=cloud qabuddy eval --write` (Task 13)
```

- [ ] **Step 4: Implement the settings changes in `backend/qabuddy/config.py`** per the Interfaces block. Use a recursive `_deep_merge(base: dict, overlay: dict) -> dict`; dict values merge, everything else replaces.

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/test_config.py -q && uv run pytest -q`
Expected: new tests PASS; full suite still passes.

- [ ] **Step 6: Commit**

```bash
git add backend/qabuddy/config.py backend/tests/test_config.py config/settings.cloud.yaml
git commit -m "feat(config): settings profiles with a cloud overlay

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: BM25 query encoder

Reproduces FastEmbed `Qdrant/bm25` query vectors without importing FastEmbed.

**Files:**
- Create: `backend/qabuddy/index/bm25.py`
- Create: `backend/qabuddy/index/bm25_english.txt`. Copy it byte-for-byte from the FastEmbed cache: `%TEMP%/fastembed_cache/models--Qdrant--bm25/snapshots/*/english.txt`, 179 lines, Apache-2.0 (credit it in the module docstring).
- Modify: `backend/qabuddy/index/embedder.py`: `SparseEmbedder.embed_query` returns `Bm25QueryEncoder().embed_query(question)` (one module-level encoder instance).
- Modify: `backend/pyproject.toml`: add `"mmh3>=5.3"` and `"py-rust-stemmers>=0.1.8"` to `dependencies`, then `uv lock`.
- Test: `backend/tests/test_bm25.py` (new)

**Interfaces:**
- Produces:
  - `class Bm25QueryEncoder` with `__init__(self, stopwords_path: Path = DEFAULT_STOPWORDS, token_max_length: int = 40)`;
  - `tokens(self, text: str) -> list[str]` (stemmed tokens in order);
  - `embed_query(self, text: str) -> qdrant_client.models.SparseVector` (indices sorted ascending, unique; every value `1.0`).

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_bm25.py`

```python
import pytest
from qabuddy.index.bm25 import Bm25QueryEncoder

SAMPLES = [
    "Why did testLoginPositiveVWO fail in Jenkins build 142?", "VWO-26 status", "VALID-018 INVALID-049",
    "How does DriverManager create and manage the WebDriver?", "snake_case_identifier and CamelCaseThing",
    "naïve café résumé — ünïcödé", "a_b _ __ ___", "x" * 41 + " short", "the and of to", "",
    "C++ / C# / .NET 8.0!", "user@example.com https://app.vwo.com/#/login?x=1",
    "Order APIs: OrderApi.ts:99-121", "Tests passed only after a retry", "日本語 テスト",
]

@pytest.mark.parametrize("text", SAMPLES)
def test_matches_fastembed_query_embed(text):
    fastembed = pytest.importorskip("fastembed")
    expected = next(iter(fastembed.SparseTextEmbedding("Qdrant/bm25").query_embed(text)))
    got = Bm25QueryEncoder().embed_query(text)
    assert got.indices == sorted(int(i) for i in expected.indices)
    assert all(v == 1.0 for v in got.values) and len(got.values) == len(got.indices)

def test_empty_text_gives_empty_vector():
    v = Bm25QueryEncoder().embed_query("")
    assert v.indices == [] and v.values == []

@pytest.mark.integration
def test_matches_fastembed_on_indexed_chunks(settings):
    fastembed = pytest.importorskip("fastembed")
    from qabuddy.index.store import VectorStore
    store = VectorStore(settings.qdrant.url, settings.qdrant.collection)
    try:
        points, _ = store.client.scroll(settings.qdrant.collection, limit=50, with_payload=True)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Qdrant not available: {exc}")
    model, encoder = fastembed.SparseTextEmbedding("Qdrant/bm25"), Bm25QueryEncoder()
    for p in points:
        text = f"{p.payload['header']}\n{p.payload['text']}"
        expected = next(iter(model.query_embed(text)))
        assert encoder.embed_query(text).indices == sorted(int(i) for i in expected.indices)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_bm25.py -q`
Expected: FAIL (`No module named 'qabuddy.index.bm25'`).

- [ ] **Step 3: Implement `backend/qabuddy/index/bm25.py`.** The algorithm must match FastEmbed 0.8.1 `Bm25.query_embed` exactly:

```
text   = re.sub(r"[^\w\s]", " ", text)                            # remove_non_alphanumeric
tokens = re.sub(r"\s+", " ", re.sub(r"[^\w]", " ", text.lower())).strip().split()
for tok in tokens:
    skip if len(tok) == 1 and unicodedata.category(tok).startswith("P")   # FastEmbed's punctuation set
    skip if tok in stopwords
    skip if len(tok) > token_max_length
    stem = SnowballStemmer("english").stem_word(tok); keep if stem
indices = sorted({abs(mmh3.hash(stem)) for stem in kept}); values = [1.0] * len(indices)
```

- [ ] **Step 4: Delegate `SparseEmbedder.embed_query`** to the encoder. Ingest's `embed_documents` stays on FastEmbed.

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/test_bm25.py tests/test_retrieval.py -q`
Expected: PASS (the integration test runs if local Qdrant is up, otherwise skips).

- [ ] **Step 6: Commit**

```bash
git add backend/qabuddy/index/bm25.py backend/qabuddy/index/bm25_english.txt backend/qabuddy/index/embedder.py backend/pyproject.toml backend/uv.lock backend/tests/test_bm25.py
git commit -m "feat(index): pure-Python BM25 query encoder matching FastEmbed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Cloudflare Workers AI client

**Files:**
- Create: `backend/qabuddy/inference/__init__.py` (empty), `backend/qabuddy/inference/errors.py`, `backend/qabuddy/inference/cloudflare.py`
- Modify: `backend/qabuddy/index/embedder.py`: add `QUERY_TASK` (the instruction text from Global Constraints) and define `QUERY_INSTRUCTION = f"Instruct: {QUERY_TASK}\nQuery: "` (same string as today). `OllamaUnavailable` now subclasses `InferenceUnavailable`.
- Test: `backend/tests/test_cloudflare.py` (new)

**Interfaces:**
- Produces:
  - `errors.InferenceUnavailable(RuntimeError)`.
  - `CloudflareClient(account_id: str, api_token: str, timeout_s: float = 30, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep, attempts: int = 3)`, with `run(model: str, payload: dict) -> dict` returning the response's `result` object.
  - `make_cloudflare_client(settings: Settings, env: Mapping[str, str] | None = None, transport: httpx.BaseTransport | None = None) -> CloudflareClient`. It raises `InferenceUnavailable("<NAME> is not set")` for a missing account id or token, checking the account id first.
  - `CloudflareEmbedder(client: CloudflareClient, model: str, batch_size: int = 32)`, with:
    - `embed_query(question: str) -> list[float]`, sending `{"queries": [question], "instruction": QUERY_TASK}`;
    - `embed_documents(texts: list[str], progress: Callable[[int, int], None] | None = None) -> list[list[float]]`, sending `{"documents": batch}`, where empty strings become `" "`;
    - `dimension -> int`, a cached property that probes one document.
  - Constructor arguments are stored as public attributes (`.model`, `.batch_size`); Task 7 asserts on them.
  - `CloudflareReranker(client: CloudflareClient, model: str)`, with `score(query: str, texts: list[str]) -> list[float]`. It sends `{"query", "contexts": [{"text"}], "top_k": len(texts)}`, maps scores back by `id`, gives missing ids `float("-inf")`, and makes no call for an empty list.
- Error messages (`short` = the model name after the last `/`):
  - 429 after retries: `Cloudflare Workers AI returned 429 for {short} — the daily free quota may be used up.`
  - 401/403: `Cloudflare Workers AI rejected the credentials ({status}) — check CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID.`
  - other HTTP: `Cloudflare Workers AI returned {status} for {short}: {body[:200]}`
  - network: `Cloudflare Workers AI is not reachable ({ExceptionName}).`
  - `success: false`: `Cloudflare Workers AI error for {short}: {errors[0]["message"]}`
- Retry: up to `attempts` tries on network errors, 429 and 5xx, sleeping `1.0 * 2 ** (try - 1)` between tries (1 s, 2 s). Other 4xx fail at once.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_cloudflare.py`. Use an `httpx.MockTransport` handler that records requests and pops queued responses, and `sleep=sleeps.append`. Assertions:
  - `test_run_posts_to_model_url_with_bearer`: URL `https://api.cloudflare.com/client/v4/accounts/acc/ai/run/@cf/qwen/qwen3-embedding-0.6b`, header `Authorization == "Bearer tok"`; returns `{"data": [[0.1]], "shape": [1, 1]}`.
  - `test_embed_query_sends_queries_and_instruction`: body `== {"queries": ["why?"], "instruction": QUERY_TASK}`; return value `[0.1, 0.2]`.
  - `test_embed_documents_batches_by_32`: 70 texts → batch sizes `[32, 32, 6]` under key `documents`; 70 vectors in input order; progress calls `[(32, 70), (64, 70), (70, 70)]`.
  - `test_empty_document_text_is_sent_as_space`: `embed_documents(["", "a"])` sends `{"documents": [" ", "a"]}`.
  - `test_rerank_maps_scores_by_id`: response `[{"id": 2, "score": 0.9}, {"id": 0, "score": 0.1}]` for texts `["a", "b", "c"]` → `[0.1, float("-inf"), 0.9]`; body `== {"query": "q", "contexts": [{"text": "a"}, {"text": "b"}, {"text": "c"}], "top_k": 3}`.
  - `test_rerank_empty_texts_makes_no_call`: returns `[]`, zero requests.
  - `test_retries_429_then_succeeds`: responses 429, 429, 200 → result returned; `sleeps == [1.0, 2.0]`.
  - `test_429_after_retries_mentions_quota`: 3 × 429 → `InferenceUnavailable` matching `"daily free quota"`; 3 requests.
  - `test_400_is_not_retried`: 1 request; message contains `"400"`.
  - `test_401_names_token`: message contains `"CLOUDFLARE_API_TOKEN"`.
  - `test_success_false_envelope_raises`: HTTP 200 `{"success": false, "errors": [{"code": 5006, "message": "bad input"}], "result": null}` → message contains `"bad input"`.
  - `test_network_error_raises`: the handler raises `httpx.ConnectError("boom")` → message contains `"not reachable"`; 3 attempts.
  - `test_make_client_requires_env`: `make_cloudflare_client(cloud_settings, env={})` → matches `"CLOUDFLARE_ACCOUNT_ID is not set"`.
  - `test_ollama_unavailable_is_inference_unavailable`: `issubclass(OllamaUnavailable, InferenceUnavailable)`.
  - Success responses use the envelope `{"success": true, "errors": [], "messages": [], "result": {...}}`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_cloudflare.py -q`
Expected: FAIL (`No module named 'qabuddy.inference'`).

- [ ] **Step 3: Implement** `errors.py` and `cloudflare.py`, and make the `embedder.py` changes, per the Interfaces block.

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_cloudflare.py tests/test_pipeline.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/qabuddy/inference backend/qabuddy/index/embedder.py backend/tests/test_cloudflare.py
git commit -m "feat(inference): Cloudflare Workers AI embedder and reranker

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Vector store cloud support

**Files:**
- Modify: `backend/qabuddy/index/store.py`
- Test: `backend/tests/test_store.py` (new)

**Interfaces:**
- Produces:
  - `VectorStore(url: str, collection: str, api_key: str | None = None)` passes `api_key` to `QdrantClient`. The client is created **lazily** on first access of the `client` property, so building the app never fails on an unset `QDRANT_URL` (Task 12 imports the app without it).
  - `make_store(settings: Settings) -> VectorStore` uses `settings.qdrant.url`, `.collection` and `.api_key()`.
  - `scroll_all(with_vectors: bool = False, batch: int = 256) -> Iterator[tuple[str, dict, dict]]` yields `(id, payload, vectors)`, where `vectors` is `{}` when `with_vectors` is False.
  - `upsert_raw(points: list[models.PointStruct], batch_size: int = 64) -> None`.
  - `ensure_meta_collection(name: str) -> None` creates a collection with `vectors_config={}` if missing.
  - `read_meta(name: str, point_id: str) -> dict | None` returns None when the collection or point is missing.
  - `write_meta(name: str, point_id: str, payload: dict) -> None` upserts a point with `vector={}`.
- Error mapping in `_call` / `hybrid`:
  - `UnexpectedResponse` 401/403 → `IndexUnavailable("Qdrant at {url} rejected the API key ({code}) — check QDRANT_API_KEY.")`.
  - 404 from `hybrid` → `IndexUnavailable("Collection {collection} not found at {url} — run qabuddy ingest (local) or qabuddy publish (cloud).")`.
  - Connection errors keep today's message for `localhost`/`127.0.0.1` URLs; any other host ends with `Check QDRANT_URL and the cluster status in Qdrant Cloud.`

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_store.py`. Build `UnexpectedResponse(status_code, "x", b"{}", httpx.Headers())` and monkeypatch `store.client.<method>` to raise:
  - `test_auth_error_maps_to_index_unavailable`: 401 from `client.count` via `store.count()` → `IndexUnavailable` matching `"QDRANT_API_KEY"`.
  - `test_missing_collection_in_hybrid`: 404 from `client.query_points` → matches `"qabuddy publish"`.
  - `test_cloud_hint_in_connection_error`: `VectorStore("https://x.cloud.qdrant.io", "c")`, `client.count` raises `httpx.ConnectError("x")` → matches `"QDRANT_URL"`.
  - `@pytest.mark.integration test_meta_and_scroll_round_trip`: skip unless `http://localhost:6333/collections` answers. On temp collections `pytest_meta_<uuid8>` and `pytest_pts_<uuid8>`:
    - `ensure_meta_collection`, then `write_meta`/`read_meta` round-trip `{"index_version": 1}`, and `read_meta` of an unknown point → None.
    - Create the points collection via `ensure_collection(3)`, then `upsert_raw` 2 points with `{"dense": [...], "bm25": SparseVector}`.
    - `scroll_all(with_vectors=True)` returns both ids, with payloads and a `bm25` vector.
    - Drop both collections in `finally`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_store.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** per the Interfaces block.

- [ ] **Step 4: Run the tests**, with local Qdrant running for the integration test (`scripts/start.ps1` or `tools/qdrant/qdrant.exe` as in `start.ps1`)

Run: `cd backend && uv run pytest tests/test_store.py tests/test_retrieval.py -q -m "integration or not integration"`
Expected: PASS. The integration test must actually run (not skip), proving Qdrant 1.19 accepts `vectors_config={}`. If Qdrant rejects it, use a 1-dim dummy vector `{"_": VectorParams(size=1, distance=DOT)}` and write `[0.0]`.

- [ ] **Step 5: Commit**

```bash
git add backend/qabuddy/index/store.py backend/tests/test_store.py
git commit -m "feat(store): Qdrant Cloud API key, auth/missing-collection errors, meta and raw helpers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Index catalog and manifest

**Files:**
- Create: `backend/qabuddy/catalog.py`
- Test: `backend/tests/test_catalog.py` (new)

**Interfaces:**
- Consumes: `VectorStore.read_meta / write_meta / ensure_meta_collection`, `make_store` (Task 5); `Database` (existing).
- Produces:
  - `MANIFEST_ID = "00000000-0000-0000-0000-000000000001"`.
  - `file_stats(db: Database) -> dict` → `{"by_source": {source: chunk_count}, "files": int, "failed_files": [{"path", "error"}]}`. This is the exact logic now in `/api/stats`, including skipping `__`-prefixed paths in the `files` count.
  - `SqliteCatalog(db: Database)`, `hint = "qabuddy ingest"`.
  - `ManifestCatalog(store: VectorStore, meta_collection: str, ttl_s: float = 300.0, clock: Callable[[], float] = time.monotonic)`, `hint = "qabuddy publish"`, plus `manifest() -> dict | None`. A found manifest is cached for `ttl_s`; a missing one is not cached.
  - Both catalogs provide `index_version() -> int`, `known_entities() -> set[str]`, `stats() -> dict` (same shape as `file_stats`) and `last_ingest_at() -> float | None`. For a missing manifest these return `0`, `set()`, empty stats and `None`. `IndexUnavailable` from the store propagates.
  - `build_manifest(db: Database, chunks: int, index_version: int, embed_model: str, published_at: float) -> dict` returns the keys of spec §6.4, with `entities` sorted.
  - `read_manifest(store: VectorStore, meta_collection: str) -> dict | None` and `write_manifest(store: VectorStore, meta_collection: str, manifest: dict) -> None` (ensures the collection, then writes at `MANIFEST_ID`).
  - `make_catalog(settings: Settings, db: Database | None = None, store: VectorStore | None = None) -> SqliteCatalog | ManifestCatalog`. `sqlite` uses the given `db` or `Database(settings.db_path)`; `manifest` uses the given store or `make_store(settings)`. An unknown provider raises `ValueError`.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_catalog.py`. Use the `db` fixture pattern from `test_db.py` and a `FakeMetaStore` (a dict, counting `read_meta` calls).
  - `test_file_stats`: files `a.md` (jira, 2 chunks, ok), `b.pdf` (requirements, 0 chunks, failed, "bad pdf"), `__entities__` (jira, 1 chunk) → `by_source == {"jira": 3, "requirements": 0}`, `files == 2`, `failed_files == [{"path": "b.pdf", "error": "bad pdf"}]`.
  - `test_sqlite_catalog_reads_database`: after `bump_index_version()`, `set_meta("last_ingest_at", "12.5")` and `replace_entities([("vwo-26", ...)])` → version 1, entities `{"vwo-26"}`, last ingest `12.5`.
  - `test_build_manifest_from_database`: `build_manifest(db, 1162, 3, "@cf/qwen/qwen3-embedding-0.6b", 99.0)` has `index_version == 3`, `chunks == 1162`, `entities == ["vwo-26"]`, `last_ingest_at == 12.5`, `published_at == 99.0`, and `by_source`/`files`/`failed_files` from `file_stats`.
  - `test_manifest_catalog_caches_for_ttl`: clock at 0 → two `index_version()` calls make 1 read; clock at 301 → 2 reads.
  - `test_manifest_catalog_missing_manifest`: version 0, entities `set()`, stats `{"by_source": {}, "files": 0, "failed_files": []}`, last ingest None; a second call reads again.
  - `test_make_catalog_by_provider`: local settings → `SqliteCatalog`; `load_settings(env={"QABUDDY_PROFILE": "cloud"})` with a fake store → `ManifestCatalog`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_catalog.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement `catalog.py`** per the Interfaces block.

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_catalog.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/qabuddy/catalog.py backend/tests/test_catalog.py
git commit -m "feat(catalog): SQLite and Qdrant-manifest index catalogs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Component factories and searcher wiring

**Files:**
- Create: `backend/qabuddy/components.py`
- Modify: `backend/qabuddy/retrieval/search.py`. Rename the `Searcher.__init__` parameter `db` → `catalog` (same position; the duck type `index_version()`/`known_entities()` is unchanged). `build_searcher` uses the factories.
- Test: `backend/tests/test_components.py` (new); `backend/tests/test_retrieval.py` must pass unchanged.

**Interfaces:**
- Consumes: `make_store` (Task 5), `make_catalog` (Task 6), `make_cloudflare_client`, `CloudflareEmbedder`, `CloudflareReranker` (Task 4), `Bm25QueryEncoder` (Task 3).
- Produces:
  - `make_embedder(settings: Settings, env: Mapping[str, str] | None = None) -> DenseEmbedder | CloudflareEmbedder`. `ollama` → `DenseEmbedder(settings.ollama.url, settings.ollama.embed_model)`; `cloudflare` → `CloudflareEmbedder(client, settings.cloudflare.embed_model, settings.cloudflare.embed_batch_size)`.
  - `make_reranker(settings: Settings, env: Mapping[str, str] | None = None) -> Reranker | CloudflareReranker`. `fastembed` imports `Reranker` lazily.
  - `active_embed_model(settings) -> str` and `active_reranker_model(settings) -> str` return the model name the active provider uses.
  - Unknown providers raise `ValueError("Unknown embeddings.provider 'x'")` and `ValueError("Unknown reranker.provider 'x'")`.
  - `build_searcher(settings) -> Searcher` wires `make_store(settings)`, `make_embedder(settings)`, `Bm25QueryEncoder()`, `make_reranker(settings)`, `make_catalog(settings)` and the glossary.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_components.py`
  - `test_cloud_embedder_and_reranker`: `cloud = load_settings(env={"QABUDDY_PROFILE": "cloud"})`, `env = {"CLOUDFLARE_ACCOUNT_ID": "a", "CLOUDFLARE_API_TOKEN": "t"}` → `make_embedder(cloud, env)` is a `CloudflareEmbedder` with `.model == "@cf/qwen/qwen3-embedding-0.6b"` and `.batch_size == 32`; `make_reranker(cloud, env)` is a `CloudflareReranker` with `.model == "@cf/baai/bge-reranker-base"`.
  - `test_local_embedder_is_ollama`: `make_embedder(load_settings(env={}))` is a `DenseEmbedder`.
  - `test_active_models`: local → `("qwen3-embedding:0.6b", "Xenova/ms-marco-MiniLM-L-12-v2")`; cloud → `("@cf/qwen/qwen3-embedding-0.6b", "@cf/baai/bge-reranker-base")`.
  - `test_unknown_provider_raises`: `QABUDDY_EMBEDDINGS__PROVIDER=x` → `ValueError` matching `"embeddings.provider"`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_components.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** `components.py` and the `search.py` changes.

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_components.py tests/test_retrieval.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/qabuddy/components.py backend/qabuddy/retrieval/search.py backend/tests/test_components.py
git commit -m "feat(retrieval): build the searcher from profile-selected components

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `qabuddy publish`

**Files:**
- Create: `backend/qabuddy/publish.py`
- Modify: `backend/qabuddy/cli.py` (new `publish` command)
- Test: `backend/tests/test_publish.py` (new)

**Interfaces:**
- Consumes:
  - `VectorStore.scroll_all / upsert_raw / delete / ensure_collection / exists` and `make_store` (Task 5);
  - `read_manifest / write_manifest / build_manifest` (Task 6);
  - `make_embedder`, `active_embed_model` (Task 7).
- Produces:
  - `class PublishError(RuntimeError)`.
  - `@dataclass PublishReport` with `new: int`, `changed: int`, `deleted: int`, `unchanged: int`, `texts_embedded: int`, `index_version: int`, `seconds: float`, `dry_run: bool`.
  - `diff_points(local: dict[str, dict], cloud: dict[str, dict]) -> tuple[list[str], list[str], list[str], int]`, returning sorted `(new, changed, deleted)` plus the unchanged count. "Changed" means the payload dicts differ.
  - `run_publish(local_store, cloud_store, embedder, db: Database, meta_collection: str, embed_model: str, dry_run: bool = False, progress: Callable[[str], None] | None = None, clock: Callable[[], float] = time.time) -> PublishReport`.
- Behaviour (spec §6.5):
  1. If local has 0 points, raise `PublishError("Local index is empty — run qabuddy ingest first. Refusing to publish: it would delete the cloud index.")` before touching the cloud.
  2. Unless dry-run, `cloud_store.ensure_collection(embedder.dimension)`.
  3. Diff the two scrolls.
  4. For new + changed points, in groups of 64: embed `f"{payload.get('header', '')}\n{payload.get('text', '')}"` (must equal `ingest.pipeline.dense_text`), then `upsert_raw` with `vector={"dense": d, "bm25": local_vectors["bm25"]}`.
  5. Delete the deleted ids.
  6. Write the manifest last. `index_version` = previous + 1 when anything changed or no manifest existed, else the previous value. Use `build_manifest(db, len(local), version, embed_model, clock())`.
  7. Dry-run stops after the diff and writes nothing.
- CLI: `qabuddy publish [--dry-run]` loads `load_settings(profile="local")` and `load_settings(profile="cloud")`, then calls `run_publish` with the two stores, `make_embedder(cloud)`, `Database(local.db_path)`, `cloud.qdrant.meta_collection` and `active_embed_model(cloud)`.
  - On success it prints `Published: {new} new, {changed} changed, {deleted} deleted, {unchanged} unchanged · {texts_embedded} chunks embedded · index version {v} · {seconds}s`, prefixed with `Dry run — nothing written. ` when dry.
  - `InferenceUnavailable`, `IndexUnavailable` and `PublishError` → red `ERROR: ...`, exit code 2.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_publish.py`. Use a `FakeStore` with `points: dict[id, (payload, vectors)]`, `meta: dict`, and an `ops: list[str]` log of write calls (`ensure_collection`, `upsert_raw`, `delete`, `ensure_meta_collection`, `write_meta`). Use a `FakeEmbedder` (`dimension = 2`, records texts, returns `[float(len(t)), 0.0]`, can fail on call N) and the `db` fixture.
  - `test_diff_points_buckets`: local `{a, b, c}` / cloud `{b, c', d}` (c' payload differs) → `(["a"], ["c"], ["d"], 1)`.
  - `test_first_publish_uploads_everything_and_writes_manifest`: 3 local points → `new == 3`; cloud points carry `dense` from the embedder and the same `bm25` objects as local; embedded texts are `"h\nt"` style; manifest `index_version == 1`, `chunks == 3`.
  - `test_second_publish_without_changes_keeps_version`: zero new/changed/deleted, no embed calls, version stays 1.
  - `test_changed_and_deleted_points`: change one payload and remove one local point → `changed == 1`, `deleted == 1`, version 2, deleted id gone from the cloud.
  - `test_manifest_written_last`: `cloud.ops[-1] == "write_meta"`.
  - `test_dry_run_writes_nothing`: `cloud.ops == []`, `report.dry_run` is True, counts correct.
  - `test_refuses_when_local_index_empty`: `PublishError`; `cloud.ops == []` and the cloud points are unchanged.
  - `test_failure_mid_way_leaves_manifest_unchanged`: 100 new points with the embedder failing on its 2nd call (`InferenceUnavailable`) → raises; `"write_meta" not in cloud.ops`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_publish.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** `publish.py` and the CLI command.

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_publish.py -q && uv run qabuddy publish --help`
Expected: PASS; help text lists `--dry-run`.

- [ ] **Step 5: Commit**

```bash
git add backend/qabuddy/publish.py backend/qabuddy/cli.py backend/tests/test_publish.py
git commit -m "feat(cli): qabuddy publish mirrors the local index to Qdrant Cloud

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Profile-neutral health and API wiring

**Files:**
- Create: `backend/qabuddy/health.py`
- Modify: `backend/qabuddy/api.py`
- Test: `backend/tests/test_health.py` (new), `backend/tests/test_api.py` (update)

**Interfaces:**
- Consumes:
  - `make_catalog` and the catalogs' `hint` (Task 6);
  - `make_store` (Task 5);
  - `InferenceUnavailable` (Task 4);
  - `llm_status` (existing);
  - `active_embed_model`, `active_reranker_model` (Task 7).
- Produces:
  - `check_health(settings: Settings, catalog, client: httpx.Client | None = None, env: Mapping[str, str] | None = None) -> dict` with the shape of spec §6.6: `status {level, text}`, `stack [{key, label, value, ok, error?}]` and `index {chunks, last_ingest_at, index_version}`.
  - Pill keys, labels and values:

    | key | label | local value | cloud value |
    |---|---|---|---|
    | `vector` | `Vector DB` | `Qdrant · hybrid RRF` | `Qdrant Cloud · hybrid RRF` |
    | `embed` | `Embeddings` | `qwen3-embedding:0.6b · Ollama` | `qwen3-embedding-0.6b · Cloudflare` |
    | `rerank` | `Reranker` | `ms-marco-MiniLM-L-12-v2 · FastEmbed` (always ok) | `bge-reranker-base · Cloudflare` (ok iff both Cloudflare env vars are set) |
    | `llm` | `LLM` | `DeepSeek · deepseek-flash` / `Ollama · <model>` | same |

    Model names in pills are the text after the last `/`.
  - Checks:
    - **Vector:**
      - empty `qdrant.url` → `"<url_env> is not set"`, with no request made;
      - `api_key_env` set but missing → `"<api_key_env> is not set"`;
      - otherwise `GET {url}/collections/{collection}` (header `api-key` when there is a key, timeout 3 s). 200 → chunks = `result.points_count`; 404 → ok with 0 chunks; 401/403 → `"Qdrant rejected the API key ({code}) — check QDRANT_API_KEY"`; `httpx.HTTPError` → `"Qdrant not reachable: {ExceptionName}"`.
    - **Embeddings, local:** `GET {ollama.url}/api/tags`. Unreachable → `"Ollama not reachable: {ExceptionName}"`; embed model missing → `"Run: ollama pull {model}"`. When `llm.provider == "ollama"` and the LLM model is missing, the LLM pill errors with `"Run: ollama pull {llm_model}"`.
    - **Embeddings, cloud:** `"<NAME> is not set"` for a missing Cloudflare env var (account id first).
    - **LLM:** `llm_status`.
  - Index: `catalog.index_version()` and `catalog.last_ingest_at()`; on `IndexUnavailable`, use 0 and None.
  - Status:
    - problems = errors of failing pills, plus `f"index is empty — run {catalog.hint}"` when the vector pill is ok and chunks == 0 or version == 0;
    - level `ok` if there are no problems; `down` if both the vector and embeddings pills fail; else `warn`;
    - text when ok: `f"Ready · {chunks:,} chunks indexed · {llm model}"`, otherwise the problems joined with `" · "`.
  - Refinement of spec §8: a missing manifest alone does not block chat. Health warns "index is empty — run qabuddy publish" because the version is 0, and a missing collection already returns `index_unavailable` (Task 5).
  - `create_app(settings, searcher=None, llm=None, db=None, catalog=None, http_client=None)`:
    - `database = db or Database(settings.cache_db_path)`;
    - `catalog = catalog or make_catalog(settings, db=database if settings.cache_db_path == settings.db_path else None)`;
    - the answer-cache version comes from `catalog.index_version()`. On `IndexUnavailable`, emit SSE `error` `index_unavailable`, then `done`;
    - `InferenceUnavailable` from search → SSE `llm_unavailable`;
    - `get_store` falls back to `make_store(settings)`;
    - `/api/health` = `check_health(settings, catalog, client=http_client)`;
    - `/api/stats` = `{**catalog.stats(), "sources": [...]}` (as today), with `IndexUnavailable` → 503 `index_unavailable`.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_health.py`. Use an `httpx.Client(transport=httpx.MockTransport(handler))` whose handler records URLs and headers and answers only Qdrant/Ollama URLs. Use a `FakeCatalog(version=1, hint="qabuddy publish", last=12.5)`. Env helpers: `LOCAL_ENV = {"DEEPSEEK_API_KEY": "x"}`; `CLOUD_ENV = {"QABUDDY_PROFILE": "cloud", "QDRANT_URL": "https://x.cloud.qdrant.io", "QDRANT_API_KEY": "k", "CLOUDFLARE_ACCOUNT_ID": "a", "CLOUDFLARE_API_TOKEN": "t", "DEEPSEEK_API_KEY": "x"}`.
  - `test_local_all_ok`: Qdrant 200 with `points_count: 1162`; Ollama tags include `qwen3-embedding:0.6b` → status `{"level": "ok", "text": "Ready · 1,162 chunks indexed · deepseek-flash"}`; `[(p["label"], p["value"], p["ok"]) for p in stack]` equals the local column of the table, all True.
  - `test_cloud_all_ok`: values equal the cloud column; exactly one request, to `https://x.cloud.qdrant.io/collections/qabuddy_chunks` with header `api-key: k`. No Cloudflare or DeepSeek host is contacted.
  - `test_cloud_missing_qdrant_url`: `QDRANT_URL` removed → vector pill `{"ok": False, "error": "QDRANT_URL is not set"}`, zero requests, level `warn`.
  - `test_cloud_missing_cloudflare_token`: the embed and rerank pills have error `"CLOUDFLARE_API_TOKEN is not set"`.
  - `test_qdrant_401`: the vector error contains `"QDRANT_API_KEY"`.
  - `test_empty_index_tells_how_to_fill_it`: points_count 0 → level `warn`, text contains `"index is empty — run qabuddy publish"`.
  - `test_local_everything_down`: both handlers raise `httpx.ConnectError` → level `down`.
- Update `backend/tests/test_api.py`:
  - delete `test_health_lists_backend_stack`;
  - rewrite `test_health_reports_deepseek_key_status` to assert on the `llm` pill (`ok` False with error `"DEEPSEEK_API_KEY is not set"`, then ok True once the key is set). Pass `http_client` with a MockTransport answering Qdrant 200 and Ollama tags;
  - add `test_chat_reports_index_unavailable_when_catalog_fails` (catalog whose `index_version` raises `IndexUnavailable("down")` → events `[("error", {"code": "index_unavailable", ...}), ("done", ...)]`);
  - add `test_chat_maps_inference_errors_to_llm_unavailable` (searcher raising `InferenceUnavailable("cf down")` → error code `llm_unavailable`);
  - add `test_stats_comes_from_catalog` (FakeCatalog stats plus `sources` from `sources.yaml`).

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_health.py tests/test_api.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** `health.py` and the `api.py` changes. Remove the inline health code and the Task 1 `stack` dict from `api.py`.

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/qabuddy/health.py backend/qabuddy/api.py backend/tests/test_health.py backend/tests/test_api.py
git commit -m "feat(api): profile-neutral health with stack pills; catalog-backed cache and stats

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Frontend renders the backend stack

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/components/StackBar.tsx`, `frontend/src/styles.css` (drop unused variants)
- Rename + rewrite: `frontend/src/stackbar.test.ts` → `frontend/src/stackbar.test.tsx`

**Interfaces:**
- Consumes: the `/api/health` shape (Task 9).
- Produces:
  - `types.ts`: `interface StackPill { key: string; label: string; value: string; ok: boolean; error?: string }` and `interface Health { status: { level: 'ok' | 'warn' | 'down'; text: string }; stack: StackPill[]; index: { chunks: number; last_ingest_at: number | null; index_version: number } }`.
  - `StackBar({ health, error }: { health: Health | null; error: boolean })`. `healthState` is deleted from the frontend.
  - `statusLabel(health: Health | null, error: boolean): string` → `'API offline'` when `error || !health`; `` `Ready · ${health.index.chunks.toLocaleString()} chunks` `` when level is ok; `'Degraded'` for warn; `'Services down'` for down.
- Rendering:
  - Status pill: class `pill pill--status pill--{level}` (offline → `down`), icon `✓` / `!` / `✗`, title = `status.text` (offline → `"Server not reachable"`).
  - Each stack pill: class `pill pill--{state}` with state `ok` / `down` / `stale` (stale whenever offline). It contains a dot, `pill__label` and `pill__value`. Title = `error ?? value`, prefixed with `"Last known: "` when stale.
  - Offline before any health response → status pill only.

- [ ] **Step 1: Write the failing tests** in `frontend/src/stackbar.test.tsx` (`render`/`screen` from `@testing-library/react`)
  - `renders backend pills`: four pills; `screen.getByText('Qdrant Cloud · hybrid RRF')` is present; the status reads `Ready · 1,162 chunks`.
  - `marks a failing pill`: a pill `{ok: false, error: 'QDRANT_URL is not set'}` has class `pill--down` and title `'QDRANT_URL is not set'`.
  - `greys last-known pills when offline`: status text `API offline`; every stack pill has class `pill--stale` and a title starting `Last known: `.
  - `shows only the status pill before first health`: `container.querySelectorAll('.pill').length === 1`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/stackbar.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement** the types and `StackBar` changes. `App.tsx` keeps passing `health` and `error`.

- [ ] **Step 4: Run the tests and the build**

Run: `cd frontend && npx vitest run && npm run build`
Expected: all tests PASS; `tsc -b` reports no errors; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): stack strip renders backend-provided status and pills

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Profile-aware eval

**Files:**
- Modify: `backend/qabuddy/evaluate.py`: `write_settings(settings_path: Path, reranker: str | None, threshold: float) -> None`. When `reranker` is None, only the `min_rerank_score:` line changes.
- Modify: `backend/qabuddy/cli.py` (`eval_command`):
  - writes to `settings.settings_file`;
  - passes `reranker=None` unless `settings.reranker.provider == "fastembed"`;
  - prints `active_reranker_model(settings)` in the summary and `"{settings.settings_file.name} updated"`;
  - `--reranker` with a non-fastembed provider exits 2 with `"--reranker only applies to the local FastEmbed reranker"`.
- Test: `backend/tests/test_evaluate.py` (add)

**Interfaces:**
- Consumes: `Settings.settings_file` (Task 2), `active_reranker_model` (Task 7).

- [ ] **Step 1: Write the failing test** `test_write_settings_without_reranker_only_changes_threshold`: copy `config/settings.cloud.yaml` to `tmp_path`, call `write_settings(path, None, -1.25)` → the file equals the original with `min_rerank_score: 0.0` replaced by `min_rerank_score: -1.25`, all other lines unchanged.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_evaluate.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** the `write_settings` and CLI changes.

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_evaluate.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/qabuddy/evaluate.py backend/qabuddy/cli.py backend/tests/test_evaluate.py
git commit -m "feat(eval): write the calibrated threshold to the active profile's settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Vercel packaging

**Files:**
- Create (repo root): `vercel.json`, `vercel_app.py`, `requirements.txt`, `.python-version`, `.vercelignore`
- Modify: `.gitignore` (add `.vercel/`)
- Test: `backend/tests/test_vercel_app.py` (new)

**Interfaces:**
- Consumes: `create_app`, `load_settings` (Tasks 2, 9).
- Produces: module `vercel_app` exposing `settings` (cloud profile) and `app`.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_vercel_app.py`
  - `test_cloud_entrypoint_imports_without_heavy_packages`:
    - run `[sys.executable, "-c", CODE]` with `cwd=REPO_ROOT` and an env copied from `os.environ` minus `QABUDDY_PROFILE`, plus `QABUDDY_CACHE__DB_PATH=<tmp_path>/cache.db`;
    - `CODE` sets `sys.modules[m] = None` for each heavy module in Global Constraints, then `import vercel_app` and prints `vercel_app.settings.profile, vercel_app.app.title`;
    - assert return code 0 and stdout `cloud QABuddy.ai`.
  - `test_vercelignore_excludes_private_folders`: the `.vercelignore` lines include `data/`, `storage/`, `tools/`, `.env`, `*.env`.
  - `test_requirements_are_slim`: package names in `requirements.txt` don't intersect `{docling, fastembed, onnxruntime, ollama, tree-sitter, openpyxl, pypdf, torch}` and include `mmh3`, `py-rust-stemmers`, `fastapi`, `qdrant-client`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_vercel_app.py -q`
Expected: FAIL (`No module named 'vercel_app'`).

- [ ] **Step 3: Create the files**

`vercel_app.py`:
```python
"""Vercel entrypoint: the QABuddy API with the cloud profile (spec §7)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QABUDDY_PROFILE", "cloud")
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from qabuddy.api import create_app  # noqa: E402
from qabuddy.config import load_settings  # noqa: E402

settings = load_settings()
app = create_app(settings)
```

`vercel.json`:
```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "services": {
    "web": { "root": "frontend/" },
    "api": {
      "root": "./",
      "entrypoint": "vercel_app:app",
      "functions": {
        "vercel_app.py": {
          "maxDuration": 60,
          "excludeFiles": "{frontend/**,docs/**,scripts/**,backend/tests/**,backend/uv.lock,**/__pycache__/**}"
        }
      }
    }
  },
  "rewrites": [
    { "source": "/api/(.*)", "destination": { "service": "api" } },
    { "source": "/(.*)", "destination": { "service": "web" } }
  ],
  "crons": [{ "path": "/api/health", "schedule": "0 6 * * *" }]
}
```

`requirements.txt`:
```
fastapi>=0.142,<1
qdrant-client>=1.19,<2
httpx>=0.28,<1
pydantic>=2.13,<3
pyyaml>=6.0
mmh3>=5.3
py-rust-stemmers>=0.1.8
```

`.python-version`: `3.12`

`.vercelignore`:
```
data/
storage/
tools/
.superpowers/
docs/
scripts/
backend/.venv/
backend/tests/
**/__pycache__/
**/node_modules/
frontend/dist/
.env
*.env
*.log
.qdrant-initialized
snapshots/
```

No `regions` key: Vercel's default function region `iad1` already sits next to Qdrant AWS `us-east-1` (spec §7).

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_vercel_app.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add vercel.json vercel_app.py requirements.txt .python-version .vercelignore .gitignore backend/tests/test_vercel_app.py
git commit -m "build(vercel): Services config, slim cloud entrypoint and upload ignores

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Publish to Qdrant Cloud and calibrate (needs the owner's accounts)

**Gate:** start only after the owner confirms they created the Qdrant Cloud free cluster (AWS `us-east-1`) and the Cloudflare token, and set the env vars on the laptop.

- [ ] **Step 1: Confirm the env vars are present without printing them**

Run: `cd backend && uv run python -c "import os; print({k: bool(os.environ.get(k)) for k in ['QDRANT_URL','QDRANT_API_KEY','CLOUDFLARE_ACCOUNT_ID','CLOUDFLARE_API_TOKEN','DEEPSEEK_API_KEY']})"`
Expected: all `True`. If any is False, stop and tell the owner which one.

- [ ] **Step 2: Make sure local Qdrant is running** (as in `scripts/start.ps1`), then dry-run

Run: `cd backend && uv run qabuddy publish --dry-run`
Expected: `Dry run — nothing written. Published: 1162 new, 0 changed, 0 deleted, 0 unchanged ...` (the count equals the local index).

- [ ] **Step 3: Publish, then publish again**

Run: `cd backend && uv run qabuddy publish && uv run qabuddy publish`
Expected: first `1162 new ... index version 1`; second `0 new, 0 changed, 0 deleted, 1162 unchanged · 0 chunks embedded · index version 1`.

- [ ] **Step 4: Run the integration BM25 parity test against the real index**

Run: `cd backend && uv run pytest tests/test_bm25.py -q -m integration`
Expected: PASS (not skipped).

- [ ] **Step 5: Cloud eval with calibration**

Run (PowerShell): `cd backend; $env:QABUDDY_PROFILE='cloud'; uv run qabuddy eval --write; Remove-Item Env:QABUDDY_PROFILE`
Expected: `gate=PASS`, hit@6 ≥ 95.65 % (at least 22 of the 23 answerable questions; local is 23/23), `no-answer=3/3`, `settings.cloud.yaml updated`. If the gate fails or quality drops more than one question: **stop**, report the per-question table to the owner, and do not deploy.

- [ ] **Step 6: Commit the calibrated threshold**

```bash
git add config/settings.cloud.yaml
git commit -m "chore(cloud): calibrate bge-reranker-base threshold on the eval set

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Deploy to Vercel (owner adds secrets)

- [ ] **Step 1: Link the project**

Run (repo root): `vercel link --yes --scope qa-explorer --project qabuddy`
Expected: `.vercel/project.json` exists; `git status` does not show `.vercel/`.

- [ ] **Step 2: Hand the owner these commands.** The owner runs them and pastes the values at the CLI prompt; Claude never sees the values.

```powershell
foreach ($name in 'DEEPSEEK_API_KEY','QDRANT_URL','QDRANT_API_KEY','CLOUDFLARE_ACCOUNT_ID','CLOUDFLARE_API_TOKEN') {
  vercel env add $name production --scope qa-explorer
  vercel env add $name preview --scope qa-explorer
}
```

Then run `vercel env ls --scope qa-explorer`.
Expected: all five names are listed for Production and Preview (names only).

- [ ] **Step 3: Preview deploy**

Run: `vercel deploy --scope qa-explorer`
Expected: build succeeds for both services and prints a preview URL.

If the build rejects `services`, or `/api/health` returns 404 from the frontend, switch to the fallback layout and re-run Task 12's tests:
- `vercel.json` becomes `{"framework": "vite", "installCommand": "cd frontend && npm ci", "buildCommand": "cd frontend && npm run build", "outputDirectory": "frontend/dist", "functions": {"api/index.py": {"maxDuration": 60, "excludeFiles": <same glob>}}, "rewrites": [{"source": "/api/(.*)", "destination": "/api/index"}], "crons": <same>}`;
- add `api/index.py`, which adds the repo root to `sys.path` and does `from vercel_app import app`.

- [ ] **Step 4: Preview acceptance checks.** If the preview URL answers 401 (Deployment Protection), use `vercel curl <path> --deployment <preview-url>` when `vercel curl --help` exists. Otherwise ask the owner to open the preview in their logged-in browser, and run these checks on production right after Step 5.
  - `GET /api/health` → `status.level == "ok"`, four `ok` pills with the cloud values.
  - For each of these three questions:
    - "What is VWO-26 about and what is its status?"
    - "How does DriverManager create and manage the WebDriver?"
    - "What is our flaky test policy and when is a test quarantined?"

    run `curl -N -X POST <url>/api/chat -H "Content-Type: application/json" -d "{\"question\": \"...\"}"`. Expect `event: sources`, then `event: token` events, then `event: done` with non-empty `citations_used`.
  - `GET /api/chunks/<chunk_id of the first source>` → 200 with `text`.
  - Record the cold-start time (first request after deploy) and the warm time (second request) for `/api/health` and one chat.
  - Owner check: the deployment's Source view in the Vercel dashboard has no `data/` or `storage/` folder.

- [ ] **Step 5: Production deploy**

Run: `vercel deploy --prod --scope qa-explorer`
Expected: a production URL. Repeat `/api/health` and one chat question on it, then give the owner the URL.

- [ ] **Step 6: Commit any fallback changes** (only if Step 3 switched layouts)

```bash
git add vercel.json api/index.py
git commit -m "build(vercel): fall back to Vite preset + api/ function

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Cloud runbook and final verification

**Files:**
- Create: `docs/deploy-vercel.md`. It covers:
  - the architecture line;
  - accounts to create (Qdrant Cloud free on AWS `us-east-1`; a Cloudflare token with Workers AI permission; keep Workers on the Free plan);
  - the five env vars and where each goes;
  - the routine "after each ingest: `uv run qabuddy publish`" (no redeploy needed);
  - cloud eval: `QABUDDY_PROFILE=cloud qabuddy eval --write`;
  - deploy commands (`vercel deploy`, `vercel deploy --prod`);
  - the daily keep-alive cron;
  - cost guards (small prepaid DeepSeek balance; Cloudflare Free plan caps at 10k neurons/day);
  - the public-URL caveat.
- Modify (only with the owner's OK, because of their pending edits): `README.md`, one line linking `docs/deploy-vercel.md`.

- [ ] **Step 1: Write `docs/deploy-vercel.md`** from the list above, using the production URL from Task 14.

- [ ] **Step 2: Ask the owner** whether their pending README edits may be committed with the new link line. If not, leave README untouched.

- [ ] **Step 3: Full verification**

Run: `cd backend && uv run pytest -q` then `cd frontend && npx vitest run && npm run build`
Expected: all pass. Then confirm the local app still works: start it (`scripts/start.ps1`), `GET http://localhost:8000/api/health` shows local pill values and level `ok`.

- [ ] **Step 4: Commit**

```bash
git add docs/deploy-vercel.md   # plus README.md only if the owner agreed
git commit -m "docs: Vercel cloud runbook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Finish the branch** with superpowers:finishing-a-development-branch (merge/PR choice is the owner's).
