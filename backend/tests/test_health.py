import httpx

from qabuddy.config import load_settings
from qabuddy.health import check_health

LOCAL_ENV = {"DEEPSEEK_API_KEY": "x"}
CLOUD_ENV = {"QABUDDY_PROFILE": "cloud", "QDRANT_URL": "https://x.cloud.qdrant.io", "QDRANT_API_KEY": "k",
             "CLOUDFLARE_ACCOUNT_ID": "a", "CLOUDFLARE_API_TOKEN": "t", "DEEPSEEK_API_KEY": "x"}
LOCAL_STACK = [("Vector DB", "Qdrant · hybrid RRF", True), ("Embeddings", "qwen3-embedding:0.6b · Ollama", True),
               ("Reranker", "ms-marco-MiniLM-L-12-v2 · FastEmbed", True), ("LLM", "DeepSeek · deepseek-flash", True)]
CLOUD_STACK = [("Vector DB", "Qdrant Cloud · hybrid RRF", True),
               ("Embeddings", "qwen3-embedding-0.6b · Cloudflare", True),
               ("Reranker", "bge-reranker-base · Cloudflare", True), ("LLM", "DeepSeek · deepseek-flash", True)]


class FakeCatalog:
    def __init__(self, version=1, hint="qabuddy publish", last=12.5):
        self.version, self.hint, self.last = version, hint, last

    def index_version(self):
        return self.version

    def last_ingest_at(self):
        return self.last


class Services:
    """MockTransport handler for Qdrant and Ollama only; records every request."""

    def __init__(self, points=1162, qdrant_status=200, down=False):
        self.points, self.qdrant_status, self.down = points, qdrant_status, down
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("refused")
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3-embedding:0.6b"}]})
        if request.url.path == "/collections/qabuddy_chunks":
            if self.qdrant_status != 200:
                return httpx.Response(self.qdrant_status)
            return httpx.Response(200, json={"result": {"points_count": self.points}, "status": "ok"})
        raise AssertionError(f"unexpected request to {request.url}")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _check(env, services, catalog=None):
    settings = load_settings(env=env)
    return check_health(settings, catalog or FakeCatalog(), client=services.client(), env=env)


def _rows(health):
    return [(p["label"], p["value"], p["ok"]) for p in health["stack"]]


def _pill(health, key):
    return next(p for p in health["stack"] if p["key"] == key)


def test_local_all_ok():
    health = _check(LOCAL_ENV, Services())
    assert health["status"] == {"level": "ok", "text": "Ready · 1,162 chunks indexed · deepseek-flash"}
    assert _rows(health) == LOCAL_STACK
    assert health["index"] == {"chunks": 1162, "last_ingest_at": 12.5, "index_version": 1}


def test_cloud_all_ok():
    services = Services()
    health = _check(CLOUD_ENV, services)
    assert _rows(health) == CLOUD_STACK and health["status"]["level"] == "ok"
    assert [str(r.url) for r in services.requests] == ["https://x.cloud.qdrant.io/collections/qabuddy_chunks"]
    assert services.requests[0].headers["api-key"] == "k"


def test_cloud_missing_qdrant_url():
    services = Services()
    health = _check({k: v for k, v in CLOUD_ENV.items() if k != "QDRANT_URL"}, services)
    vector = _pill(health, "vector")
    assert vector["ok"] is False and vector["error"] == "QDRANT_URL is not set"
    assert services.requests == []
    assert health["status"]["level"] == "warn"


def test_cloud_missing_cloudflare_token():
    health = _check({k: v for k, v in CLOUD_ENV.items() if k != "CLOUDFLARE_API_TOKEN"}, Services())
    for key in ("embed", "rerank"):
        assert _pill(health, key)["ok"] is False
        assert _pill(health, key)["error"] == "CLOUDFLARE_API_TOKEN is not set"


def test_qdrant_401():
    health = _check(CLOUD_ENV, Services(qdrant_status=401))
    assert "QDRANT_API_KEY" in _pill(health, "vector")["error"]


def test_empty_index_tells_how_to_fill_it():
    health = _check(CLOUD_ENV, Services(points=0))
    assert health["status"]["level"] == "warn"
    assert "index is empty — run qabuddy publish" in health["status"]["text"]


def test_local_everything_down():
    health = _check(LOCAL_ENV, Services(down=True))
    assert health["status"]["level"] == "down"
    assert _pill(health, "vector")["error"] == "Qdrant not reachable: ConnectError"
    assert _pill(health, "embed")["error"] == "Ollama not reachable: ConnectError"
