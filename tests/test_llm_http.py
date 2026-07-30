"""Unit tests for multi-provider llm_http (ollama / openai / google)."""
from __future__ import annotations

from pathlib import Path

import pytest

from rag_knowledge.config import Config
from rag_knowledge.llm_http import ModelEndpoint, chat


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
