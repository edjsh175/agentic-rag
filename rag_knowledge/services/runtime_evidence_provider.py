"""Runtime evidence provider for single-turn and cross-turn execution events."""
from __future__ import annotations

import logging
from typing import Any

from rag_knowledge.services.qa_trace import QaTraceStore

logger = logging.getLogger(__name__)


class RuntimeEvidenceProvider:
    """Collects runtime execution events from active traces and persisted QaTraceStore."""

    @classmethod
    def collect_events(
        cls,
        *,
        trace: Any = None,
        history: list[dict[str, Any]] | None = None,
        cfg: Any | None = None,
        max_prior_traces: int = 3,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        seen_events: set[str] = set()

        def _add_event(evt: Any, source_trace_id: str | None = None) -> None:
            if not isinstance(evt, dict):
                return
            evt_type = str(evt.get("type") or "").strip()
            if not evt_type:
                return
            data = evt.get("data") if isinstance(evt.get("data"), dict) else {}
            # 引入 Trace 命名空间的精准去重键
            dedup_key = f"{source_trace_id or ''}:{evt_type}:{data.get('name') or data.get('step') or data.get('card_id') or ''}:{evt.get('step') or ''}"
            if dedup_key in seen_events and dedup_key != f"{source_trace_id or ''}:{evt_type}::":
                return
            seen_events.add(dedup_key)
            events.append(dict(evt))

        # 1. 提取前序轮次已持久化的真实 Trace 事件
        if history and cfg is not None:
            prior_trace_ids: list[str] = []
            for h_turn in reversed(history or []):
                if not isinstance(h_turn, dict):
                    continue
                # 优先提取该消息内所有澄清交互对应的真实 Trace ID。
                clar_records = list(h_turn.get("clarification_history") or [])
                current_clar = h_turn.get("clarification") or h_turn.get("clarification_selection") or {}
                if isinstance(current_clar, dict):
                    clar_records.append(current_clar)
                for clar_info in clar_records:
                    if not isinstance(clar_info, dict):
                        continue
                    for key in ("published_trace_id", "response_trace_id"):
                        clar_tid = str(clar_info.get(key) or "").strip()
                        if clar_tid and clar_tid not in prior_trace_ids:
                            prior_trace_ids.append(clar_tid)
                # 提取轮次的主 Trace ID
                tid = str(h_turn.get("trace_id") or "").strip()
                if tid and tid not in prior_trace_ids:
                    prior_trace_ids.append(tid)
                if len(prior_trace_ids) >= max_prior_traces:
                    break

            if prior_trace_ids:
                try:
                    store = QaTraceStore(cfg)
                    for tid in reversed(prior_trace_ids):
                        payload = store.get(tid)
                        if not payload:
                            continue
                        # 从历史 trace 中提取真实的 execution_events，不进行任何反推或伪造
                        exec_evts = payload.get("execution_events") or []
                        for e in exec_evts:
                            _add_event(e, source_trace_id=tid)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("RuntimeEvidenceProvider failed to load prior traces: %s", exc)

        # 2. 提取当前 turn 正在活跃收集的真实事件（支持 _execution_events 与 _events）
        if trace is not None:
            active_events = getattr(trace, "_execution_events", None)
            if active_events is None:
                active_events = getattr(trace, "execution_events", None)
            if isinstance(active_events, (list, tuple)):
                for e in active_events:
                    _add_event(e)

            raw_events = getattr(trace, "_events", None)
            if raw_events is None:
                raw_events = getattr(trace, "events", None)
            if isinstance(raw_events, (list, tuple)):
                for e in raw_events:
                    _add_event(e)

        return events
