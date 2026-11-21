"""Desktop sync: connectors download into the data folder, then the file-hash pipeline indexes it."""
from __future__ import annotations

import json
import logging
import platform
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Protocol

from qabuddy.catalog import build_manifest, write_manifest
from qabuddy.components import active_embed_model
from qabuddy.config import Settings
from qabuddy.db import Database
from qabuddy.desktop.inputs import Inputs
from qabuddy.desktop.meta import SharedMeta
from qabuddy.desktop.sources import source_entries, write_sources_file
from qabuddy.ingest.pipeline import HISTORY_PATH, run_ingest

log = logging.getLogger(__name__)


@dataclass
class ConnectorResult:
    ok: bool
    error: str = ""
    jira_keys: set[str] = field(default_factory=set)
    issue_ids: set[int] = field(default_factory=set)
    # source ids whose LOCAL copy is complete and current; only these are diffed against the shared index
    ready_sources: set[str] = field(default_factory=set)
    # data-dir-relative paths (a trailing "/" means a whole folder) of items whose fetch FAILED this run: their
    # local file is missing or stale, so the pipeline must not take their absence as an upstream deletion
    failed_paths: set[str] = field(default_factory=set)


@dataclass
class SyncContext:
    settings: Settings
    inputs: Inputs
    data_dir: Path
    state: dict
    now: float
    progress: Callable[[str], None]
    ready: set[str] = field(default_factory=set)  # ready_sources of the connectors that already ran
    failed: set[str] = field(default_factory=set)  # failed_paths of the connectors that already ran


class Connector(Protocol):
    name: str

    def run(self, ctx: SyncContext) -> ConnectorResult: ...


@dataclass
class SyncReport:
    status: Literal["done", "skipped", "locked", "failed"]
    holder: str = ""
    sources: dict = field(default_factory=dict)
    pending: bool = False
    error: str = ""


LOCAL_SOURCES_FILE = "local_sources.json"


def read_local_sources(settings: Settings) -> dict:
    """This PC's record of which local copies were fully loaded (never shared: other PCs have their own)."""
    try:
        return json.loads((settings.storage_path / LOCAL_SOURCES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mark_local_source(settings: Settings, key: str, now: float) -> None:
    marks = read_local_sources(settings)
    marks[key] = {"complete": True, "at": now}
    path = settings.storage_path / LOCAL_SOURCES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marks, indent=2), encoding="utf-8")


def should_sync_on_startup(last: dict | None, now: float, skip_s: int) -> bool:
    return last is None or bool(last.get("pending")) or now - last.get("finished_at", 0) >= skip_s


def _remove_unwanted_folders(data_dir: Path, inputs: Inputs) -> None:
    """Delete downloaded folders no longer in the inputs, so the pipeline drops their chunks."""
    wanted = {e["folder"] for e in source_entries(inputs)}
    for parent in ("github", "zephyr", "confluence", "jira"):
        root = data_dir / parent
        if not root.is_dir():
            continue
        if parent == "jira":
            if "jira" not in wanted:
                shutil.rmtree(root, ignore_errors=True)
            continue
        for child in root.iterdir():
            if child.is_dir() and f"{parent}/{child.name}" not in wanted:
                shutil.rmtree(child, ignore_errors=True)


class SyncRunner:
    def __init__(self, settings: Settings, meta: SharedMeta, connectors: list[Connector], store=None,
                 ingest=run_ingest, machine: str = platform.node(), clock=time.time):
        self.settings, self.meta, self.connectors, self.store = settings, meta, connectors, store
        self.ingest, self.machine, self.clock = ingest, machine, clock

    def run(self, trigger: Literal["startup", "manual"], progress: Callable[[str], None]) -> SyncReport:
        s = self.settings
        try:
            if trigger == "startup" and not should_sync_on_startup(
                    self.meta.read_last_sync(), self.clock(), s.desktop.startup_skip_s):
                return SyncReport(status="skipped")
            holder = self.meta.acquire_lock(self.machine, s.desktop.lock_ttl_s)
        except Exception as e:
            log.warning("Sync could not start: %s", e)
            return SyncReport(status="failed", error=str(e) or type(e).__name__)
        if holder is not None:
            return SyncReport(status="locked", holder=holder)
        try:
            return self._sync(progress)
        except Exception as e:
            log.exception("Sync failed")
            return SyncReport(status="failed", error=str(e) or type(e).__name__)
        finally:
            try:
                self.meta.release_lock(self.machine)
            except Exception as e:
                log.warning("Could not release sync lock: %s", e)

    def _sync(self, progress: Callable[[str], None]) -> SyncReport:
        s = self.settings
        inputs = self.meta.read_inputs()
        db = Database(s.db_path)
        db.init()
        self.meta.pull_state(db)
        state = json.loads(db.get_meta("connector_state") or "{}")
        expected = {e["id"] for e in source_entries(inputs)}
        removed = {f["source"] for f in db.all_files() if f["path"] != HISTORY_PATH} - expected
        write_sources_file(inputs, s.sources_path)
        _remove_unwanted_folders(s.data_path, inputs)

        ctx = SyncContext(s, inputs, s.data_path, state, self.clock(), progress)
        results: dict[str, dict] = {}
        keys: set[str] = set()
        ids: set[int] = set()
        for connector in sorted(self.connectors, key=lambda c: c.name == "jira"):  # Jira last; stable otherwise
            if connector.name == "jira":
                state["jira_wanted"] = {"keys": sorted(keys), "issue_ids": sorted(ids)}
            try:
                result = connector.run(ctx)
            except Exception as e:
                log.warning("Connector %s failed: %s", connector.name, e)
                results[connector.name] = {"ok": False, "error": str(e) or type(e).__name__}
                continue
            keys |= result.jira_keys
            ids |= result.issue_ids
            ctx.ready |= result.ready_sources
            ctx.failed |= result.failed_paths
            results[connector.name] = {"ok": result.ok, "error": result.error}

        # Diff only sources whose local copy is complete (plus removed inputs, whose chunks must go): a PC with
        # an empty or partial data folder must never delete what another PC indexed. Items whose fetch failed
        # keep their shared chunks too: only a genuine upstream deletion (or a removed input) deletes.
        scope = (ctx.ready & expected) | removed
        report = (self.ingest(s, sources=sorted(scope), progress=progress, keep_paths=set(ctx.failed))
                  if scope else None)
        db.set_meta("connector_state", json.dumps(state))
        self.meta.push_state(db)
        if self.store is not None and report is not None:
            labels = {e["id"]: e["label"] for e in source_entries(inputs)}
            manifest = build_manifest(db, report.chunks_total, db.index_version(), active_embed_model(s),
                                      self.clock(), labels)
            write_manifest(self.store, s.qdrant.meta_collection, manifest)
        # a failed connector counts as pending so the next startup retries instead of skipping for an hour
        # a source not ready this run is pending too: its local copy is still to be completed
        pending = (bool(report and report.pending) or any(not r["ok"] for r in results.values())
                   or bool(expected - ctx.ready) or bool(ctx.failed))
        self.meta.write_last_sync({"finished_at": self.clock(), "pending": pending, "sources": results,
                                   "machine": self.machine})
        return SyncReport(status="done", sources=results, pending=pending)
