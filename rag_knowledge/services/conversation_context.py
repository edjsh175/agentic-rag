"""
对话上下文契约与生成侧打包。

SessionState / UnderstandingResult 为唯一状态与理解出口的数据契约；
GenerationPack 是生成路径 Token 预算的唯一所有者（compress + trim）。
Phase 2：DialogueFocus 结构化短记忆 + 检索侧短记忆视图。
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)


class _FrozenDict(dict):
    """Dict-compatible immutable container for UnderstandingResult payloads."""

    def _blocked(self, *_args, **_kwargs):
        raise TypeError("UnderstandingResult payload is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _blocked
    __ior__ = _blocked


class _FrozenList(list):
    """List-compatible immutable container for UnderstandingResult payloads."""

    def _blocked(self, *_args, **_kwargs):
        raise TypeError("UnderstandingResult payload is immutable")

    __setitem__ = __delitem__ = __iadd__ = __imul__ = _blocked
    append = clear = extend = insert = pop = remove = reverse = sort = _blocked


def _freeze_understanding(value: Any) -> Any:
    if isinstance(value, _FrozenDict) or isinstance(value, _FrozenList):
        return value
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze_understanding(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_freeze_understanding(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_understanding(item) for item in value)
    return value


def _thaw_understanding(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _thaw_understanding(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_understanding(item) for item in value]
    return value

CompressFallback = Literal[
    "none",
    "summary_cache",
    "truncate_recent",
    "cooldown_truncate",
    "disabled",
]

_FOCUS_TOPIC_MAX = 80
_FOCUS_OPEN_MAX = 120
_RECENT_ROUNDS_DEFAULT = 2
_RECENT_CONTENT_CHARS = 120


@dataclass
class DialogueFocus:
    """结构化对话焦点（非事实来源）。"""

    topic: str = ""
    confirmed_entity: str = ""
    open_question: str = ""
    notes: str = ""

    def to_text(self, *, max_chars: int = 200) -> str:
        parts: list[str] = []
        if self.confirmed_entity:
            parts.append(f"实体:{self.confirmed_entity}")
        if self.topic:
            parts.append(f"主题:{self.topic}")
        if self.open_question:
            parts.append(f"焦点:{self.open_question}")
        if self.notes:
            parts.append(self.notes)
        text = " | ".join(parts).strip()
        return text[:max_chars] if text else ""

    def to_dict(self) -> dict[str, str]:
        return {
            "topic": self.topic,
            "confirmed_entity": self.confirmed_entity,
            "open_question": self.open_question,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DialogueFocus":
        if not isinstance(data, dict):
            return cls()
        return cls(
            topic=str(data.get("topic") or "")[:_FOCUS_TOPIC_MAX],
            confirmed_entity=str(data.get("confirmed_entity") or "")[:80],
            open_question=str(data.get("open_question") or "")[:_FOCUS_OPEN_MAX],
            notes=str(data.get("notes") or "")[:80],
        )


@dataclass
class SessionTurn:
    role: str
    content: str
    sources: list[dict[str, Any]] | None = None


@dataclass
class SessionState:
    """会话结构化状态（请求级真相源）。"""

    turns: list[SessionTurn] = field(default_factory=list)
    last_sources: list[dict[str, Any]] = field(default_factory=list)
    resolved_entity: str | None = None
    doc_category: str | None = None
    rolling_summary: str | None = None
    dialogue_focus: str | None = None
    focus: DialogueFocus | None = None
    chat_id: str | None = None

    def to_history(self) -> list[dict[str, Any]]:
        """导出下游仍使用的 history list[dict] 形态。"""
        out: list[dict[str, Any]] = []
        for turn in self.turns:
            item: dict[str, Any] = {"role": turn.role, "content": turn.content}
            if turn.role == "assistant" and turn.sources:
                item["sources"] = list(turn.sources)
            out.append(item)
        return out


@dataclass(frozen=True)
class UnderstandingResult:
    """对话理解出口。"""

    mode: Literal["clarify", "retrieve"] = "retrieve"
    user_utterance: str = ""
    resolved_question: str = ""
    retrieval_queries: list[dict[str, Any]] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    dialogue_focus: str = ""
    focus: dict[str, str] = field(default_factory=dict)
    semantic_task_context: dict[str, Any] = field(default_factory=dict)
    is_context_dependent: bool = False
    confidence: float = 1.0
    clarify: dict[str, Any] | None = None
    rationale: str = ""

    def __post_init__(self) -> None:
        for name in (
            "retrieval_queries",
            "filters",
            "focus",
            "semantic_task_context",
            "clarify",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _freeze_understanding(value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "user_utterance": self.user_utterance,
            "resolved_question": self.resolved_question,
            "retrieval_queries": _thaw_understanding(self.retrieval_queries),
            "filters": _thaw_understanding(self.filters),
            "dialogue_focus": self.dialogue_focus,
            "focus": _thaw_understanding(self.focus),
            "semantic_task_context": _thaw_understanding(self.semantic_task_context),
            "is_context_dependent": self.is_context_dependent,
            "confidence": self.confidence,
            "clarify": _thaw_understanding(self.clarify),
            "rationale": self.rationale,
        }


@dataclass
class PackDecision:
    """GenerationPack 裁剪/压缩决策，供 trace 回放。"""

    compress_fallback: CompressFallback = "none"
    used_summary: bool = False
    kept_history_messages: int = 0
    removed_history_messages: int = 0
    removed_chunks: int = 0
    scheduled_background_summary: bool = False
    compress_background_busy: bool = False
    compress_pending_rewarm: bool = False
    compress_older_hash_prefix: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PackResult:
    source_docs: list[dict[str, Any]]
    context: str
    history: list[dict[str, Any]] | None
    history_summary: str | None
    decision: PackDecision


def session_from_history(
    history: list[dict[str, Any]] | None,
    *,
    entity_name: str | None = None,
    doc_category: str | None = None,
    rolling_summary: str | None = None,
    dialogue_focus: str | None = None,
    focus: DialogueFocus | dict[str, Any] | None = None,
    chat_id: str | None = None,
) -> SessionState:
    """从旧版 history[] 适配为 SessionState。"""
    turns: list[SessionTurn] = []
    last_sources: list[dict[str, Any]] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")
        sources = item.get("sources")
        normalized_sources: list[dict[str, Any]] | None = None
        if isinstance(sources, list) and sources:
            normalized_sources = [s for s in sources if isinstance(s, dict)]
            if role == "assistant":
                last_sources = list(normalized_sources)
        turns.append(SessionTurn(role=role, content=content, sources=normalized_sources))

    focus_obj: DialogueFocus | None
    if isinstance(focus, DialogueFocus):
        focus_obj = focus
    elif isinstance(focus, dict):
        focus_obj = DialogueFocus.from_dict(focus)
    else:
        focus_obj = None

    return SessionState(
        turns=turns,
        last_sources=last_sources,
        resolved_entity=entity_name,
        doc_category=doc_category,
        rolling_summary=rolling_summary,
        dialogue_focus=dialogue_focus or (focus_obj.to_text() if focus_obj else None),
        focus=focus_obj,
        chat_id=chat_id,
    )


def _token_overlap(a: str, b: str) -> bool:
    """True when two labels refer to the same token (casefold / substring)."""
    left = (a or "").strip().casefold()
    right = (b or "").strip().casefold()
    if not left or not right:
        return False
    return left == right or left in right or right in left


def _previous_user_topic(session: SessionState) -> str:
    for turn in reversed(session.turns):
        if turn.role == "user" and turn.content.strip():
            return turn.content.strip()[:_FOCUS_TOPIC_MAX]
    return ""


def _previous_anchor_labels(session: SessionState, prev_topic: str) -> list[str]:
    """Labels that represent 'what we were talking about' before the current turn."""
    labels: list[str] = []
    if session.resolved_entity:
        labels.append(str(session.resolved_entity).strip())
    if prev_topic:
        labels.append(prev_topic)
    for src in session.last_sources or []:
        if not isinstance(src, dict):
            continue
        for key in ("section_title", "file_name", "source"):
            val = src.get(key)
            if val:
                labels.append(str(val).strip())
    if session.rolling_summary:
        m = re.search(r"实体[:：]\s*(.+)", session.rolling_summary)
        if m:
            labels.append(m.group(1).strip())
        m2 = re.search(r"主题[:：]\s*(.+)", session.rolling_summary)
        if m2:
            labels.append(m2.group(1).strip())
    return [x for x in labels if x]


def detect_topic_shift(question: str, session: SessionState) -> bool:
    """当前问句显式点名了与上文无关的技术实体时视为主题漂移。

    若问句为纠偏/否定句式且否定了前文实体，同样视为切题/解绑。
    无显式实体（指代/省略追问）不判定漂移，保留锚点。
    """
    from rag_knowledge.services.query_entity_guard import (
        detect_correction_or_negation,
        extract_explicit_entities,
    )

    is_corr, neg_ents = detect_correction_or_negation(question or "")
    if is_corr and neg_ents:
        prev_topic = _previous_user_topic(session)
        anchors = _previous_anchor_labels(session, prev_topic)
        for ne in neg_ents:
            for ae in anchors:
                if _token_overlap(ne, ae):
                    return True

    q_ents = extract_explicit_entities(question or "", exclude_negated=True)
    if not q_ents:
        return False

    prev_topic = _previous_user_topic(session)
    anchors = _previous_anchor_labels(session, prev_topic)
    if not anchors:
        return False

    # Expand anchors with explicit entities found in previous topic / source names.
    anchor_ents: list[str] = []
    for label in anchors:
        anchor_ents.extend(extract_explicit_entities(label))
        # Keep raw label too (covers Chinese / short titles without Latin entities).
        anchor_ents.append(label)

    for qe in q_ents:
        for ae in anchor_ents:
            if _token_overlap(qe, ae):
                return False
    return True


def build_dialogue_focus(
    question: str,
    session: SessionState,
    *,
    resolved_question: str | None = None,
) -> DialogueFocus:
    """从 Session + 当前问句构造结构化焦点（不调用 LLM）。"""
    from rag_knowledge.services.query_entity_guard import (
        detect_correction_or_negation,
        extract_explicit_entities,
    )

    entity = (session.resolved_entity or "").strip()
    prev_topic = _previous_user_topic(session)
    topic = prev_topic
    if not topic and session.last_sources:
        src = session.last_sources[0]
        topic = str(src.get("section_title") or src.get("file_name") or "")[:_FOCUS_TOPIC_MAX]

    open_q = (resolved_question or question or "").strip()[:_FOCUS_OPEN_MAX]
    if session.rolling_summary:
        m = re.search(r"主题[:：]\s*(.+)", session.rolling_summary)
        if m and not topic:
            topic = m.group(1).strip()[:_FOCUS_TOPIC_MAX]
        m2 = re.search(r"实体[:：]\s*(.+)", session.rolling_summary)
        if m2 and not entity:
            entity = m2.group(1).strip()[:80]

    notes = ""
    is_corr, neg_ents = detect_correction_or_negation(question or "")
    if is_corr and entity and any(_token_overlap(entity, ne) for ne in neg_ents):
        entity = ""
        notes = "correction"

    if detect_topic_shift(question, session):
        topic = (question or "").strip()[:_FOCUS_TOPIC_MAX]
        q_ents = extract_explicit_entities(question or "", exclude_negated=True)
        if entity and q_ents and not any(_token_overlap(entity, e) for e in q_ents):
            entity = ""
        notes = "topic_shift"

    return DialogueFocus(
        topic=topic,
        confirmed_entity=entity,
        open_question=open_q,
        notes=notes,
    )


def format_retrieval_memory(
    history: list[dict[str, Any]] | None,
    *,
    focus_text: str = "",
    rolling_summary: str = "",
    recent_rounds: int = _RECENT_ROUNDS_DEFAULT,
    content_chars: int = _RECENT_CONTENT_CHARS,
) -> str:
    """检索侧短记忆视图：焦点 + 滚动摘要 + 最近少量轮次（替代 6×200 dump）。"""
    blocks: list[str] = []
    focus = (focus_text or "").strip()
    if focus:
        blocks.append(f"对话焦点：{focus[:200]}")

    summary = (rolling_summary or "").strip()
    if summary:
        blocks.append(f"历史摘要：\n{summary[:400]}")

    if history:
        keep = max(1, int(recent_rounds)) * 2
        recent = history[-keep:]
        lines: list[str] = []
        for h in recent:
            if not isinstance(h, dict):
                continue
            role = h.get("role", "?")
            content = str(h.get("content") or "")[:content_chars]
            lines.append(f"{role}: {content}")
        if lines:
            blocks.append("最近对话：\n" + "\n".join(lines))

    return "\n\n".join(blocks) if blocks else "（无历史对话）"


def extract_source_summaries(
    source_docs: list[dict[str, Any]] | None,
    *,
    limit: int = 4,
    preview_chars: int = 200,
) -> list[dict[str, str]]:
    """从检索返回的 source_documents 提取轻量 SourceSummary（前后端统一字段）。"""
    if not source_docs:
        return []

    summaries: list[dict[str, str]] = []
    for doc in source_docs[:limit]:
        meta = doc.get("metadata", {}) if isinstance(doc, dict) else {}
        summary: dict[str, str] = {}
        for key in (
            "file_name",
            "source",
            "section_title",
            "page_label",
            "chunk_id",
            "citation_id",
        ):
            val = meta.get(key)
            if val is not None and val != "":
                summary[key] = str(val)
        content = doc.get("content", "") if isinstance(doc, dict) else ""
        if content:
            summary["preview"] = str(content)[:preview_chars]
        if summary:
            summaries.append(summary)
    return summaries


class GenerationPack:
    """生成侧唯一打包入口：历史压缩策略 + token 预算裁剪。"""

    def __init__(self, compressor: Any, budget: Any):
        self._compressor = compressor
        self._budget = budget

    def pack(
        self,
        source_docs: list[dict[str, Any]],
        context: str,
        history: list[dict[str, Any]] | None,
        question: str,
        agent_prompt: str | None = None,
        *,
        on_cache_miss: str = "truncate_recent",
    ) -> PackResult:
        history_before = list(history) if history else []
        docs_before = len(source_docs or [])

        compress_result = self._compressor.compress_detailed(
            history,
            on_cache_miss=on_cache_miss,
        )
        packed_history = compress_result.history
        history_summary = compress_result.summary

        trimmed_docs, trimmed_context, trimmed_history = self._budget.trim(
            source_docs,
            context,
            packed_history,
            question,
            agent_prompt=agent_prompt,
        )

        final_history = list(trimmed_history) if trimmed_history else []
        removed_history = max(0, len(history_before) - len(final_history))
        removed_chunks = max(0, docs_before - len(trimmed_docs or []))

        reason_parts = [compress_result.reason] if compress_result.reason else []
        if removed_history:
            reason_parts.append(f"budget_removed_history={removed_history}")
        if removed_chunks:
            reason_parts.append(f"budget_removed_chunks={removed_chunks}")

        decision = PackDecision(
            compress_fallback=compress_result.fallback,
            used_summary=bool(history_summary),
            kept_history_messages=len(final_history),
            removed_history_messages=removed_history,
            removed_chunks=removed_chunks,
            scheduled_background_summary=compress_result.scheduled_background,
            compress_background_busy=bool(
                getattr(compress_result, "background_busy", False)
            ),
            compress_pending_rewarm=bool(
                getattr(compress_result, "pending_rewarm_queued", False)
            ),
            compress_older_hash_prefix=str(
                getattr(compress_result, "older_hash_prefix", "") or ""
            ),
            reason="; ".join(reason_parts) if reason_parts else "ok",
        )
        logger.info(
            "generation_pack | fallback=%s summary=%s kept_hist=%d removed_hist=%d "
            "removed_chunks=%d scheduled=%s busy=%s pending=%s hash=%s",
            decision.compress_fallback,
            decision.used_summary,
            decision.kept_history_messages,
            decision.removed_history_messages,
            decision.removed_chunks,
            decision.scheduled_background_summary,
            decision.compress_background_busy,
            decision.compress_pending_rewarm,
            decision.compress_older_hash_prefix,
        )
        return PackResult(
            source_docs=trimmed_docs,
            context=trimmed_context,
            history=trimmed_history,
            history_summary=history_summary,
            decision=decision,
        )
