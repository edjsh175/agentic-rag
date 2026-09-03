from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from rag_knowledge.llm_http import LLMStreamPart, ModelEndpoint
from rag_knowledge.services.model_stream_runner import (
    ModelStreamRunner,
    StreamRunOptions,
)


def _qwen_endpoint() -> ModelEndpoint:
    return ModelEndpoint(
        role="llm",
        provider="ollama",
        model="qwen3.5:9b",
        base_url="http://unused.test",
    )


def _unsupported_endpoint() -> ModelEndpoint:
    return ModelEndpoint(
        role="llm",
        provider="ollama",
        model="qwen2.5:7b",
        base_url="http://unused.test",
    )


def test_stream_runner_token_policy_streams_delta_and_emits_end():
    runner = ModelStreamRunner()
    events: list[dict] = []

    async def fake_stream(*_args, **_kwargs):
        yield LLMStreamPart("reasoning", "思考片段 1")
        yield LLMStreamPart("reasoning", "思考片段 2")
        yield LLMStreamPart("content", "正文内容")

    options = StreamRunOptions(
        endpoint=_qwen_endpoint(),
        messages=[{"role": "user", "content": "hello"}],
        stage="test_stage",
        role="main",
        call_id="call_test_1",
        step=1,
        stream_policy="token",
    )

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream):
        result = asyncio.run(runner.arun(options, on_event=events.append))

    assert result.content == "正文内容"
    assert result.reasoning_requested is True
    assert result.reasoning_available is True
    assert result.reasoning_chars == len("思考片段 1") + len("思考片段 2")
    assert result.content_chars == len("正文内容")
    assert result.raw_reasoning == "思考片段 1思考片段 2"

    types = [e["type"] for e in events]
    assert types == [
        "llm_reasoning_start",
        "llm_reasoning_delta",
        "llm_reasoning_delta",
        "llm_reasoning_end",
    ]
    assert events[0]["data"]["reasoning_requested"] is True
    assert events[0]["data"]["step"] == 1
    assert events[1]["data"]["delta"] == "思考片段 1"
    assert events[3]["data"]["reasoning_available"] is True
    assert events[3]["data"]["reasoning_chars"] == len("思考片段 1") + len("思考片段 2")


def test_stream_runner_separates_sse_semantic_role_from_model_route_audit():
    runner = ModelStreamRunner()
    events: list[dict] = []
    audit_calls: list[dict] = []

    async def fake_stream(*_args, **_kwargs):
        yield LLMStreamPart("reasoning", "规划下一步")
        yield LLMStreamPart("content", "{}")

    def fake_record(**kwargs):
        audit_calls.append(kwargs)

    options = StreamRunOptions(
        endpoint=_qwen_endpoint(),
        messages=[{"role": "user", "content": "hello"}],
        stage="agent_controller",
        semantic_role="main",
        model_route_role="llm",
        call_id="controller_1",
    )

    with (
        patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream),
        patch("rag_knowledge.llm_http.record_model_call", fake_record),
    ):
        asyncio.run(runner.arun(options, on_event=events.append))

    assert {event["data"]["role"] for event in events} == {"main"}
    assert {event["data"]["model_route_role"] for event in events} == {"llm"}
    assert audit_calls[0]["role"] == "llm"


def test_stream_runner_request_reasoning_override():
    runner = ModelStreamRunner()
    events: list[dict] = []

    async def fake_stream(*_args, **kwargs):
        assert kwargs.get("think") is False, "think flag must be False when request_reasoning=False"
        yield LLMStreamPart("content", "正文内容")

    options = StreamRunOptions(
        endpoint=_qwen_endpoint(),
        messages=[{"role": "user", "content": "hello"}],
        stage="test_stage",
        role="main",
        call_id="call_override",
        request_reasoning=False,
        stream_policy="token",
    )

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream):
        result = asyncio.run(runner.arun(options, on_event=events.append))

    assert result.content == "正文内容"
    assert result.reasoning_requested is False
    assert result.reasoning_available is False
    assert events[0]["data"]["reasoning_requested"] is False
    assert events[-1]["data"]["reasoning_available"] is False


