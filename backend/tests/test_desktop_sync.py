import json

import httpx

from qabuddy.desktop.inputs import Inputs
from qabuddy.desktop.sync import SyncContext
from qabuddy.ingest.connectors.zephyr import ZephyrConnector


class Server:
    def __init__(self, n=150, projects=("AUSWEB",)):
        self.projects = set(projects)
        self.cases = [{"key": f"AUSWEB-T{i}", "name": f"Case {i}", "objective": "<p>obj</p>", "labels": ["a"],
                       "status": {"id": 5}, "priority": {"id": 2}, "folder": {"id": 12}, "project": {"id": 1}}
                      for i in range(1, n + 1)]
        self.calls: list[str] = []
        self.auth = set()

    def page(self, request, values):
        start = int(request.url.params.get("startAt", 0))
        size = int(request.url.params.get("maxResults", 100))
        chunk = values[start:start + size]
        return httpx.Response(200, json={"startAt": start, "maxResults": size, "total": len(values),
                                         "isLast": start + size >= len(values), "values": chunk})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.auth.add(request.headers.get("Authorization"))
        path = request.url.path.removeprefix("/v2")
        self.calls.append(path)
        key = request.url.params.get("projectKey")
        if path.startswith("/projects/"):
            return httpx.Response(200 if path.rsplit("/", 1)[1] in self.projects else 404, json={})
        if path == "/folders":
            if key not in self.projects:
                return httpx.Response(404)
            return self.page(request, [{"id": 11, "parentId": None, "name": "R76 SIT", "folderType": "TEST_CASE"},
                                       {"id": 12, "parentId": 11, "name": "Login", "folderType": "TEST_CASE"}])
        if path == "/statuses":
            return self.page(request, [{"id": 5, "name": "Draft"}])
        if path == "/priorities":
            return self.page(request, [{"id": 2, "name": "High"}])
        if path == "/testcases":
            return self.page(request, self.cases)
        if path.endswith("/teststeps"):
            return self.page(request, [{"inline": {"description": "Do it", "testData": "", "expectedResult": "Ok"}}])
        if path.endswith("/testscript"):
            return httpx.Response(404)
        if path.endswith("/links"):
            return httpx.Response(200, json={"issues": [{"issueId": 10001, "target": "x"}],
                                             "webLinks": [{"url": "http://w", "description": "d"}]})
        return httpx.Response(500)


def ctx(tmp_path, projects=("AUSWEB",), state=None, now=1_000_000.0):
    return SyncContext(None, Inputs(zephyr_projects=list(projects)), tmp_path, {} if state is None else state,
                       now, lambda m: None)


def run(server, tmp_path, **kw):
    c = ctx(tmp_path, **kw)
    return ZephyrConnector("tok", transport=httpx.MockTransport(server)).run(c), c


def step_calls(server):
    return [p for p in server.calls if p.endswith("/teststeps")]


def test_paging_folders_and_testcases(tmp_path):
    server = Server()
    result, _ = run(server, tmp_path)
    assert result.ok and result.issue_ids == {10001}
    files = list((tmp_path / "zephyr" / "AUSWEB").glob("*.json"))
    assert len(files) == 150
    data = json.loads((tmp_path / "zephyr" / "AUSWEB" / "AUSWEB-T1.json").read_text(encoding="utf-8"))
    assert data["folder_path"] == "R76 SIT / Login"
    assert data["status_name"] == "Draft" and data["priority_name"] == "High"
    assert data["issue_ids"] == [10001] and data["web_links"] == [{"url": "http://w", "description": "d"}]
    assert data["listing_hash"] and data["steps"][0]["inline"]["description"] == "Do it"
    assert server.auth == {"Bearer tok"}


def test_unchanged_listing_skips_step_calls(tmp_path):
    server = Server(n=3)
    _, c = run(server, tmp_path)
    assert len(step_calls(server)) == 3
    f = tmp_path / "zephyr" / "AUSWEB" / "AUSWEB-T1.json"
    before = f.read_bytes()
    server.calls.clear()
    result, _ = run(server, tmp_path, state=c.state, now=1_000_100.0)
    assert result.ok and step_calls(server) == [] and result.issue_ids == {10001}
    assert f.read_bytes() == before


def test_changed_listing_refetches_only_that_case(tmp_path):
    server = Server(n=3)
    _, c = run(server, tmp_path)
    server.cases[1]["name"] = "Renamed"
    server.calls.clear()
    run(server, tmp_path, state=c.state, now=1_000_100.0)
    assert step_calls(server) == ["/testcases/AUSWEB-T2/teststeps"]


def test_daily_step_refresh(tmp_path):
    server = Server(n=3)
    _, c = run(server, tmp_path)
    assert c.state["zephyr"]["AUSWEB"]["steps_refreshed_at"] == 1_000_000.0
    server.calls.clear()
    run(server, tmp_path, state=c.state, now=1_000_000.0 + 25 * 3600)
    assert len(step_calls(server)) == 3


def test_deleted_testcase_file_removed(tmp_path):
    server = Server(n=3)
    _, c = run(server, tmp_path)
    server.cases.pop()
    run(server, tmp_path, state=c.state, now=1_000_100.0)
    names = sorted(p.name for p in (tmp_path / "zephyr" / "AUSWEB").glob("*.json"))
    assert names == ["AUSWEB-T1.json", "AUSWEB-T2.json"]


