import json

import httpx
import pytest

from qabuddy.config import load_settings
from qabuddy.index.embedder import QUERY_TASK, OllamaUnavailable
from qabuddy.inference.cloudflare import (CloudflareClient, CloudflareEmbedder, CloudflareReranker,
                                          make_cloudflare_client)
from qabuddy.inference.errors import InferenceUnavailable

EMBED = "@cf/qwen/qwen3-embedding-0.6b"
RERANK = "@cf/baai/bge-reranker-base"


def ok(result):
    return httpx.Response(200, json={"success": True, "errors": [], "messages": [], "result": result})


class Recorder:
    """MockTransport handler: records requests and answers from a queue (the last answer repeats)."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer(request) if callable(answer) else answer

    def bodies(self):
        return [json.loads(r.content) for r in self.requests]


def make(handler, sleeps=None):
    return CloudflareClient("acc", "tok", transport=httpx.MockTransport(handler),
                            sleep=sleeps.append if sleeps is not None else (lambda _: None))


def echo_embeddings(request):
    docs = json.loads(request.content)["documents"]
    return ok({"shape": [len(docs), 1], "data": [[float(len(d))] for d in docs]})


def test_run_posts_to_model_url_with_bearer():
    rec = Recorder(ok({"data": [[0.1]], "shape": [1, 1]}))
    assert make(rec).run(EMBED, {"documents": ["a"]}) == {"data": [[0.1]], "shape": [1, 1]}
    request = rec.requests[0]
    assert str(request.url) == "https://api.cloudflare.com/client/v4/accounts/acc/ai/run/@cf/qwen/qwen3-embedding-0.6b"
    assert request.headers["Authorization"] == "Bearer tok"


def test_embed_query_sends_queries_and_instruction():
    rec = Recorder(ok({"data": [[0.1, 0.2]], "shape": [1, 2]}))
    assert CloudflareEmbedder(make(rec), EMBED).embed_query("why?") == [0.1, 0.2]
    assert rec.bodies() == [{"queries": ["why?"], "instruction": QUERY_TASK}]


def test_embed_documents_batches_by_32():
    rec, progress = Recorder(echo_embeddings), []
    texts = [f"t{i}" for i in range(70)]
    vectors = CloudflareEmbedder(make(rec), EMBED).embed_documents(texts, progress=lambda d, t: progress.append((d, t)))
    assert [len(b["documents"]) for b in rec.bodies()] == [32, 32, 6]
    assert vectors == [[float(len(t))] for t in texts]
    assert progress == [(32, 70), (64, 70), (70, 70)]


def test_empty_document_text_is_sent_as_space():
    rec = Recorder(echo_embeddings)
    CloudflareEmbedder(make(rec), EMBED).embed_documents(["", "a"])
    assert rec.bodies() == [{"documents": [" ", "a"]}]


def test_rerank_maps_scores_by_id():
    rec = Recorder(ok({"response": [{"id": 2, "score": 0.9}, {"id": 0, "score": 0.1}]}))
    assert CloudflareReranker(make(rec), RERANK).score("q", ["a", "b", "c"]) == [0.1, float("-inf"), 0.9]
    assert rec.bodies() == [{"query": "q", "contexts": [{"text": "a"}, {"text": "b"}, {"text": "c"}], "top_k": 3}]


def test_rerank_empty_texts_makes_no_call():
    rec = Recorder(ok({"response": []}))
    assert CloudflareReranker(make(rec), RERANK).score("q", []) == []
    assert rec.requests == []


def test_retries_429_then_succeeds():
    rec, sleeps = Recorder(httpx.Response(429), httpx.Response(429), ok({"data": [[1.0]]})), []
    assert make(rec, sleeps).run(EMBED, {"documents": ["a"]}) == {"data": [[1.0]]}
    assert sleeps == [1.0, 2.0]


def test_429_after_retries_mentions_quota():
    rec = Recorder(httpx.Response(429))
    with pytest.raises(InferenceUnavailable, match="daily free quota"):
        make(rec).run(EMBED, {"documents": ["a"]})
    assert len(rec.requests) == 3


def test_400_is_not_retried():
    rec = Recorder(httpx.Response(400, text="bad"))
    with pytest.raises(InferenceUnavailable, match="400"):
        make(rec).run(EMBED, {"documents": ["a"]})
    assert len(rec.requests) == 1


def test_401_names_token():
    with pytest.raises(InferenceUnavailable, match="CLOUDFLARE_API_TOKEN"):
        make(Recorder(httpx.Response(401))).run(EMBED, {"documents": ["a"]})


def test_success_false_envelope_raises():
    body = {"success": False, "errors": [{"code": 5006, "message": "bad input"}], "messages": [], "result": None}
    with pytest.raises(InferenceUnavailable, match="bad input"):
        make(Recorder(httpx.Response(200, json=body))).run(EMBED, {"documents": ["a"]})


def test_network_error_raises():
    rec = Recorder(httpx.ConnectError("boom"))
    with pytest.raises(InferenceUnavailable, match="not reachable"):
        make(rec).run(EMBED, {"documents": ["a"]})
    assert len(rec.requests) == 3


def test_make_client_requires_env():
    cloud = load_settings(env={"QABUDDY_PROFILE": "cloud"})
    with pytest.raises(InferenceUnavailable, match="CLOUDFLARE_ACCOUNT_ID is not set"):
        make_cloudflare_client(cloud, env={})


def test_ollama_unavailable_is_inference_unavailable():
    assert issubclass(OllamaUnavailable, InferenceUnavailable)
