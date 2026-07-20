"""EvidencePack construction and lightweight answer-governance helpers."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


NO_KNOWLEDGE_ANSWER = "当前知识库中未查询到相关内容。"
_CITATION_RE = re.compile(r"\[(\d+)\]|\((\d+)\)")
_COMPLETE_RE = re.compile(r"完整|全部|所有步骤|分别说明|逐一|按顺序|端到端")
_KEY_VALUE_RE = re.compile(r"(?im)^\s*([A-Za-z][A-Za-z0-9_.-]{1,80})\s*(?:=|:|：)\s*([^\s,，;；]+)")
_TLS_PORT_RE = re.compile(
    r"(?im)(?:tls(?:/dtls)?[-_\s]?listening[-_\s]?port|tls\s*端口)\s*(?:=|:|：|\||为|是)\s*(\d{2,5})"
)


def citation_ids(answer: str) -> set[int]:
    return {int(left or right) for left, right in _CITATION_RE.findall(answer or "")}


def cited_sources(answer: str, source_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = citation_ids(answer)
    return [
        source for source in source_docs
        if source.get("metadata", {}).get("citation_id") in wanted
    ]


def _evidence_item(source: dict[str, Any], *, drop_reason: str | None = None) -> dict[str, Any]:
    meta = source.get("metadata", {})
    item = {
        "index": meta.get("citation_id"),
        "document": meta.get("source") or meta.get("file_name") or "",
        "source": meta.get("source") or meta.get("file_name") or "",
        "section_id": meta.get("section_id") or "",
        "section_path": meta.get("section_path") or meta.get("section_title") or "",
        "chunk_id": meta.get("chunk_id") or "",
        "snippet": str(source.get("content") or "")[:500],
    }
    if drop_reason:
        item["drop_reason"] = drop_reason
    return item


def _conflicts(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values_by_key: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for source in sources:
        content = str(source.get("content") or "")
        for key, value in _KEY_VALUE_RE.findall(content):
            item = _evidence_item(source)
            item["value"] = value
            values_by_key[key.lower()][value].append(item)
        for value in _TLS_PORT_RE.findall(content):
            item = _evidence_item(source)
            item["value"] = value
            values_by_key["tls_port"][value].append(item)
    conflicts = []
    for key, values in values_by_key.items():
        if len(values) <= 1:
            continue
        entries = []
        seen: set[tuple[Any, ...]] = set()
        for value, items in values.items():
            for item in items:
                marker = (value, item.get("chunk_id"), item.get("index"))
                if marker in seen:
                    continue
                seen.add(marker)
                entries.append(item)
        conflicts.append({"key": key, "values": entries})
    return conflicts


def build_evidence_pack(
    answer: str,
    retrieved_docs: list[dict[str, Any]],
    context_docs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a request-local trace without persisting query content."""
    cited = cited_sources(answer, context_docs)
    cited_ids = {item.get("metadata", {}).get("citation_id") for item in cited}
    context_ids = {item.get("metadata", {}).get("citation_id") for item in context_docs}
    uncited: list[dict[str, Any]] = []
    for source in retrieved_docs:
        citation_id = source.get("metadata", {}).get("citation_id")
        if citation_id in cited_ids:
            continue
        reason = "not_cited" if citation_id in context_ids else "budget_trim"
        uncited.append(_evidence_item(source, drop_reason=reason))
    gaps = []
    if retrieved_docs and not cited and answer.strip() != NO_KNOWLEDGE_ANSWER:
        gaps.append({"status": "insufficient_evidence", "reason": "no_valid_citation"})
    return {
        "cited": [_evidence_item(source) for source in cited],
        "retrieved_uncited": uncited,
        "gaps": gaps,
        "conflicts": _conflicts(retrieved_docs),
    }


def _conflict_notice(sources: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for conflict in _conflicts(sources):
        values: list[str] = []
        seen: set[tuple[Any, Any]] = set()
        for item in conflict["values"]:
            marker = (item.get("value"), item.get("index"))
            if marker in seen:
                continue
            seen.add(marker)
            values.append(f"{item['value']} [{item['index']}]")
        lines.append(f"- `{conflict['key']}`: {'；'.join(values)}")
    if not lines:
        return ""
    return "\n\n检测到同一配置项存在不同证据值：\n" + "\n".join(lines) + "\n请核对原文。"


def govern_answer(answer: str, question: str, context_docs: list[dict[str, Any]]) -> str:
    """Prevent an uncited or completeness-sensitive answer from overclaiming."""
    answer = (answer or "").strip()
    if not answer or answer == NO_KNOWLEDGE_ANSWER:
        return answer or NO_KNOWLEDGE_ANSWER
    cited = cited_sources(answer, context_docs)
    if not cited:
        return "检索到相关片段，但没有可验证的引用证据，当前无法给出有依据的回答。"
    conflict_notice = _conflict_notice(context_docs)
    if conflict_notice and "请核对原文" not in answer:
        answer += conflict_notice
    if _COMPLETE_RE.search(question or "") and "证据不足" not in answer and "未查询到" not in answer:
        citation_id = cited[0].get("metadata", {}).get("citation_id")
        return f"{answer}\n\n以上仅覆盖已引用证据，不能据此确认完整流程。[{citation_id}]"
    return answer
