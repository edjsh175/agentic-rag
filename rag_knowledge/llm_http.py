"""Multi-provider chat HTTP (ollama / openai-compatible / google Gemini).

Internal message format: [{"role": "system"|"user"|"assistant", "content": str}].
Uses httpx with trust_env=False (same proxy policy as ollama_http).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextvars import ContextVar
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from rag_knowledge.ollama_http import async_client, client as http_client

logger = logging.getLogger("rag_knowledge.llm_http")

SUPPORTED_PROVIDERS = frozenset({"ollama", "openai", "google"})

# ---------------------------------------------------------------------------
# 并发控制：每个 endpoint.role 对应独立的信号量，限制同时进入底层 HTTP 的并发数
# ---------------------------------------------------------------------------
_sync_semaphores: dict[str, threading.Semaphore] = {}
_sync_sem_lock = threading.Lock()

_async_semaphores: dict[str, asyncio.Semaphore] = {}
_async_sem_lock = threading.Lock()

_model_call_audit: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "model_call_audit", default=None
)


def clear_model_call_audit() -> None:
    """Start a request-local model call audit."""
    _model_call_audit.set([])


def get_model_call_audit() -> list[dict[str, Any]]:
    """Return a copy of the current request-local model call audit."""
    return [dict(item) for item in (_model_call_audit.get() or [])]


def record_model_call(
    *,
    role: str,
    stage: str,
    provider: str,
    model: str,
    elapsed_ms: float | None = None,
    fallback: str | None = None,
    prompt_version: str = "v1",
) -> None:
    records = _model_call_audit.get()
    if records is None:
        return
    records.append({
        "call_id": f"llmcall_{len(records) + 1:04d}",
        "stage": stage,
        "role": role,
        "provider": provider,
        "model": model,
        "prompt_version": prompt_version,
        "elapsed_ms": round(float(elapsed_ms or 0.0), 1),
        "fallback": fallback,
    })


def _get_sync_semaphore(role: str, limit: int) -> threading.Semaphore:
    key = f"{role}:{limit}"
    with _sync_sem_lock:
        if key not in _sync_semaphores:
            _sync_semaphores[key] = threading.Semaphore(limit)
        return _sync_semaphores[key]


def _get_async_semaphore(role: str, limit: int) -> asyncio.Semaphore:
    """Return an asyncio.Semaphore bound to the *current* running event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    key = f"{role}:{limit}:{id(loop)}"
    with _async_sem_lock:
        existing = _async_semaphores.get(key)
        # Discard stale semaphores from closed loops
        if existing is not None:
            return existing
        sem = asyncio.Semaphore(limit)
        _async_semaphores[key] = sem
        return sem


# ---------------------------------------------------------------------------
# 可重试异常判定
# ---------------------------------------------------------------------------
_RETRYABLE_NETWORK = (
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
)


