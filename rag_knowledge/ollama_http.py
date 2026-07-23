"""httpx helpers for Ollama that never use the process/system HTTP proxy.

Windows IE/system proxies (e.g. 127.0.0.1:xxxx) often return an empty HTTP 502
for LAN Ollama hosts. Config also sets NO_PROXY, but trust_env=False is the
hard guarantee for Ollama-bound clients.
"""
from __future__ import annotations

import httpx

# Passed through to langchain_ollama ChatOllama / OllamaEmbeddings.
OLLAMA_CLIENT_KWARGS: dict = {"trust_env": False}


def client(**kwargs) -> httpx.Client:
    kwargs.setdefault("trust_env", False)
    return httpx.Client(**kwargs)


def async_client(**kwargs) -> httpx.AsyncClient:
    kwargs.setdefault("trust_env", False)
    return httpx.AsyncClient(**kwargs)


def post(url: str, **kwargs) -> httpx.Response:
    kwargs.setdefault("trust_env", False)
    return httpx.post(url, **kwargs)


def get(url: str, **kwargs) -> httpx.Response:
    kwargs.setdefault("trust_env", False)
    return httpx.get(url, **kwargs)
