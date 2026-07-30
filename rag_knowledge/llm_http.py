"""Multi-provider chat HTTP (ollama / openai-compatible / google Gemini).

Internal message format: [{"role": "system"|"user"|"assistant", "content": str}].
Uses httpx with trust_env=False (same proxy policy as ollama_http).
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from rag_knowledge.ollama_http import async_client, client as http_client

logger = logging.getLogger("rag_knowledge.llm_http")

SUPPORTED_PROVIDERS = frozenset({"ollama", "openai", "google"})


@dataclass(frozen=True)
class ModelEndpoint:
    """Per-role model binding."""

    role: str
    provider: str = "ollama"
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""

    def normalized_provider(self) -> str:
        return (self.provider or "ollama").strip().lower()

    def resolved_base_url(self, default_ollama: str = "") -> str:
        override = (self.base_url or "").strip().rstrip("/")
        if override:
            return override
        provider = self.normalized_provider()
        if provider == "google":
            return "https://generativelanguage.googleapis.com/v1beta"
        if provider == "openai":
            return "https://api.openai.com/v1"
        return (default_ollama or "http://127.0.0.1:11434").rstrip("/")

    def resolved_api_key(self) -> str:
        env_name = (self.api_key_env or "").strip()
        if not env_name:
            provider = self.normalized_provider()
            if provider == "google":
                env_name = "GOOGLE_API_KEY"
            elif provider == "openai":
                env_name = "OPENAI_API_KEY"
            else:
                return ""
        return (os.getenv(env_name) or "").strip()


def _strip_think(text: str) -> str:
    import re

    return re.sub(r"(?is)<think>.*?</think>", "", text or "").strip()


def chat(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str = "",
    temperature: float = 0.0,
    format_json: bool = False,
    timeout: float = 60.0,
    num_predict: int | None = None,
    think: bool | None = False,
) -> str:
    """Non-streaming chat; returns assistant text."""
    provider = endpoint.normalized_provider()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    if provider == "ollama":
        return _chat_ollama(
            endpoint,
            messages,
            default_ollama=default_ollama,
            temperature=temperature,
            format_json=format_json,
            timeout=timeout,
            num_predict=num_predict,
            think=False if think is None else think,
        )
    if provider == "openai":
        return _chat_openai(
            endpoint,
            messages,
            default_ollama=default_ollama,
            temperature=temperature,
            format_json=format_json,
            timeout=timeout,
            num_predict=num_predict,
        )
    return _chat_google(
        endpoint,
        messages,
        default_ollama=default_ollama,
        temperature=temperature,
        format_json=format_json,
        timeout=timeout,
        num_predict=num_predict,
    )


async def achat_stream(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str = "",
    temperature: float = 0.1,
    timeout: float = 600.0,
    num_predict: int | None = 2048,
    think: bool = False,
) -> AsyncIterator[str]:
    """Yield assistant text deltas."""
    provider = endpoint.normalized_provider()
    if provider == "ollama":
        async for part in _astream_ollama(
            endpoint,
            messages,
            default_ollama=default_ollama,
            temperature=temperature,
            timeout=timeout,
            num_predict=num_predict,
            think=think,
        ):
            yield part
        return
    if provider == "openai":
        async for part in _astream_openai(
            endpoint,
            messages,
            default_ollama=default_ollama,
            temperature=temperature,
            timeout=timeout,
            num_predict=num_predict,
        ):
            yield part
        return
    async for part in _astream_google(
        endpoint,
        messages,
        default_ollama=default_ollama,
        temperature=temperature,
        timeout=timeout,
        num_predict=num_predict,
    ):
        yield part


def _chat_ollama(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str,
    temperature: float,
    format_json: bool,
    timeout: float,
    num_predict: int | None,
    think: bool,
) -> str:
    base = endpoint.resolved_base_url(default_ollama)
    options: dict[str, Any] = {"temperature": temperature}
    if num_predict is not None:
        options["num_predict"] = num_predict
    payload: dict[str, Any] = {
        "model": endpoint.model,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": options,
    }
    if format_json:
        payload["format"] = "json"
    with http_client(timeout=timeout) as client:
        resp = client.post(f"{base}/api/chat", json=payload)
        resp.raise_for_status()
        message = resp.json().get("message") or {}
        content = (message.get("content") or "").strip()
        if not content:
            content = (message.get("thinking") or "").strip()
        return _strip_think(content)


def _chat_openai(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str,
    temperature: float,
    format_json: bool,
    timeout: float,
    num_predict: int | None,
) -> str:
    base = endpoint.resolved_base_url(default_ollama)
    api_key = endpoint.resolved_api_key()
    if not api_key:
        raise ValueError(
            f"api key missing for openai endpoint role={endpoint.role} "
            f"(set {endpoint.api_key_env or 'OPENAI_API_KEY'})"
        )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {
        "model": endpoint.model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if num_predict is not None:
        payload["max_tokens"] = num_predict
    if format_json:
        payload["response_format"] = {"type": "json_object"}
    with http_client(timeout=timeout) as client:
        resp = client.post(f"{base}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        choices = resp.json().get("choices") or []
        if not choices:
            return ""
        return _strip_think((choices[0].get("message") or {}).get("content") or "")


def _google_url(base: str, model: str, method: str, api_key: str) -> str:
    # base like https://generativelanguage.googleapis.com/v1beta
    root = base.rstrip("/")
    if not root.endswith("/v1beta") and "/models" not in root:
        # allow either full v1beta root or host-only
        if root.endswith("googleapis.com"):
            root = f"{root}/v1beta"
    qs = urlencode({"key": api_key})
    return f"{root}/models/{model}:{method}?{qs}"


def _to_google_body(
    messages: list[dict[str, Any]],
    *,
    temperature: float,
    format_json: bool,
    num_predict: int | None,
) -> dict[str, Any]:
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for msg in messages:
        role = (msg.get("role") or "user").lower()
        text = msg.get("content") or ""
        if role == "system":
            system_parts.append(str(text))
            continue
        g_role = "model" if role == "assistant" else "user"
        contents.append({"role": g_role, "parts": [{"text": str(text)}]})
    body: dict[str, Any] = {"contents": contents}
    if system_parts:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    gen: dict[str, Any] = {"temperature": temperature}
    if num_predict is not None:
        gen["maxOutputTokens"] = num_predict
    if format_json:
        gen["responseMimeType"] = "application/json"
    body["generationConfig"] = gen
    return body


def _chat_google(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str,
    temperature: float,
    format_json: bool,
    timeout: float,
    num_predict: int | None,
) -> str:
    base = endpoint.resolved_base_url(default_ollama)
    api_key = endpoint.resolved_api_key()
    if not api_key:
        raise ValueError(
            f"api key missing for google endpoint role={endpoint.role} "
            f"(set {endpoint.api_key_env or 'GOOGLE_API_KEY'})"
        )
    url = _google_url(base, endpoint.model, "generateContent", api_key)
    body = _to_google_body(
        messages, temperature=temperature, format_json=format_json, num_predict=num_predict
    )
    with http_client(timeout=timeout) as client:
        resp = client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    texts = [str(p.get("text") or "") for p in parts if isinstance(p, dict)]
    return _strip_think("".join(texts))


async def _astream_ollama(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str,
    temperature: float,
    timeout: float,
    num_predict: int | None,
    think: bool,
) -> AsyncIterator[str]:
    base = endpoint.resolved_base_url(default_ollama)
    options: dict[str, Any] = {
        "temperature": temperature,
        "top_p": 0.9,
        "top_k": 40,
    }
    if num_predict is not None:
        options["num_predict"] = num_predict
    if think:
        options["thinking"] = True
    payload = {
        "model": endpoint.model,
        "messages": messages,
        "stream": True,
        "options": options,
    }
    async with async_client(base_url=base, timeout=timeout) as client:
        async with client.stream("POST", "/api/chat", json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(f"ollama stream HTTP {resp.status_code}: {body[:300]!r}")
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = chunk.get("message") or {}
                piece = msg.get("content") or ""
                if piece:
                    yield piece


async def _astream_openai(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str,
    temperature: float,
    timeout: float,
    num_predict: int | None,
) -> AsyncIterator[str]:
    base = endpoint.resolved_base_url(default_ollama)
    api_key = endpoint.resolved_api_key()
    if not api_key:
        raise ValueError("OPENAI_API_KEY (or api_key_env) required for openai stream")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {
        "model": endpoint.model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if num_predict is not None:
        payload["max_tokens"] = num_predict
    async with async_client(timeout=timeout) as client:
        async with client.stream(
            "POST", f"{base}/chat/completions", headers=headers, json=payload
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(f"openai stream HTTP {resp.status_code}: {body[:300]!r}")
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content") or ""
                if piece:
                    yield piece


async def _astream_google(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str,
    temperature: float,
    timeout: float,
    num_predict: int | None,
) -> AsyncIterator[str]:
    base = endpoint.resolved_base_url(default_ollama)
    api_key = endpoint.resolved_api_key()
    if not api_key:
        raise ValueError("GOOGLE_API_KEY (or api_key_env) required for google stream")
    url = _google_url(base, endpoint.model, "streamGenerateContent", api_key)
    # alt=sse for Server-Sent Events
    if "alt=" not in url:
        url = url + ("&" if "?" in url else "?") + "alt=sse"
    body = _to_google_body(
        messages, temperature=temperature, format_json=False, num_predict=num_predict
    )
    async with async_client(timeout=timeout) as client:
        async with client.stream("POST", url, json=body) as resp:
            if resp.status_code != 200:
                raw = await resp.aread()
                raise RuntimeError(f"google stream HTTP {resp.status_code}: {raw[:300]!r}")
            async for line in resp.aiter_lines():
                if not line:
                    continue
                data = line[5:].strip() if line.startswith("data:") else line.strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                candidates = chunk.get("candidates") or []
                if not candidates:
                    continue
                parts = ((candidates[0].get("content") or {}).get("parts")) or []
                for part in parts:
                    if isinstance(part, dict) and part.get("text"):
                        yield str(part["text"])


def chat_role(cfg: Any, role: str, messages: list[dict[str, Any]], **kwargs: Any) -> str:
    """Resolve Config.endpoint_for(role) then chat()."""
    endpoint = cfg.endpoint_for(role)
    return chat(
        endpoint,
        messages,
        default_ollama=getattr(cfg, "ollama_base_url", ""),
        **kwargs,
    )


def _guess_mime(image_b64: str, mime_type: str | None) -> str:
    if mime_type:
        return mime_type
    # Ollama / callers often omit mime; jpeg is a safe default for vision APIs.
    return "image/jpeg"


def chat_vision(
    endpoint: ModelEndpoint,
    prompt: str,
    image_b64: str,
    *,
    default_ollama: str = "",
    mime_type: str | None = None,
    temperature: float = 0.0,
    timeout: float = 180.0,
    num_predict: int | None = None,
) -> str:
    """Non-streaming vision chat (text prompt + one base64 image)."""
    provider = endpoint.normalized_provider()
    mime = _guess_mime(image_b64, mime_type)
    if provider == "ollama":
        messages = [{
            "role": "user",
            "content": prompt,
            "images": [image_b64],
        }]
        return _chat_ollama(
            endpoint,
            messages,
            default_ollama=default_ollama,
            temperature=temperature,
            format_json=False,
            timeout=timeout,
            num_predict=num_predict,
            think=False,
        )
    if provider == "openai":
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                },
            ],
        }]
        return _chat_openai(
            endpoint,
            messages,
            default_ollama=default_ollama,
            temperature=temperature,
            format_json=False,
            timeout=timeout,
            num_predict=num_predict,
        )
    if provider == "google":
        return _chat_google_vision(
            endpoint,
            prompt,
            image_b64,
            mime=mime,
            default_ollama=default_ollama,
            temperature=temperature,
            timeout=timeout,
            num_predict=num_predict,
        )
    raise ValueError(f"unsupported provider: {provider}")


def _chat_google_vision(
    endpoint: ModelEndpoint,
    prompt: str,
    image_b64: str,
    *,
    mime: str,
    default_ollama: str,
    temperature: float,
    timeout: float,
    num_predict: int | None,
) -> str:
    base = endpoint.resolved_base_url(default_ollama)
    api_key = endpoint.resolved_api_key()
    if not api_key:
        raise ValueError(
            f"api key missing for google endpoint role={endpoint.role} "
            f"(set {endpoint.api_key_env or 'GOOGLE_API_KEY'})"
        )
    url = _google_url(base, endpoint.model, "generateContent", api_key)
    body: dict[str, Any] = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": image_b64}},
            ],
        }],
        "generationConfig": {"temperature": temperature},
    }
    if num_predict is not None:
        body["generationConfig"]["maxOutputTokens"] = num_predict
    with http_client(timeout=timeout) as client:
        resp = client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    texts = [str(p.get("text") or "") for p in parts if isinstance(p, dict)]
    return _strip_think("".join(texts))


async def achat_vision_stream(
    endpoint: ModelEndpoint,
    prompt: str,
    image_b64: str,
    *,
    default_ollama: str = "",
    mime_type: str | None = None,
    temperature: float = 0.1,
    timeout: float = 120.0,
    num_predict: int | None = None,
) -> AsyncIterator[str]:
    """Streaming vision chat."""
    provider = endpoint.normalized_provider()
    mime = _guess_mime(image_b64, mime_type)
    if provider == "ollama":
        messages = [{
            "role": "user",
            "content": prompt,
            "images": [image_b64],
        }]
        async for piece in _astream_ollama(
            endpoint,
            messages,
            default_ollama=default_ollama,
            temperature=temperature,
            timeout=timeout,
            num_predict=num_predict,
            think=False,
        ):
            yield piece
        return
    if provider == "openai":
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                },
            ],
        }]
        async for piece in _astream_openai(
            endpoint,
            messages,
            default_ollama=default_ollama,
            temperature=temperature,
            timeout=timeout,
            num_predict=num_predict,
        ):
            yield piece
        return
    if provider == "google":
        async for piece in _astream_google_vision(
            endpoint,
            prompt,
            image_b64,
            mime=mime,
            default_ollama=default_ollama,
            temperature=temperature,
            timeout=timeout,
            num_predict=num_predict,
        ):
            yield piece
        return
    raise ValueError(f"unsupported provider: {provider}")


async def _astream_google_vision(
    endpoint: ModelEndpoint,
    prompt: str,
    image_b64: str,
    *,
    mime: str,
    default_ollama: str,
    temperature: float,
    timeout: float,
    num_predict: int | None,
) -> AsyncIterator[str]:
    base = endpoint.resolved_base_url(default_ollama)
    api_key = endpoint.resolved_api_key()
    if not api_key:
        raise ValueError("GOOGLE_API_KEY (or api_key_env) required for google vision stream")
    url = _google_url(base, endpoint.model, "streamGenerateContent", api_key)
    if "alt=" not in url:
        url = url + ("&" if "?" in url else "?") + "alt=sse"
    body: dict[str, Any] = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": image_b64}},
            ],
        }],
        "generationConfig": {"temperature": temperature},
    }
    if num_predict is not None:
        body["generationConfig"]["maxOutputTokens"] = num_predict
    async with async_client(timeout=timeout) as client:
        async with client.stream("POST", url, json=body) as resp:
            if resp.status_code != 200:
                raw = await resp.aread()
                raise RuntimeError(f"google vision stream HTTP {resp.status_code}: {raw[:300]!r}")
            async for line in resp.aiter_lines():
                if not line:
                    continue
                data = line[5:].strip() if line.startswith("data:") else line.strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                candidates = chunk.get("candidates") or []
                if not candidates:
                    continue
                parts = ((candidates[0].get("content") or {}).get("parts")) or []
                for part in parts:
                    if isinstance(part, dict) and part.get("text"):
                        yield str(part["text"])
