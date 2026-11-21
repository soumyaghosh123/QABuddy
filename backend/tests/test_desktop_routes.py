import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from qabuddy.api import create_app
from qabuddy.catalog import MANIFEST_ID
from qabuddy.db import Database
from qabuddy.desktop.inputs import Inputs
from qabuddy.desktop.meta import SharedMeta
from qabuddy.desktop.routes import start_startup_sync
from qabuddy.desktop.sync import SyncReport
from qabuddy.desktop.wiring import DesktopServices
from qabuddy.index.store import IndexUnavailable

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class FakeStore:
    def __init__(self, points=None):
        self.points, self.meta = points or [], {}

    def ensure_meta_collection(self, name):
        pass

    def write_meta(self, name, point_id, payload):
        self.meta[(name, point_id)] = payload

    def read_meta(self, name, point_id):
        return self.meta.get((name, point_id))

    def set_manifest(self, manifest):
        self.meta[("qabuddy_meta", MANIFEST_ID)] = manifest

    def scroll_sources(self, prefixes):
        return [p for p in self.points if p["source"].startswith(tuple(prefixes))]


class FakeCatalog:
    def stats(self):
        return {"by_source": {}, "files": 0, "failed_files": []}


class FakeRunner:
    def __init__(self, report=None, gate=None):
        self.report = report or SyncReport(status="done", sources={"github": {"ok": True, "error": ""}})
        self.gate, self.triggers = gate, []

    def run(self, trigger, progress):
        self.triggers.append(trigger)
        progress("Downloading repos")
        if self.gate:
            self.gate.wait(5)
        return self.report


TC = {"source": "zephyr_ausweb", "kind": "testcase", "parent_id": "AUSWEB-T1", "citation": "AUSWEB-T1",
      "meta": {"name": "Login", "status": "Approved", "project": "AUSWEB", "folder": "/Login",
               "linked_keys": ["VWO-1"]}}


def _desk(settings, tmp_path, searcher=None):
    cfg = settings.model_copy(update={"profile": "desktop", "storage_dir": str(tmp_path),
                                      "qdrant": settings.qdrant.model_copy(update={"meta_collection": "qabuddy_meta"})})
    store = FakeStore(points=[TC])
    meta = SharedMeta(store, cfg.qdrant.meta_collection)
    services = DesktopServices(store, meta, FakeRunner(), "PC-1", env={}, poll_s=0.01, start_wait_s=0.05)
    db = Database(tmp_path / "cache.db")
    db.init()
    app = create_app(cfg, searcher=searcher or object(), llm=object(), db=db, catalog=FakeCatalog(),
                     desktop=services)
    return TestClient(app), services, app


@pytest.fixture()
def desk(settings, tmp_path):
    return _desk(settings, tmp_path)


def test_put_inputs_validation_error_message(desk):
    client, services, _ = desk
    r = client.put("/api/desktop/inputs", json={"github_repos": ["nonsense"], "zephyr_projects": "",
                                                 "confluence_spaces": []})
    assert r.status_code == 400
    assert r.json() == {"code": "bad_request",
                        "message": "Enter a repository link like https://<host>/<owner>/<repo>"}
    ok = client.put("/api/desktop/inputs", json={"github_repos": [], "zephyr_projects": "ausweb, abc",
                                                  "confluence_spaces": []})
    assert ok.status_code == 200 and ok.json()["zephyr_projects"] == ["AUSWEB", "ABC"]
    assert ok.json()["updated_by"] == "PC-1"
    assert client.get("/api/desktop/inputs").json()["zephyr_projects"] == ["AUSWEB", "ABC"]


