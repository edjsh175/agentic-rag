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
_LATIN_SUBJECT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}")
_CJK_SUBJECT_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_QUESTION_STOPWORDS = {
    "什么", "怎么", "如何", "哪些", "哪个", "介绍", "一下", "属于", "区别",
    "相关", "内容", "问题", "查询", "请问", "是否", "可以", "怎么用",
}


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


def question_subjects(question: str) -> list[str]:
    """Extract coarse subject tokens from a question for grounded partial answers."""
    text = (question or "").strip()
    if not text:
        return []
    subjects: list[str] = []
    seen: set[str] = set()
    for match in _LATIN_SUBJECT_RE.findall(text):
        key = match.casefold()
        if key in seen:
            continue
        seen.add(key)
        subjects.append(match)
    for match in _CJK_SUBJECT_RE.findall(text):
        if match in _QUESTION_STOPWORDS:
            continue
        key = match.casefold()
        if key in seen:
            continue
        seen.add(key)
        subjects.append(match)
    return subjects


def _doc_blob(source: dict[str, Any]) -> str:
    meta = source.get("metadata") or {}
    return " ".join(
        [
            str(source.get("content") or ""),
            str(meta.get("section_path") or ""),
            str(meta.get("section_title") or ""),
            str(meta.get("source") or ""),
            str(meta.get("file_name") or ""),
        ]
    ).casefold()


def matching_context_docs(
    question: str,
    context_docs: list[dict[str, Any]],
    *,
    max_docs: int = 3,
) -> list[dict[str, Any]]:
    """Prefer context docs that mention question subjects; else keep top docs."""
    docs = [doc for doc in (context_docs or []) if isinstance(doc, dict)]
    if not docs:
        return []
    subjects = [s.casefold() for s in question_subjects(question)]
    if subjects:
        matched = [doc for doc in docs if any(subject in _doc_blob(doc) for subject in subjects)]
        if matched:
            return matched[:max_docs]
    return docs[:max_docs]


def build_partial_grounded_answer(
    question: str,
    context_docs: list[dict[str, Any]],
) -> str | None:
    """Rule-4 fallback: keep subject-related citations when the model omitted them."""
    matched = matching_context_docs(question, context_docs)
    if not matched:
        return None
    citation_ids_ordered: list[int] = []
    section_hints: list[str] = []
    for doc in matched:
        meta = doc.get("metadata") or {}
        try:
            cid = int(meta.get("citation_id"))
        except (TypeError, ValueError):
            continue
        if cid in citation_ids_ordered:
            continue
        citation_ids_ordered.append(cid)
        hint = str(meta.get("section_path") or meta.get("section_title") or "").strip()
        if hint and hint not in section_hints:
            section_hints.append(hint)
    if not citation_ids_ordered:
        return None
    subjects = question_subjects(question)
    subject = subjects[0] if subjects else "该主题"
    hint_text = "、".join(section_hints[:3]) if section_hints else "相关章节"
    cites = "".join(f"[{cid}]" for cid in citation_ids_ordered)
    aspect = (question or "").strip() or "该问题"
    return (
        f"知识库中查到了{subject}的部分相关内容（如{hint_text}），"
        f"但未检索到关于「{aspect}」的完整说明。{cites}"
    )


_THIN_PARTIAL_RE = re.compile(
    r"^知识库中查到了.+?的部分相关内容（如.+?），"
    r"但未检索到关于[「\[][^」\]]+[」\]]的完整说明。"
    r"(?:\[\d+\])*$",
    re.DOTALL,
)


def _supplement_uncited_answer(
    answer: str,
    question: str,
    context_docs: list[dict[str, Any]],
) -> str:
    """Keep model body; append matching citations and a short verification notice."""
    matched = matching_context_docs(question, context_docs)
    citation_ids_ordered: list[int] = []
    for doc in matched:
        meta = doc.get("metadata") or {}
        try:
            cid = int(meta.get("citation_id"))
        except (TypeError, ValueError):
            continue
        if cid not in citation_ids_ordered:
            citation_ids_ordered.append(cid)
    body = answer.rstrip()
    if citation_ids_ordered:
        cites = "".join(f"[{cid}]" for cid in citation_ids_ordered)
        return (
            f"{body}\n\n"
            f"（以上正文缺少模型引用编号，已根据检索结果补充{cites}；请结合来源栏核对原文。）"
        )
    return f"{body}\n\n（以上正文缺少可验证的引用证据，请结合来源栏核对原文。）"