def _is_retryable_status(exc: BaseException) -> bool:
    """Return True when an httpx.HTTPStatusError has a retryable status code."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


def _should_retry(exc: BaseException) -> bool:
    return isinstance(exc, _RETRYABLE_NETWORK) or _is_retryable_status(exc)


def _sync_retry(func, *, max_retries: int, role: str):
    """Call *func()* up to ``max_retries+1`` times with exponential back-off."""
    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001
            if attempt == max_retries or not _should_retry(exc):
                raise
            logger.warning(
                "llm_http [%s] attempt %d/%d failed (%s: %s); retry in %.1fs",
                role, attempt + 1, max_retries + 1, type(exc).__name__, exc, delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, 16.0)



@dataclass(frozen=True)
class LLMStreamPart:
    """One provider-native stream delta, separated by semantic channel."""

    kind: str
    delta: str

    def __post_init__(self) -> None:
        if self.kind not in {"reasoning", "content"}:
            raise ValueError(f"unsupported stream part kind: {self.kind}")


@dataclass(frozen=True)
class ModelEndpoint:
    """Per-role model binding."""

    role: str
    provider: str = "ollama"
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""
    max_retries: int = 3
    concurrency_limit: int = 5

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


@dataclass(frozen=True)
class NativeReasoningCapability:
    """Provider capability for requesting and consuming a native reasoning channel."""

    can_request: bool
    can_stream: bool


def native_reasoning_capability(
    endpoint: ModelEndpoint,
    *,
    default_ollama: str = "",
) -> NativeReasoningCapability:
    """Return the single source of truth for native reasoning support.

    Stream parsers always preserve provider reasoning fields when present.  The
    request flag is deliberately narrower: it is only sent to endpoints whose
    documented protocol accepts it.
    """
    provider = endpoint.normalized_provider()
    model_name = str(endpoint.model or "").lower()
    if provider == "ollama":
        is_reasoning_model = any(
            pattern in model_name for pattern in ("qwen3", "deepseek-r1", "-r1", ":r1", "r1-")
        )
        return NativeReasoningCapability(
            can_request=is_reasoning_model,
            can_stream=True,
        )
    if provider == "openai":
        base = endpoint.resolved_base_url(default_ollama).lower()
        return NativeReasoningCapability(
            can_request="api.deepseek.com" in base or "deepseek-r1" in model_name,
            can_stream=True,
        )
    return NativeReasoningCapability(can_request=False, can_stream=False)


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
    json_schema: dict[str, Any] | None = None,
    timeout: float = 60.0,
    num_predict: int | None = None,
    num_ctx: int | None = None,
    think: bool | None = False,
) -> str:
    """Non-streaming chat; returns assistant text.

    Concurrency is throttled per ``endpoint.role`` using a threading.Semaphore
    (``endpoint.concurrency_limit``).  Transient network/server errors are
    automatically retried up to ``endpoint.max_retries`` times with exponential
    back-off starting at 1 second.

    ``num_ctx`` is Ollama-only (ignored by OpenAI/Google). When unset, Ollama
    keeps its default (typically 4096), which can silently truncate long prompts.
    """
    provider = endpoint.normalized_provider()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")

    sem = _get_sync_semaphore(endpoint.role, endpoint.concurrency_limit)

    def _dispatch() -> str:
        if provider == "ollama":
            return _chat_ollama(
                endpoint,
                messages,
                default_ollama=default_ollama,
                temperature=temperature,
                format_json=format_json,
                json_schema=json_schema,
                timeout=timeout,
                num_predict=num_predict,
                num_ctx=num_ctx,
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
                think=False if think is None else think,
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

    with sem:
        return _sync_retry(_dispatch, max_retries=endpoint.max_retries, role=endpoint.role)


async def achat_stream_parts(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str = "",
    temperature: float = 0.1,
    timeout: float = 600.0,
    num_predict: int | None = 2048,
    think: bool = False,
    num_ctx: int | None = None,
    format_json: bool = False,
    json_schema: dict[str, Any] | None = None,
) -> AsyncIterator[LLMStreamPart]:
    """Yield provider output with reasoning and content on separate channels."""
    provider = endpoint.normalized_provider()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    sem = _get_async_semaphore(endpoint.role, endpoint.concurrency_limit)

    delay = 1.0
    emitted_any = False
    for attempt in range(endpoint.max_retries + 1):
        try:
            async with sem:
                if provider == "ollama":
                    stream = _astream_ollama(
                        endpoint,
                        messages,
                        default_ollama=default_ollama,
                        temperature=temperature,
                        timeout=timeout,
                        num_predict=num_predict,
                        think=think,
                        num_ctx=num_ctx,
                        format_json=format_json,
                        json_schema=json_schema,
                    )
                elif provider == "openai":
                    stream = _astream_openai(
                        endpoint,
                        messages,
                        default_ollama=default_ollama,
                        temperature=temperature,
                        timeout=timeout,
                        num_predict=num_predict,
                        think=think,
                        format_json=format_json,
                    )
                else:
                    stream = _astream_google(
                        endpoint,
                        messages,
                        default_ollama=default_ollama,
                        temperature=temperature,
                        timeout=timeout,
                        num_predict=num_predict,
                        format_json=format_json,
                    )
                async for part in stream:
                    emitted_any = True
                    yield part
                return
        except Exception as exc:  # noqa: BLE001
            # Once any streaming output has escaped, the generation is no longer
            # replay-safe. Retrying from the beginning would duplicate partial
            # reasoning/content and merge multiple attempts into one response.
            if emitted_any or attempt == endpoint.max_retries or not _should_retry(exc):
                raise
            logger.warning(
                "llm_http [%s] stream attempt %d/%d failed (%s: %s); retry in %.1fs",
                endpoint.role, attempt + 1, endpoint.max_retries + 1,
                type(exc).__name__, exc, delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 16.0)


async def achat_stream(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str = "",
    temperature: float = 0.1,
    timeout: float = 600.0,
    num_predict: int | None = 2048,
    think: bool = False,
    num_ctx: int | None = None,
    format_json: bool = False,
    json_schema: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """Compatibility stream that serializes reasoning as ``<think>`` blocks."""
    in_reasoning = False
    async for part in achat_stream_parts(
        endpoint,
        messages,
        default_ollama=default_ollama,
        temperature=temperature,
        timeout=timeout,
        num_predict=num_predict,
        think=think,
        num_ctx=num_ctx,
        format_json=format_json,
        json_schema=json_schema,
    ):
        if part.kind == "reasoning":
            if not in_reasoning:
                yield "<think>"
                in_reasoning = True
            yield part.delta
            continue
        if in_reasoning:
            yield "</think>"
            in_reasoning = False
        yield part.delta
    if in_reasoning:
        yield "</think>"


def _resolve_default_num_ctx(num_ctx: int | None) -> int:
    if num_ctx is not None and num_ctx > 0:
        return num_ctx
    env_val = os.environ.get("OLLAMA_NUM_CTX")
    if env_val and env_val.isdigit():
        return int(env_val)
    try:
        from rag_knowledge.config import Config
        cfg = Config()
        return int(getattr(cfg.context_budget, "context_window", 32768) or 32768)
    except Exception:
        return 32768


def _chat_ollama(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str,
    temperature: float,
    format_json: bool,
    json_schema: dict[str, Any] | None = None,
    timeout: float,
    num_predict: int | None,
    think: bool,
    num_ctx: int | None = None,
) -> str:
    base = endpoint.resolved_base_url(default_ollama)
    options: dict[str, Any] = {"temperature": temperature}
    if num_predict is not None:
        options["num_predict"] = num_predict
    options["num_ctx"] = _resolve_default_num_ctx(num_ctx)
    payload: dict[str, Any] = {
        "model": endpoint.model,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": options,
    }
    if json_schema is not None:
        payload["format"] = json_schema
    elif format_json:
        payload["format"] = "json"
    with http_client(timeout=timeout) as client:
        resp = client.post(f"{base}/api/chat", json=payload)
        resp.raise_for_status()
        message = resp.json().get("message") or {}
        content = (message.get("content") or "").strip()
        if not content:
            content = (message.get("thinking") or "").strip()
        return _strip_think(content)


def _set_deepseek_thinking(payload: dict[str, Any], base: str, think: bool) -> None:
    """Add DeepSeek's non-standard thinking fields only to its official API."""
    if "api.deepseek.com" not in base.lower():
        return
    payload["thinking"] = {"type": "enabled" if think else "disabled"}
    if think:
        # Flash maps low effort to its lower-cost reasoning path.
        payload["reasoning_effort"] = "low"


