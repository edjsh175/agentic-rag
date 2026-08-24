from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

NO_KNOWLEDGE_ANSWER = "当前知识库中未查询到相关内容。"
_CITATION_RE = re.compile(r"\[(\d+)\]|\((\d+)\)")
_KEY_VALUE_RE = re.compile(r"(?im)^\s*([A-Za-z][A-Za-z0-9_.-]{1,80})\s*(?:=|:|：)\s*([^\s,，;；]+)")
_TLS_PORT_RE = re.compile(
    r"(?im)(?:tls(?:/dtls)?[-_\s]?listening[-_\s]?port|tls\s*端口)\s*(?:=|:|：|\||为|是)\s*(\d{2,5})"
)


def citation_ids(text: str) -> set[int]:
    """Extract citation IDs from text formatted as [1] or (1)."""
    ids: set[int] = set()
    for match in _CITATION_RE.finditer(text or ""):
        token = match.group(1) or match.group(2)
        if token:
            try:
                ids.add(int(token))
            except ValueError:
                continue
    return ids


def cited_sources(answer: str, context_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter context documents to only those cited in the answer."""
    cids = citation_ids(answer)
    if not cids:
        return []
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for doc in context_docs or []:
        if not isinstance(doc, dict):
            continue
        try:
            cid = int(doc.get("metadata", {}).get("citation_id"))
        except (TypeError, ValueError):
            continue
        if cid in cids and cid not in seen:
            seen.add(cid)
            result.append(doc)
    return result


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
    """Build a request-local evidence trace without persisting query content."""
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
