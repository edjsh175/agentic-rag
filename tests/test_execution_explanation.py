from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from rag_knowledge.llm_http import LLMStreamPart, ModelEndpoint
from rag_knowledge.services.execution_explanation import (
    generate_public_explanation,
    public_explanation_event,
)


def test_public_explanation_uses_a_clear_system_fallback_when_text_is_missing():
    event = public_explanation_event(
        stage="answer_generation",
        call_id="answer_1",
        endpoint=ModelEndpoint(role="llm", model="any"),
        text="",
        source="model_generated",
        context={"evidence_count": 2},
    )

    assert event["type"] == "public_explanation"
    assert event["data"]["source"] == "system_fallback"
    assert "2 条证据" in event["data"]["text"]


@pytest.mark.parametrize(
    "endpoint",
    [
        ModelEndpoint(role="llm", provider="ollama", model="qwen3.5:9b"),
        ModelEndpoint(role="llm", provider="openai", model="deepseek-chat"),
        ModelEndpoint(role="llm", provider="google", model="gemini"),
    ],
)
def test_public_explanation_has_one_model_independent_wire_shape(endpoint):
    async def fake_stream(*_args, **_kwargs):
        yield LLMStreamPart("content", "将根据当前证据组织回答。")

    with patch(
        "rag_knowledge.services.execution_explanation.achat_stream_parts",
        fake_stream,
    ):
        event = asyncio.run(generate_public_explanation(
            stage="answer_generation",
            call_id="answer_1",
            endpoint=endpoint,
            context={"question": "测试"},
            default_ollama="http://unused.test",
            num_ctx=4096,
        ))

    assert event["type"] == "public_explanation"
    assert event["data"]["stage"] == "answer_generation"
    assert event["data"]["source"] == "model_generated"
    assert event["data"]["text"] == "将根据当前证据组织回答。"
