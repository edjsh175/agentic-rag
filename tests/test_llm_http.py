"""Unit tests for multi-provider llm_http (ollama / openai / google)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rag_knowledge.config import Config
from rag_knowledge.llm_http import ModelEndpoint, achat_stream, chat


class _FakeResp:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, capture: list, payload: dict):
        self._capture = capture
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url: str, json=None, headers=None, **kwargs):
        self._capture.append({"url": url, "json": json, "headers": headers or {}})
        return _FakeResp(self._payload)


class _FakeAsyncResponse:
    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aread(self):
        return b""

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    def __init__(self, capture: list, lines: list[str]):
        self._capture = capture
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method: str, url: str, json=None):
        self._capture.append({"method": method, "url": url, "json": json})
        return _FakeAsyncResponse(self._lines)


def test_model_endpoint_defaults():
    ollama = ModelEndpoint(role="llm", provider="ollama", model="m")
    assert ollama.resolved_base_url("http://host:11434") == "http://host:11434"

    google = ModelEndpoint(role="llm", provider="google", model="gemini-2.0-flash")
    assert google.resolved_base_url("") == "https://generativelanguage.googleapis.com/v1beta"

    openai = ModelEndpoint(role="llm", provider="openai", model="gpt-4o-mini")
    assert openai.resolved_base_url("") == "https://api.openai.com/v1"


def test_chat_ollama_payload(monkeypatch):
    capture: list = []
    monkeypatch.setattr(
        "rag_knowledge.llm_http.http_client",
        lambda **kw: _FakeClient(
            capture, {"message": {"content": "hello-ollama"}}
        ),
    )
    ep = ModelEndpoint(role="llm", provider="ollama", model="qwen3:30b", base_url="http://x:1")
    text = chat(ep, [{"role": "user", "content": "hi"}], temperature=0.0)
    assert text == "hello-ollama"
    assert capture[0]["url"] == "http://x:1/api/chat"
    assert capture[0]["json"]["model"] == "qwen3:30b"
    assert capture[0]["json"]["stream"] is False


def test_chat_ollama_accepts_json_schema_structured_output(monkeypatch):
    capture: list = []
    monkeypatch.setattr(
        "rag_knowledge.llm_http.http_client",
        lambda **kw: _FakeClient(capture, {"message": {"content": "{}"}}),
    )
    schema = {
        "type": "object",
        "properties": {"verdict": {"type": "string"}},
        "required": ["verdict"],
    }
    ep = ModelEndpoint(role="helper_llm", provider="ollama", model="qwen3.5:4b", base_url="http://x:1")

    chat(
        ep,
        [{"role": "user", "content": "hi"}],
        format_json=True,
        json_schema=schema,
    )

    assert capture[0]["json"]["format"] == schema


def test_chat_ollama_passes_num_ctx_and_num_predict(monkeypatch):
    capture: list = []
    monkeypatch.setattr(
        "rag_knowledge.llm_http.http_client",
        lambda **kw: _FakeClient(
            capture, {"message": {"content": "{}"}}
        ),
    )
    ep = ModelEndpoint(role="graph_extraction", provider="ollama", model="qwen3.5:9b", base_url="http://x:1")
    chat(
        ep,
        [{"role": "user", "content": "hi"}],
        format_json=True,
        num_ctx=16384,
        num_predict=2048,
    )
    options = capture[0]["json"]["options"]
    assert options["num_ctx"] == 16384
    assert options["num_predict"] == 2048
    assert capture[0]["json"]["format"] == "json"


def test_ollama_stream_explicitly_disables_thinking(monkeypatch):
    capture: list = []
    monkeypatch.setattr(
        "rag_knowledge.llm_http.async_client",
        lambda **kwargs: _FakeAsyncClient(
            capture,
            ['{"message":{"content":"answer"},"done":false}'],
        ),
    )
    ep = ModelEndpoint(role="llm", provider="ollama", model="qwen3.5:9b", base_url="http://x:1")

    async def collect():
        return [
            part
            async for part in achat_stream(
                ep,
                [{"role": "user", "content": "hi"}],
                think=False,
            )
        ]

    assert asyncio.run(collect()) == ["answer"]
    assert capture[0]["json"]["think"] is False


def test_chat_openai_payload(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    capture: list = []
    monkeypatch.setattr(
        "rag_knowledge.llm_http.http_client",
        lambda **kw: _FakeClient(
            capture,
            {"choices": [{"message": {"content": "hello-openai"}}]},
        ),
    )
    ep = ModelEndpoint(
        role="helper_llm",
        provider="openai",
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
    )
    text = chat(ep, [{"role": "user", "content": "hi"}], format_json=True)
    assert text == "hello-openai"
    assert capture[0]["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert capture[0]["headers"]["Authorization"] == "Bearer sk-test"
    assert capture[0]["json"]["response_format"] == {"type": "json_object"}


def test_chat_google_payload(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
    capture: list = []
    monkeypatch.setattr(
        "rag_knowledge.llm_http.http_client",
        lambda **kw: _FakeClient(
            capture,
            {
                "candidates": [
                    {"content": {"parts": [{"text": "hello-google"}]}}
                ]
            },
        ),
    )
    ep = ModelEndpoint(role="llm", provider="google", model="gemini-2.0-flash")
    text = chat(
        ep,
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
        format_json=True,
        num_predict=128,
    )
    assert text == "hello-google"
    url = capture[0]["url"]
    assert "generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent" in url
    assert "key=g-test" in url
    body = capture[0]["json"]
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert body["contents"][0]["role"] == "user"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["maxOutputTokens"] == 128


def test_chat_google_requires_api_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    ep = ModelEndpoint(role="llm", provider="google", model="gemini-2.0-flash")
    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        chat(ep, [{"role": "user", "content": "hi"}])


def test_config_per_role_endpoints(tmp_path: Path, monkeypatch, isolated_storage):
    ini = tmp_path / "mix.ini"
    ini.write_text(
        "\n".join(
            [
                "[ollama]",
                "base_url = http://remote:11434",
                "[model]",
                "llm = remote-llm",
                "helper_llm = remote-helper",
                "embedding = remote-embed",
                "vision = remote-vision",
                "[model.llm]",
                "provider = google",
                "model = gemini-2.0-flash",
                "api_key_env = GOOGLE_API_KEY",
                "[model.helper_llm]",
                "provider = openai",
                "model = deepseek-chat",
                "base_url = https://api.deepseek.com/v1",
                "[graph_extraction.llm]",
                "provider = ollama",
                "model = gemma4:12b",
                "base_url = http://127.0.0.1:11434",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RAG_CONFIG", str(ini))
    isolated_storage()
    Config._instance = None
    cfg = Config()
    assert cfg.llm_endpoint.provider == "google"
    assert cfg.llm_endpoint.model == "gemini-2.0-flash"
    assert cfg.helper_llm_endpoint.provider == "openai"
    assert cfg.helper_llm_endpoint.resolved_base_url(cfg.ollama_base_url).endswith("/v1")
    assert cfg.embedding_endpoint.model == "remote-embed"
    assert cfg.graph_llm_endpoint() == "http://127.0.0.1:11434"


# ---------------------------------------------------------------------------
# Retry boundary tests
# ---------------------------------------------------------------------------

class _FailingThenSucceedingClient:
    """Simulate a client that raises ConnectError on first N calls then succeeds."""

    def __init__(self, fail_times: int, payload: dict):
        self._fail_times = fail_times
        self._calls = 0
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url: str, json=None, headers=None, **kwargs):
        self._calls += 1
        if self._calls <= self._fail_times:
            import httpx
            raise httpx.ConnectError("simulated connection failure")
        return _FakeResp(self._payload)


def test_chat_retry_on_transient_network_failure(monkeypatch):
    """chat() should transparently retry on ConnectError and eventually succeed."""
    failing_client = _FailingThenSucceedingClient(
        fail_times=2,
        payload={"message": {"content": "retry-success"}},
    )
    monkeypatch.setattr(
        "rag_knowledge.llm_http.http_client",
        lambda **kw: failing_client,
    )
    # Patch time.sleep to avoid actual delays during the test
    monkeypatch.setattr("rag_knowledge.llm_http.time.sleep", lambda s: None)

    ep = ModelEndpoint(
        role="llm",
        provider="ollama",
        model="qwen3:30b",
        base_url="http://x:1",
        max_retries=3,
        concurrency_limit=5,
    )
    result = chat(ep, [{"role": "user", "content": "hi"}])
    assert result == "retry-success"
    assert failing_client._calls == 3  # 2 failures + 1 success


def test_chat_raises_after_max_retries_exhausted(monkeypatch):
    """chat() should re-raise the original exception when all retries are exhausted."""
    import httpx

    def _always_fail(**kw):
        class _AlwaysFailClient:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **kw):
                raise httpx.ConnectError("permanent failure")
        return _AlwaysFailClient()

    monkeypatch.setattr("rag_knowledge.llm_http.http_client", _always_fail)
    monkeypatch.setattr("rag_knowledge.llm_http.time.sleep", lambda s: None)

    ep = ModelEndpoint(
        role="llm",
        provider="ollama",
        model="qwen3:30b",
        base_url="http://x:1",
        max_retries=2,
        concurrency_limit=5,
    )
    with pytest.raises(httpx.ConnectError, match="permanent failure"):
        chat(ep, [{"role": "user", "content": "hi"}])


def test_chat_does_not_retry_non_retryable_error(monkeypatch):
    """chat() should NOT retry on ValueError (non-network errors)."""
    call_count = 0

    def _value_error_client(**kw):
        class _BadClient:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **kw):
                nonlocal call_count
                call_count += 1
                raise ValueError("bad payload")
        return _BadClient()

    monkeypatch.setattr("rag_knowledge.llm_http.http_client", _value_error_client)
    monkeypatch.setattr("rag_knowledge.llm_http.time.sleep", lambda s: None)

    ep = ModelEndpoint(
        role="llm",
        provider="ollama",
        model="qwen3:30b",
        base_url="http://x:1",
        max_retries=3,
        concurrency_limit=5,
    )
    with pytest.raises(ValueError, match="bad payload"):
        chat(ep, [{"role": "user", "content": "hi"}])
    # Should have given up immediately without retrying
    assert call_count == 1


def test_concurrency_limit_respected(monkeypatch):
    """Concurrent requests beyond concurrency_limit must queue (semaphore enforced)."""
    import threading

    entered: list[int] = []
    max_concurrent = [0]
    current = [0]
    lock = threading.Lock()

    def _counting_client(**kw):
        class _CountingClient:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **kw):
                with lock:
                    current[0] += 1
                    if current[0] > max_concurrent[0]:
                        max_concurrent[0] = current[0]
                    entered.append(current[0])
                import time; time.sleep(0.01)  # hold the slot briefly
                with lock:
                    current[0] -= 1
                return _FakeResp({"message": {"content": "ok"}})
        return _CountingClient()

    monkeypatch.setattr("rag_knowledge.llm_http.http_client", _counting_client)

    limit = 2
    ep = ModelEndpoint(
        role="concurrency_test_role",
        provider="ollama",
        model="m",
        base_url="http://x:1",
        max_retries=0,
        concurrency_limit=limit,
    )

    results: list = []
    errors: list = []

    def _call():
        try:
            results.append(chat(ep, [{"role": "user", "content": "hi"}]))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_call) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"unexpected errors: {errors}"
    assert all(r == "ok" for r in results)
    # Peak concurrency must never exceed the configured limit
    assert max_concurrent[0] <= limit, (
        f"concurrency exceeded limit: peak={max_concurrent[0]} limit={limit}"
    )
