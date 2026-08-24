"""ConversationContext / EvidencePool / Tool contract (PRD V1.3 Phase 1)."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, Mapping


from rag_knowledge.services.conversation_context import (
    SessionState,
    UnderstandingResult,
    session_from_history,
)

EvidenceStatus = Literal["ACTIVE", "FROZEN"]
ToolAction = Literal["tool_call", "finish", "finalize"]


class ToolProgressStatus:
    """Canonical outcome of one guarded tool attempt."""

    PROGRESS = "PROGRESS"
    NO_PROGRESS = "NO_PROGRESS"
    DENIED = "DENIED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class EvidenceDelta:
    """Evidence changes produced by one tool attempt."""

    new_chunks: int = 0
    new_entities: int = 0
    new_relations: int = 0
    evidence_version_before: int = 0
    evidence_version_after: int = 0
    status: str = ToolProgressStatus.PROGRESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "new_chunks": self.new_chunks,
            "new_entities": self.new_entities,
            "new_relations": self.new_relations,
            "evidence_version_before": self.evidence_version_before,
            "evidence_version_after": self.evidence_version_after,
            "status": self.status,
        }


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
    """Unified tool result; ``status`` is authoritative for its evidence delta."""

    tool: str
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    elapsed_ms: float = 0.0
    fallback: str | None = None
    status: str = ToolProgressStatus.PROGRESS
    evidence_delta: EvidenceDelta = field(default_factory=EvidenceDelta)

    def __post_init__(self) -> None:
        if not self.ok and self.status == ToolProgressStatus.PROGRESS:
            self.status = ToolProgressStatus.ERROR
        if self.evidence_delta.status != self.status:
            self.evidence_delta = replace(self.evidence_delta, status=self.status)


@dataclass
class AgentDecision:
    action: ToolAction = "finish"
    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    thought: str = ""
    source: str = "llm"
    gate: str = ""
    missing: str = ""
    gap: str | None = None
    expected_gain: str | None = None
    focus_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # ``thought`` remains a trace compatibility field. Product-visible
        # execution events use ``reason`` exclusively.
        if not self.reason and self.thought:
            self.reason = self.thought
        elif self.reason and not self.thought:
            self.thought = self.reason

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action,
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "reason": self.reason,
            "source": self.source,
        }
        if self.focus_evidence_ids:
            payload["focus_evidence_ids"] = list(self.focus_evidence_ids)
        if self.gap:
            payload["gap"] = self.gap
        if self.expected_gain:
            payload["expected_gain"] = self.expected_gain
        if self.gate:
            payload["gate"] = self.gate
        return payload


class ExecutionEventType(str, Enum):
    """Finite event vocabulary shared by the runtime, SSE and QA Trace."""

    UNDERSTANDING = "understanding"
    DECISION = "decision"
    GUARD = "guard"
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"
    EVIDENCE_UPDATE = "evidence_update"
    EVIDENCE_GAP = "evidence_gap"
    FINALIZATION_CHECK = "finalization_check"
    FINALIZATION_REQUESTED = "finalization_requested"
    FINALIZATION_REJECTED = "finalization_rejected"
    EVIDENCE_SNAPSHOT_CREATED = "evidence_snapshot_created"
    ANSWER_GENERATION_STARTED = "answer_generation_started"
    CANDIDATE_STATUS = "candidate_status"
    HELPER_GROUNDING_REVIEW_STARTED = "helper_grounding_review_started"
    REVIEW_STATUS = "review_status"
    REWRITE_STATUS = "rewrite_status"
    PUBLICATION = "publication"
    FINAL_ANSWER = "final_answer"
    SOURCES = "sources"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    NOTICE = "notice"
    CLARIFY = "clarify"
    TRACE = "trace"
    STATUS = "status"
    PIPELINE = "pipeline"
    TOKEN = "token"
    THINKING = "thinking"
    DONE = "done"


@dataclass(frozen=True)
class ExecutionEvent:
    """Normalized execution event used as both SSE and Trace source data."""

    event_type: ExecutionEventType | str
    data: Any = None
    sequence: int = 0
    elapsed_ms: float = 0.0

    def __post_init__(self) -> None:
        raw_type = (
            self.event_type.value
            if isinstance(self.event_type, ExecutionEventType)
            else str(self.event_type or "").strip()
        )
        try:
            normalized_type = ExecutionEventType(raw_type)
        except ValueError as exc:
            raise ValueError(f"unsupported execution event type: {raw_type}") from exc
        object.__setattr__(self, "event_type", normalized_type)
        object.__setattr__(self, "data", deepcopy(self.data))
        object.__setattr__(self, "sequence", max(0, int(self.sequence or 0)))
        object.__setattr__(self, "elapsed_ms", round(max(0.0, float(self.elapsed_ms or 0.0)), 1))

    def to_sse(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.event_type.value}
        if self.data is not None:
            payload["data"] = deepcopy(self.data)
        return payload

    def to_trace(self) -> dict[str, Any]:
        return {
            **self.to_sse(),
            "sequence": self.sequence,
            "elapsed_ms": self.elapsed_ms,
        }


def normalize_execution_event(
    event: ExecutionEvent | Mapping[str, Any],
    *,
    sequence: int | None = None,
    elapsed_ms: float | None = None,
) -> ExecutionEvent:
    """Single validation/normalization entry for runtime and trace events."""

    if isinstance(event, ExecutionEvent):
        return ExecutionEvent(
            event_type=event.event_type,
            data=event.data,
            sequence=event.sequence if sequence is None else sequence,
            elapsed_ms=event.elapsed_ms if elapsed_ms is None else elapsed_ms,
        )
    if not isinstance(event, Mapping):
        raise TypeError("execution event must be an ExecutionEvent or mapping")
    return ExecutionEvent(
        event_type=str(event.get("type") or ""),
        data=event.get("data"),
        sequence=int(event.get("sequence") or 0) if sequence is None else sequence,
        elapsed_ms=float(event.get("elapsed_ms") or 0.0) if elapsed_ms is None else elapsed_ms,
    )



@dataclass
class AttemptedGap:
    gap: str
    target_scope: str | None
    status: str
    tool: str
    query: str | None = None
    step: int = 0


@dataclass
class AttemptedGapRegistry:
    entries: list[AttemptedGap] = field(default_factory=list)

    @staticmethod
    def normalize_gap(gap: str | None) -> str:
        return " ".join(str(gap or "").strip().casefold().split())

    @staticmethod
    def normalize_scope(scope: str | None) -> str:
        return str(scope or "").strip().casefold()

    def record(
        self,
        *,
        gap: str | None,
        target_scope: str | None,
        status: str,
        tool: str,
        query: str | None = None,
        step: int = 0,
    ) -> None:
        norm_gap = self.normalize_gap(gap)
        if not norm_gap:
            return
        self.entries.append(
            AttemptedGap(
                gap=norm_gap,
                target_scope=self.normalize_scope(target_scope),
                status=status,
                tool=tool,
                query=query,
                step=step,
            )
        )

    def is_exhausted(self, gap: str | None, target_scope: str | None = None) -> bool:
        norm_gap = self.normalize_gap(gap)
        if not norm_gap:
            return False
        norm_scope = self.normalize_scope(target_scope)
        for entry in reversed(self.entries):
            if (
                entry.gap == norm_gap
                and entry.target_scope == norm_scope
                and entry.status == ToolProgressStatus.NO_PROGRESS
            ):
                return True
        return False


@dataclass
class AgentBudget:
    max_steps: int = 8
    max_retrieve_attempts: int | None = 2
    hard_retrieve_cap: int = 8
    steps_used: int = 0
    retrieve_attempts: int = 0
    call_history: list[tuple[str, str]] = field(default_factory=list)

    def can_step(self) -> bool:
        return self.steps_used < self.max_steps

    def effective_max_retrieves(self) -> int:
        return self.max_retrieve_attempts if self.max_retrieve_attempts is not None else self.hard_retrieve_cap

    def can_retrieve(self) -> bool:
        return self.retrieve_attempts < self.effective_max_retrieves()

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

    @staticmethod
    def _call_fingerprint(
        arguments: dict[str, Any] | str | None,
        gap: str | None = None,
        expected_gain: str | None = None,
    ) -> str:
        # Keep gap/gain in the public signature for compatibility. Semantic gap
        # cycles are governed by AttemptedGapRegistry, not exact-call identity.
        payload: dict[str, Any] = {}
        if isinstance(arguments, dict):
            payload["args"] = arguments
        elif isinstance(arguments, str):
            payload["args"] = arguments.strip()
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).casefold()

    def record_call(
        self,
        tool: str,
        arguments: dict[str, Any] | str | None = None,
        gap: str | None = None,
        expected_gain: str | None = None,
    ) -> None:
        self.call_history.append((tool, self._call_fingerprint(arguments, gap=gap, expected_gain=expected_gain)))

    def is_cycle(
        self,
        tool: str,
        arguments: dict[str, Any] | str | None = None,
        gap: str | None = None,
        expected_gain: str | None = None,
    ) -> bool:
        """Detect an immediately repeated tool call with identical arguments."""
        if not self.call_history:
            return False
        return self.call_history[-1] == (tool, self._call_fingerprint(arguments, gap=gap, expected_gain=expected_gain))

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_retrieve_attempts": self.effective_max_retrieves(),
            "hard_retrieve_cap": self.hard_retrieve_cap,
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
    target_entity: str | None = None
    relation_key: str | None = None
    grant_id: str | None = None
    provenance: list[dict[str, Any]] = field(default_factory=list)

    def to_trace(self) -> dict[str, Any]:
        payload = {
            "question_id": self.question_id,
            "kind": self.kind,
            "retrieve_index": self.retrieve_index,
            "chunk_ids": list(self.chunk_ids),
            "status": self.status,
            "head_entity": self.head_entity,
            "target_entity": self.target_entity,
            "relation_key": self.relation_key,
            "grant_id": self.grant_id,
            "provenance": list(self.provenance),
            "tool": self.tool,
        }
        return payload


def _chunk_id(doc: dict[str, Any]) -> str:
    meta = doc.get("metadata") if isinstance(doc, dict) else None
    if isinstance(meta, dict):
        return str(meta.get("chunk_id") or "")
    return ""


def _document_evidence_key(doc: dict[str, Any]) -> str:
    chunk_id = _chunk_id(doc)
    if chunk_id:
        return f"chunk:{chunk_id}"
    stable = json.dumps(
        {
            "content": doc.get("content"),
            "metadata": doc.get("metadata") or {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"anonymous:{stable}"


@dataclass
class EvidencePool:
    question_id: str
    groups: list[EvidenceGroup] = field(default_factory=list)
    _retrieve_seq: int = 0
    evidence_version: int = 0

    def _touch(self) -> None:
        self.evidence_version += 1

    @staticmethod
    def _group_evidence_keys(group: EvidenceGroup) -> set[str]:
        if group.kind == "relation" and group.relation_key:
            relation_key = str(group.relation_key).strip().casefold()
            return {f"relation:{relation_key}"} if relation_key else set()
        return {
            _document_evidence_key(doc)
            for doc in group.docs
            if isinstance(doc, dict)
        }

    def _evidence_keys(self, *, active_only: bool = False) -> set[str]:
        keys: set[str] = set()
        for group in self.groups:
            if active_only and group.status != "ACTIVE":
                continue
            keys.update(self._group_evidence_keys(group))
        return keys

    def seed_previous_cited(
        self,
        docs: list[dict[str, Any]] | None,
        *,
        head_entity: str | None = None,
    ) -> EvidenceGroup | None:
        before_keys = self._evidence_keys()
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
        if self._evidence_keys() != before_keys:
            self._touch()
        return group

    def add_retrieve(
        self,
        docs: list[dict[str, Any]],
        *,
        query: str | None = None,
        tool: str = "retrieve_kb",
        head_entity: str | None = None,
        target_entity: str | None = None,
        grant: Any = None,
    ) -> EvidenceGroup:
        before_keys = self._evidence_keys(active_only=True)
        self._retrieve_seq += 1
        cleaned = [d for d in (docs or []) if isinstance(d, dict)]
        provenance = []
        grant_id = None
        if grant is not None:
            grant_id = str(getattr(grant, "grant_id", "") or "") or None
            provenance.append({
                "source_type": str(getattr(grant, "source_type", "") or ""),
                "source_ref": str(getattr(grant, "source_ref", "") or ""),
                "target_entities": list(getattr(grant, "target_entities", ()) or ()),
                "hop_depth": int(getattr(grant, "hop_depth", 0) or 0),
            })
        group = EvidenceGroup(
            group_id=uuid.uuid4().hex[:12],
            question_id=self.question_id,
            kind="retrieve",
            retrieve_index=self._retrieve_seq,
            chunk_ids=[cid for cid in (_chunk_id(d) for d in cleaned) if cid],
            docs=list(cleaned),
            status="ACTIVE",
            head_entity=head_entity,
            target_entity=target_entity or getattr(grant, "primary_root", None),
            grant_id=grant_id,
            provenance=provenance,
            query=query,
            tool=tool,
        )
        self.groups.append(group)
        if self._evidence_keys(active_only=True) != before_keys:
            self._touch()
        return group

    def add_relation(
        self,
        *,
        relation_key: str,
        target_entity: str | None = None,
        grant: Any = None,
        provenance: list[dict[str, Any]] | None = None,
    ) -> EvidenceGroup:
        normalized_relation = str(relation_key or "").strip().casefold()
        for group in self.groups:
            if (
                group.kind == "relation"
                and str(group.relation_key or "").strip().casefold() == normalized_relation
            ):
                return group
        before_keys = self._evidence_keys(active_only=True)
        provenance_items = list(provenance or [])
        source_ref = ""
        if provenance_items:
            source_ref = str(provenance_items[0].get("source_ref") or "").strip()
        if not source_ref and grant is not None:
            source_ref = str(getattr(grant, "source_ref", "") or "").strip()
        relation_id = source_ref.split("relation:", 1)[1] if source_ref.startswith("relation:") else ""
        relation_type = ""
        if provenance_items:
            relation_type = str(provenance_items[0].get("relation_type") or "").strip()
        synthetic_chunk_id = f"graph-relation:{relation_id or uuid.uuid4().hex[:12]}"
        identity_scope_id = str(getattr(grant, "identity_scope_id", "") or "")
        grant_id = str(getattr(grant, "grant_id", "") or "") or None
        relation_doc = {
            "content": relation_key,
            "metadata": {
                "chunk_id": synthetic_chunk_id,
                "source_type": "graph_relation",
                "file_name": "知识图谱（已审核关系）",
                "document_entity": target_entity or "",
                "evidence_target_entity": target_entity or "",
                "relation_key": relation_key,
                "relation_id": relation_id,
                "relation_type": relation_type,
                "grant_id": grant_id or "",
                "grant_admitted": True,
                "identity_scope_id": identity_scope_id,
                "provenance_source_type": "graph_relation",
                "provenance_path": provenance_items[0] if provenance_items else {},
            },
        }
        group = EvidenceGroup(
            group_id=uuid.uuid4().hex[:12],
            question_id=self.question_id,
            kind="relation",
            retrieve_index=None,
            chunk_ids=[synthetic_chunk_id],
            docs=[relation_doc],
            status="ACTIVE",
            target_entity=target_entity,
            relation_key=relation_key,
            grant_id=grant_id,
            provenance=provenance_items,
            tool="link_entities",
        )
        self.groups.append(group)
        if self._evidence_keys(active_only=True) != before_keys:
            self._touch()
        return group

    def has_relation(self, relation_key: str) -> bool:
        key = str(relation_key or "").strip().casefold()
        return any(
            group.kind == "relation"
            and str(group.relation_key or "").strip().casefold() == key
            for group in self.groups
        )

    def add_external(
        self,
        docs: list[dict[str, Any]],
        *,
        query: str | None = None,
        tool: str = "web_search",
        kind: str = "web_search",
        head_entity: str | None = None,
    ) -> EvidenceGroup:
        before_keys = self._evidence_keys(active_only=True)
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
        if self._evidence_keys(active_only=True) != before_keys:
            self._touch()
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
        before_keys = self._evidence_keys(active_only=True)
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
        if self._evidence_keys(active_only=True) != before_keys:
            self._touch()
        return group

    def citable_docs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in self.groups:
            if group.status != "ACTIVE":
                continue
            for doc in group.docs:
                key = _document_evidence_key(doc)
                if key in seen:
                    continue
                seen.add(key)
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
            target = f" target={group.target_entity}" if group.target_entity else ""
            relation = f" relation={group.relation_key}" if group.relation_key else ""
            grant = f" grant={group.grant_id}" if group.grant_id else ""
            groups.append(
                f"- {label} status={group.status} chunks={len(group.chunk_ids)}{target}{relation}{grant}"
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

    def decision_digest(
        self,
        *,
        max_items: int = 6,
        max_fact_chars: int = 180,
    ) -> str:
        """Compact factual view for Controller decisions, not answer generation."""
        items: list[str] = []
        for index, doc in enumerate(self.citable_docs()[:max_items], start=1):
            meta = doc.get("metadata") or {}
            chunk_id = str(meta.get("chunk_id") or f"evidence-{index}")
            entity = str(
                meta.get("evidence_target_entity")
                or meta.get("document_entity")
                or "未标注"
            ).strip()
            section = str(meta.get("section_path") or meta.get("section_title") or "未标注").strip()
            relation = str(meta.get("relation_key") or "").strip()
            fact = " ".join(str(doc.get("content") or "").split())
            if len(fact) > max_fact_chars:
                fact = f"{fact[:max_fact_chars].rstrip()}…"
            parts = [f"Evidence #{index}", f"id={chunk_id}", f"entity={entity}"]
            if relation:
                parts.append(f"relation={relation}")
            else:
                parts.extend((f"section={section}", f"fact={fact or '（空）'}"))
            items.append("; ".join(parts))
        return "\n".join(items) if items else "（暂无可用证据）"

    def create_snapshot(
        self,
        *,
        verdict: dict[str, Any],
        focus_evidence_ids: list[str] | tuple[str, ...] | None = None,
    ) -> "EvidenceSnapshot":
        """Freeze the current citable evidence into an answer-only snapshot."""
        docs = self.citable_docs_renumbered()
        wanted = {str(item).strip() for item in (focus_evidence_ids or ()) if str(item).strip()}
        if wanted:
            focused: list[dict[str, Any]] = []
            rest: list[dict[str, Any]] = []
            for doc in docs:
                meta = doc.get("metadata") or {}
                evidence_id = str(meta.get("evidence_id") or meta.get("chunk_id") or "")
                (focused if evidence_id in wanted else rest).append(doc)
            docs = focused + rest
        self.freeze_active()
        return EvidenceSnapshot.from_documents(
            question_id=self.question_id,
            documents=docs,
            verdict=verdict,
            evidence_groups=self.to_trace(),
            evidence_version=self.evidence_version,
        )


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(k): _freeze_value(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: Any) -> Any:
    if isinstance(value, MappingProxyType):
        return {key: _thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value


@dataclass(frozen=True)
class EvidenceSnapshot:
    """Immutable evidence input shared by Answer, Grounding and citations."""

    snapshot_id: str
    question_id: str
    evidence_items: tuple[Any, ...]
    evidence_verdict: Any
    evidence_groups: tuple[Any, ...] = ()
    evidence_version: int = 0

    @classmethod
    def from_documents(
        cls,
        *,
        question_id: str,
        documents: list[dict[str, Any]],
        verdict: dict[str, Any],
        evidence_groups: list[dict[str, Any]] | None = None,
        evidence_version: int = 0,
    ) -> "EvidenceSnapshot":
        return cls(
            snapshot_id=f"evs_{uuid.uuid4().hex[:16]}",
            question_id=question_id,
            evidence_items=tuple(_freeze_value(dict(document)) for document in documents),
            evidence_verdict=_freeze_value(dict(verdict or {})),
            evidence_groups=tuple(_freeze_value(dict(group)) for group in (evidence_groups or [])),
            evidence_version=int(evidence_version or 0),
        )

    def documents(self) -> list[dict[str, Any]]:
        return [_thaw_value(item) for item in self.evidence_items]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_snapshot_id": self.snapshot_id,
            "question_id": self.question_id,
            "evidence_version": self.evidence_version,
            "evidence_items": self.documents(),
            "evidence_verdict": _thaw_value(self.evidence_verdict),
            "evidence_groups": _thaw_value(self.evidence_groups),
        }


@dataclass(frozen=True)
class AnswerGenerationContext:
    """Clean answer-stage contract; it intentionally has no tool/trace fields."""

    original_question: str
    resolved_question: str
    conversation_context: str
    answer_contract: Any
    evidence_snapshot_id: str
    evidence_items: tuple[Any, ...]
    evidence_verdict: Any
    answer_policy: Any
    execution_summary: str | None = None
    evidence_version: int = 0

    @classmethod
    def from_snapshot(
        cls,
        *,
        original_question: str,
        resolved_question: str,
        conversation_context: str,
        snapshot: EvidenceSnapshot,
        answer_contract: dict[str, Any] | None = None,
        answer_policy: dict[str, Any] | None = None,
        execution_summary: str | None = None,
    ) -> "AnswerGenerationContext":
        return cls(
            original_question=original_question,
            resolved_question=resolved_question,
            conversation_context=conversation_context,
            answer_contract=_freeze_value(dict(answer_contract or {})),
            evidence_snapshot_id=snapshot.snapshot_id,
            evidence_items=snapshot.evidence_items,
            evidence_verdict=snapshot.evidence_verdict,
            answer_policy=_freeze_value(dict(answer_policy or {})),
            execution_summary=execution_summary,
            evidence_version=snapshot.evidence_version,
        )

    def documents(self) -> list[dict[str, Any]]:
        return [_thaw_value(item) for item in self.evidence_items]

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_question": self.original_question,
            "resolved_question": self.resolved_question,
            "conversation_context": self.conversation_context,
            "answer_contract": _thaw_value(self.answer_contract),
            "evidence_snapshot_id": self.evidence_snapshot_id,
            "evidence_items": self.documents(),
            "evidence_verdict": _thaw_value(self.evidence_verdict),
            "answer_policy": _thaw_value(self.answer_policy),
            "evidence_version": self.evidence_version,
            "execution_summary": self.execution_summary,
        }


@dataclass
class ConversationContext:
    user_question: str
    session: SessionState
    understanding: UnderstandingResult | None = None
    semantic_task: Any = None
    topic_shift: bool = False
    entity_transition: bool = False
    selected_entity: str | None = None
    clarification_option_id: str | None = None
    clarification_selected_candidate: dict[str, Any] | None = None
    clarification_selection_kind: str | None = None
    clarification_free_text: str | None = None
    clarification_history: list[dict[str, Any]] = field(default_factory=list)
    clarification_callback: bool = False
    head_entity: str | None = None
    previous_head_entity: str | None = None
    resolved_question: str = ""
    rewritten: bool = False
    linked_entities: list[dict[str, Any]] = field(default_factory=list)
    domain_context: str = ""
    scope: Any = None
    identity_status: str = "unresolved"
    confirmed_entity: str | None = None
    confirmed_topic: str | None = None
    raw_entity_mention: str | None = None
    confirmed_entities: tuple[str, ...] = ()
    raw_entity_mentions: tuple[str, ...] = ()
    version: str = "v3"

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
        clarification_option_id: str | None = None,
        clarification_selected_candidate: dict[str, Any] | None = None,
        clarification_selection_kind: str | None = None,
        clarification_free_text: str | None = None,
        understanding: UnderstandingResult | None = None,
    ) -> "ConversationContext":
        from rag_knowledge.services.dialogue_understanding import (
            SemanticTaskContext,
            build_semantic_task_context,
            collapse_clarification_selection,
        )
        from rag_knowledge.services.identity_scope import IdentityScopeResolver

        selected = (clarification_selected or "").strip() or None
        option_id = (clarification_option_id or "").strip() or None
        selection_kind = (clarification_selection_kind or "").strip().casefold() or None
        free_text = (clarification_free_text or "").strip() or None
        selected_candidate = (
            dict(clarification_selected_candidate)
            if isinstance(clarification_selected_candidate, dict)
            else None
        )
        effective_question = (question or "").strip()
        if free_text and free_text not in effective_question:
            effective_question = f"{effective_question}\n用户在澄清卡片中补充：{free_text}".strip()
        is_callback = bool(selected or option_id or selection_kind or free_text)
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

        previous_identity = ""
        for source in session.last_sources or []:
            if not isinstance(source, dict):
                continue
            root = str(
                source.get("identity_primary_entity")
                or source.get("scope_root")
                or ""
            ).strip()
            strength = str(source.get("scope_binding_strength") or "").strip().lower()
            if root and strength in {"confirmed", "explicit"}:
                if previous_identity and previous_identity.casefold() != root.casefold():
                    previous_identity = ""
                    break
                previous_identity = root
        if not previous and previous_identity:
            previous = previous_identity

        stage1 = understanding or UnderstandingResult(
            mode="retrieve",
            user_utterance=effective_question,
            resolved_question=effective_question,
            confidence=1.0,
        )
        if stage1.semantic_task_context:
            semantic_task = SemanticTaskContext.from_dict(stage1.semantic_task_context)
        else:
            semantic_task = build_semantic_task_context(effective_question, stage1)
            stage1 = replace(stage1, semantic_task_context=semantic_task.to_dict())

        identity_scope = IdentityScopeResolver.resolve(
            semantic_task,
            entity_name=entity_name,
            clarification_selected=selected,
            selected_candidate=selected_candidate,
            previous_confirmed_entity=previous,
            doc_category=doc_category,
        )
        if selected and identity_scope.primary_entity:
            semantic_task = collapse_clarification_selection(
                effective_question,
                semantic_task,
                identity_scope.primary_entity,
            )
            stage1 = replace(
                stage1,
                resolved_question=semantic_task.resolved_question,
                semantic_task_context=semantic_task.to_dict(),
            )
        elif selected and getattr(identity_scope, "confirmed_topic", None):
            stage1 = replace(
                stage1,
                resolved_question=f"{identity_scope.confirmed_topic} 的相关信息",
            )
        head = identity_scope.primary_entity

        clar_hist: list[dict[str, Any]] = []
        if clarification_question or is_callback:
            clar_hist.append({
                "question": clarification_question or "",
                "selected": selected or "",
                "option_id": option_id,
                "selection_kind": selection_kind,
                "free_text": free_text,
                "selected_candidate": selected_candidate or {},
            })
        return cls(
            user_question=effective_question,
            session=session,
            understanding=stage1,
            semantic_task=semantic_task,
            selected_entity=(
                getattr(identity_scope, "confirmed_entity", None)
                if getattr(identity_scope, "identity_status", "") == "confirmed_entity"
                else None
            ),
            clarification_option_id=option_id,
            clarification_selected_candidate=selected_candidate,
            clarification_selection_kind=selection_kind,
            clarification_free_text=free_text,
            clarification_history=clar_hist,
            clarification_callback=is_callback,
            head_entity=head,
            previous_head_entity=previous,
            resolved_question=semantic_task.resolved_question or (question or "").strip(),
            scope=identity_scope,
            identity_status=getattr(identity_scope, "identity_status", "unresolved"),
            confirmed_entity=getattr(identity_scope, "confirmed_entity", None) or head,
            confirmed_topic=getattr(identity_scope, "confirmed_topic", None),
            raw_entity_mention=getattr(identity_scope, "raw_entity_mention", None),
            confirmed_entities=tuple(getattr(identity_scope, "confirmed_entities", ()) or ()),
            raw_entity_mentions=tuple(getattr(identity_scope, "raw_entity_mentions", ()) or ()),
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
        if self.confirmed_topic:
            lines.append(f"- 当前确认主题方向: {self.confirmed_topic}（非知识库实体）")
        elif self.confirmed_entities and len(self.confirmed_entities) > 1:
            lines.append(f"- 当前确认多实体范围: {', '.join(self.confirmed_entities)}")
        elif self.confirmed_entity:
            lines.append(f"- 当前主体身份: {self.confirmed_entity}")
        elif self.head_entity:
            lines.append(f"- 当前主体身份: {self.head_entity}")
        elif self.identity_status == "unresolved":
            lines.append("- 当前主体身份: 未确认（需澄清或仅做常规文本问答）")
        if self.semantic_task is not None:
            mentioned = list(getattr(self.semantic_task, "mentioned_entities", ()) or ())
            if mentioned:
                lines.append(f"- 本轮显式提及实体: {', '.join(mentioned)}")
            task_type = str(getattr(self.semantic_task, "task_type", "") or "")
            if task_type:
                lines.append(f"- 任务结构: {task_type}")
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
            "identity_status": self.identity_status,
            "confirmed_entity": self.confirmed_entity,
            "confirmed_entities": list(self.confirmed_entities),
            "confirmed_topic": self.confirmed_topic,
            "raw_entity_mention": self.raw_entity_mention,
            "raw_entity_mentions": list(self.raw_entity_mentions),
            "selected_entity": self.selected_entity,
            "clarification_option_id": self.clarification_option_id,
            "clarification_selected_candidate": dict(self.clarification_selected_candidate or {}),
            "clarification_selection_kind": self.clarification_selection_kind,
            "clarification_free_text": self.clarification_free_text,
            "resolved_question": self.resolved_question,
            "semantic_task_context": (
                self.semantic_task.to_dict()
                if self.semantic_task is not None and hasattr(self.semantic_task, "to_dict")
                else {}
            ),
            "identity_scope": (
                self.scope.to_dict()
                if self.scope is not None and hasattr(self.scope, "to_dict")
                else {}
            ),
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
    terminal_action: str = ""
    evidence_snapshot: EvidenceSnapshot | None = None
    answer_context: AnswerGenerationContext | None = None
    answer_contract: dict[str, Any] = field(default_factory=dict)
    finalization_attempts: int = 0
    finalization_rejections: int = 0
    answer_stage_started: bool = False
    lifecycle_events: list[dict[str, Any]] = field(default_factory=list)

    def to_trace(self) -> dict[str, Any]:
        grant_authorizations = []
        for tool in self.tools:
            data = tool.get("data") if isinstance(tool, dict) else None
            auth = data.get("grant_authorization") if isinstance(data, dict) else None
            if isinstance(auth, dict):
                grant_authorizations.append(dict(auth))
        return {
            "agent_steps": list(self.agent_steps),
            "tools": list(self.tools),
            "grant_authorizations": grant_authorizations,
            "route": self.route,
            "conversation_context": self.conversation.to_trace(),
            "evidence_groups": self.evidence.to_trace(),
            "evidence_version": self.evidence.evidence_version,
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
            "terminal_action": self.terminal_action,
            "evidence_snapshot_id": (
                self.evidence_snapshot.snapshot_id
                if self.evidence_snapshot is not None
                else None
            ),
            "evidence_snapshot_version": (
                self.evidence_snapshot.evidence_version
                if self.evidence_snapshot is not None
                else None
            ),
            "answer_contract": dict(self.answer_contract or {}),
            "evidence_verdict": dict(
                (self.evidence_snapshot.evidence_verdict if self.evidence_snapshot is not None else {})
                or {}
            ),
            "finalization_attempts": self.finalization_attempts,
            "finalization_rejections": self.finalization_rejections,
            "answer_stage_started": self.answer_stage_started,
            "lifecycle_events": deepcopy(self.lifecycle_events),
            "clarify": {
                "needs_clarification": bool((self.clarify or {}).get("needs_clarification")),
                "reason": (self.clarify or {}).get("reason"),
                "option_count": len((self.clarify or {}).get("options") or []),
            } if self.clarify else {},
        }
