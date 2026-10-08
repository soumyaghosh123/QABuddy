"""Dense embeddings via Ollama; BM25 sparse vectors via FastEmbed (documents) and Bm25QueryEncoder (queries)."""

from __future__ import annotations

from typing import Callable

import httpx
from qdrant_client import models

from qabuddy.index.bm25 import Bm25QueryEncoder

QUERY_INSTRUCTION = ("Instruct: Given a QA engineer's question, retrieve the code, test cases, tickets, "
                     "logs or documents that answer it\nQuery: ")


class OllamaUnavailable(RuntimeError):
    pass


class DenseEmbedder:
    def __init__(self, url: str, model: str, batch_size: int = 16, timeout: float = 300, num_ctx: int = 2048):
        self.url, self.model, self.batch_size, self.num_ctx = url.rstrip("/"), model, batch_size, num_ctx
        self._client = httpx.Client(base_url=self.url, timeout=timeout)
        self._dimension: int | None = None

    def _embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.post("/api/embed", json={
                "model": self.model, "input": texts, "truncate": True, "options": {"num_ctx": self.num_ctx}})
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"Ollama is not reachable at {self.url} ({exc}). Start Ollama and retry.") from exc
        if response.status_code == 404:
            raise OllamaUnavailable(f"Embedding model {self.model!r} is not available. Run: ollama pull {self.model}")
        if response.status_code >= 400:
            raise OllamaUnavailable(f"Ollama embed failed ({response.status_code}): {response.text[:300]}")
        return response.json()["embeddings"]

    def embed_documents(self, texts: list[str], progress: Callable[[int, int], None] | None = None) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(self._embed(texts[start:start + self.batch_size]))
            if progress:
                progress(min(start + self.batch_size, len(texts)), len(texts))
        return vectors

    def embed_query(self, question: str) -> list[float]:
        return self._embed([QUERY_INSTRUCTION + question])[0]

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = len(self._embed(["dimension probe"])[0])
        return self._dimension


class SparseEmbedder:
    def __init__(self, model: str = "Qdrant/bm25"):
        from fastembed import SparseTextEmbedding

        self._model = SparseTextEmbedding(model_name=model)

    @staticmethod
    def _to_vector(embedding) -> models.SparseVector:
        return models.SparseVector(indices=embedding.indices.tolist(), values=embedding.values.tolist())

    def embed_documents(self, texts: list[str]) -> list[models.SparseVector]:
        return [self._to_vector(e) for e in self._model.embed(texts, batch_size=64)]

    def embed_query(self, question: str) -> models.SparseVector:
        return _QUERY_ENCODER.embed_query(question)


_QUERY_ENCODER = Bm25QueryEncoder()
