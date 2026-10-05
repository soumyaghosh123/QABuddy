import pytest

from qabuddy.index.embedder import DenseEmbedder, SparseEmbedder
from qabuddy.index.store import VectorStore
from qabuddy.ingest.pipeline import run_ingest

from .integration import require_services

pytestmark = pytest.mark.integration


@pytest.fixture()
def test_settings(settings, tmp_path):
    require_services(settings)
    s = settings.model_copy(deep=True)
    s.qdrant.collection = "qabuddy_test"
    s.storage_dir = str(tmp_path)
    yield s
    VectorStore(s.qdrant.url, s.qdrant.collection).drop()


def test_ingest_and_find_ticket(test_settings):
    report = run_ingest(test_settings, full=True, sources=["jira"])
    assert report.files_failed == []
    assert report.chunks_upserted == 5

    store = VectorStore(test_settings.qdrant.url, test_settings.qdrant.collection)
    dense = DenseEmbedder(test_settings.ollama.url, test_settings.ollama.embed_model)
    sparse = SparseEmbedder()
    question = "VWO-26 login failure"
    hits = store.hybrid(dense.embed_query(question), sparse.embed_query(question), ids=["vwo-26"],
                        sources=None, dense_k=30, sparse_k=30, id_k=10, limit=10)
    assert hits[0].payload["citation"] == "VWO-26"

    again = run_ingest(test_settings, full=False, sources=["jira"])
    assert again.files_changed == 0 and again.chunks_upserted == 0