def _chat_openai(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str,
    temperature: float,
    format_json: bool,
    timeout: float,
    num_predict: int | None,
    think: bool = False,
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
    _set_deepseek_thinking(payload, base, think)
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
    num_ctx: int | None = None,
    format_json: bool = False,
    json_schema: dict[str, Any] | None = None,
) -> AsyncIterator[LLMStreamPart]:
    base = endpoint.resolved_base_url(default_ollama)
    options: dict[str, Any] = {
        "temperature": temperature,
        "top_p": 0.9,
        "top_k": 40,
    }
    if num_predict is not None:
        options["num_predict"] = num_predict
    options["num_ctx"] = _resolve_default_num_ctx(num_ctx)
    if think:
        options["thinking"] = True
    payload: dict[str, Any] = {
        "model": endpoint.model,
        "messages": messages,
        "stream": True,
        # Ollama's reasoning-capable models may enable thinking by default.
        # Send the flag explicitly so ``think=False`` cannot be ignored.
        "think": bool(think),
        "options": options,
    }
    if json_schema is not None:
        payload["format"] = json_schema
    elif format_json:
        payload["format"] = "json"
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
                thinking_piece = msg.get("thinking") or ""
                content_piece = msg.get("content") or ""
                if thinking_piece:
                    yield LLMStreamPart("reasoning", str(thinking_piece))
                if content_piece:
                    yield LLMStreamPart("content", str(content_piece))


