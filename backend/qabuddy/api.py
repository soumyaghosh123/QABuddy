"""FastAPI app: /api/chat (Server-Sent Events), /api/chunks, /api/health, /api/stats, /api/examples."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import Iterator

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from qabuddy.answer.llm import LLMUnavailable, llm_status, make_llm
from qabuddy.answer.prompt import build_messages, check_citations
from qabuddy.config import REPO_ROOT, Settings
from qabuddy.db import Database
from qabuddy.index.embedder import OllamaUnavailable
from qabuddy.index.store import IndexUnavailable
from qabuddy.ingest.discover import load_sources

log = logging.getLogger(__name__)
MAX_QUESTION_CHARS = 2000
NO_ANSWER_TEXT = ("I couldn't find this in the QABuddy knowledge base. The closest matches are listed in the "
                  "sources panel — try rephrasing, or filter by a specific source.")
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    question: str
    sources: list[str] | None = None
    history: list[ChatMessage] | None = None


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _cache_key(question: str, sources: list[str] | None) -> str:
    normalized = re.sub(r"\s+", " ", question.strip().lower()).rstrip("?!. ")
    return hashlib.sha256(f"{normalized}|{','.join(sorted(sources or []))}".encode()).hexdigest()


def create_app(settings: Settings, searcher=None, llm=None, db: Database | None = None) -> FastAPI:
    app = FastAPI(title="QABuddy.ai", version="0.1.0")
    state: dict = {"searcher": searcher, "llm": llm}
    database = db or Database(settings.db_path)
    database.init()
    source_ids = {s.id for s in load_sources(settings.config_path / "sources.yaml") if s.enabled}

    def get_searcher():
        if state["searcher"] is None:
            from qabuddy.retrieval.search import build_searcher

            state["searcher"] = build_searcher(settings)
        return state["searcher"]

    def get_llm():
        if state["llm"] is None:
            state["llm"] = make_llm(settings)
        return state["llm"]

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content=detail)

    def bad_request(message: str) -> HTTPException:
        return HTTPException(400, {"code": "bad_request", "message": message})

    @app.post("/api/chat")
    def chat(request: ChatRequest) -> StreamingResponse:
        question = request.question.strip()
        if not question:
            raise bad_request("Question is empty.")
        if len(request.question) > MAX_QUESTION_CHARS:
            raise bad_request(f"Question is longer than {MAX_QUESTION_CHARS} characters.")
        unknown = sorted(set(request.sources or []) - source_ids)
        if unknown:
            raise bad_request(f"Unknown source(s): {', '.join(unknown)}")
        history = [m.model_dump() for m in request.history or []]
        return StreamingResponse(_answer_stream(question, request.sources or None, history),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def _answer_stream(question: str, sources: list[str] | None, history: list[dict]) -> Iterator[str]:
        started = time.perf_counter()
        timings = {"retrieve": 0, "rerank": 0, "llm_first_token": None, "total": 0}

        def done(**extra) -> str:
            timings["total"] = round((time.perf_counter() - started) * 1000)
            payload = {"citations_used": [], "uncited": False, "no_answer": False, "cached": False,
                       "timings_ms": timings}
            payload.update(extra)
            return _sse("done", payload)

        cache_key = _cache_key(question, sources)
        version = database.index_version()
        if not history:
            cached = database.cache_get(cache_key, version)
            if cached:
                yield _sse("sources", cached["sources"])
                yield _sse("token", {"t": cached["answer"]})
                used, uncited = check_citations(cached["answer"], len(cached["sources"]))
                yield done(citations_used=used, uncited=uncited, cached=True)
                return

        try:
            result = get_searcher().search(question, history=history, sources=sources)
        except IndexUnavailable as exc:
            yield _sse("error", {"code": "index_unavailable", "message": str(exc)})
            yield done()
            return
        except OllamaUnavailable as exc:
            yield _sse("error", {"code": "llm_unavailable", "message": str(exc)})
            yield done()
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("search failed")
            yield _sse("error", {"code": "internal", "message": f"Search failed: {exc}"})
            yield done()
            return
        timings.update(result.timings)
        public_hits = [h.public() for h in result.hits]
        yield _sse("sources", public_hits)

        if result.no_answer:
            yield _sse("token", {"t": NO_ANSWER_TEXT})
            yield done(no_answer=True)
            return

        p = settings.prompt
        messages, used_hits = build_messages(question, result.hits, history, p.max_source_tokens, p.history_turns)
        answer_parts: list[str] = []
        llm_started = time.perf_counter()
        try:
            for piece in get_llm().stream(messages):
                if timings["llm_first_token"] is None:
                    timings["llm_first_token"] = round((time.perf_counter() - llm_started) * 1000)
                answer_parts.append(piece)
                yield _sse("token", {"t": piece})
        except LLMUnavailable as exc:
            yield _sse("error", {"code": "llm_unavailable", "message": str(exc)})
            yield done(partial=bool(answer_parts))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("LLM stream failed")
            yield _sse("error", {"code": "internal", "message": f"Answer interrupted: {exc}"})
            yield done(partial=bool(answer_parts))
            return

        answer = "".join(answer_parts)
        used, uncited = check_citations(answer, len(used_hits))
        if not history and answer and not uncited:
            database.cache_put(cache_key, version, question, ",".join(sources or []), answer, public_hits)
        yield done(citations_used=used, uncited=uncited)

    def get_store():
        if state.get("store") is None:
            from qabuddy.index.store import VectorStore

            existing = getattr(state["searcher"], "store", None)
            state["store"] = existing or VectorStore(settings.qdrant.url, settings.qdrant.collection)
        return state["store"]

    @app.get("/api/chunks/{chunk_id}")
    def get_chunk(chunk_id: str) -> dict:
        try:
            payload = get_store().get(chunk_id)
        except IndexUnavailable as exc:
            raise HTTPException(503, {"code": "index_unavailable", "message": str(exc)})
        if payload is None:
            raise HTTPException(404, {"code": "not_found", "message": "Chunk not found"})
        keys = ("chunk_id", "citation", "source", "kind", "path", "location", "title", "breadcrumb", "header", "text")
        return {k: payload.get(k) for k in keys}

    @app.get("/api/health")
    def health() -> dict:
        result: dict = {"qdrant": {"ok": False}, "ollama": {"ok": False}, "models": {"embed": False, "llm": False},
                        "index": {"chunks": 0, "last_ingest_at": None, "index_version": database.index_version()}}
        try:
            info = httpx.get(f"{settings.qdrant.url}/collections/{settings.qdrant.collection}", timeout=3)
            result["qdrant"]["ok"] = info.status_code in (200, 404)
            if info.status_code == 200:
                result["index"]["chunks"] = info.json()["result"].get("points_count", 0)
        except httpx.HTTPError as exc:
            result["qdrant"]["error"] = f"Qdrant not reachable: {type(exc).__name__}"
        try:
            tags = httpx.get(f"{settings.ollama.url}/api/tags", timeout=3).json()
            names = {m["name"] for m in tags.get("models", [])}
            result["ollama"]["ok"] = True
            result["models"] = {"embed": settings.ollama.embed_model in names, "llm": settings.ollama.llm_model in names}
        except httpx.HTTPError as exc:
            result["ollama"]["error"] = f"Ollama not reachable: {type(exc).__name__}"
        result["llm"] = llm_status(settings)
        result["stack"] = {"vector_db": "Qdrant", "search": "hybrid RRF", "embed_model": settings.ollama.embed_model,
                           "rerank_model": settings.reranker.model}
        if settings.llm.provider != "ollama":
            result["models"]["llm"] = result["llm"]["ok"]
        last = database.get_meta("last_ingest_at")
        result["index"]["last_ingest_at"] = float(last) if last else None
        return result

    @app.get("/api/stats")
    def stats() -> dict:
        files = database.all_files()
        by_source: Counter = Counter()
        for f in files:
            by_source[f["source"]] += len(f["chunk_ids"])
        return {"by_source": dict(by_source), "files": sum(1 for f in files if not f["path"].startswith("__")),
                "failed_files": [{"path": f["path"], "error": f["error"]} for f in files if f["status"] == "failed"],
                "sources": [{"id": s.id, "label": s.label} for s in load_sources(settings.config_path / "sources.yaml")
                            if s.enabled]}

    @app.get("/api/examples")
    def examples() -> list[str]:
        path = REPO_ROOT / "eval" / "questions.yaml"
        if not path.exists():
            return []
        items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        return [q["q"] for q in items if q.get("example")][:6]

    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=Path(FRONTEND_DIST), html=True), name="ui")
    return app
