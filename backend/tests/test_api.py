import json

import pytest
from fastapi.testclient import TestClient

from qabuddy.answer.llm import LLMUnavailable
from qabuddy.api import create_app
from qabuddy.db import Database
from qabuddy.retrieval.search import Hit, SearchResult


def _hit(n):
    return Hit(n=n, chunk_id=f"id{n}", citation=f"VWO-{n}", source="jira", kind="ticket", path="01_JIRA/x.md",
               location={"ticket": f"VWO-{n}"}, header="JIRA › VWO", text="Login fails with valid credentials.",
               snippet="Login fails", score=4.2)


class FakeSearcher:
    def __init__(self, no_answer=False):
        self.no_answer = no_answer
        self.calls = []

    def search(self, question, history=None, sources=None):
        self.calls.append((question, sources))
        return SearchResult([_hit(1), _hit(2)], self.no_answer, {"retrieve": 5, "rerank": 7})


class FakeLLM:
    def __init__(self, pieces=("Hello ", "[1]")):
        self.pieces = pieces
        self.calls = 0

    def stream(self, messages):
        self.calls += 1
        yield from self.pieces


class FailingLLM:
    calls = 0

    def stream(self, messages):
        FailingLLM.calls += 1
        raise LLMUnavailable("Ollama is not running")
        yield  # pragma: no cover


def _events(text):
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "q.db")
    database.init()
    return database


def _client(settings, db, searcher=None, llm=None):
    return TestClient(create_app(settings, searcher=searcher or FakeSearcher(), llm=llm or FakeLLM(), db=db))


def test_chat_streams_sources_then_tokens(settings, db):
    response = _client(settings, db).post("/api/chat", json={"question": "Why does login fail?"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    names = [e for e, _ in events]
    assert names[0] == "sources" and names[-1] == "done" and "token" in names
    assert events[0][1][0]["citation"] == "VWO-1" and "text" not in events[0][1][0]
    assert "".join(d["t"] for e, d in events if e == "token") == "Hello [1]"
    done = events[-1][1]
    assert done["citations_used"] == [1] and done["uncited"] is False and done["no_answer"] is False
    assert {"retrieve", "rerank", "llm_first_token", "total"} <= set(done["timings_ms"])


def test_chat_streams_sources_then_llm_unavailable(settings, db):
    response = _client(settings, db, llm=FailingLLM()).post("/api/chat", json={"question": "Why does login fail?"})
    events = _events(response.text)
    assert [e for e, _ in events] == ["sources", "error", "done"]
    assert events[1][1]["code"] == "llm_unavailable"


def test_chat_no_answer_skips_llm(settings, db):
    llm = FakeLLM()
    response = _client(settings, db, searcher=FakeSearcher(no_answer=True), llm=llm).post(
        "/api/chat", json={"question": "What is the salary band for SDET II?"})
    events = _events(response.text)
    assert llm.calls == 0
    assert events[-1][1]["no_answer"] is True
    assert "couldn't find" in "".join(d["t"] for e, d in events if e == "token")


def test_chat_rejects_empty_and_long(settings, db):
    client = _client(settings, db)
    assert client.post("/api/chat", json={"question": "   "}).status_code == 400
    assert client.post("/api/chat", json={"question": "x" * 2001}).status_code == 400
    assert client.post("/api/chat", json={"question": "ok?", "sources": ["nope"]}).status_code == 400


def test_chat_passes_source_filter(settings, db):
    searcher = FakeSearcher()
    _client(settings, db, searcher=searcher).post("/api/chat", json={"question": "q?", "sources": ["jira"]})
    assert searcher.calls == [("q?", ["jira"])]


def test_chat_cache_replays_answer(settings, db):
    llm = FakeLLM()
    client = _client(settings, db, llm=llm)
    client.post("/api/chat", json={"question": "Why does login fail?"})
    second = _events(client.post("/api/chat", json={"question": "why does LOGIN fail? "}).text)
    assert llm.calls == 1
    assert second[-1][1]["cached"] is True


@pytest.mark.integration
def test_chunk_endpoint_returns_pdf_chunk(settings):
    from qdrant_client import models

    from .integration import require_services

    require_services(settings)
    from qabuddy.index.store import VectorStore

    store = VectorStore(settings.qdrant.url, settings.qdrant.collection)
    if not store.exists():
        pytest.skip("index is empty; run `qabuddy ingest` first")
    points, _ = store.client.scroll(settings.qdrant.collection, limit=1, with_payload=True, scroll_filter=models.Filter(
        must=[models.FieldCondition(key="source", match=models.MatchValue(value="requirements"))]))
    if not points:
        pytest.skip("no requirements chunks indexed")
    client = TestClient(create_app(settings))
    body = client.get(f"/api/chunks/{points[0].id}").json()
    assert " " in body["path"] and body["path"].endswith(".pdf")
    assert body["location"]["page"] >= 1
    assert client.get("/api/chunks/00000000-0000-0000-0000-000000000000").status_code == 404


def test_health_reports_deepseek_key_status(settings, db, monkeypatch):
    deepseek = settings.model_copy(update={"llm": settings.llm.model_copy(update={"provider": "deepseek"})})
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    health = _client(deepseek, db).get("/api/health").json()
    assert health["llm"]["provider"] == "deepseek" and health["llm"]["ok"] is False
    assert "DEEPSEEK_API_KEY" in health["llm"]["error"] and health["models"]["llm"] is False

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    health = _client(deepseek, db).get("/api/health").json()
    assert health["llm"] == {"provider": "deepseek", "model": "deepseek-flash", "ok": True}
    assert health["models"]["llm"] is True
