"""Qdrant collection: named dense vector + BM25 sparse vector, hybrid query with RRF fusion."""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import QdrantClient, models

from qabuddy.ingest.models import Chunk

PAYLOAD_INDEXES = ("source", "kind", "ids_norm", "path", "module", "parent_id")


class IndexUnavailable(RuntimeError):
    pass


@dataclass
class ScoredChunk:
    id: str
    score: float
    payload: dict


class VectorStore:
    def __init__(self, url: str, collection: str):
        self.url, self.collection = url, collection
        self.client = QdrantClient(url=url, timeout=60, check_compatibility=False)

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if "Connect" in type(exc).__name__ or "connect" in str(exc).lower():
                raise IndexUnavailable(f"Qdrant is not reachable at {self.url}. Start it with scripts/start.ps1.") from exc
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
        for start in range(0, len(points), batch_size):
            self._call(self.client.upsert, self.collection, points=points[start:start + batch_size], wait=True)

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
        result = self._call(self.client.query_points, self.collection, prefetch=prefetch,
                            query=models.FusionQuery(fusion=models.Fusion.RRF), limit=limit, with_payload=True)
        return [ScoredChunk(str(p.id), p.score, p.payload or {}) for p in result.points]
