"""/api/desktop/* routes: shared inputs, sync (start + progress stream), Measure, traceability matrix and export."""
from __future__ import annotations

import dataclasses
import json
import threading
import time
from datetime import date
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from qabuddy.catalog import read_manifest
from qabuddy.config import Settings
from qabuddy.db import Database
from qabuddy.desktop.inputs import Inputs, InputError, parse_space_url
from qabuddy.desktop.sync import SyncReport
from qabuddy.ingest.connectors.confluence import atlassian_headers, measure_space
from qabuddy.ingest.connectors.http import ApiClient, ServiceError
from qabuddy.traceability import (TraceNotFound, build_matrix, load_trace_data, make_suggester, to_xlsx,
                                  xlsx_filename)

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SyncStatus:
    """Progress of the sync running (or last run) in this process; read by the SSE stream."""

    def __init__(self):
        self.lock = threading.Lock()
        self.done = threading.Event()  # set when the run ends
        self.lines: list[str] = []
        self.sources: dict = {}
        self.running = False
        self.finished = False
        self.status = ""
        self.pending = False
        self.error = ""
        self.holder = ""

    def try_begin(self) -> bool:
        """Atomically claim the single sync slot; False when a sync is already running here."""
        with self.lock:
            if self.running:
                return False
            self.lines, self.sources = [], {}
            self.running, self.finished = True, False
            self.status, self.pending, self.error, self.holder = "", False, "", ""
            self.done.clear()
            return True

    def add_line(self, line: str) -> None:
        with self.lock:
            self.lines.append(line)

    def finish(self, report: SyncReport) -> None:
        with self.lock:
            self.sources = dict(report.sources)
            self.status, self.pending, self.error, self.holder = (report.status, report.pending, report.error,
                                                                  report.holder)
            self.running, self.finished = False, True
        self.done.set()


class InputsBody(BaseModel):
    github_repos: list[str] = []
    zephyr_projects: str | list[str] = ""
    confluence_spaces: list[str] = []


class TraceBody(BaseModel):
    scope: Literal["story", "folder", "project"]
    value: str
    project: str | None = None
    include_suggested: bool = False


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


def _run_sync(app: FastAPI, trigger: Literal["startup", "manual"]) -> threading.Thread | None:
    """Start a sync thread, or return None when one is already running."""
    services, status = app.state.desktop, app.state.sync_status
    if not status.try_begin():
        return None

    def work() -> None:
        try:
            report = services.runner.run(trigger, status.add_line)
        except Exception as e:  # noqa: BLE001 - the runner reports its own failures; this is a last resort
            report = SyncReport(status="failed", error=str(e) or type(e).__name__)
        if report.error:
            status.add_line(f"Sync failed: {report.error}")
        status.finish(report)

    thread = threading.Thread(target=work, daemon=True, name=f"sync-{trigger}")
    thread.start()
    return thread


def start_startup_sync(app: FastAPI) -> threading.Thread | None:
    """Called by the desktop entry point once the app is up; the UI banner follows it through /sync/status."""
    return _run_sync(app, "startup")


def build_router(settings: Settings, app: FastAPI, get_searcher) -> APIRouter:
    router = APIRouter(prefix="/api/desktop")
    services = app.state.desktop
    status: SyncStatus = app.state.sync_status

    @router.get("/inputs")
    def get_inputs() -> dict:
        return services.meta.read_inputs().model_dump()

    @router.put("/inputs")
    def put_inputs(body: InputsBody) -> dict:
        try:
            inputs = Inputs.validated(body.model_dump())
        except InputError as e:
            raise _error(400, "bad_request", str(e))
        return services.meta.write_inputs(inputs, services.machine).model_dump()

    @router.post("/sync", status_code=202)
    def start_sync() -> dict:
        if _run_sync(app, "manual") is None:
            raise _error(409, "sync_running", f"Sync already running on {services.machine}")
        status.done.wait(services.start_wait_s)  # a lock held elsewhere ends the run at once
        with status.lock:
            locked = status.finished and status.status == "locked"
            holder = status.holder
        if locked:
            raise _error(409, "sync_running", f"Sync already running on {holder}")
        return {"started": True}

    @router.get("/sync/status")
    def sync_status() -> StreamingResponse:
        def stream():
            lines_sent = 0
            while True:
                with status.lock:
                    new_lines = status.lines[lines_sent:]
                    done = not status.running
                    results = dict(status.sources)
                    final = {"status": status.status or "idle", "pending": status.pending, "error": status.error}
                lines_sent += len(new_lines)
                for line in new_lines:
                    yield _sse("progress", {"line": line})
                if done:
                    for sid, r in results.items():
                        yield _sse("source", {"id": sid, "ok": r.get("ok", False), "chunks": r.get("chunks", 0),
                                              "error": r.get("error", "")})
                    yield _sse("finished", final)
                    return
                time.sleep(services.poll_s)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/sync/last")
    def last_sync() -> dict | None:
        return services.meta.read_last_sync()

    @router.post("/measure")
    def measure() -> dict:
        spaces = services.meta.read_inputs().confluence_spaces
        if not spaces:
            raise _error(400, "bad_request", "Set the Confluence space first")
        ref = parse_space_url(spaces[0])
        email, token = services.env.get("ATLASSIAN_EMAIL", ""), services.env.get("ATLASSIAN_API_TOKEN", "")
        client = ApiClient("confluence", ref.base, atlassian_headers(email, token), transport=services.transport)
        try:
            return dataclasses.asdict(measure_space(client, ref, settings.desktop.attachment_max_mb))
        except ServiceError as e:
            raise _error(502, "upstream_error", str(e))

    def build(body: TraceBody):
        scope, value, project = body.scope, body.value.strip(), (body.project or "").strip().upper() or None
        if scope in ("story", "project"):
            value = value.upper()
        elif not project:
            raise _error(400, "bad_request", "Project is required for folder scope")
        db = Database(settings.db_path)
        db.init()
        services.meta.pull_state(db)
        id_to_key = json.loads(db.get_meta("connector_state") or "{}").get("jira", {}).get("id_to_key", {})
        data = load_trace_data(services.store, id_to_key)
        suggest = None
        if body.include_suggested:
            manifest = read_manifest(services.store, settings.qdrant.meta_collection) or {}
            zephyr = [s for s in manifest.get("labels", {}) if s.startswith("zephyr_")]
            if zephyr:
                suggest = make_suggester(get_searcher(), zephyr)
        try:
            matrix = build_matrix(data, scope, value, project, suggest)
        except TraceNotFound as e:
            raise _error(404, "not_found", str(e))
        return matrix, scope, value

    @router.post("/traceability")
    def traceability(body: TraceBody) -> dict:
        matrix, _, _ = build(body)
        return {"rows": [dataclasses.asdict(r) for r in matrix.rows], "summary": matrix.summary}

    @router.post("/traceability.xlsx")
    def traceability_xlsx(body: TraceBody) -> Response:
        matrix, scope, value = build(body)
        name = xlsx_filename(scope, value, date.today())
        return Response(to_xlsx(matrix, f"Traceability: {scope} {value}"), media_type=XLSX_TYPE,
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    return router
