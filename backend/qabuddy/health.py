"""Profile-neutral /api/health: a status line plus one pill per backend component (spec §6.6).

Costs nothing to poll: one Qdrant collection GET, an Ollama /api/tags GET only when something runs on
Ollama, and key-presence checks for Cloudflare and DeepSeek. It never calls an embedding, rerank or LLM API.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Mapping

import httpx

from qabuddy.answer.llm import llm_status
from qabuddy.index.store import IndexUnavailable

if TYPE_CHECKING:
    from qabuddy.config import Settings

PROVIDERS = {"deepseek": "DeepSeek", "ollama": "Ollama"}


def _short(model: str) -> str:
    return model.rsplit("/", 1)[-1]


def _pill(key: str, label: str, value: str, error: str | None = None) -> dict:
    pill = {"key": key, "label": label, "value": value, "ok": error is None}
    if error:
        pill["error"] = error
    return pill


def _missing_env(names: tuple[str, ...], env: Mapping[str, str]) -> str | None:
    return next((f"{name} is not set" for name in names if not env.get(name)), None)


def _check_qdrant(settings: Settings, client: httpx.Client, env: Mapping[str, str]) -> tuple[str | None, int]:
    q = settings.qdrant
    if not q.url:
        return f"{q.url_env or 'qdrant.url'} is not set", 0
    key = q.api_key(env)
    if q.api_key_env and not key:
        return f"{q.api_key_env} is not set", 0
    try:
        response = client.get(f"{q.url.rstrip('/')}/collections/{q.collection}",
                              headers={"api-key": key} if key else {}, timeout=3)
    except httpx.HTTPError as exc:
        return f"Qdrant not reachable: {type(exc).__name__}", 0
    if response.status_code in (401, 403):
        return f"Qdrant rejected the API key ({response.status_code}) — check QDRANT_API_KEY", 0
    if response.status_code == 404:
        return None, 0
    if response.status_code != 200:
        return f"Qdrant returned {response.status_code}", 0
    return None, int(response.json()["result"].get("points_count") or 0)


def _ollama_models(settings: Settings, client: httpx.Client) -> tuple[set[str], str | None]:
    try:
        tags = client.get(f"{settings.ollama.url}/api/tags", timeout=3).json()
    except (httpx.HTTPError, ValueError) as exc:
        return set(), f"Ollama not reachable: {type(exc).__name__}"
    return {m["name"] for m in tags.get("models", [])}, None


def check_health(settings: Settings, catalog, client: httpx.Client | None = None,
                 env: Mapping[str, str] | None = None) -> dict:
    env = os.environ if env is None else env
    own_client = client is None
    client = client or httpx.Client()
    try:
        vector_error, chunks = _check_qdrant(settings, client, env)
        uses_ollama = settings.embeddings.provider == "ollama" or settings.llm.provider == "ollama"
        models, ollama_error = _ollama_models(settings, client) if uses_ollama else (set(), None)
    finally:
        if own_client:
            client.close()

    vector = _pill("vector", "Vector DB", f"{'Qdrant' if settings.profile == 'local' else 'Qdrant Cloud'} · hybrid RRF",
                   vector_error)
    cf = settings.cloudflare
    cf_error = _missing_env((cf.account_id_env, cf.api_token_env), env)
    if settings.embeddings.provider == "cloudflare":
        embed = _pill("embed", "Embeddings", f"{_short(cf.embed_model)} · Cloudflare", cf_error)
    else:
        model = settings.ollama.embed_model
        embed = _pill("embed", "Embeddings", f"{model} · Ollama",
                      ollama_error or (None if model in models else f"Run: ollama pull {model}"))
    if settings.reranker.provider == "cloudflare":
        rerank = _pill("rerank", "Reranker", f"{_short(cf.rerank_model)} · Cloudflare", cf_error)
    else:
        rerank = _pill("rerank", "Reranker", f"{_short(settings.reranker.model)} · FastEmbed")  # runs in-process
    llm = llm_status(settings, env)
    llm_error = None if llm["ok"] else llm.get("error", f"{llm['model']} unavailable")
    if llm["provider"] == "ollama" and llm_error is None:
        llm_error = ollama_error or (None if llm["model"] in models else f"Run: ollama pull {llm['model']}")
    llm_pill = _pill("llm", "LLM", f"{PROVIDERS.get(llm['provider'], llm['provider'])} · {llm['model']}", llm_error)
    stack = [vector, embed, rerank, llm_pill]

    try:
        version, last = catalog.index_version(), catalog.last_ingest_at()
    except IndexUnavailable:
        version, last = 0, None
    problems = [p["error"] for p in stack if not p["ok"]]
    if vector["ok"] and (chunks == 0 or version == 0):
        problems.append(f"index is empty — run {catalog.hint}")
    if not problems:
        status = {"level": "ok", "text": f"Ready · {chunks:,} chunks indexed · {llm['model']}"}
    else:
        status = {"level": "down" if not vector["ok"] and not embed["ok"] else "warn", "text": " · ".join(problems)}
    return {"status": status, "stack": stack,
            "index": {"chunks": chunks, "last_ingest_at": last, "index_version": version}}