def test_stream_runner_content_only():
    runner = ModelStreamRunner()
    events: list[dict] = []

    async def fake_stream(*_args, **_kwargs):
        yield LLMStreamPart("content", "段落 A")
        yield LLMStreamPart("content", "段落 B")

    options = StreamRunOptions(
        endpoint=_unsupported_endpoint(),
        messages=[{"role": "user", "content": "hello"}],
        stage="answer_generation",
        role="main",
        call_id="call_content_only",
        stream_policy="token",
    )

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream):
        result = asyncio.run(runner.arun(options, on_event=events.append))

    assert result.content == "段落 A段落 B"
    assert result.reasoning_requested is False
    assert result.reasoning_available is False
    assert result.reasoning_chars == 0
    assert result.content_chars == len("段落 A段落 B")
    assert [e["type"] for e in events] == ["llm_reasoning_start", "llm_reasoning_end"]
    assert events[-1]["data"]["content_chars"] == len("段落 A段落 B")


def test_stream_runner_reasoning_only_empty_content():
    runner = ModelStreamRunner()
    events: list[dict] = []

    async def fake_stream(*_args, **_kwargs):
        yield LLMStreamPart("reasoning", "只思考但没有正文")

    options = StreamRunOptions(
        endpoint=_qwen_endpoint(),
        messages=[{"role": "user", "content": "hello"}],
        stage="agent_controller",
        role="main",
        call_id="call_reasoning_only",
        stream_policy="token",
    )

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream):
        result = asyncio.run(runner.arun(options, on_event=events.append))

    assert result.content == ""
    assert result.reasoning_available is True
    assert result.reasoning_chars == len("只思考但没有正文")
    assert result.content_chars == 0
    assert events[-1]["data"]["content_chars"] == 0
    assert events[-1]["data"]["reasoning_chars"] == len("只思考但没有正文")


def test_stream_runner_provider_error_before_start():
    runner = ModelStreamRunner()
    events: list[dict] = []

    async def fake_stream_error(*_args, **_kwargs):
        raise ConnectionRefusedError("Provider unreachable")
        yield  # make it a generator

    options = StreamRunOptions(
        endpoint=_qwen_endpoint(),
        messages=[{"role": "user", "content": "hello"}],
        stage="test_stage",
        role="main",
        call_id="call_err_init",
        stream_policy="token",
    )

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream_error):
        with pytest.raises(ConnectionRefusedError):
            asyncio.run(runner.arun(options, on_event=events.append))

    assert len(events) == 2
    assert events[0]["type"] == "llm_reasoning_start"
    assert events[1]["type"] == "llm_reasoning_end"
    assert events[1]["data"]["error"] == "ConnectionRefusedError"


def test_stream_runner_mid_stream_error_preserves_end_and_audit():
    runner = ModelStreamRunner()
    events: list[dict] = []

    async def fake_stream_mid_error(*_args, **_kwargs):
        yield LLMStreamPart("reasoning", "思考前半段")
        raise RuntimeError("mid_stream_disconnection")

    options = StreamRunOptions(
        endpoint=_qwen_endpoint(),
        messages=[{"role": "user", "content": "hello"}],
        stage="test_stage",
        role="main",
        call_id="call_mid_err",
        stream_policy="token",
    )

    audit_calls: list[dict] = []

    def fake_record(**kwargs):
        audit_calls.append(kwargs)

    with (
        patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream_mid_error),
        patch("rag_knowledge.llm_http.record_model_call", fake_record),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            asyncio.run(runner.arun(options, on_event=events.append))
        assert "mid_stream_disconnection" in str(exc_info.value)

    types = [e["type"] for e in events]
    assert types == ["llm_reasoning_start", "llm_reasoning_delta", "llm_reasoning_end"]
    assert events[-1]["data"]["error"] == "RuntimeError"
    assert events[-1]["data"]["reasoning_chars"] == len("思考前半段")

    assert len(audit_calls) == 1
    assert audit_calls[0]["fallback"] == "RuntimeError"
    assert audit_calls[0]["stage"] == "test_stage"


