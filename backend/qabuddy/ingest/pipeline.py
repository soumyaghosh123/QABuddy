"""Ingestion: discover → parse → tag entities → embed → upsert, incrementally by file hash."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from qabuddy.config import Settings
from qabuddy.db import Database
from qabuddy.index.embedder import DenseEmbedder, SparseEmbedder
from qabuddy.index.store import VectorStore
from qabuddy.ingest.context import ParseContext, tag
from qabuddy.ingest.discover import discover, source_labels
from qabuddy.ingest.models import Chunk, SourceFile, make_chunk_id
from qabuddy.ingest.normalize import load_glossary, split_identifiers
from qabuddy.ingest.parsers import get_parser
from qabuddy.ingest.parsers.code import code_entities
from qabuddy.ingest.parsers.jenkins import build_test_history, merge_runs
from qabuddy.ingest.parsers.testcases import entities_from_testcases

log = logging.getLogger(__name__)
HISTORY_PATH = "__jenkins_test_history__"


@dataclass
class IngestReport:
    files_total: int = 0
    files_changed: int = 0
    files_removed: int = 0
    files_failed: list[tuple[str, str]] = field(default_factory=list)
    chunks_upserted: int = 0
    chunks_deleted: int = 0
    chunks_total: int = 0
    seconds: float = 0.0


def dense_text(chunk: Chunk) -> str:
    return f"{chunk.header}\n{chunk.text}"


def sparse_text(chunk: Chunk) -> str:
    body = f"{chunk.header}\n{chunk.text}"
    return f"{body}\n{split_identifiers(body)}"


def _entities_for(sf: SourceFile, chunks: list[Chunk]) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    if sf.parser == "code":
        rows += [(name, name, "symbol", sf.rel_path) for name in sorted(code_entities(chunks))]
    elif sf.parser == "testcases":
        rows += [(i, i.upper(), "testcase_id", sf.rel_path) for i in sorted(entities_from_testcases(chunks))]
    elif sf.parser == "jira":
        rows += [(c.citation.lower(), c.citation, "jira_key", sf.rel_path) for c in chunks]
    elif sf.parser == "jenkins":
        builds = {i for c in chunks for i in c.ids_norm if i.startswith("build-")}
        rows += [(b, b, "build", sf.rel_path) for b in sorted(builds)]
    return list(dict.fromkeys(rows))


def needs_history(full: bool, jenkins_changed: bool, jenkins_removed: bool, jenkins_indexed: bool,
                  history_recorded: bool) -> bool:
    """Rebuild the per-test history when Jenkins data changed, or a previous run stopped before storing it."""
    return full or jenkins_changed or jenkins_removed or (jenkins_indexed and not history_recorded)


def _unique_ids(chunks: list[Chunk]) -> None:
    seen: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        seen[chunk.chunk_id] += 1
        if seen[chunk.chunk_id] > 1:
            chunk.chunk_id = make_chunk_id(chunk.path, f"{chunk.chunk_id}#dup{seen[chunk.chunk_id]}")


def run_ingest(settings: Settings, full: bool = False, sources: list[str] | None = None,
               progress: Callable[[str], None] = lambda message: log.info(message)) -> IngestReport:
    started = time.time()
    report = IngestReport()
    db = Database(settings.db_path)
    db.init()
    store = VectorStore(settings.qdrant.url, settings.qdrant.collection)
    dense = DenseEmbedder(settings.ollama.url, settings.ollama.embed_model)
    dimension = dense.dimension  # fails fast (before any writes) if Ollama or the model is missing
    if full:
        progress("Full rebuild: dropping collection and index state")
        store.drop()
        db.clear_index_state()
    store.ensure_collection(dimension)

    files = discover(settings, only=sources)
    report.files_total = len(files)
    previous = {f["path"]: f for f in db.all_files() if f["path"] != HISTORY_PATH}
    in_scope = {p: f for p, f in previous.items() if not sources or f["source"] in sources}
    current_paths = {sf.rel_path for sf in files}
    changed = [sf for sf in files if full or sf.rel_path not in previous
               or previous[sf.rel_path]["file_hash"] != sf.file_hash or previous[sf.rel_path]["status"] != "ok"]
    removed = [p for p in in_scope if p not in current_paths]
    report.files_changed, report.files_removed = len(changed), len(removed)
    progress(f"{len(files)} files found: {len(changed)} new/changed, {len(removed)} removed")

    for path in removed:
        store.delete(previous[path]["chunk_ids"])
        report.chunks_deleted += len(previous[path]["chunk_ids"])
        db.delete_file(path)

    labels = source_labels(settings)
    glossary = load_glossary(settings.config_path / "glossary.yaml")
    known = db.known_entities()
    parsed: dict[str, list[Chunk]] = {}
    parsed_files: dict[str, SourceFile] = {}
    jenkins_runs: dict[str, list] = {}
    for sf in changed:
        ctx = ParseContext(settings, labels.get(sf.source, sf.source), known, glossary)
        try:
            chunks = get_parser(sf.parser)(sf, ctx)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
            log.exception("failed to parse %s", sf.rel_path)
            report.files_failed.append((sf.rel_path, f"{type(exc).__name__}: {exc}"))
            old = previous.get(sf.rel_path, {}).get("chunk_ids", [])
            db.upsert_file(sf.rel_path, sf.source, sf.file_hash, sf.abs_path.stat().st_size,
                           sf.abs_path.stat().st_mtime, old, "failed", str(exc)[:500])
            continue
        _unique_ids(chunks)
        parsed[sf.rel_path], parsed_files[sf.rel_path] = chunks, sf
        if sf.parser == "jenkins":
            jenkins_runs[sf.rel_path] = list(ctx.test_runs)
        db.replace_entities(_entities_for(sf, chunks), source_path=sf.rel_path)
        progress(f"parsed {sf.rel_path}: {len(chunks)} chunks")

    # Second pass: tag every new chunk against the full entity vocabulary.
    known = db.known_entities()
    retag_ctx = ParseContext(settings, "", known, glossary)
    for chunks in parsed.values():
        for chunk in chunks:
            tag(chunk, retag_ctx, chunk.ids_norm)

    history_chunks: list[Chunk] | None = None
    if needs_history(full=full, jenkins_changed=bool(jenkins_runs),
                     jenkins_removed=any(previous[p]["source"] == "jenkins" for p in removed),
                     jenkins_indexed=any(f["source"] == "jenkins" for f in db.all_files() if f["path"] != HISTORY_PATH),
                     history_recorded=db.get_file(HISTORY_PATH) is not None):
        for path, runs in jenkins_runs.items():
            db.replace_test_runs(runs, source_path=path)
        history_chunks = build_test_history(merge_runs(db.all_test_runs()), retag_ctx)

    # Embed and store file by file so an interrupted run keeps its progress (re-run resumes).
    total = sum(len(c) for c in parsed.values()) + len(history_chunks or [])
    if total:
        progress(f"embedding {total} chunks with {settings.ollama.embed_model}")
    sparse = SparseEmbedder() if total else None
    embedded = 0
    embed_started = time.time()

    def index_chunks(chunks: list[Chunk]) -> None:
        nonlocal embedded
        for start in range(0, len(chunks), 64):
            group = chunks[start:start + 64]
            store.upsert(group, dense.embed_documents([dense_text(c) for c in group]),
                         sparse.embed_documents([sparse_text(c) for c in group]))
            embedded += len(group)
            report.chunks_upserted += len(group)
            rate = embedded / max(time.time() - embed_started, 1e-6)
            progress(f"  embedded {embedded}/{total} chunks (~{(total - embedded) / rate / 60:.1f} min left)")

    for path, chunks in parsed.items():
        sf = parsed_files[path]
        index_chunks(chunks)
        new_ids = [c.chunk_id for c in chunks]
        stale = [i for i in previous.get(path, {}).get("chunk_ids", []) if i not in set(new_ids)]
        store.delete(stale)
        report.chunks_deleted += len(stale)
        stat = sf.abs_path.stat()
        db.upsert_file(path, sf.source, sf.file_hash, stat.st_size, stat.st_mtime, new_ids, "ok", None)
    if history_chunks is not None:
        index_chunks(history_chunks)
        old_history = (db.get_file(HISTORY_PATH) or {}).get("chunk_ids", [])
        new_ids = [c.chunk_id for c in history_chunks]
        store.delete([i for i in old_history if i not in set(new_ids)])
        db.upsert_file(HISTORY_PATH, "jenkins", "", 0, 0.0, new_ids, "ok", None)

    if report.chunks_upserted or report.chunks_deleted or removed:
        db.bump_index_version()
    db.set_meta("last_ingest_at", str(time.time()))
    db.set_meta("embed_dim", str(dimension))
    report.chunks_total = store.count()
    report.seconds = round(time.time() - started, 1)
    return report
