"""SQLite storage: file manifest, test runs, known entities, answer cache, metadata."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import astuple, fields
from pathlib import Path

from qabuddy.ingest.normalize import extract_ids
from qabuddy.ingest.parsers.jenkins import TestRun

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, source TEXT, file_hash TEXT, size INTEGER, mtime REAL,
    chunk_ids TEXT, status TEXT, error TEXT, indexed_at REAL
);
CREATE TABLE IF NOT EXISTS test_runs (
    job TEXT, build INTEGER, test_id TEXT, class_name TEXT, method TEXT, title TEXT, file TEXT,
    status TEXT, attempt INTEGER, duration_s REAL, error_type TEXT, error_message TEXT, source_path TEXT,
    UNIQUE(job, build, test_id, attempt, source_path)
);
CREATE TABLE IF NOT EXISTS entities (id_norm TEXT, display TEXT, kind TEXT, source_path TEXT);
CREATE INDEX IF NOT EXISTS entities_path ON entities(source_path);
CREATE TABLE IF NOT EXISTS answer_cache (
    key TEXT PRIMARY KEY, index_version INTEGER, question TEXT, filters TEXT, answer TEXT,
    sources_json TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
RUN_COLUMNS = [f.name for f in fields(TestRun)]


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA)

    def clear_index_state(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executescript("DELETE FROM files; DELETE FROM test_runs; DELETE FROM entities; DELETE FROM answer_cache;")

    # ---- files
    def upsert_file(self, path: str, source: str, file_hash: str, size: int, mtime: float,
                    chunk_ids: list[str], status: str, error: str | None) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?)",
                (path, source, file_hash, size, mtime, json.dumps(chunk_ids), status, error, time.time()),
            )

    @staticmethod
    def _file_row(row: sqlite3.Row) -> dict:
        record = dict(row)
        record["chunk_ids"] = json.loads(record["chunk_ids"] or "[]")
        return record

    def get_file(self, path: str) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
        return self._file_row(row) if row else None

    def all_files(self) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM files ORDER BY path").fetchall()
        return [self._file_row(r) for r in rows]

    def delete_file(self, path: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM files WHERE path = ?", (path,))
            conn.execute("DELETE FROM entities WHERE source_path = ?", (path,))
            conn.execute("DELETE FROM test_runs WHERE source_path = ?", (path,))

    # ---- test runs
    def replace_test_runs(self, runs: list[TestRun], source_path: str | None = None) -> None:
        """Replace all runs, or only the runs recorded from `source_path`."""
        with closing(self._connect()) as conn, conn:
            if source_path is None:
                conn.execute("DELETE FROM test_runs")
            else:
                conn.execute("DELETE FROM test_runs WHERE source_path = ?", (source_path,))
            conn.executemany(
                f"INSERT OR REPLACE INTO test_runs ({','.join(RUN_COLUMNS)}) VALUES ({','.join('?' * len(RUN_COLUMNS))})",
                [astuple(r) for r in runs],
            )

    def all_test_runs(self) -> list[TestRun]:
        with closing(self._connect()) as conn:
            rows = conn.execute(f"SELECT {','.join(RUN_COLUMNS)} FROM test_runs").fetchall()
        return [TestRun(**dict(r)) for r in rows]

    # ---- entities
    def replace_entities(self, rows: list[tuple[str, str, str, str]], source_path: str | None = None) -> None:
        with closing(self._connect()) as conn, conn:
            if source_path is None:
                conn.execute("DELETE FROM entities")
            else:
                conn.execute("DELETE FROM entities WHERE source_path = ?", (source_path,))
            conn.executemany("INSERT INTO entities VALUES (?,?,?,?)", rows)

    def known_entities(self) -> set[str]:
        with closing(self._connect()) as conn:
            return {r[0] for r in conn.execute("SELECT DISTINCT id_norm FROM entities")}

    def entities_in(self, text: str) -> list[str]:
        known = self.known_entities()
        return [i for i in extract_ids(text, known) if i in known]

    # ---- meta
    def get_meta(self, key: str) -> str | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    def bump_index_version(self) -> int:
        version = int(self.get_meta("index_version") or 0) + 1
        self.set_meta("index_version", str(version))
        return version

    def index_version(self) -> int:
        return int(self.get_meta("index_version") or 0)

    # ---- answer cache
    def cache_get(self, key: str, index_version: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM answer_cache WHERE key = ? AND index_version = ?",
                               (key, index_version)).fetchone()
        if not row:
            return None
        record = dict(row)
        record["sources"] = json.loads(record.pop("sources_json"))
        return record

    def cache_put(self, key: str, index_version: int, question: str, filters: str, answer: str,
                  sources: list[dict]) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO answer_cache VALUES (?,?,?,?,?,?,?)",
                         (key, index_version, question, filters, answer, json.dumps(sources), time.time()))
