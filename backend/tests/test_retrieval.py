import pytest
from qdrant_client import models

from qabuddy.index.store import ScoredChunk
from qabuddy.retrieval.query import extract_query_ids, prepare_query
from qabuddy.retrieval.search import Hit, Searcher, apply_diversity


def test_extract_query_ids_variants():
    assert "vwo-26" in extract_query_ids("What is VWO-26 about?", known=set())
    assert "vwo-26" in extract_query_ids("status of vwo-26", known=set())
    assert "vwo-26" in extract_query_ids("is vwo 26 fixed", known={"vwo-26"})
    assert "build-142" in extract_query_ids("why did build 142 fail", known=set())
    assert "build-142" in extract_query_ids("failures in #142", known=set())
    assert "attempt-1" not in extract_query_ids("attempt 1 failed", known={"vwo-26"})


def test_extract_query_ids_known_identifiers():
    ids = extract_query_ids("Why does testLoginPositiveVWO time out?", known={"testloginpositivevwo"})
    assert ids == ["testloginpositivevwo"]


def test_followup_prepends_previous_question():
    history = [{"role": "user", "content": "Why did build 142 fail?"}, {"role": "assistant", "content": "x"}]
    prepared = prepare_query("and in 143?", history, known=set(), glossary={})
    assert "Why did build 142 fail?" in prepared.retrieval_text and "and in 143?" in prepared.retrieval_text


def test_standalone_question_ignores_history():
    history = [{"role": "user", "content": "Why did build 142 fail?"}]
    prepared = prepare_query("Which test cases cover the Support page module?", history, known=set(), glossary={})
    assert prepared.retrieval_text == "Which test cases cover the Support page module?"


def test_bm25_text_has_glossary_and_identifier_parts():
    prepared = prepare_query("Which TC covers loggedInUserName?", [], known=set(), glossary={"tc": ["test case"]})
    assert "test case" in prepared.bm25_text and "logged in user name" in prepared.bm25_text


def _hit(n, parent, score=1.0):
    return Hit(n=n, chunk_id=str(n), citation=f"c{n}", source="s", kind="k", path="p", location={},
               header="h", text="t", snippet="t", score=score, parent_id=parent)


def test_diversity_cap():
    hits = [_hit(i, "same") for i in range(5)] + [_hit(9, "other")]
    capped = apply_diversity(hits, max_per_parent=3)
    assert [h.chunk_id for h in capped] == ["0", "1", "2", "9"]


class FakeStore:
    def __init__(self, n=8):
        self.n = n

    def hybrid(self, dense_q, sparse_q, ids, sources, dense_k, sparse_k, id_k, limit):
        return [ScoredChunk(str(i), 0.5, {"chunk_id": str(i), "citation": f"c{i}", "source": "jira", "kind": "ticket",
                                          "path": "p", "location": {}, "header": "h", "text": f"text {i}",
                                          "parent_id": f"p{i}"}) for i in range(self.n)]


class FakeDense:
    def embed_query(self, q):
        return [0.0]


class FakeSparse:
    def embed_query(self, q):
        return models.SparseVector(indices=[1], values=[1.0])


class FakeReranker:
    def __init__(self, scores):
        self.scores = scores

    def score(self, query, texts):
        return self.scores[: len(texts)]


class FakeDb:
    def known_entities(self):
        return set()

    def index_version(self):
        return 0


class CountingDb:
    def __init__(self):
        self.version, self.loads = 1, 0

    def index_version(self):
        return self.version

    def known_entities(self):
        self.loads += 1
        return {"vwo-26"} if self.version > 1 else set()


class CapturingReranker:
    def __init__(self):
        self.texts = []

    def score(self, query, texts):
        self.texts = texts
        return [1.0] * len(texts)


def test_rerank_input_is_truncated(settings):
    s = settings.model_copy(deep=True)
    s.reranker.max_chars = 50
    reranker = CapturingReranker()
    store = FakeStore(3)
    for_long = store.hybrid
    store.hybrid = lambda *a, **k: [
        type(c)(c.id, c.score, {**c.payload, "text": "x" * 500}) for c in for_long(*a, **k)]
    Searcher(s, store, FakeDense(), FakeSparse(), reranker, FakeDb(), {}).search("q")
    assert reranker.texts and all(len(t) <= 50 for t in reranker.texts)


def test_searcher_reloads_entities_after_reindex(settings):
    db = CountingDb()
    searcher = Searcher(settings, FakeStore(2), FakeDense(), FakeSparse(), FakeReranker([1.0, 2.0]), db, {})
    searcher.search("q")
    searcher.search("q")
    assert db.loads == 1
    db.version = 2
    searcher.search("q")
    assert db.loads == 2


def _searcher(settings, scores):
    return Searcher(settings, FakeStore(len(scores)), FakeDense(), FakeSparse(), FakeReranker(scores), FakeDb(), {})


def test_search_ranks_by_rerank_score_and_numbers_hits(settings):
    result = _searcher(settings, [0.1, 3.0, 2.0, -1.0]).search("q")
    assert [h.chunk_id for h in result.hits][:3] == ["1", "2", "0"]
    assert [h.n for h in result.hits] == list(range(1, len(result.hits) + 1))
    assert result.no_answer is False


def test_no_answer_below_threshold(settings):
    result = _searcher(settings, [-5.0, -6.0]).search("q")
    assert result.no_answer is True
    assert result.hits  # still returned as closest matches


@pytest.mark.integration
def test_search_build_142(settings):
    from .integration import require_services

    require_services(settings)
    from qabuddy.retrieval.search import build_searcher

    searcher = build_searcher(settings)
    if searcher.store.count() == 0:
        pytest.skip("index is empty; run `qabuddy ingest` first")
    result = searcher.search("Why did testLoginPositiveVWO fail in build 142?")
    assert any(h.source == "jenkins" for h in result.hits)
