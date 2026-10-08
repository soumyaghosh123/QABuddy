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
    assert s.retrieval.top_k == 6 and s.llm.provider == "deepseek"  # untouched keys survive the merge


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
