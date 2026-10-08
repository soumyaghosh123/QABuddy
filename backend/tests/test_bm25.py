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