async def _astream_openai(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str,
    temperature: float,
    timeout: float,
    num_predict: int | None,
    think: bool = False,
    format_json: bool = False,
) -> AsyncIterator[LLMStreamPart]:
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
    if format_json:
        payload["response_format"] = {"type": "json_object"}
    _set_deepseek_thinking(payload, base, think)
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
                reasoning_piece = delta.get("reasoning_content") or delta.get("reasoning") or ""
                content_piece = delta.get("content") or ""
                if reasoning_piece:
                    yield LLMStreamPart("reasoning", str(reasoning_piece))
                if content_piece:
                    yield LLMStreamPart("content", str(content_piece))


async def _astream_google(
    endpoint: ModelEndpoint,
    messages: list[dict[str, Any]],
    *,
    default_ollama: str,
    temperature: float,
    timeout: float,
    num_predict: int | None,
    format_json: bool = False,
) -> AsyncIterator[LLMStreamPart]:
    base = endpoint.resolved_base_url(default_ollama)
    api_key = endpoint.resolved_api_key()
    if not api_key:
        raise ValueError("GOOGLE_API_KEY (or api_key_env) required for google stream")
    url = _google_url(base, endpoint.model, "streamGenerateContent", api_key)
    # alt=sse for Server-Sent Events
    if "alt=" not in url:
        url = url + ("&" if "?" in url else "?") + "alt=sse"
    body = _to_google_body(
        messages, temperature=temperature, format_json=format_json, num_predict=num_predict
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
                    if isinstance(part, dict):
                        thought = part.get("thought") or ""
                        text = part.get("text") or ""
                        if thought:
                            yield LLMStreamPart("reasoning", str(thought))
                        if text:
                            yield LLMStreamPart("content", str(text))


def chat_role(cfg: Any, role: str, messages: list[dict[str, Any]], **kwargs: Any) -> str:
    """Resolve Config.endpoint_for(role) then chat()."""
    stage = str(kwargs.pop("stage", "") or "").strip()
    prompt_version = str(kwargs.pop("prompt_version", "v1") or "v1")
    endpoint = cfg.endpoint_for(role)
    if "num_ctx" not in kwargs or kwargs["num_ctx"] is None:
        budget_win = getattr(getattr(cfg, "context_budget", None), "context_window", None)
        if budget_win is not None:
            kwargs["num_ctx"] = int(budget_win)
        elif getattr(cfg, "context_window", None) is not None:
            kwargs["num_ctx"] = int(cfg.context_window)
    started = time.perf_counter()
    fallback = None
    try:
        return chat(
            endpoint,
            messages,
            default_ollama=getattr(cfg, "ollama_base_url", ""),
            **kwargs,
        )
    except Exception as exc:
        fallback = type(exc).__name__
        raise
    finally:
        record_model_call(
            role=role,
            stage=stage or {
                "helper_llm": "helper_call",
                "llm": "main_call",
            }.get(role, "model_call"),
            provider=endpoint.normalized_provider(),
            model=endpoint.model,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            fallback=fallback,
            prompt_version=prompt_version,
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
    """Non-streaming vision chat (text prompt + one base64 image).

    Concurrency is throttled per ``endpoint.role`` and retried up to
    ``endpoint.max_retries`` times on transient network/server errors.
    """
    provider = endpoint.normalized_provider()
    mime = _guess_mime(image_b64, mime_type)
    sem = _get_sync_semaphore(endpoint.role, endpoint.concurrency_limit)

    def _dispatch() -> str:
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

    with sem:
        return _sync_retry(_dispatch, max_retries=endpoint.max_retries, role=endpoint.role)


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
    """Streaming vision chat.

    Connection phase retried up to ``endpoint.max_retries`` times with exponential
    back-off.  Concurrent calls are throttled by ``endpoint.concurrency_limit``.
    """
    provider = endpoint.normalized_provider()
    mime = _guess_mime(image_b64, mime_type)
    sem = _get_async_semaphore(endpoint.role, endpoint.concurrency_limit)

    delay = 1.0
    for attempt in range(endpoint.max_retries + 1):
        try:
            async with sem:
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
        except Exception as exc:  # noqa: BLE001
            if attempt == endpoint.max_retries or not _should_retry(exc):
                raise
            logger.warning(
                "llm_http [%s] vision stream attempt %d/%d failed (%s: %s); retry in %.1fs",
                endpoint.role, attempt + 1, endpoint.max_retries + 1,
                type(exc).__name__, exc, delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 16.0)


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
