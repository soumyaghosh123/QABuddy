"""Profile-selected retrieval components: dense embedder and reranker (local or Cloudflare)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from qabuddy.config import Settings


def make_embedder(settings: Settings, env: Mapping[str, str] | None = None):
    provider = settings.embeddings.provider
    if provider == "ollama":
        from qabuddy.index.embedder import DenseEmbedder

        return DenseEmbedder(settings.ollama.url, settings.ollama.embed_model)
    if provider == "cloudflare":
        from qabuddy.inference.cloudflare import CloudflareEmbedder, make_cloudflare_client

        return CloudflareEmbedder(make_cloudflare_client(settings, env), settings.cloudflare.embed_model,
                                  settings.cloudflare.embed_batch_size)
    raise ValueError(f"Unknown embeddings.provider {provider!r}")


def make_reranker(settings: Settings, env: Mapping[str, str] | None = None):
    provider = settings.reranker.provider
    if provider == "fastembed":
        from qabuddy.retrieval.reranker import Reranker  # imports FastEmbed: local profile only

        return Reranker(settings.reranker.model)
    if provider == "cloudflare":
        from qabuddy.inference.cloudflare import CloudflareReranker, make_cloudflare_client

        return CloudflareReranker(make_cloudflare_client(settings, env), settings.cloudflare.rerank_model)
    raise ValueError(f"Unknown reranker.provider {provider!r}")


def active_embed_model(settings: Settings) -> str:
    return settings.cloudflare.embed_model if settings.embeddings.provider == "cloudflare" else settings.ollama.embed_model


def active_reranker_model(settings: Settings) -> str:
    return settings.cloudflare.rerank_model if settings.reranker.provider == "cloudflare" else settings.reranker.model