def _is_thin_partial_answer(answer: str) -> bool:
    return bool(_THIN_PARTIAL_RE.match((answer or "").strip()))


def _append_evidence_bullets(
    answer: str,
    question: str,
    context_docs: list[dict[str, Any]],
    *,
    max_docs: int = 3,
    snippet_chars: int = 160,
) -> str:
    """Attach short grounded bullets when the model only emitted a rule-4 shell."""
    if "相关原文要点" in (answer or ""):
        return answer
    matched = matching_context_docs(question, context_docs, max_docs=max_docs)
    bullets: list[str] = []
    for doc in matched:
        meta = doc.get("metadata") or {}
        try:
            cid = int(meta.get("citation_id"))
        except (TypeError, ValueError):
            continue
        section = str(meta.get("section_path") or meta.get("section_title") or "").strip()
        snippet = " ".join(str(doc.get("content") or "").split())
        if len(snippet) > snippet_chars:
            snippet = snippet[:snippet_chars] + "…"
        label = f"{section}：" if section else ""
        bullets.append(f"- {label}{snippet} [{cid}]")
    if not bullets:
        return answer
    return answer.rstrip() + "\n\n相关原文要点：\n" + "\n".join(bullets)


def _has_substantial_grounding(answer: str, context_docs: list[dict[str, Any]]) -> bool:
    """Check if an uncited answer has substantial keyword/token overlap with context docs."""
    ans_folded = (answer or "").casefold()
    overlap_count = 0
    for doc in context_docs:
        blob = _doc_blob(doc)
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{3,}", blob)
        for tok in tokens[:30]:
            if tok in ans_folded:
                overlap_count += 1
                if overlap_count >= 2:
                    return True
    return False


def govern_answer(answer: str, question: str, context_docs: list[dict[str, Any]]) -> str:
    """Prevent an uncited or completeness-sensitive answer from overclaiming."""
    answer = (answer or "").strip()
    docs = [doc for doc in (context_docs or []) if isinstance(doc, dict)]

    # Empty / fixed miss: repair to rule-4 partial answer when subject context exists.
    if (not answer or answer == NO_KNOWLEDGE_ANSWER) and docs:
        repaired = build_partial_grounded_answer(question, docs)
        if repaired:
            return _append_evidence_bullets(repaired, question, docs)
        return answer or NO_KNOWLEDGE_ANSWER
    if not answer or answer == NO_KNOWLEDGE_ANSWER:
        return answer or NO_KNOWLEDGE_ANSWER

    cited = cited_sources(answer, docs)
    if not cited:
        if docs:
            if _has_substantial_grounding(answer, docs):
                return _supplement_uncited_answer(answer, question, docs)
            repaired = build_partial_grounded_answer(question, docs)
            if repaired:
                return _append_evidence_bullets(repaired, question, docs)
        return "检索到相关片段，但没有可验证的引用证据，当前无法给出有依据的回答。"
    conflict_notice = _conflict_notice(docs)
    if conflict_notice and "请核对原文" not in answer:
        answer += conflict_notice
    if _COMPLETE_RE.search(question or "") and "证据不足" not in answer and "未查询到" not in answer:
        citation_id = cited[0].get("metadata", {}).get("citation_id")
        return f"{answer}\n\n以上仅覆盖已引用证据，不能据此确认完整流程。[{citation_id}]"
    # Model (or prior repair) emitted only the rule-4 shell — keep it, attach evidence bullets.
    if docs and _is_thin_partial_answer(answer):
        return _append_evidence_bullets(answer, question, docs)
    return answer
