"""Ollama httpx helpers must ignore process/system HTTP proxies."""

from rag_knowledge.ollama_http import OLLAMA_CLIENT_KWARGS, async_client, client, post


def test_ollama_client_kwargs_disable_trust_env():
    assert OLLAMA_CLIENT_KWARGS == {"trust_env": False}


def test_client_defaults_trust_env_false():
    with client(timeout=1.0) as c:
        assert c._trust_env is False


def test_async_client_defaults_trust_env_false():
    import asyncio

    async def _check():
        async with async_client(timeout=1.0) as c:
            assert c._trust_env is False

    asyncio.run(_check())


def test_post_accepts_trust_env_override(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        captured["url"] = url

        class _Resp:
            status_code = 200

        return _Resp()

    monkeypatch.setattr("rag_knowledge.ollama_http.httpx.post", fake_post)
    post("http://example.invalid/api/tags", timeout=1.0)
    assert captured["trust_env"] is False
