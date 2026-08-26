"""One model-agnostic public execution explanation protocol for Main stages."""
from __future__ import annotations

import json
from typing import Any

from rag_knowledge.llm_http import ModelEndpoint, achat_stream_parts


_PUBLIC_EXPLANATION_SYSTEM = """你负责生成公开执行说明，不要输出隐藏思考过程或逐步推理。
只用简体中文，以一到两句说明本阶段将如何处理用户问题、证据或改写约束。
不要杜撰事实，不要输出标题、JSON、Markdown、引用编号或最终答案。"""


def _fallback(stage: str, context: dict[str, Any]) -> str:
    if stage == "agent_controller":
        return "正在根据当前问题、可用工具与证据状态决定下一步。"
    if stage == "answer_generation":
        count = int(context.get("evidence_count") or 0)
        return f"将基于已冻结的 {count} 条证据组织回答，并保留来源边界。"
    if stage == "grounded_retry":
        return "将按证据审查结论修正候选内容，仅保留被支持的表述。"
    return "正在按当前执行约束处理本阶段任务。"


def public_explanation_event(
    *,
    stage: str,
    call_id: str,
    endpoint: ModelEndpoint | None,
    text: str | None,
    source: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the sole wire shape consumed by SSE, Trace and the UI."""
    clean_text = " ".join(str(text or "").split()).strip()
    fallback_used = not bool(clean_text)
    if fallback_used:
        clean_text = _fallback(stage, context or {})
        source = "system_fallback"
    return {
        "type": "public_explanation",
        "data": {
            "call_id": call_id,
            "role": "main",
            "stage": stage,
            "model": endpoint.model if endpoint else None,
            "provider": endpoint.normalized_provider() if endpoint else None,
            "text": clean_text,
            "source": source,
            "fallback_used": fallback_used,
        },
    }


async def generate_public_explanation(
    *,
    stage: str,
    call_id: str,
    endpoint: ModelEndpoint,
    context: dict[str, Any],
    default_ollama: str,
    num_ctx: int | None,
    timeout: float = 45.0,
) -> dict[str, Any]:
    """Ask the configured Main endpoint for a bounded public explanation.

    This intentionally uses normal content, never a provider reasoning channel,
    so the guarantee is independent of model-specific native-thinking support.
    """
    prompt = json.dumps(
        {"stage": stage, "execution_context": context},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    parts: list[str] = []
    try:
        async for part in achat_stream_parts(
            endpoint,
            [
                {"role": "system", "content": _PUBLIC_EXPLANATION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            default_ollama=default_ollama,
            temperature=0.0,
            timeout=timeout,
            num_predict=256,
            think=False,
            num_ctx=num_ctx,
        ):
            if part.kind == "content":
                parts.append(part.delta)
    except Exception:
        return public_explanation_event(
            stage=stage,
            call_id=call_id,
            endpoint=endpoint,
            text=None,
            source="model_generated",
            context=context,
        )
    return public_explanation_event(
        stage=stage,
        call_id=call_id,
        endpoint=endpoint,
        text="".join(parts),
        source="model_generated",
        context=context,
    )
