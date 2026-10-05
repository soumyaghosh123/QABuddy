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
    data_dir: str = "data"
    storage_dir: str = "storage"
    qdrant: QdrantSettings = QdrantSettings()
    ollama: OllamaSettings = OllamaSettings()
    llm: LLMSettings = LLMSettings()
    reranker: RerankerSettings = RerankerSettings()
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


def load_settings(path: Path | None = None, env: Mapping[str, str] | None = None) -> Settings:
    path = path or CONFIG_DIR / "settings.yaml"
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _apply_env(raw, os.environ if env is None else env)
    return Settings.model_validate(raw)
