"""Qdrant collection: named dense vector + BM25 sparse vector, hybrid query with RRF fusion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from qabuddy.ingest.models import Chunk

if TYPE_CHECKING:
    from qabuddy.config import Settings

PAYLOAD_INDEXES = ("source", "kind", "ids_norm", "path", "module", "parent_id")


class IndexUnavailable(RuntimeError):
    pass


@dataclass
class ScoredChunk:
    id: str
    score: float
    payload: dict


class VectorStore:
    def __init__(self, url: str, collection: str, api_key: str | None = None, url_env: str = ""):
        self.url, self.collection, self.api_key, self.url_env = url, collection, api_key, url_env
        self._client: QdrantClient | None = None

    @property
    def client(self) -> QdrantClient:
        # Created on first use, so building the app never fails on an unset QDRANT_URL.
        if not self.url:  # an empty URL would make QdrantClient silently use localhost:6333
            raise IndexUnavailable(f"{self.url_env or 'The Qdrant URL'} is not set")
        if self._client is None:
            self._client = QdrantClient(url=self.url, api_key=self.api_key, timeout=60, check_compatibility=False)
        return self._client

    def _unreachable(self, exc: Exception) -> IndexUnavailable:
        local = "localhost" in self.url or "127.0.0.1" in self.url
        hint = "Start it with scripts/start.ps1." if local else "Check QDRANT_URL and the cluster status in Qdrant Cloud."
        return IndexUnavailable(f"Qdrant is not reachable at {self.url} ({type(exc).__name__}). {hint}")

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except UnexpectedResponse as exc:
            if exc.status_code in (401, 403):
                raise IndexUnavailable(f"Qdrant at {self.url} rejected the API key ({exc.status_code}) — "
                                       "check QDRANT_API_KEY.") from exc
            if exc.status_code is not None and exc.status_code >= 500:
                raise IndexUnavailable(f"Qdrant at {self.url} returned {exc.status_code}.") from exc
            raise
        except ResponseHandlingException as exc:  # DNS failures, timeouts, dropped connections
            raise self._unreachable(exc.source) from exc
        except Exception as exc:  # noqa: BLE001
            if "Connect" in type(exc).__name__ or "connect" in str(exc).lower():
                raise self._unreachable(exc) from exc
            raise

    def exists(self) -> bool:
        return self._call(self.client.collection_exists, self.collection)

    def ensure_collection(self, dim: int) -> None:
        if self.exists():
            return
        self._call(
            self.client.create_collection,
            collection_name=self.collection,
            vectors_config={"dense": models.VectorParams(size=dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        for field in PAYLOAD_INDEXES:
            self.client.create_payload_index(self.collection, field_name=field,
                                             field_schema=models.PayloadSchemaType.KEYWORD)

    def upsert(self, chunks: list[Chunk], dense: list[list[float]], sparse: list[models.SparseVector],
               batch_size: int = 64) -> None:
        points = [
            models.PointStruct(id=c.chunk_id, vector={"dense": d, "bm25": s}, payload=c.payload())
            for c, d, s in zip(chunks, dense, sparse)
        ]
        self.upsert_raw(points, batch_size)

    def upsert_raw(self, points: list[models.PointStruct], batch_size: int = 64) -> None:
        for start in range(0, len(points), batch_size):
            self._call(self.client.upsert, self.collection, points=points[start:start + batch_size], wait=True)

    def scroll_all(self, with_vectors: bool = False, batch: int = 256) -> Iterator[tuple[str, dict, dict]]:
        """Every point as (id, payload, named vectors); vectors is {} unless with_vectors."""
        offset = None
        while True:
            points, offset = self._call(self.client.scroll, self.collection, limit=batch, offset=offset,
                                        with_payload=True, with_vectors=with_vectors)
            for p in points:
                yield str(p.id), p.payload or {}, (p.vector or {}) if with_vectors else {}
            if offset is None:
                return

    def ensure_meta_collection(self, name: str) -> None:
        if not self._call(self.client.collection_exists, name):
            self._call(self.client.create_collection, collection_name=name, vectors_config={})

    def read_meta(self, name: str, point_id: str) -> dict | None:
        if not self._call(self.client.collection_exists, name):
            return None
        points = self._call(self.client.retrieve, name, ids=[point_id], with_payload=True)
        return (points[0].payload or {}) if points else None

    def write_meta(self, name: str, point_id: str, payload: dict) -> None:
        self._call(self.client.upsert, name, points=[models.PointStruct(id=point_id, vector={}, payload=payload)],
                   wait=True)

    def delete(self, chunk_ids: list[str]) -> None:
        if chunk_ids and self.exists():
            self._call(self.client.delete, self.collection,
                       points_selector=models.PointIdsList(points=list(chunk_ids)), wait=True)

    def drop(self) -> None:
        if self.exists():
            self._call(self.client.delete_collection, self.collection)

    def count(self) -> int:
        if not self.exists():
            return 0
        return self._call(self.client.count, self.collection, exact=True).count

    def get(self, chunk_id: str) -> dict | None:
        if not self.exists():
            return None
        try:
            points = self._call(self.client.retrieve, self.collection, ids=[chunk_id], with_payload=True)
        except IndexUnavailable:
            raise
        except Exception:  # noqa: BLE001 - invalid id format
            return None
        return points[0].payload if points else None

    def hybrid(self, dense_q: list[float], sparse_q: models.SparseVector, ids: list[str], sources: list[str] | None,
               dense_k: int, sparse_k: int, id_k: int, limit: int) -> list[ScoredChunk]:
        must = [models.FieldCondition(key="source", match=models.MatchAny(any=sources))] if sources else []
        base = models.Filter(must=must) if must else None
        prefetch = [models.Prefetch(query=dense_q, using="dense", limit=dense_k, filter=base)]
        if sparse_q.indices:
            prefetch.append(models.Prefetch(query=sparse_q, using="bm25", limit=sparse_k, filter=base))
        if ids:
            id_filter = models.Filter(must=[*must, models.FieldCondition(key="ids_norm", match=models.MatchAny(any=ids))])
            prefetch.append(models.Prefetch(query=dense_q, using="dense", limit=id_k, filter=id_filter))
        try:
            result = self._call(self.client.query_points, self.collection, prefetch=prefetch,
                                query=models.FusionQuery(fusion=models.Fusion.RRF), limit=limit, with_payload=True)
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                raise IndexUnavailable(f"Collection {self.collection} not found at {self.url} — run qabuddy ingest "
                                       "(local) or qabuddy publish (cloud).") from exc
            raise
        return [ScoredChunk(str(p.id), p.score, p.payload or {}) for p in result.points]


def make_store(settings: Settings) -> VectorStore:
    return VectorStore(settings.qdrant.url, settings.qdrant.collection, api_key=settings.qdrant.api_key(),
                       url_env=settings.qdrant.url_env)
