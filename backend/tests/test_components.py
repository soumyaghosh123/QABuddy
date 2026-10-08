import pytest

from qabuddy.components import active_embed_model, active_reranker_model, make_embedder, make_reranker
from qabuddy.config import load_settings
from qabuddy.index.embedder import DenseEmbedder
from qabuddy.inference.cloudflare import CloudflareEmbedder, CloudflareReranker

CF_ENV = {"CLOUDFLARE_ACCOUNT_ID": "a", "CLOUDFLARE_API_TOKEN": "t"}


def test_cloud_embedder_and_reranker():
    cloud = load_settings(env={"QABUDDY_PROFILE": "cloud"})
    embedder = make_embedder(cloud, CF_ENV)
    assert isinstance(embedder, CloudflareEmbedder)
    assert (embedder.model, embedder.batch_size) == ("@cf/qwen/qwen3-embedding-0.6b", 32)
    reranker = make_reranker(cloud, CF_ENV)
    assert isinstance(reranker, CloudflareReranker) and reranker.model == "@cf/baai/bge-reranker-base"


def test_local_embedder_is_ollama():
    assert isinstance(make_embedder(load_settings(env={})), DenseEmbedder)


def test_active_models():
    local, cloud = load_settings(env={}), load_settings(env={"QABUDDY_PROFILE": "cloud"})
    assert (active_embed_model(local), active_reranker_model(local)) == (
        "qwen3-embedding:0.6b", "Xenova/ms-marco-MiniLM-L-12-v2")
    assert (active_embed_model(cloud), active_reranker_model(cloud)) == (
        "@cf/qwen/qwen3-embedding-0.6b", "@cf/baai/bge-reranker-base")


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="embeddings.provider"):
        make_embedder(load_settings(env={"QABUDDY_EMBEDDINGS__PROVIDER": "x"}))
    with pytest.raises(ValueError, match="reranker.provider"):
        make_reranker(load_settings(env={"QABUDDY_RERANKER__PROVIDER": "x"}))
