"""Helpers for tests that need the local Qdrant and Ollama services."""

import httpx
import pytest


def services_available(settings) -> tuple[bool, str]:
    try:
        httpx.get(f"{settings.qdrant.url}/collections", timeout=2).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return False, f"Qdrant not reachable at {settings.qdrant.url}: {exc}"
    try:
        tags = httpx.get(f"{settings.ollama.url}/api/tags", timeout=2).json()
    except Exception as exc:  # noqa: BLE001
        return False, f"Ollama not reachable at {settings.ollama.url}: {exc}"
    names = {m["name"] for m in tags.get("models", [])}
    if settings.ollama.embed_model not in names:
        return False, f"Ollama model {settings.ollama.embed_model} not pulled"
    return True, ""


def require_services(settings):
    ok, reason = services_available(settings)
    if not ok:
        pytest.skip(reason)
