"""`qabuddy publish`: mirror the local index into Qdrant Cloud and write the manifest (spec §6.5).

Payloads and BM25 vectors are copied as-is; dense vectors are re-embedded by the cloud embedder, so the
cloud's query and document vectors always come from the same provider. The manifest is written last:
a publish that fails half-way leaves the cloud's index version (and every answer cache) unchanged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from qdrant_client import models

from qabuddy.catalog import build_manifest, read_manifest, write_manifest
from qabuddy.db import Database

GROUP = 64
EMPTY_LOCAL = ("Local index is empty — run qabuddy ingest first. "
               "Refusing to publish: it would delete the cloud index.")


class PublishError(RuntimeError):
    pass


@dataclass
class PublishReport:
    new: int
    changed: int
    deleted: int
    unchanged: int
    texts_embedded: int
    index_version: int
    seconds: float
    dry_run: bool


def diff_points(local: dict[str, dict], cloud: dict[str, dict]) -> tuple[list[str], list[str], list[str], int]:
    """(new, changed, deleted) point IDs, sorted, plus the unchanged count. Changed = payload differs."""
    shared = local.keys() & cloud.keys()
    changed = sorted(pid for pid in shared if local[pid] != cloud[pid])
    return sorted(local.keys() - cloud.keys()), changed, sorted(cloud.keys() - local.keys()), len(shared) - len(changed)


def _dense_text(payload: dict) -> str:
    return f"{payload.get('header', '')}\n{payload.get('text', '')}"  # = ingest.pipeline.dense_text


def run_publish(local_store, cloud_store, embedder, db: Database, meta_collection: str, embed_model: str,
                dry_run: bool = False, progress: Callable[[str], None] | None = None,
                clock: Callable[[], float] = time.time) -> PublishReport:
    started = time.perf_counter()
    local_url = getattr(local_store, "url", None)
    if local_url and local_url == getattr(cloud_store, "url", None):
        raise PublishError("The cloud Qdrant URL is the local one — set QDRANT_URL to your Qdrant Cloud endpoint.")
    if not local_store.exists():
        raise PublishError(EMPTY_LOCAL)
    local = {pid: (payload, vectors) for pid, payload, vectors in local_store.scroll_all(with_vectors=True)}
    if not local:
        raise PublishError(EMPTY_LOCAL)
    cloud = {pid: payload for pid, payload, _ in cloud_store.scroll_all()} if cloud_store.exists() else {}
    new, changed, deleted, unchanged = diff_points({pid: p for pid, (p, _) in local.items()}, cloud)
    previous = read_manifest(cloud_store, meta_collection)
    previous_version = int(previous["index_version"]) if previous else 0

    def report(version: int, embedded: int) -> PublishReport:
        return PublishReport(len(new), len(changed), len(deleted), unchanged, embedded, version,
                             round(time.perf_counter() - started, 1), dry_run)

    if dry_run:
        return report(previous_version, 0)

    cloud_store.ensure_collection(embedder.dimension)
    upload = new + changed
    for start in range(0, len(upload), GROUP):
        group = upload[start:start + GROUP]
        dense = embedder.embed_documents([_dense_text(local[pid][0]) for pid in group])
        cloud_store.upsert_raw([
            models.PointStruct(id=pid, vector={"dense": d, "bm25": local[pid][1]["bm25"]}, payload=local[pid][0])
            for pid, d in zip(group, dense)
        ])
        if progress:
            progress(f"{min(start + GROUP, len(upload))}/{len(upload)} chunks embedded and uploaded")
    if deleted:
        cloud_store.delete(deleted)

    changed_any = bool(upload or deleted) or previous is None
    version = previous_version + 1 if changed_any else previous_version
    write_manifest(cloud_store, meta_collection, build_manifest(db, len(local), version, embed_model, clock()))
    return report(version, len(upload))
