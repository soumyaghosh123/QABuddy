"""Shared state in the Qdrant meta collection: inputs, sync state, lock, last sync."""
from __future__ import annotations

import base64
import gzip
import json
import time

from qabuddy.db import Database
from qabuddy.desktop.inputs import Inputs

INPUTS_ID = "00000000-0000-0000-0000-0000000000a1"
STATE_ID = "00000000-0000-0000-0000-0000000000a2"
LOCK_ID = "00000000-0000-0000-0000-0000000000a3"
LAST_SYNC_ID = "00000000-0000-0000-0000-0000000000a4"


class SharedMeta:
    def __init__(self, store, meta_collection: str, clock=time.time):
        self.store = store
        self.collection = meta_collection
        self.clock = clock

    def _write(self, point_id: str, payload: dict) -> None:
        self.store.ensure_meta_collection(self.collection)
        self.store.write_meta(self.collection, point_id, payload)

    def _read(self, point_id: str) -> dict | None:
        return self.store.read_meta(self.collection, point_id)

    # ---- inputs
    def read_inputs(self) -> Inputs:
        payload = self._read(INPUTS_ID)
        return Inputs(**payload) if payload else Inputs()

    def write_inputs(self, inputs: Inputs, machine: str) -> Inputs:
        stamped = inputs.model_copy(update={"updated_by": machine, "updated_at": self.clock()})
        self._write(INPUTS_ID, stamped.model_dump())
        return stamped

    # ---- sync state
    def _state(self) -> dict | None:
        payload = self._read(STATE_ID)
        if not payload:
            return None
        return json.loads(gzip.decompress(base64.b64decode(payload["blob"])))

    def pull_state(self, db: Database) -> None:
        state = self._state()
        if state is not None:
            db.import_state(state)

    def read_connector_state(self) -> dict:
        """The shared connector_state, decoded in memory (no local database is touched)."""
        rows = (self._state() or {}).get("meta", [])
        value = next((v for k, v in rows if k == "connector_state"), None)
        return json.loads(value) if value else {}

    def push_state(self, db: Database) -> None:
        raw = json.dumps(db.export_state()).encode()
        self._write(STATE_ID, {"blob": base64.b64encode(gzip.compress(raw)).decode()})

    # ---- lock (read-then-write, not atomic; acceptable for two users)
    def acquire_lock(self, machine: str, ttl_s: int) -> str | None:
        now = self.clock()
        lock = self._read(LOCK_ID) or {}
        holder = lock.get("machine") or ""
        if holder and holder != machine and lock.get("expires_at", 0) > now:
            return holder
        self._write(LOCK_ID, {"machine": machine, "expires_at": now + ttl_s})
        return None

    def release_lock(self, machine: str) -> None:
        lock = self._read(LOCK_ID) or {}
        if lock.get("machine") == machine:
            self._write(LOCK_ID, {"machine": "", "expires_at": 0})

    # ---- last sync
    def read_last_sync(self) -> dict | None:
        return self._read(LAST_SYNC_ID) or None

    def write_last_sync(self, info: dict) -> None:
        self._write(LAST_SYNC_ID, info)
