from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import rag_knowledge.llm_http as llm_http
from rag_knowledge.llm_http import ModelEndpoint


@dataclass
class StreamRunOptions:
    """Configuration options for a single model streaming invocation."""

    endpoint: ModelEndpoint
    messages: list[dict[str, Any]]
    stage: str
    role: str = "main"
    call_id: str = ""
    step: int | None = None
    stream_policy: str = "token"  # "token" | "summary" | "never"
    request_reasoning: bool | None = None  # None = follow capability; bool = explicit intent
    temperature: float = 0.0
    num_predict: int = 8192
    num_ctx: int | None = None
    timeout: float = 600.0
    format_json: bool = False
    json_schema: dict[str, Any] | None = None
    default_ollama: str = ""
    trace_max_summary_chars: int = 2000
    extra_end_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamRunResult:
    """Result of a model stream run."""

    content: str
    reasoning_requested: bool
    reasoning_available: bool
    reasoning_chars: int
    content_chars: int
    elapsed_ms: float
    error: str | None = None
    reasoning_summary: str | None = None
    raw_reasoning: str = ""


class ModelStreamRunner:
    """Single source of truth for executing and observing model streaming lifecycles."""

    async def arun(
        self,
        options: StreamRunOptions,
        on_event: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> StreamRunResult:
        """Asynchronously run model stream, emit normalized reasoning events, and record audit metrics."""
        started = time.perf_counter()
        capability = llm_http.native_reasoning_capability(
            options.endpoint,
            default_ollama=options.default_ollama,
        )
        if options.request_reasoning is None:
            reasoning_requested = capability.can_request
        else:
            reasoning_requested = bool(options.request_reasoning) and capability.can_request

        stream_policy = str(options.stream_policy or "token").lower()
        normalized_policy = {"summarized": "summary"}.get(stream_policy, stream_policy)

        async def _emit(event: dict[str, Any]) -> None:
            if on_event is None:
                return
            res = on_event(event)
            if inspect.isawaitable(res):
                await res

        if normalized_policy != "never":
            start_payload: dict[str, Any] = {
                "call_id": options.call_id,
                "role": options.role,
                "stage": options.stage,
                "model": options.endpoint.model,
                "provider": options.endpoint.normalized_provider(),
                "reasoning_requested": reasoning_requested,
            }
            if options.step is not None:
                start_payload["step"] = options.step
            await _emit({"type": "llm_reasoning_start", "data": start_payload})

        reasoning_available = False
        reasoning_chars = 0
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        error_name: str | None = None

        try:
            async for part in llm_http.achat_stream_parts(
                options.endpoint,
                options.messages,
                default_ollama=options.default_ollama,
                temperature=options.temperature,
                timeout=options.timeout,
                num_predict=options.num_predict,
                think=reasoning_requested,
                num_ctx=options.num_ctx,
                format_json=options.format_json,
                json_schema=options.json_schema,
            ):
                if part.kind == "reasoning":
                    if not part.delta:
                        continue
                    reasoning_available = True
                    reasoning_chars += len(part.delta)
                    reasoning_parts.append(part.delta)
                    if normalized_policy == "token":
                        delta_payload: dict[str, Any] = {
                            "call_id": options.call_id,
                            "role": options.role,
                            "stage": options.stage,
                            "delta": part.delta,
                        }
                        if options.step is not None:
                            delta_payload["step"] = options.step
                        await _emit({"type": "llm_reasoning_delta", "data": delta_payload})
                else:
                    if part.delta:
                        content_parts.append(part.delta)
        except Exception as exc:
            error_name = type(exc).__name__
            raise
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            summary_text: str | None = None
            if normalized_policy == "summary" and reasoning_parts:
                raw_summary = "".join(reasoning_parts)
                summary_text = (
                    raw_summary[: options.trace_max_summary_chars] + "..."
                    if len(raw_summary) > options.trace_max_summary_chars
                    else raw_summary
                )
                summary_payload: dict[str, Any] = {
                    "call_id": options.call_id,
                    "role": options.role,
                    "stage": options.stage,
                    "summary": summary_text,
                }
                if options.step is not None:
                    summary_payload["step"] = options.step
                await _emit({"type": "llm_reasoning_summary", "data": summary_payload})

            if normalized_policy != "never":
                end_payload: dict[str, Any] = {
                    "call_id": options.call_id,
                    "role": options.role,
                    "stage": options.stage,
                    "model": options.endpoint.model,
                    "provider": options.endpoint.normalized_provider(),
                    "reasoning_requested": reasoning_requested,
                    "reasoning_available": reasoning_available,
                    "reasoning_chars": reasoning_chars,
                    "content_chars": sum(len(p) for p in content_parts),
                    "num_predict": options.num_predict,
                    "elapsed_ms": round(elapsed_ms, 1),
                }
                if options.step is not None:
                    end_payload["step"] = options.step
                if error_name:
                    end_payload["error"] = error_name
                if options.extra_end_payload:
                    end_payload.update(options.extra_end_payload)
                await _emit({"type": "llm_reasoning_end", "data": end_payload})

            llm_http.record_model_call(
                role=options.role,
                stage=options.stage,
                provider=options.endpoint.normalized_provider(),
                model=options.endpoint.model,
                elapsed_ms=elapsed_ms,
                fallback=error_name,
            )

        raw_reasoning = "".join(reasoning_parts)
        return StreamRunResult(
            content="".join(content_parts),
            reasoning_requested=reasoning_requested,
            reasoning_available=reasoning_available,
            reasoning_chars=reasoning_chars,
            content_chars=sum(len(p) for p in content_parts),
            elapsed_ms=elapsed_ms,
            error=error_name,
            reasoning_summary=summary_text,
            raw_reasoning=raw_reasoning,
        )

    def run(
        self,
        options: StreamRunOptions,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> StreamRunResult:
        """Synchronously execute stream runner using an event loop."""
        return asyncio.run(self.arun(options, on_event=on_event))
