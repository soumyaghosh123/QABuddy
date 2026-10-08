"""Cloudflare Workers AI REST client: query/document embeddings and cross-encoder reranking (spec §6.1)."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Callable, Mapping

import httpx

from qabuddy.index.embedder import QUERY_TASK
from qabuddy.inference.errors import InferenceUnavailable

if TYPE_CHECKING:
    from qabuddy.config import Settings

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"


class CloudflareClient:
    def __init__(self, account_id: str, api_token: str, timeout_s: float = 30,
                 transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
                 attempts: int = 3):
        self.base_url = f"{CLOUDFLARE_API}/accounts/{account_id}/ai/run"
        self.sleep, self.attempts = sleep, attempts
        self._client = httpx.Client(transport=transport, timeout=httpx.Timeout(timeout_s, connect=10),
                                    headers={"Authorization": f"Bearer {api_token}"})

    def run(self, model: str, payload: dict) -> dict:
        """POST the payload to the model; retry network errors, 429 and 5xx; return the `result` object."""
        short = model.rsplit("/", 1)[-1]
        for attempt in range(1, self.attempts + 1):
            last = attempt == self.attempts
            try:
                response = self._client.post(f"{self.base_url}/{model}", json=payload)
            except httpx.HTTPError as exc:
                if last:
                    raise InferenceUnavailable(f"Cloudflare Workers AI is not reachable ({type(exc).__name__}).") from exc
            else:
                status = response.status_code
                if status == 429 or status >= 500:
                    if last:
                        if status == 429:
                            raise InferenceUnavailable(f"Cloudflare Workers AI returned 429 for {short} — "
                                                       "the daily free quota may be used up.")
                        raise InferenceUnavailable(f"Cloudflare Workers AI returned {status} for {short}: "
                                                   f"{response.text[:200]}")
                elif status in (401, 403):
                    raise InferenceUnavailable(f"Cloudflare Workers AI rejected the credentials ({status}) — "
                                               "check CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID.")
                elif status >= 400:
                    raise InferenceUnavailable(f"Cloudflare Workers AI returned {status} for {short}: "
                                               f"{response.text[:200]}")
                else:
                    body = response.json()
                    if not body.get("success", True):
                        errors = body.get("errors") or [{"message": "unknown error"}]
                        raise InferenceUnavailable(f"Cloudflare Workers AI error for {short}: {errors[0].get('message')}")
                    return body["result"]
            self.sleep(1.0 * 2 ** (attempt - 1))
        raise AssertionError("unreachable")


def make_cloudflare_client(settings: Settings, env: Mapping[str, str] | None = None,
                           transport: httpx.BaseTransport | None = None) -> CloudflareClient:
    env = os.environ if env is None else env
    cfg = settings.cloudflare
    values = {}
    for name in (cfg.account_id_env, cfg.api_token_env):
        values[name] = env.get(name)
        if not values[name]:
            raise InferenceUnavailable(f"{name} is not set")
    return CloudflareClient(values[cfg.account_id_env], values[cfg.api_token_env], cfg.timeout_s, transport=transport)


class CloudflareEmbedder:
    def __init__(self, client: CloudflareClient, model: str, batch_size: int = 32):
        self.client, self.model, self.batch_size = client, model, batch_size
        self._dimension: int | None = None

    def embed_query(self, question: str) -> list[float]:
        return self.client.run(self.model, {"queries": [question], "instruction": QUERY_TASK})["data"][0]

    def embed_documents(self, texts: list[str], progress: Callable[[int, int], None] | None = None) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = [t or " " for t in texts[start:start + self.batch_size]]  # the API rejects empty strings
            vectors.extend(self.client.run(self.model, {"documents": batch})["data"])
            if progress:
                progress(min(start + self.batch_size, len(texts)), len(texts))
        return vectors

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = len(self.embed_documents(["dimension probe"])[0])
        return self._dimension


class CloudflareReranker:
    def __init__(self, client: CloudflareClient, model: str):
        self.client, self.model = client, model

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        result = self.client.run(self.model, {"query": query, "contexts": [{"text": t or " "} for t in texts],
                                              "top_k": len(texts)})
        scores = [float("-inf")] * len(texts)
        for item in result.get("response", []):
            scores[item["id"]] = float(item["score"])
        return scores
