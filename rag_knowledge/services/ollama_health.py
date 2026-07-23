"""Ollama reachability checks for graph LLM extraction."""
from __future__ import annotations

from rag_knowledge.config import Config
from rag_knowledge.ollama_http import client as ollama_client


class OllamaUnreachableError(RuntimeError):
    """Raised when include_llm requires Ollama but the endpoint is unreachable."""


def assert_ollama_reachable(*, base_url: str | None = None, timeout: float = 5.0) -> str:
    """Probe GET /api/tags. Returns the base URL on success; raises otherwise."""
    url = (base_url or Config().ollama_base_url).rstrip("/")
    try:
        with ollama_client(timeout=timeout) as client:
            resp = client.get(f"{url}/api/tags")
            resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — surface any transport/HTTP failure
        raise OllamaUnreachableError(
            f"Ollama unreachable at {url} (required for --include-llm / graph LLM extract): {exc}"
        ) from exc
    return url
