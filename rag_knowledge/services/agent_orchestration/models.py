"""ConversationContext / EvidencePool / Tool contract (PRD V1.3 Phase 1)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from rag_knowledge.services.conversation_context import (
    SessionState,
    UnderstandingResult,
    session_from_history,
)

EvidenceStatus = Literal["ACTIVE", "FROZEN"]
ToolAction = Literal["tool_call", "finish"]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    permission: str = "allow"
    side_effect: str = "none"
    confirmation_required: bool = False
    timeout: float | None = None


@dataclass
class ToolObservation:
    tool: str
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    elapsed_ms: float = 0.0
    fallback: str | None = None


@dataclass
class AgentDecision:
    action: ToolAction = "finish"
    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    thought: str = ""
    source: str = "llm"
    gate: str = ""
    gap_type: str = ""
    recovery_strategy: str = ""
    missing: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "action": self.action,
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "thought": self.thought,
            "source": self.source,
        }
        if self.gate:
            payload["gate"] = self.gate
        if self.gap_type:
            payload["gap_type"] = self.gap_type
        if self.recovery_strategy:
            payload["recovery_strategy"] = self.recovery_strategy
        return payload


@dataclass
class AgentBudget:
    max_steps: int
    max_retrieve_attempts: int
    steps_used: int = 0
    retrieve_attempts: int = 0

    def can_step(self) -> bool:
        return self.steps_used < self.max_steps

    def can_retrieve(self) -> bool:
        return self.retrieve_attempts < self.max_retrieve_attempts

    def consume_step(self) -> bool:
        if not self.can_step():
            return False
        self.steps_used += 1
        return True

    def consume_retrieve(self) -> bool:
        if not self.can_retrieve():
            return False
        self.retrieve_attempts += 1
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_retrieve_attempts": self.max_retrieve_attempts,
            "steps_used": self.steps_used,
            "retrieve_attempts": self.retrieve_attempts,
        }


@dataclass
class EvidenceGroup:
    group_id: str
    question_id: str
    kind: str
    retrieve_index: int | None
    chunk_ids: list[str]
    docs: list[dict[str, Any]]
    status: EvidenceStatus
    head_entity: str | None = None
    query: str | None = None
    tool: str | None = None
    gap_type: str | None = None
    recovery_strategy: str | None = None

    def to_trace(self) -> dict[str, Any]:
        payload = {
            "question_id": self.question_id,
            "kind": self.kind,
            "retrieve_index": self.retrieve_index,
            "chunk_ids": list(self.chunk_ids),
            "status": self.status,
            "head_entity": self.head_entity,
            "tool": self.tool,
        }
        if self.gap_type:
            payload["gap_type"] = self.gap_type
        if self.recovery_strategy:
            payload["recovery_strategy"] = self.recovery_strategy
        return payload


def _chunk_id(doc: dict[str, Any]) -> str:
    meta = doc.get("metadata") if isinstance(doc, dict) else None
    if isinstance(meta, dict):
        return str(meta.get("chunk_id") or "")
    return ""


@dataclass
class EvidencePool:
    question_id: str
    groups: list[EvidenceGroup] = field(default_factory=list)
    _retrieve_seq: int = 0

    def seed_previous_cited(
        self,
        docs: list[dict[str, Any]] | None,
        *,
        head_entity: str | None = None,
    ) -> EvidenceGroup | None:
        cleaned = [d for d in (docs or []) if isinstance(d, dict)]
        if not cleaned:
            return None
        group = EvidenceGroup(
            group_id=uuid.uuid4().hex[:12],
            question_id=self.question_id,
            kind="previous_turn_cited",
            retrieve_index=None,
            chunk_ids=[cid for cid in (_chunk_id(d) for d in cleaned) if cid],
            docs=list(cleaned),
            status="FROZEN",
            head_entity=head_entity,
            tool=None,
        )
        self.groups.append(group)
        return group

    def add_retrieve(
        self,
        docs: list[dict[str, Any]],
        *,
        query: str | None = None,
        tool: str = "retrieve_kb",
        head_entity: str | None = None,
        gap_type: str | None = None,
        recovery_strategy: str | None = None,
    ) -> EvidenceGroup:
        self._retrieve_seq += 1
        cleaned = [d for d in (docs or []) if isinstance(d, dict)]
        group = EvidenceGroup(
            group_id=uuid.uuid4().hex[:12],
            question_id=self.question_id,
            kind="retrieve",
            retrieve_index=self._retrieve_seq,
            chunk_ids=[cid for cid in (_chunk_id(d) for d in cleaned) if cid],
            docs=list(cleaned),
            status="ACTIVE",
            head_entity=head_entity,
            query=query,
            tool=tool,
            gap_type=gap_type,
            recovery_strategy=recovery_strategy,
        )
        self.groups.append(group)
        return group

    def add_external(
        self,
        docs: list[dict[str, Any]],
        *,
        query: str | None = None,
        tool: str = "web_search",
        kind: str = "web_search",
        head_entity: str | None = None,
    ) -> EvidenceGroup:
        cleaned = [d for d in (docs or []) if isinstance(d, dict)]
        group = EvidenceGroup(
            group_id=uuid.uuid4().hex[:12],
            question_id=self.question_id,
            kind=kind,
            retrieve_index=None,
            chunk_ids=[cid for cid in (_chunk_id(d) for d in cleaned) if cid],
            docs=list(cleaned),
            status="ACTIVE",
            head_entity=head_entity,
            query=query,
            tool=tool,
        )
        self.groups.append(group)
        return group

    def freeze_active(self) -> list[str]:
        frozen: list[str] = []
        for group in self.groups:
            if group.status == "ACTIVE":
                group.status = "FROZEN"
                frozen.append(group.group_id)
        return frozen

    def previous_cited_group(self) -> EvidenceGroup | None:
        for group in reversed(self.groups):
            if group.kind == "previous_turn_cited":
                return group
        return None

    def reuse(
        self,
        chunk_ids: list[str] | None = None,
        *,
        head_entity: str | None = None,
    ) -> EvidenceGroup | None:
        source = self.previous_cited_group()
        if source is None or not source.docs:
            return None
        wanted = {str(x) for x in (chunk_ids or []) if str(x).strip()}
        docs = list(source.docs)
        if wanted:
            docs = [d for d in docs if _chunk_id(d) in wanted]
        if not docs:
            return None
        group = EvidenceGroup(
            group_id=uuid.uuid4().hex[:12],
            question_id=self.question_id,
            kind="reuse",
            retrieve_index=None,
            chunk_ids=[cid for cid in (_chunk_id(d) for d in docs) if cid],
            docs=docs,
            status="ACTIVE",
            head_entity=head_entity or source.head_entity,
            tool="reuse_evidence",
        )
        self.groups.append(group)
        return group

    def citable_docs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in self.groups:
            if group.status != "ACTIVE":
                continue
            for doc in group.docs:
                cid = _chunk_id(doc) or f"anon:{id(doc)}"
                if cid in seen:
                    continue
                seen.add(cid)
                out.append(doc)
        return out

    def citable_docs_renumbered(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for index, doc in enumerate(self.citable_docs(), start=1):
            meta = dict(doc.get("metadata") or {})
            meta["citation_id"] = index
            cloned = dict(doc)
            cloned["metadata"] = meta
            out.append(cloned)
        return out

    def to_trace(self) -> list[dict[str, Any]]:
        return [group.to_trace() for group in self.groups]

    def to_prompt(self, formatted_context: str) -> str:
        groups = []
        for group in self.groups:
            if group.status != "ACTIVE":
                continue
            label = group.kind
            if group.retrieve_index is not None:
                label = f"{group.kind}#{group.retrieve_index}"
            groups.append(
                f"- {label} status={group.status} chunks={len(group.chunk_ids)}"
            )
        header = "当前可引用证据组：\n" + ("\n".join(groups) if groups else "- （空）")
        body = formatted_context.strip() if formatted_context.strip() else "（暂无）"
        return (
            "## 证据池（EvidencePool）\n"
            "以下是当前问题允许引用的知识事实来源。知识库事实只能来自本区；"
            "不得把对话上下文、历史消息或未 reuse 的旧证据当作事实。\n"
            f"{header}\n"
            "<evidence_pool>\n"
            f"{body}\n"
            "</evidence_pool>"
        )


@dataclass
class ConversationContext:
    user_question: str
    session: SessionState
    understanding: UnderstandingResult | None = None
    topic_shift: bool = False
    entity_transition: bool = False
    selected_entity: str | None = None
    clarification_history: list[dict[str, Any]] = field(default_factory=list)
    clarification_callback: bool = False
    head_entity: str | None = None
    previous_head_entity: str | None = None
    resolved_question: str = ""
    rewritten: bool = False
    linked_entities: list[dict[str, Any]] = field(default_factory=list)
    domain_context: str = ""
    version: str = "v1"

    @classmethod
    def from_request(
        cls,
        question: str,
        history: list[dict[str, Any]] | None,
        *,
        entity_name: str | None = None,
        doc_category: str | None = None,
        clarification_question: str | None = None,
        clarification_selected: str | None = None,
    ) -> "ConversationContext":
        selected = (clarification_selected or "").strip() or None
        session = session_from_history(
            history,
            entity_name=entity_name,
            doc_category=doc_category,
        )
        previous = None
        if session.focus and session.focus.confirmed_entity:
            previous = session.focus.confirmed_entity.strip() or None
        if not previous:
            previous = (session.resolved_entity or "").strip() or None
        head = selected or (entity_name or "").strip() or previous
        clar_hist: list[dict[str, Any]] = []
        if clarification_question or selected:
            clar_hist.append({
                "question": clarification_question or "",
                "selected": selected or "",
            })
        return cls(
            user_question=(question or "").strip(),
            session=session,
            selected_entity=selected,
            clarification_history=clar_hist,
            clarification_callback=bool(selected),
            head_entity=head,
            previous_head_entity=previous,
            resolved_question=(question or "").strip(),
        )

    def to_prompt(self, *, history_summary: str | None = None) -> str:
        lines = [
            "## 对话上下文（ConversationContext）",
            "以下内容仅用于理解指代、省略、切题和用户意图，**不得作为知识事实依据**。",
        ]
        if self.user_question:
            lines.append(f"- 当前问题: {self.user_question}")
        if self.resolved_question and self.resolved_question != self.user_question:
            lines.append(f"- 当前解析问题: {self.resolved_question}")
        if self.head_entity:
            lines.append(f"- 当前实体: {self.head_entity}")
        if self.selected_entity:
            lines.append(f"- 用户已选实体: {self.selected_entity}")
        if self.linked_entities:
            cands = [str(e.get("canonical_name") or "") for e in self.linked_entities if e.get("canonical_name")]
            if cands:
                lines.append(f"- 已定位图谱实体: {', '.join(cands)}")
        if self.domain_context:
            lines.append(f"- 图谱关联背景: {self.domain_context}")
        if self.topic_shift:
            lines.append("- topic_shift: true")
        if self.entity_transition:
            lines.append("- entity_transition: true（旧证据默认不可引用）")
        if self.clarification_callback:
            lines.append("- 本轮为澄清回调：禁止 reuse_evidence，必须重新检索")
        focus = ""
        if self.understanding is not None:
            focus = getattr(self.understanding, "dialogue_focus", "") or ""
        elif self.session.focus is not None:
            focus = self.session.focus.to_text()
        if focus:
            lines.append(f"- 对话焦点: {focus}")
        if history_summary:
            lines.append(f"- 历史摘要: {history_summary}")
        if self.session and self.session.turns:
            recent_pairs = []
            for t in self.session.turns[-4:]:
                role = "用户" if t.role == "user" else "助手"
                content = (t.content or "").strip()
                if content:
                    recent_pairs.append(f"  {role}: {content[:120]}")
            if recent_pairs:
                lines.append("- 近期对话历史:\n" + "\n".join(recent_pairs))
        return "\n".join(lines) + "\n"

    def to_trace(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "topic_shift": self.topic_shift,
            "entity_transition": self.entity_transition,
            "head_entity": self.head_entity,
            "selected_entity": self.selected_entity,
            "resolved_question": self.resolved_question,
            "clarification_callback": self.clarification_callback,
            "linked_count": len(self.linked_entities),
            "not_a_fact_source": True,
        }


@dataclass
class AgentTurnResult:
    conversation: ConversationContext
    evidence: EvidencePool
    plan: Any = None
    route: str = "retrieve"
    agent_steps: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    retrieve_attempts: int = 0
    reuse: bool = False
    clarify: dict[str, Any] | None = None
    entity_link: dict[str, Any] | None = None
    llm_gate: str = ""
    answer_gate: dict[str, Any] = field(default_factory=dict)
    evidence_gap: list[dict[str, Any]] = field(default_factory=list)
    retrieve_improvement: int | None = None
    retrieval_trace: dict[str, Any] | None = None

    def to_trace(self) -> dict[str, Any]:
        return {
            "agent_steps": list(self.agent_steps),
            "tools": list(self.tools),
            "route": self.route,
            "conversation_context": self.conversation.to_trace(),
            "evidence_groups": self.evidence.to_trace(),
            "budget": dict(self.budget),
            "fallback": list(self.fallbacks),
            "retrieve_attempts": self.retrieve_attempts,
            "reuse": self.reuse,
            "entity_link": dict(self.entity_link or {}),
            "gate": self.llm_gate,
            "answer_gate": dict(self.answer_gate or {}),
            "evidence_gap": list(self.evidence_gap),
            "retrieve_improvement": self.retrieve_improvement,
            "retrieval_trace": dict(self.retrieval_trace or {}),
            "clarify": {
                "needs_clarification": bool((self.clarify or {}).get("needs_clarification")),
                "reason": (self.clarify or {}).get("reason"),
                "option_count": len((self.clarify or {}).get("options") or []),
            } if self.clarify else {},
        }
