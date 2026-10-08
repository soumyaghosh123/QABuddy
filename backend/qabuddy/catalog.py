"""Index catalog: version, known entity IDs and per-source stats (spec §6.4).

Locally they come from SQLite (storage/qabuddy.db). In the cloud they come from a manifest that
`qabuddy publish` writes into Qdrant, so a new ingest goes live without a redeploy.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import TYPE_CHECKING, Callable

from qabuddy.db import Database

if TYPE_CHECKING:
    from qabuddy.config import Settings
    from qabuddy.index.store import VectorStore

MANIFEST_ID = "00000000-0000-0000-0000-000000000001"


def file_stats(db: Database) -> dict:
    files = db.all_files()
    by_source: Counter = Counter()
    for f in files:
        by_source[f["source"]] += len(f["chunk_ids"])
    return {"by_source": dict(by_source), "files": sum(1 for f in files if not f["path"].startswith("__")),
            "failed_files": [{"path": f["path"], "error": f["error"]} for f in files if f["status"] == "failed"]}


class SqliteCatalog:
    hint = "qabuddy ingest"

    def __init__(self, db: Database):
        self.db = db

    def index_version(self) -> int:
        return self.db.index_version()

    def known_entities(self) -> set[str]:
        return self.db.known_entities()

    def stats(self) -> dict:
        return file_stats(self.db)

    def last_ingest_at(self) -> float | None:
        last = self.db.get_meta("last_ingest_at")
        return float(last) if last else None


def build_manifest(db: Database, chunks: int, index_version: int, embed_model: str, published_at: float) -> dict:
    last = db.get_meta("last_ingest_at")
    return {"index_version": index_version, "published_at": published_at,
            "last_ingest_at": float(last) if last else None, "embed_model": embed_model, "chunks": chunks,
            **file_stats(db), "entities": sorted(db.known_entities())}


def read_manifest(store: VectorStore, meta_collection: str) -> dict | None:
    return store.read_meta(meta_collection, MANIFEST_ID)


def write_manifest(store: VectorStore, meta_collection: str, manifest: dict) -> None:
    store.ensure_meta_collection(meta_collection)
    store.write_meta(meta_collection, MANIFEST_ID, manifest)


class ManifestCatalog:
    hint = "qabuddy publish"

    def __init__(self, store: VectorStore, meta_collection: str, ttl_s: float = 300.0,
                 clock: Callable[[], float] = time.monotonic):
        self.store, self.meta_collection, self.ttl_s, self.clock = store, meta_collection, ttl_s, clock
        self._manifest: dict | None = None
        self._loaded_at = 0.0

    def manifest(self) -> dict | None:
        """The published manifest, re-read at most every ttl_s; a missing one is re-checked every call."""
        if self._manifest is None or self.clock() - self._loaded_at > self.ttl_s:
            self._manifest = read_manifest(self.store, self.meta_collection)
            self._loaded_at = self.clock()
        return self._manifest

    def index_version(self) -> int:
        return int((self.manifest() or {}).get("index_version", 0))

    def known_entities(self) -> set[str]:
        return set((self.manifest() or {}).get("entities", []))

    def stats(self) -> dict:
        m = self.manifest() or {}
        return {"by_source": m.get("by_source", {}), "files": m.get("files", 0),
                "failed_files": m.get("failed_files", [])}

    def last_ingest_at(self) -> float | None:
        return (self.manifest() or {}).get("last_ingest_at")


def make_catalog(settings: Settings, db: Database | None = None,
                 store: VectorStore | None = None) -> SqliteCatalog | ManifestCatalog:
    provider = settings.catalog.provider
    if provider == "sqlite":
        if db is None:
            db = Database(settings.db_path)
            db.init()
        return SqliteCatalog(db)
    if provider == "manifest":
        if store is None:
            from qabuddy.index.store import make_store

            store = make_store(settings)
        return ManifestCatalog(store, settings.qdrant.meta_collection)
    raise ValueError(f"Unknown catalog.provider {provider!r}")
