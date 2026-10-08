"""Settings loaded from config/settings.yaml, overridable by QABUDDY_<SECTION>__<KEY> env vars."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
ENV_PREFIX = "QABUDDY_"


class QdrantSettings(BaseModel):
    url: str = "http://localhost:6333"
    collection: str = "qabuddy_chunks"
    url_env: str = ""  # cloud: QDRANT_URL — replaces `url` when that env var is set
    api_key_env: str = ""  # cloud: QDRANT_API_KEY; empty → no key (local)
    meta_collection: str = "qabuddy_meta"

    def api_key(self, env: Mapping[str, str] | None = None) -> str | None:
        if not self.api_key_env:
            return None
        return (os.environ if env is None else env).get(self.api_key_env) or None


class EmbeddingsSettings(BaseModel):
    provider: str = "ollama"  # "ollama" (local) or "cloudflare" (Workers AI)


class CatalogSettings(BaseModel):
    provider: str = "sqlite"  # "sqlite" (storage/qabuddy.db) or "manifest" (stored in Qdrant by `qabuddy publish`)


class CacheSettings(BaseModel):
    db_path: str = ""  # answer-cache SQLite file; empty → storage/qabuddy.db


class CloudflareSettings(BaseModel):
    account_id_env: str = "CLOUDFLARE_ACCOUNT_ID"
    api_token_env: str = "CLOUDFLARE_API_TOKEN"
    embed_model: str = "@cf/qwen/qwen3-embedding-0.6b"
    rerank_model: str = "@cf/baai/bge-reranker-base"
    timeout_s: float = 30
    embed_batch_size: int = 32


class OllamaSettings(BaseModel):
    url: str = "http://localhost:11434"
    embed_model: str = "qwen3-embedding:0.6b"
    llm_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    num_ctx: int = 8192
    temperature: float = 0.2
    max_answer_tokens: int = 700
    timeout_s: int = 300


class LLMSettings(BaseModel):
    """Which model writes the answer. Temperature, answer length and timeout come from `ollama`."""
    provider: str = "ollama"  # "ollama" (local) or "deepseek" (any OpenAI-compatible chat API)
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    api_key_env: str = "DEEPSEEK_API_KEY"  # name of the env var holding the key; the key never goes in a file
    extra_body: dict = Field(default_factory=lambda: {"thinking": {"type": "disabled"}})


class RerankerSettings(BaseModel):
    provider: str = "fastembed"  # "fastembed" (local ONNX) or "cloudflare" (Workers AI)
    model: str = "Xenova/ms-marco-MiniLM-L-12-v2"
    max_chars: int = 1200  # header + text characters scored per candidate (bounds CPU time)


class RetrievalSettings(BaseModel):
    dense_k: int = 30
    sparse_k: int = 30
    id_k: int = 10
    rerank_candidates: int = 30
    top_k: int = 6
    max_per_parent: int = 3
    min_rerank_score: float = 0.0


class ChunkingSettings(BaseModel):
    target_tokens: int = 400
    max_tokens: int = 800
    min_tokens: int = 40
    prose_overlap_tokens: int = 60


class PromptSettings(BaseModel):
    max_source_tokens: int = 2800
    history_turns: int = 2


class ApiSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class Settings(BaseModel):
    profile: str = "local"  # "local" (laptop) or "cloud" (Vercel); picks config/settings.<profile>.yaml
    data_dir: str = "data"
    storage_dir: str = "storage"
    qdrant: QdrantSettings = QdrantSettings()
    ollama: OllamaSettings = OllamaSettings()
    llm: LLMSettings = LLMSettings()
    embeddings: EmbeddingsSettings = EmbeddingsSettings()
    reranker: RerankerSettings = RerankerSettings()
    catalog: CatalogSettings = CatalogSettings()
    cache: CacheSettings = CacheSettings()
    cloudflare: CloudflareSettings = CloudflareSettings()
    retrieval: RetrievalSettings = RetrievalSettings()
    chunking: ChunkingSettings = ChunkingSettings()
    prompt: PromptSettings = PromptSettings()
    api: ApiSettings = ApiSettings()

    @property
    def data_path(self) -> Path:
        return _resolve(self.data_dir)

    @property
    def storage_path(self) -> Path:
        return _resolve(self.storage_dir)

    @property
    def config_path(self) -> Path:
        return CONFIG_DIR

    @property
    def db_path(self) -> Path:
        return self.storage_path / "qabuddy.db"

    @property
    def cache_db_path(self) -> Path:
        if not self.cache.db_path:
            return self.db_path
        path = Path(self.cache.db_path)
        return path if path.is_absolute() or path.root else REPO_ROOT / path  # "/tmp/..." stays rooted on Windows too

    @property
    def settings_file(self) -> Path:
        return CONFIG_DIR / ("settings.yaml" if self.profile == "local" else f"settings.{self.profile}.yaml")


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def _apply_env(raw: dict[str, Any], env: Mapping[str, str]) -> None:
    for name, value in env.items():
        if not name.startswith(ENV_PREFIX):
            continue
        keys = name[len(ENV_PREFIX):].lower().split("__")
        target = raw
        for key in keys[:-1]:
            target = target.setdefault(key, {})
        target[keys[-1]] = yaml.safe_load(value)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings(path: Path | None = None, env: Mapping[str, str] | None = None,
                  profile: str | None = None) -> Settings:
    """settings.yaml → settings.<profile>.yaml → QABUDDY_* env overrides → qdrant.url from qdrant.url_env."""
    env = os.environ if env is None else env
    path = path or CONFIG_DIR / "settings.yaml"
    profile = profile or env.get("QABUDDY_PROFILE") or "local"
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if profile != "local":
        overlay = path.parent / f"settings.{profile}.yaml"
        if not overlay.exists():
            raise ValueError(f"Unknown settings profile {profile!r}: {overlay} not found")
        raw = _deep_merge(raw, yaml.safe_load(overlay.read_text(encoding="utf-8")) or {})
    _apply_env(raw, env)
    raw["profile"] = profile  # the profile argument beats QABUDDY_PROFILE
    settings = Settings.model_validate(raw)
    if settings.qdrant.url_env and env.get(settings.qdrant.url_env):
        settings.qdrant.url = env[settings.qdrant.url_env]
    return settings
