import json

import httpx
import pytest

from qabuddy.answer.llm import LLMUnavailable, OllamaLLM, OpenAICompatLLM, llm_status, make_llm
from qabuddy.config import Settings

SSE_BODY = "\n".join([
    ": keep-alive",
    "",
    'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}',
    'data: {"choices":[{"delta":{"reasoning_content":"thinking..."}}]}',
    'data: {"choices":[{"delta":{"content":"Login fails "}}]}',
    'data: {"choices":[{"delta":{"content":"because of IP blocking [1]."},"finish_reason":"stop"}]}',
    "data: [DONE]",
    "",
])


def _client(handler, key="sk-test", **kwargs):
    return OpenAICompatLLM("https://api.deepseek.com/", "deepseek-flash", key,
                           transport=httpx.MockTransport(handler), **kwargs)


def test_openai_compat_streams_answer_text_only():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=SSE_BODY, headers={"content-type": "text/event-stream"})

    llm = _client(handler, temperature=0.2, max_tokens=700, extra_body={"thinking": {"type": "disabled"}})
    out = "".join(llm.stream([{"role": "user", "content": "q?"}]))

    assert out == "Login fails because of IP blocking [1]."
    assert seen["url"] == "https://api.deepseek.com/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert body["model"] == "deepseek-flash" and body["stream"] is True
    assert body["temperature"] == 0.2 and body["max_tokens"] == 700
    assert body["thinking"] == {"type": "disabled"}
    assert body["messages"] == [{"role": "user", "content": "q?"}]


def test_openai_compat_missing_key_names_the_env_var():
    llm = _client(lambda r: httpx.Response(200), key="", key_env="DEEPSEEK_API_KEY")
    with pytest.raises(LLMUnavailable, match="DEEPSEEK_API_KEY"):
        list(llm.stream([{"role": "user", "content": "q?"}]))


@pytest.mark.parametrize("status, phrase", [(401, "rejected"), (402, "balance"), (500, "500")])
def test_openai_compat_http_errors(status, phrase):
    llm = _client(lambda r: httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(LLMUnavailable, match=phrase):
        list(llm.stream([{"role": "user", "content": "q?"}]))


def test_openai_compat_error_inside_stream():
    body = 'data: {"error":{"message":"overloaded"}}\n\n'
    llm = _client(lambda r: httpx.Response(200, text=body))
    with pytest.raises(LLMUnavailable, match="overloaded"):
        list(llm.stream([{"role": "user", "content": "q?"}]))


def test_openai_compat_unreachable():
    def handler(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(LLMUnavailable, match="not reachable"):
        list(_client(handler).stream([{"role": "user", "content": "q?"}]))


def _settings(provider, **llm):
    return Settings.model_validate({"llm": {"provider": provider, **llm}})


def test_make_llm_selects_provider():
    deepseek = make_llm(_settings("deepseek"), env={"DEEPSEEK_API_KEY": "sk-x"})
    assert isinstance(deepseek, OpenAICompatLLM)
    assert deepseek.model == "deepseek-flash" and deepseek.api_key == "sk-x"
    assert deepseek.extra_body == {"thinking": {"type": "disabled"}}

    assert isinstance(make_llm(_settings("ollama"), env={}), OllamaLLM)

    with pytest.raises(ValueError, match="provider"):
        make_llm(_settings("nonsense"), env={})


def test_llm_status():
    assert llm_status(_settings("deepseek"), env={"DEEPSEEK_API_KEY": "sk-x"}) == \
        {"provider": "deepseek", "model": "deepseek-flash", "ok": True}
    missing = llm_status(_settings("deepseek"), env={})
    assert missing["ok"] is False and "DEEPSEEK_API_KEY" in missing["error"]
    # For Ollama the model check happens against /api/tags in the health route.
    assert llm_status(_settings("ollama"), env={})["provider"] == "ollama"