def test_stream_runner_json_mode_and_schema_forwarded():
    runner = ModelStreamRunner()
    captured_kwargs: dict = {}

    async def fake_stream(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        yield LLMStreamPart("content", "{}")

    custom_schema = {"type": "object", "properties": {"action": {"type": "string"}}}
    options = StreamRunOptions(
        endpoint=_qwen_endpoint(),
        messages=[{"role": "user", "content": "hello"}],
        stage="agent_controller",
        role="main",
        call_id="call_json_mode",
        format_json=True,
        json_schema=custom_schema,
    )

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream):
        result = asyncio.run(runner.arun(options))

    assert captured_kwargs["format_json"] is True
    assert captured_kwargs["json_schema"] == custom_schema
    assert result.content == "{}"


def test_stream_runner_multiple_calls_no_crosstalk():
    runner = ModelStreamRunner()
    events_1: list[dict] = []
    events_2: list[dict] = []

    async def fake_stream_1(*_args, **_kwargs):
        yield LLMStreamPart("reasoning", "call 1 thinking")
        yield LLMStreamPart("content", "call 1 answer")

    async def fake_stream_2(*_args, **_kwargs):
        yield LLMStreamPart("reasoning", "call 2 thinking")
        yield LLMStreamPart("content", "call 2 answer")

    opt1 = StreamRunOptions(
        endpoint=_qwen_endpoint(),
        messages=[],
        stage="stage_alpha",
        call_id="call_alpha",
    )
    opt2 = StreamRunOptions(
        endpoint=_qwen_endpoint(),
        messages=[],
        stage="stage_beta",
        call_id="call_beta",
    )

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream_1):
        res1 = asyncio.run(runner.arun(opt1, on_event=events_1.append))

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream_2):
        res2 = asyncio.run(runner.arun(opt2, on_event=events_2.append))

    assert res1.content == "call 1 answer"
    assert res2.content == "call 2 answer"
    assert all(e["data"]["call_id"] == "call_alpha" for e in events_1)
    assert all(e["data"]["stage"] == "stage_alpha" for e in events_1)
    assert all(e["data"]["call_id"] == "call_beta" for e in events_2)
    assert all(e["data"]["stage"] == "stage_beta" for e in events_2)


def test_stream_runner_never_policy_suppresses_reasoning_events():
    runner = ModelStreamRunner()
    events: list[dict] = []

    async def fake_stream(*_args, **_kwargs):
        yield LLMStreamPart("reasoning", "内部思考")
        yield LLMStreamPart("content", "正文内容")

    options = StreamRunOptions(
        endpoint=_qwen_endpoint(),
        messages=[{"role": "user", "content": "hello"}],
        stage="test_stage",
        role="main",
        call_id="call_test_2",
        stream_policy="never",
    )

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream):
        result = asyncio.run(runner.arun(options, on_event=events.append))

    assert result.content == "正文内容"
    assert result.reasoning_available is True
    assert len(events) == 0


def test_stream_runner_summary_policy_emits_summary_event():
    runner = ModelStreamRunner()
    events: list[dict] = []

    async def fake_stream(*_args, **_kwargs):
        yield LLMStreamPart("reasoning", "思考片段 A")
        yield LLMStreamPart("reasoning", "思考片段 B")
        yield LLMStreamPart("content", "答案")

    options = StreamRunOptions(
        endpoint=_qwen_endpoint(),
        messages=[{"role": "user", "content": "hello"}],
        stage="test_stage",
        role="main",
        call_id="call_test_3",
        stream_policy="summary",
    )

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream):
        result = asyncio.run(runner.arun(options, on_event=events.append))

    types = [e["type"] for e in events]
    assert types == [
        "llm_reasoning_start",
        "llm_reasoning_summary",
        "llm_reasoning_end",
    ]
    assert events[1]["data"]["summary"] == "思考片段 A思考片段 B"


def test_stream_runner_sync_run_wrapper():
    runner = ModelStreamRunner()
    events: list[dict] = []

    async def fake_stream(*_args, **_kwargs):
        yield LLMStreamPart("reasoning", "同步思考")
        yield LLMStreamPart("content", "同步结果")

    options = StreamRunOptions(
        endpoint=_qwen_endpoint(),
        messages=[{"role": "user", "content": "hello"}],
        stage="sync_stage",
        role="main",
        call_id="call_sync",
        stream_policy="token",
    )

    with patch("rag_knowledge.llm_http.achat_stream_parts", fake_stream):
        result = runner.run(options, on_event=events.append)

    assert result.content == "同步结果"
    assert result.reasoning_available is True
    assert len(events) == 3