def _events(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_sync_conflict_409(desk):
    client, services, _ = desk
    gate = threading.Event()
    services.runner.gate = gate
    try:
        assert client.post("/api/desktop/sync").status_code == 202
        r = client.post("/api/desktop/sync")
        assert r.status_code == 409
        assert r.json() == {"code": "sync_running", "message": "Sync already running on PC-1"}
    finally:
        gate.set()


def test_post_during_startup_sync_409_and_no_second_runner(desk):
    client, services, app = desk
    gate = threading.Event()
    services.runner.gate = gate
    try:
        thread = start_startup_sync(app)
        assert thread is not None
        assert client.post("/api/desktop/sync").status_code == 409
        assert start_startup_sync(app) is None
    finally:
        gate.set()
        thread.join(5)
    assert services.runner.triggers == ["startup"]


def test_concurrent_posts_start_one_runner(desk):
    client, services, _ = desk
    gate = threading.Event()
    services.runner.gate = gate
    codes = []
    try:
        threads = [threading.Thread(target=lambda: codes.append(client.post("/api/desktop/sync").status_code))
                   for _ in range(4)]
        [t.start() for t in threads]
        [t.join(5) for t in threads]
    finally:
        gate.set()
    assert sorted(codes) == [202, 409, 409, 409]
    assert services.runner.triggers == ["manual"]


@pytest.mark.parametrize("call", [
    lambda c: c.get("/api/desktop/inputs"),
    lambda c: c.put("/api/desktop/inputs", json={}),
    lambda c: c.get("/api/desktop/sync/last"),
    lambda c: c.post("/api/desktop/traceability", json={"scope": "project", "value": "x"}),
])
def test_store_outage_is_503(desk, call):
    client, services, _ = desk

    def boom(*a, **k):
        raise IndexUnavailable("Qdrant is not reachable")

    services.meta.store.read_meta = boom
    services.meta.store.write_meta = boom
    services.store.scroll_sources = boom
    r = call(client)
    assert r.status_code == 503
    assert r.json() == {"code": "index_unavailable", "message": "Qdrant is not reachable"}


def test_sync_locked_by_other_machine_409(desk):
    client, services, _ = desk
    services.runner.report = SyncReport(status="locked", holder="PC-2")
    r = client.post("/api/desktop/sync")
    assert r.status_code == 409
    assert r.json() == {"code": "sync_running", "message": "Sync already running on PC-2"}


def test_sync_status_stream_finishes(desk):
    client, services, _ = desk
    assert client.post("/api/desktop/sync").status_code == 202
    r = client.get("/api/desktop/sync/status")
    events = _events(r.text)
    assert services.runner.triggers == ["manual"]
    assert events[0] == ("progress", {"line": "Downloading repos"})
    assert ("source", {"id": "github", "ok": True, "chunks": 0, "error": ""}) in events
    assert events[-1] == ("finished", {"status": "done", "pending": False, "error": ""})


def test_sync_status_without_sync_closes(desk):
    client, _, _ = desk
    assert _events(client.get("/api/desktop/sync/status").text)[-1][0] == "finished"


def test_sync_last(desk):
    client, services, _ = desk
    assert client.get("/api/desktop/sync/last").json() is None
    services.meta.write_last_sync({"finished_at": 5, "pending": False})
    assert client.get("/api/desktop/sync/last").json()["finished_at"] == 5


def test_startup_sync_runs_in_thread(desk):
    client, services, app = desk
    thread = start_startup_sync(app)
    thread.join(5)
    assert services.runner.triggers == ["startup"]
    assert _events(client.get("/api/desktop/sync/status").text)[0][1] == {"line": "Downloading repos"}


def test_traceability_404_message(desk):
    client, _, _ = desk
    r = client.post("/api/desktop/traceability", json={"scope": "story", "value": "nope-9",
                                                         "include_suggested": False})
    assert r.status_code == 404
    assert r.json()["message"] == "No story named NOPE-9 in the index — run Sync now if it is new."


def test_traceability_story_is_uppercased(desk):
    client, _, _ = desk
    r = client.post("/api/desktop/traceability", json={"scope": "story", "value": "vwo-1",
                                                         "include_suggested": False})
    body = r.json()
    assert r.status_code == 200 and body["summary"]["testcases"] == 1
    assert body["rows"][0]["testcase_key"] == "AUSWEB-T1"


def test_traceability_folder_needs_project(desk):
    client, _, _ = desk
    r = client.post("/api/desktop/traceability", json={"scope": "folder", "value": "/Login",
                                                         "include_suggested": False})
    assert r.status_code == 400
    assert r.json() == {"code": "bad_request", "message": "Project is required for folder scope"}
    ok = client.post("/api/desktop/traceability", json={"scope": "folder", "value": "/Login", "project": "ausweb",
                                                          "include_suggested": False})
    assert ok.status_code == 200 and ok.json()["summary"]["testcases"] == 1


def test_traceability_suggestions_use_searcher(settings, tmp_path):

    class Hit:
        citation, score = "AUSWEB-T1", 99.0

    class Result:
        hits = [Hit()]

    calls = []

    class Searcher:
        def __init__(self):
            self.settings = settings

        def search(self, text, sources=None):
            calls.append(sources)
            return Result()

    client, services, _ = _desk(settings, tmp_path, Searcher())
    services.store.set_manifest({"labels": {"zephyr_ausweb": "Zephyr · AUSWEB", "confluence": "Confluence"}})
    services.store.points = [{**TC, "meta": {**TC["meta"], "linked_keys": []}},
                             {"source": "jira_linked", "kind": "ticket", "citation": "VWO-5",
                              "meta": {"summary": "Pay"}}]
    r = client.post("/api/desktop/traceability", json={"scope": "story", "value": "vwo-5",
                                                         "include_suggested": True})
    assert r.status_code == 200
    assert calls == [["zephyr_ausweb"]]
    assert any(row["gap"] == "Suggested" for row in r.json()["rows"])


def test_xlsx_download_headers(desk):
    client, _, _ = desk
    r = client.post("/api/desktop/traceability.xlsx", json={"scope": "story", "value": "vwo-1",
                                                              "include_suggested": False})
    assert r.status_code == 200
    assert r.headers["content-type"] == XLSX
    assert r.headers["content-disposition"].startswith("attachment; filename=Traceability_story_VWO-1_")
    assert r.content[:2] == b"PK"


def test_measure_requires_space(desk):
    client, _, _ = desk
    r = client.post("/api/desktop/measure")
    assert r.status_code == 400
    assert r.json() == {"code": "bad_request", "message": "Set the Confluence space first"}


def test_measure_returns_measurement(desk):
    client, services, _ = desk

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "x.atlassian.net"
        if request.url.path == "/wiki/api/v2/spaces":
            return httpx.Response(200, json={"results": [{"id": "9", "key": "QA"}]})
        if request.url.path == "/wiki/api/v2/spaces/9/pages":
            return httpx.Response(200, json={"results": [{"id": "1"}, {"id": "2"}], "_links": {}})
        return httpx.Response(200, json={"results": [], "_links": {}})

    services.transport = httpx.MockTransport(handler)
    services.env = {"ATLASSIAN_EMAIL": "a@b.c", "ATLASSIAN_API_TOKEN": "t"}
    services.meta.write_inputs(Inputs(confluence_spaces=["https://x.atlassian.net/wiki/spaces/QA"]), "PC-1")
    r = client.post("/api/desktop/measure")
    assert r.status_code == 200, r.text
    assert r.json()["pages"] == 2 and r.json()["est_chunks"] == 12


def test_web_profile_has_no_desktop_routes(settings, tmp_path):
    db = Database(tmp_path / "c.db")
    db.init()
    app = create_app(settings, searcher=object(), llm=object(), db=db, catalog=FakeCatalog())
    assert TestClient(app).get("/api/desktop/inputs").status_code == 404


def test_stats_desktop_flag(desk, settings, tmp_path):
    client, services, _ = desk
    body = client.get("/api/stats").json()
    assert body["desktop"] is True and body["sources"] == []
    services.store.set_manifest({"labels": {"gh_x": "x", "confluence": "Confluence"}})
    body = client.get("/api/stats").json()
    assert body["sources"] == [{"id": "gh_x", "label": "x"}, {"id": "confluence", "label": "Confluence"}]
    db = Database(tmp_path / "c.db")
    db.init()
    web = TestClient(create_app(settings, searcher=object(), llm=object(), db=db, catalog=FakeCatalog()))
    assert web.get("/api/stats").json()["desktop"] is False


class DownCatalog:
    def stats(self):
        raise IndexUnavailable("Qdrant is not reachable at https://q:6333")


def test_desktop_stats_still_flags_desktop_when_qdrant_is_down(settings, tmp_path):
    """Settings must stay reachable so the user can see and work around a Qdrant outage."""
    _, services, _ = _desk(settings, tmp_path)

    def boom(*a, **k):
        raise IndexUnavailable("Qdrant is not reachable at https://q:6333")

    services.meta.store.read_meta = boom
    cfg = settings.model_copy(update={"profile": "desktop", "storage_dir": str(tmp_path),
                                      "qdrant": settings.qdrant.model_copy(update={"meta_collection": "qabuddy_meta"})})
    db = Database(tmp_path / "down.db")
    db.init()
    down = TestClient(create_app(cfg, searcher=object(), llm=object(), db=db, catalog=DownCatalog(), desktop=services))
    r = down.get("/api/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["desktop"] is True and body["sources"] == []
    assert body["index_error"] == "Qdrant is not reachable at https://q:6333"


def test_web_stats_still_503_when_qdrant_is_down(settings, tmp_path):
    db = Database(tmp_path / "w.db")
    db.init()
    web = TestClient(create_app(settings, searcher=object(), llm=object(), db=db, catalog=DownCatalog()))
    r = web.get("/api/stats")
    assert r.status_code == 503 and r.json()["code"] == "index_unavailable"


# ---- desktop chat: sources validated against the live manifest, Matches list streamed

def _desk_chat(settings, tmp_path):
    from tests.test_api import FakeLLM, FakeSearcher

    sources_file = tmp_path / "sources.desktop.yaml"
    sources_file.write_text("sources: []\n", encoding="utf-8")  # first launch: empty seed
    cfg = settings.model_copy(update={"profile": "desktop", "storage_dir": str(tmp_path),
                                      "sources_file": str(sources_file),
                                      "qdrant": settings.qdrant.model_copy(update={"meta_collection": "qabuddy_meta"})})
    store = FakeStore(points=[TC])
    services = DesktopServices(store, SharedMeta(store, "qabuddy_meta"), FakeRunner(), "PC-1", env={})
    db = Database(tmp_path / "cache.db")
    db.init()
    searcher = FakeSearcher()
    app = create_app(cfg, searcher=searcher, llm=FakeLLM(), db=db, catalog=ChatCatalog(), desktop=services)
    return TestClient(app), services, searcher, db


class ChatCatalog(FakeCatalog):
    def index_version(self):
        return 0


def test_desktop_chat_accepts_sources_from_manifest_added_after_start(settings, tmp_path):
    client, services, searcher, _ = _desk_chat(settings, tmp_path)
    assert client.post("/api/chat", json={"question": "q?", "sources": ["zephyr_ausweb"]}).status_code == 400
    services.store.set_manifest({"labels": {"zephyr_ausweb": "Zephyr · AUSWEB", "confluence": "Confluence"}})
    r = client.post("/api/chat", json={"question": "q?", "sources": ["zephyr_ausweb", "confluence"]})
    assert r.status_code == 200 and searcher.calls == [("q?", ["zephyr_ausweb", "confluence"])]
    bad = client.post("/api/chat", json={"question": "q?", "sources": ["nope"]})
    assert bad.status_code == 400 and bad.json()["message"] == "Unknown source(s): nope"


def test_desktop_chat_streams_matches_fresh_and_cached(settings, tmp_path):
    client, *_ = _desk_chat(settings, tmp_path)
    fresh = _events(client.post("/api/chat", json={"question": "Why does login fail?"}).text)
    assert [e for e, _ in fresh][:2] == ["sources", "matches"]
    matches = fresh[1][1]
    assert [m["key"] for m in matches] == ["VWO-1", "VWO-2"] and matches[0]["type"] == "story"
    cached = _events(client.post("/api/chat", json={"question": "why does LOGIN fail? "}).text)
    assert [e for e, _ in cached][:2] == ["sources", "matches"] and cached[1][1] == matches


def test_desktop_cached_entry_without_matches_emits_empty_list(settings, tmp_path):
    from qabuddy.api import _cache_key

    client, _, _, db = _desk_chat(settings, tmp_path)
    db.cache_put(_cache_key("old q", None), 0, "old q", "", "Hi [1]", [{"n": 1}])
    events = _events(client.post("/api/chat", json={"question": "old q"}).text)
    assert events[1] == ("matches", [])


# ---- traceability reads the shared Jira id map in memory; never touches the sync's scratch database

class NoPullMeta(SharedMeta):
    def pull_state(self, db):
        raise AssertionError("traceability must not import shared state into the scratch database")


def test_traceability_reads_id_map_without_touching_scratch_db(settings, tmp_path):
    client, services, app = _desk(settings, tmp_path)
    services.meta = NoPullMeta(services.store, "qabuddy_meta")
    shared = Database(tmp_path / "pc1.db")
    shared.init()
    shared.set_meta("connector_state", json.dumps({"jira": {"id_to_key": {"10001": "VWO-7"}}}))
    services.meta.push_state(shared)
    scratch = Database(tmp_path / "qabuddy.db")  # the sync's working copy (settings.db_path)
    scratch.init()
    scratch.upsert_file("mid/sync.json", "zephyr_ausweb", "h", 1, 1.0, ["x"], "ok", None)
    services.store.points = [{**TC, "meta": {**TC["meta"], "linked_keys": [], "issue_ids": [10001]}}]
    r = client.post("/api/desktop/traceability", json={"scope": "story", "value": "vwo-7",
                                                         "include_suggested": False})
    assert r.status_code == 200 and r.json()["rows"][0]["testcase_key"] == "AUSWEB-T1"
    assert [f["path"] for f in scratch.all_files()] == ["mid/sync.json"]
    assert scratch.get_meta("connector_state") is None
