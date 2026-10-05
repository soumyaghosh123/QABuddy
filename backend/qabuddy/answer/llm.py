"""Streaming chat completion: a local Ollama model or an OpenAI-compatible API such as DeepSeek."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Iterator, Mapping, Protocol

import httpx

if TYPE_CHECKING:
    from qabuddy.config import Settings


class LLMUnavailable(RuntimeError):
    pass


class LLMClient(Protocol):
    def stream(self, messages: list[dict]) -> Iterator[str]: ...


class OllamaLLM:
    def __init__(self, url: str, model: str, num_ctx: int = 8192, temperature: float = 0.2,
                 max_tokens: int = 700, timeout_s: float = 300, keep_alive: str = "30m"):
        self.url, self.model = url.rstrip("/"), model
        self.options = {"num_ctx": num_ctx, "temperature": temperature, "num_predict": max_tokens}
        self.timeout, self.keep_alive = timeout_s, keep_alive

    def stream(self, messages: list[dict]) -> Iterator[str]:
        payload = {"model": self.model, "messages": messages, "stream": True, "options": self.options,
                   "keep_alive": self.keep_alive}
        try:
            with httpx.stream("POST", f"{self.url}/api/chat", json=payload,
                              timeout=httpx.Timeout(self.timeout, connect=5)) as response:
                if response.status_code == 404:
                    raise LLMUnavailable(f"Model {self.model!r} is not available. Run: ollama pull {self.model}")
                if response.status_code >= 400:
                    response.read()
                    raise LLMUnavailable(f"Ollama chat failed ({response.status_code}): {response.text[:300]}")
                for line in response.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if "error" in data:
                        raise LLMUnavailable(f"Ollama error: {data['error']}")
                    piece = data.get("message", {}).get("content", "")
                    if piece:
                        yield piece
                    if data.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Ollama is not reachable at {self.url} ({type(exc).__name__}). Start Ollama.") from exc


class OpenAICompatLLM:
    """POST {base_url}/chat/completions with stream=true and yield the answer text.

    Works with DeepSeek and other OpenAI-compatible APIs. Reasoning text
    (delta.reasoning_content) is dropped; only delta.content reaches the user.
    """

    def __init__(self, base_url: str, model: str, api_key: str, temperature: float = 0.2,
                 max_tokens: int = 700, timeout_s: float = 300, extra_body: dict | None = None,
                 key_env: str = "DEEPSEEK_API_KEY", transport: httpx.BaseTransport | None = None):
        self.base_url, self.model, self.api_key, self.key_env = base_url.rstrip("/"), model, api_key, key_env
        self.temperature, self.max_tokens, self.timeout = temperature, max_tokens, timeout_s
        self.extra_body = extra_body or {}
        self.transport = transport

    def stream(self, messages: list[dict]) -> Iterator[str]:
        if not self.api_key:
            raise LLMUnavailable(f"No API key: set the {self.key_env} environment variable, then restart QABuddy.")
        payload = {"model": self.model, "messages": messages, "stream": True,
                   "temperature": self.temperature, "max_tokens": self.max_tokens, **self.extra_body}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            with httpx.Client(transport=self.transport, timeout=httpx.Timeout(self.timeout, connect=10)) as client:
                with client.stream("POST", f"{self.base_url}/chat/completions", json=payload,
                                   headers=headers) as response:
                    if response.status_code >= 400:
                        response.read()
                        raise LLMUnavailable(_http_error(response, self.key_env))
                    for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue  # blank lines and ": keep-alive" comments
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        if "error" in chunk:
                            error = chunk["error"]
                            raise LLMUnavailable(f"LLM API error: {error.get('message', error)}")
                        for choice in chunk.get("choices", []):
                            piece = (choice.get("delta") or {}).get("content")
                            if piece:
                                yield piece
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"LLM API is not reachable at {self.base_url} ({type(exc).__name__}).") from exc


def _http_error(response: httpx.Response, key_env: str) -> str:
    status = response.status_code
    if status in (401, 403):
        return f"LLM API key rejected ({status}). Check {key_env}."
    if status == 402:
        return "LLM API account has insufficient balance (402)."
    return f"LLM API request failed ({status}): {response.text[:300]}"


def make_llm(settings: Settings, env: Mapping[str, str] | None = None) -> LLMClient:
    """Build the answer LLM named by settings.llm.provider."""
    env = os.environ if env is None else env
    o, cfg = settings.ollama, settings.llm
    if cfg.provider == "ollama":
        return OllamaLLM(o.url, o.llm_model, o.num_ctx, o.temperature, o.max_answer_tokens, o.timeout_s)
    if cfg.provider == "deepseek":
        return OpenAICompatLLM(cfg.base_url, cfg.model, env.get(cfg.api_key_env, ""), o.temperature,
                               o.max_answer_tokens, o.timeout_s, cfg.extra_body, cfg.api_key_env)
    raise ValueError(f"Unknown llm.provider {cfg.provider!r}; use 'ollama' or 'deepseek'.")


def llm_status(settings: Settings, env: Mapping[str, str] | None = None) -> dict:
    """Cheap readiness check for /api/health (no API call, so polling costs nothing)."""
    env = os.environ if env is None else env
    cfg = settings.llm
    if cfg.provider == "ollama":
        return {"provider": "ollama", "model": settings.ollama.llm_model, "ok": True}
    status = {"provider": cfg.provider, "model": cfg.model, "ok": bool(env.get(cfg.api_key_env))}
    if not status["ok"]:
        status["error"] = f"{cfg.api_key_env} is not set"
    return status
