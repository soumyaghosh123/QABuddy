"""Hybrid search → cross-encoder rerank → diversity cap → top-k (or 'no answer')."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from qabuddy.config import Settings
from qabuddy.ingest.normalize import load_glossary

SNIPPET_CHARS = 300


@dataclass
class Hit:
    n: int
    chunk_id: str
    citation: str
    source: str
    kind: str
    path: str
    location: dict
    header: str
    text: str
    snippet: str
    score: float
    parent_id: str = ""

    def public(self) -> dict:
        data = asdict(self)
        data.pop("text")
        data.pop("parent_id")
        return data


@dataclass
class SearchResult:
    hits: list[Hit]
    no_answer: bool
    timings: dict = field(default_factory=dict)


def apply_diversity(hits: list[Hit], max_per_parent: int) -> list[Hit]:
    counts: dict[str, int] = {}
    kept = []
    for hit in hits:
        key = hit.parent_id or hit.chunk_id
        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= max_per_parent:
            kept.append(hit)
    return kept


class Searcher:
    def __init__(self, settings: Settings, store, dense, sparse, reranker, catalog, glossary: dict):
        self.settings, self.store, self.dense, self.sparse = settings, store, dense, sparse
        self.reranker, self.catalog, self.glossary = reranker, catalog, glossary
        self._known: set[str] | None = None
        self._known_version: int | None = None

    def known_entities(self) -> set[str]:
        """Entity vocabulary from the last ingest; reloaded whenever the index version changes."""
        version = self.catalog.index_version()
        if self._known is None or version != self._known_version:
            self._known, self._known_version = self.catalog.known_entities(), version
        return self._known

    def search(self, question: str, history: list[dict] | None = None, sources: list[str] | None = None) -> SearchResult:
        from qabuddy.retrieval.query import prepare_query

        cfg = self.settings.retrieval
        started = time.perf_counter()
        prepared = prepare_query(question, history or [], self.known_entities(), self.glossary)
        dense_q = self.dense.embed_query(prepared.retrieval_text)
        sparse_q = self.sparse.embed_query(prepared.bm25_text)
        candidates = self.store.hybrid(dense_q, sparse_q, prepared.ids, sources or None, cfg.dense_k, cfg.sparse_k,
                                       cfg.id_k, cfg.rerank_candidates)
        retrieved = time.perf_counter()
        limit = self.settings.reranker.max_chars
        texts = [f"{c.payload.get('header', '')}\n{c.payload.get('text', '')}"[:limit] for c in candidates]
        scores = self.reranker.score(prepared.retrieval_text, texts) if candidates else []
        ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
        hits = [
            Hit(n=0, chunk_id=c.payload.get("chunk_id", c.id), citation=c.payload.get("citation", ""),
                source=c.payload.get("source", ""), kind=c.payload.get("kind", ""), path=c.payload.get("path", ""),
                location=c.payload.get("location", {}), header=c.payload.get("header", ""),
                text=c.payload.get("text", ""), snippet=c.payload.get("text", "")[:SNIPPET_CHARS],
                score=round(float(score), 4), parent_id=c.payload.get("parent_id", ""))
            for c, score in ranked
        ]
        hits = apply_diversity(hits, cfg.max_per_parent)[: cfg.top_k]
        for index, hit in enumerate(hits, 1):
            hit.n = index
        no_answer = not hits or hits[0].score < cfg.min_rerank_score
        finished = time.perf_counter()
        return SearchResult(hits, no_answer, {"retrieve": round((retrieved - started) * 1000),
                                              "rerank": round((finished - retrieved) * 1000)})


def build_searcher(settings: Settings) -> Searcher:
    from qabuddy.catalog import make_catalog
    from qabuddy.components import make_embedder, make_reranker
    from qabuddy.index.bm25 import Bm25QueryEncoder
    from qabuddy.index.store import make_store

    return Searcher(
        settings,
        make_store(settings),
        make_embedder(settings),
        Bm25QueryEncoder(),
        make_reranker(settings),
        make_catalog(settings),
        load_glossary(settings.config_path / "glossary.yaml"),
    )