def test_unknown_project_error(tmp_path):
    result, _ = run(Server(), tmp_path, projects=("NOPE", "AUSWEB"))
    assert not result.ok and "Project NOPE not found in Zephyr" in result.error
    assert (tmp_path / "zephyr" / "AUSWEB" / "AUSWEB-T1.json").exists()


def test_script_fallback_when_no_steps(tmp_path):
    server = Server(n=1)

    def handler(request):
        if request.url.path.endswith("/teststeps"):
            return server.page(request, [])
        if request.url.path.endswith("/testscript"):
            return httpx.Response(200, json={"type": "bdd", "text": "Given x"})
        return server(request)

    c = ctx(tmp_path)
    ZephyrConnector("tok", transport=httpx.MockTransport(handler)).run(c)
    data = json.loads((tmp_path / "zephyr" / "AUSWEB" / "AUSWEB-T1.json").read_text(encoding="utf-8"))
    assert data["steps"] == [] and data["script"] == {"type": "bdd", "text": "Given x"}


def test_stale_project_state_pruned(tmp_path):
    _, c = run(Server(n=1), tmp_path, state={"zephyr": {"OLD": {"steps_refreshed_at": 1}}})
    assert list(c.state["zephyr"]) == ["AUSWEB"]


def test_failed_case_does_not_force_full_refresh_next_run(tmp_path):
    server = Server(n=3)
    fail = {"on": True}

    def handler(request):
        if fail["on"] and request.url.path.endswith("/AUSWEB-T2/teststeps"):
            return httpx.Response(500)
        return server(request)

    state: dict = {}
    first = ctx(tmp_path, state=state)
    result = ZephyrConnector("tok", transport=httpx.MockTransport(handler)).run(first)
    assert not result.ok and "AUSWEB: " in result.error
    server.calls.clear()
    fail["on"] = False
    second = ctx(tmp_path, state=state, now=1_000_100.0)
    assert ZephyrConnector("tok", transport=httpx.MockTransport(handler)).run(second).ok
    assert step_calls(server) == ["/testcases/AUSWEB-T2/teststeps"]


def test_ready_sources_per_project_without_project_error(tmp_path):
    result, _ = run(Server(n=3, projects=("AUSWEB",)), tmp_path, projects=("AUSWEB", "NOPE"))
    assert not result.ok and result.ready_sources == {"zephyr_ausweb"}


def test_case_failure_keeps_project_ready(tmp_path):
    server = Server(n=2)
    run(server, tmp_path)

    def flaky(request):
        if request.url.path.endswith("/AUSWEB-T2/links"):
            return httpx.Response(500)
        return server(request)

    server.cases[1]["name"] = "changed"
    result = ZephyrConnector("tok", transport=httpx.MockTransport(flaky)).run(ctx(tmp_path))
    assert not result.ok and result.ready_sources == {"zephyr_ausweb"}
    assert (tmp_path / "zephyr" / "AUSWEB" / "AUSWEB-T2.json").exists()


def test_failed_case_reported_in_failed_paths(tmp_path):
    server = Server(n=3)

    def flaky(request):
        if request.url.path.endswith("/AUSWEB-T2/teststeps"):
            return httpx.Response(500)
        return server(request)

    result = ZephyrConnector("tok", transport=httpx.MockTransport(flaky)).run(ctx(tmp_path))
    assert result.failed_paths == {"zephyr/AUSWEB/AUSWEB-T2.json"}
    assert not (tmp_path / "zephyr" / "AUSWEB" / "AUSWEB-T2.json").exists()
    ok, _ = run(Server(n=3), tmp_path)
    assert ok.ok and ok.failed_paths == set()


def test_test_case_details_are_fetched_in_parallel(tmp_path):
    import threading
    import time
    from qabuddy.ingest.connectors import zephyr as zmod

    class SlowServer(Server):
        def __init__(self):
            super().__init__(n=40)
            self.lock, self.inflight, self.peak = threading.Lock(), 0, 0

        def __call__(self, request):
            if request.url.path.endswith("/teststeps"):
                with self.lock:
                    self.inflight += 1
                    self.peak = max(self.peak, self.inflight)
                time.sleep(0.02)
                with self.lock:
                    self.inflight -= 1
            return super().__call__(request)

    server = SlowServer()
    result, _ = run(server, tmp_path)
    assert result.ok and len(list((tmp_path / "zephyr" / "AUSWEB").glob("*.json"))) == 40
    assert 1 < server.peak <= zmod.WORKERS
    assert zmod.WORKERS == 8


def test_auth_error_during_parallel_fetch_stops_the_project(tmp_path):
    class AuthServer(Server):
        def __call__(self, request):
            if request.url.path.endswith("/links") and request.url.path.split("/")[-2] == "AUSWEB-T7":
                return httpx.Response(401)
            return super().__call__(request)

    result, _ = run(AuthServer(n=20), tmp_path)
    assert not result.ok and "zephyr_ausweb" not in result.ready_sources
    assert "token rejected" in result.error.lower()
