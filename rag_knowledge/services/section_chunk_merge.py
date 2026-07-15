"""Technical-manual section merge and adjacency (Round 0C production path).

Evidence source remains the original file fingerprint + Raw Block ids.
This module builds the retrieval view with document-scoped section_id and
globally unique chunk_uid; prev/next point at Final chunk_uid only after
the final chunk list is known.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from langchain_core.documents import Document

TARGET_MIN = 300
TARGET_SOFT_MAX = 1200
L2_TARGET_MAX = 800
COMMAND_LEAD_IN_RE = re.compile(r"(执行以下命令|如下命令|运行以下|按下列命令)")
CHUNKING_METHOD = "technical_manual_merge"


@dataclass
class MergeUnit:
    source: str
    section_path: str
    content_markdown: str
    content_type: str
    document_key: str
    source_document_id: str = ""
    source_snapshot_hash: str = ""
    merged_from_orders: list[int] = field(default_factory=list)
    source_element_ids: list[str] = field(default_factory=list)
    source_raw_block_ids: list[str] = field(default_factory=list)
    source_section_paths: list[str] = field(default_factory=list)
    source_section_ids: list[str] = field(default_factory=list)
    chunk_index_global: int = 0
    chunk_index_in_section: int = 0
    section_id: str = ""
    chunk_uid: str = ""

    @property
    def char_len(self) -> int:
        return len(self.content_markdown or "")


def document_key_from_meta(meta: dict | None) -> str:
    meta = meta or {}
    for key in ("source_snapshot_hash", "source_document_id", "source"):
        value = str(meta.get(key) or "").strip()
        if value:
            return value
    return "unknown_document"


def section_id_for(document_key: str, section_path: str) -> str:
    """Document-scoped section id: same path in different docs must not collide."""
    normalized = " > ".join(
        part.strip() for part in (section_path or "").split(">") if part.strip()
    )
    payload = f"{document_key}\n{normalized}".encode("utf-8")
    return f"sec_{hashlib.sha1(payload).hexdigest()[:16]}"


def chunk_uid_for(
    document_key: str,
    chunk_index_global: int,
    section_id: str,
    source_element_ids: list[str],
    content_markdown: str,
) -> str:
    """Stable Final-chunk identity before Chroma UUID assignment."""
    elements = ",".join(source_element_ids)
    body_fp = hashlib.sha1((content_markdown or "").encode("utf-8")).hexdigest()[:16]
    payload = (
        f"{document_key}|{chunk_index_global}|{section_id}|{elements}|{body_fp}"
    ).encode("utf-8")
    return f"chk_{hashlib.sha1(payload).hexdigest()[:24]}"


def _path_parts(section_path: str) -> list[str]:
    return [p.strip() for p in (section_path or "").split(">") if p.strip()]


def _parent_key(section_path: str) -> tuple[str, ...]:
    parts = _path_parts(section_path)
    if len(parts) <= 1:
        return tuple(parts)
    return tuple(parts[:-1])


def _different_l1(prev_path: str, next_path: str) -> bool:
    a = _path_parts(prev_path)
    b = _path_parts(next_path)
    if not a or not b:
        return a != b
    return a[0] != b[0]


def _same_l1_different_l2(prev_path: str, next_path: str) -> bool:
    a = _path_parts(prev_path)
    b = _path_parts(next_path)
    return len(a) >= 2 and len(b) >= 2 and a[0] == b[0] and a[1] != b[1]


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _crosses_l2(paths: list[str]) -> bool:
    l2_keys = {
        tuple(parts[:2])
        for path in paths
        if len(parts := _path_parts(path)) >= 2
    }
    return len(l2_keys) > 1


def _render_bucket_content(texts: list[str], paths: list[str]) -> str:
    if not _crosses_l2(paths):
        return "\n\n".join(texts).strip()

    rendered: list[str] = []
    last_path = ""
    for text, path in zip(texts, paths):
        if path == last_path:
            rendered[-1] = f"{rendered[-1]}\n\n{text}"
        else:
            rendered.append(f"## {path}\n\n{text}")
            last_path = path
    return "\n\n".join(rendered).strip()


def _anchor_path(paths: list[str]) -> str:
    if _crosses_l2(paths):
        parts = _path_parts(paths[0])
        return parts[0] if parts else ""
    return paths[0] if paths else ""


def _is_atomic(content_type: str) -> bool:
    return content_type in {"table", "code", "embedded_image"}


def _needs_follow(text: str) -> bool:
    return bool(COMMAND_LEAD_IN_RE.search(text or ""))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    text = str(value).strip()
    return [text] if text else []


def documents_to_merge_units(
    docs: Iterable[Document],
    *,
    target_min: int = TARGET_MIN,
    target_max: int = L2_TARGET_MAX,
    soft_max: int = TARGET_SOFT_MAX,
    command_follow_max: int | None = None,
) -> list[MergeUnit]:
    """Merge short sibling body chunks. Does not assign Final prev/next."""
    items = list(docs)
    if not items:
        return []

    units: list[MergeUnit] = []
    bucket_text: list[str] = []
    bucket_orders: list[int] = []
    bucket_element_ids: list[str] = []
    bucket_raw_ids: list[str] = []
    bucket_paths: list[str] = []
    bucket_source = ""
    bucket_type = "text"
    bucket_doc_key = ""
    bucket_doc_id = ""
    bucket_snapshot = ""

    def flush() -> None:
        nonlocal bucket_text, bucket_orders, bucket_element_ids, bucket_raw_ids
        nonlocal bucket_paths, bucket_source, bucket_type
        nonlocal bucket_doc_key, bucket_doc_id, bucket_snapshot
        if not bucket_text:
            return
        source_paths = _ordered_unique(bucket_paths)
        path = _anchor_path(bucket_paths)
        doc_key = bucket_doc_key or bucket_source or "unknown_document"
        units.append(
            MergeUnit(
                source=bucket_source,
                section_path=path,
                content_markdown=_render_bucket_content(bucket_text, bucket_paths),
                content_type=bucket_type,
                document_key=doc_key,
                source_document_id=bucket_doc_id or doc_key[:32],
                source_snapshot_hash=bucket_snapshot,
                merged_from_orders=list(bucket_orders),
                source_element_ids=list(bucket_element_ids),
                source_raw_block_ids=list(bucket_raw_ids),
                source_section_paths=source_paths,
                source_section_ids=[section_id_for(doc_key, p) for p in source_paths],
                section_id=section_id_for(doc_key, path),
            )
        )
        bucket_text = []
        bucket_orders = []
        bucket_element_ids = []
        bucket_raw_ids = []
        bucket_paths = []
        bucket_type = "text"

    for doc in items:
        meta = doc.metadata or {}
        path = str(meta.get("section_path") or "")
        source = str(meta.get("source") or "")
        content_type = str(meta.get("content_type") or "text")
        order = int(meta.get("element_order") or 0)
        doc_key = document_key_from_meta(meta)
        doc_id = str(meta.get("source_document_id") or doc_key[:32])
        snapshot = str(meta.get("source_snapshot_hash") or "")
        element_ids = _as_list(meta.get("source_element_ids")) or _as_list(meta.get("element_id"))
        raw_ids = _as_list(meta.get("source_raw_block_ids"))
        text = (doc.page_content or "").strip()
        if not text:
            continue

        if _is_atomic(content_type):
            flush()
            units.append(
                MergeUnit(
                    source=source,
                    section_path=path,
                    content_markdown=text,
                    content_type=content_type,
                    document_key=doc_key,
                    source_document_id=doc_id,
                    source_snapshot_hash=snapshot,
                    merged_from_orders=[order],
                    source_element_ids=element_ids,
                    source_raw_block_ids=raw_ids,
                    source_section_paths=[path],
                    source_section_ids=[section_id_for(doc_key, path)],
                    section_id=section_id_for(doc_key, path),
                )
            )
            continue

        if not bucket_text:
            bucket_text = [text]
            bucket_orders = [order]
            bucket_element_ids = list(element_ids)
            bucket_raw_ids = list(raw_ids)
            bucket_paths = [path]
            bucket_source = source
            bucket_type = content_type
            bucket_doc_key = doc_key
            bucket_doc_id = doc_id
            bucket_snapshot = snapshot
            continue

        current_len = len(_render_bucket_content(bucket_text, bucket_paths))
        projected = len(_render_bucket_content(bucket_text + [text], bucket_paths + [path]))
        same_document = (bucket_doc_key or bucket_source) == doc_key

        reason = classify_adjacent_merge_decision(
            bucket_paths[-1],
            path,
            bucket_text[-1],
            text,
            prev_content_type=bucket_type,
            next_content_type=content_type,
            same_document=same_document,
            bucket_len=current_len,
            projected_len=projected,
            target_min=target_min,
            target_max=target_max,
            soft_max=soft_max,
            command_follow_max=command_follow_max,
        )
        can_merge = reason.startswith("merge_")

        if can_merge:
            bucket_text.append(text)
            bucket_orders.append(order)
            bucket_element_ids.extend(element_ids)
            bucket_raw_ids.extend(raw_ids)
            bucket_paths.append(path)
        else:
            flush()
            bucket_text = [text]
            bucket_orders = [order]
            bucket_element_ids = list(element_ids)
            bucket_raw_ids = list(raw_ids)
            bucket_paths = [path]
            bucket_source = source
            bucket_type = content_type
            bucket_doc_key = doc_key
            bucket_doc_id = doc_id
            bucket_snapshot = snapshot

    flush()

    section_counters: dict[str, int] = {}
    for i, unit in enumerate(units):
        unit.chunk_index_global = i
        key = f"{unit.document_key}|{unit.section_path}"
        section_counters[key] = section_counters.get(key, 0)
        unit.chunk_index_in_section = section_counters[key]
        section_counters[key] += 1
        unit.chunk_uid = chunk_uid_for(
            unit.document_key,
            unit.chunk_index_global,
            unit.section_id,
            unit.source_element_ids,
            unit.content_markdown,
        )
    return units


def merge_units_to_documents(units: list[MergeUnit], template_meta: dict | None = None) -> list[Document]:
    """Map merge units to Documents. prev/next filled only after final reassignment."""
    base = dict(template_meta or {})
    out: list[Document] = []
    for unit in units:
        meta = {
            **base,
            "source": unit.source or base.get("source", ""),
            "section_path": unit.section_path,
            "section_id": unit.section_id,
            "content_type": unit.content_type,
            "merged_from": list(unit.merged_from_orders),
            "source_element_ids": list(unit.source_element_ids),
            "source_raw_block_ids": list(unit.source_raw_block_ids),
            "source_section_paths": list(unit.source_section_paths),
            "source_section_ids": list(unit.source_section_ids),
            "source_document_id": unit.source_document_id,
            "source_snapshot_hash": unit.source_snapshot_hash,
            "element_order": (
                unit.merged_from_orders[0]
                if unit.merged_from_orders
                else unit.chunk_index_global
            ),
            "chunk_index_global": unit.chunk_index_global,
            "chunk_index_in_section": unit.chunk_index_in_section,
            "chunk_in_section": unit.chunk_index_in_section,
            "chunk_uid": unit.chunk_uid,
            "prev_chunk_id": None,
            "next_chunk_id": None,
            "chunking_method": CHUNKING_METHOD,
        }
        parts = _path_parts(unit.section_path)
        meta["section_title"] = parts[-1] if parts else ""
        meta["heading_level"] = len(parts)
        out.append(Document(page_content=unit.content_markdown, metadata=meta))
    return out


def apply_technical_manual_merge(docs: list[Document], *, policy: Any = None) -> list[Document]:
    """Production entry: merge short siblings on structured DOCX element docs."""
    if not docs:
        return []
    template = dict(docs[0].metadata or {})
    for key in (
        "section_path",
        "section_title",
        "content_type",
        "element_order",
        "chunk_in_section",
        "heading_level",
        "searchable_text",
        "element_id",
        "source_element_ids",
        "source_raw_block_ids",
        "source_section_paths",
        "source_section_ids",
        "merged_from",
        "chunk_uid",
        "prev_chunk_id",
        "next_chunk_id",
        "section_id",
    ):
        template.pop(key, None)
    units = documents_to_merge_units(
        docs,
        target_min=int(getattr(policy, "target_min", TARGET_MIN)),
        target_max=int(getattr(policy, "target_max", L2_TARGET_MAX)),
        soft_max=int(getattr(policy, "soft_max", TARGET_SOFT_MAX)),
        command_follow_max=int(
            getattr(policy, "command_follow_max", int(TARGET_SOFT_MAX * 1.25))
        ),
    )
    return merge_units_to_documents(units, template_meta=template)


def reassign_chunk_adjacency(docs: list[Document]) -> list[Document]:
    """Assign Final chunk_uid and prev/next after downstream splits."""
    if not docs:
        return docs

    section_counters: dict[str, int] = {}
    for i, doc in enumerate(docs):
        meta = dict(doc.metadata or {})
        path = str(meta.get("section_path") or "")
        doc_key = document_key_from_meta(meta)
        if not meta.get("source_document_id"):
            meta["source_document_id"] = doc_key[:32]
        meta["section_id"] = section_id_for(doc_key, path)
        meta["chunk_index_global"] = i
        section_key = f"{doc_key}|{path}"
        section_counters[section_key] = section_counters.get(section_key, 0)
        meta["chunk_index_in_section"] = section_counters[section_key]
        meta["chunk_in_section"] = section_counters[section_key]
        section_counters[section_key] += 1

        element_ids = _as_list(meta.get("source_element_ids"))
        meta["chunk_uid"] = chunk_uid_for(
            doc_key,
            i,
            meta["section_id"],
            element_ids,
            doc.page_content or "",
        )
        meta["prev_chunk_id"] = None
        meta["next_chunk_id"] = None
        doc.metadata = meta

    for i, doc in enumerate(docs):
        meta = dict(doc.metadata or {})
        meta["prev_chunk_id"] = (
            docs[i - 1].metadata.get("chunk_uid") if i > 0 else None
        )
        meta["next_chunk_id"] = (
            docs[i + 1].metadata.get("chunk_uid") if i + 1 < len(docs) else None
        )
        doc.metadata = meta
    return docs


def length_stats(units: list[MergeUnit]) -> dict[str, Any]:
    lengths = [u.char_len for u in units]
    if not lengths:
        return {
            "count": 0,
            "median": 0,
            "lt_100_rate": 0.0,
            "lt_200_rate": 0.0,
            "in_300_800_rate": 0.0,
            "gt_1200_rate": 0.0,
        }
    ordered = sorted(lengths)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    n = len(ordered)
    return {
        "count": n,
        "median": median,
        "lt_100_rate": sum(1 for x in ordered if x < 100) / n,
        "lt_200_rate": sum(1 for x in ordered if x < 200) / n,
        "in_300_800_rate": sum(1 for x in ordered if 300 <= x <= 800) / n,
        "gt_1200_rate": sum(1 for x in ordered if x > 1200) / n,
    }


def classify_adjacent_merge_decision(
    prev_path: str,
    next_path: str,
    prev_text: str,
    next_text: str,
    *,
    prev_content_type: str = "text",
    next_content_type: str = "text",
    same_document: bool = True,
    bucket_len: int | None = None,
    projected_len: int | None = None,
    target_min: int = TARGET_MIN,
    target_max: int = L2_TARGET_MAX,
    soft_max: int = TARGET_SOFT_MAX,
    command_follow_max: int | None = None,
) -> str:
    """Explain why two consecutive elements would or would not merge."""
    if not same_document:
        return "different_document"
    if _is_atomic(prev_content_type):
        return "prev_atomic"
    if _is_atomic(next_content_type):
        return "next_atomic"
    current_len = bucket_len if bucket_len is not None else len(prev_text or "")
    next_len = len(next_text or "")
    projected = (
        projected_len
        if projected_len is not None
        else current_len + (2 if current_len and next_len else 0) + next_len
    )
    if _different_l1(prev_path, next_path):
        return "different_l1_hard_boundary"
    same_parent = _parent_key(prev_path) == _parent_key(next_path)
    if _same_l1_different_l2(prev_path, next_path):
        if next_len >= target_min:
            return "next_leaf_not_short"
        if current_len >= target_min:
            return "l2_bucket_target_reached"
        if projected > target_max:
            return "l2_projected_over_target_max"
        return "merge_same_l1_short_leaf"
    if not same_parent:
        return "different_parent"
    force_follow = _needs_follow(prev_text or "")
    follow_max = command_follow_max if command_follow_max is not None else int(soft_max * 1.25)
    if force_follow and projected <= follow_max:
        return "merge_command_follow"
    if current_len >= target_min and projected > soft_max:
        return "soft_max_exceeded"
    if current_len < target_min and projected <= soft_max:
        return "merge_under_target_min"
    if current_len < target_min and projected > soft_max:
        return "projected_over_soft_max"
    if current_len >= target_min:
        return "already_at_target_min"
    return "no_merge_other"


def explain_merge_flush_reasons(docs: list[Document]) -> list[dict[str, Any]]:
    """Replay production merge rules and record why each flush/split happened."""
    items = [d for d in docs if (d.page_content or "").strip()]
    if not items:
        return []

    events: list[dict[str, Any]] = []
    bucket_text: list[str] = []
    bucket_paths: list[str] = []
    bucket_doc_key = ""
    bucket_type = "text"
    bucket_start_order = 0

    def bucket_len() -> int:
        return len(_render_bucket_content(bucket_text, bucket_paths)) if bucket_text else 0

    for doc in items:
        meta = doc.metadata or {}
        path = str(meta.get("section_path") or "")
        content_type = str(meta.get("content_type") or "text")
        order = int(meta.get("element_order") or 0)
        doc_key = document_key_from_meta(meta)
        text = (doc.page_content or "").strip()

        if _is_atomic(content_type):
            if bucket_text:
                events.append(
                    {
                        "reason": "flush_before_atomic",
                        "path": _anchor_path(bucket_paths),
                        "order": bucket_start_order,
                        "bucket_len": bucket_len(),
                    }
                )
                bucket_text = []
                bucket_paths = []
            events.append(
                {
                    "reason": "atomic_keep",
                    "path": path,
                    "order": order,
                    "content_type": content_type,
                    "len": len(text),
                }
            )
            continue

        if not bucket_text:
            bucket_text = [text]
            bucket_paths = [path]
            bucket_doc_key = doc_key
            bucket_type = content_type
            bucket_start_order = order
            continue

        reason = classify_adjacent_merge_decision(
            bucket_paths[-1],
            path,
            bucket_text[-1],
            text,
            prev_content_type=bucket_type,
            next_content_type=content_type,
            same_document=bucket_doc_key == doc_key,
            bucket_len=bucket_len(),
            projected_len=len(
                _render_bucket_content(bucket_text + [text], bucket_paths + [path])
            ),
        )
        if reason.startswith("merge_"):
            events.append(
                {
                    "reason": reason,
                    "prev_path": bucket_paths[-1],
                    "next_path": path,
                    "prev_order": bucket_start_order,
                    "next_order": order,
                    "bucket_len": bucket_len(),
                    "next_len": len(text),
                }
            )
            bucket_text.append(text)
            bucket_paths.append(path)
            continue

        events.append(
            {
                "reason": reason,
                "prev_path": bucket_paths[-1],
                "next_path": path,
                "prev_order": bucket_start_order,
                "next_order": order,
                "bucket_len": bucket_len(),
                "next_len": len(text),
            }
        )
        bucket_text = [text]
        bucket_paths = [path]
        bucket_doc_key = doc_key
        bucket_type = content_type
        bucket_start_order = order

    if bucket_text:
        events.append(
            {
                "reason": "flush_eof",
                "path": _anchor_path(bucket_paths),
                "order": bucket_start_order,
                "bucket_len": bucket_len(),
            }
        )
    return events


def fact_window_coverage(units: list[MergeUnit], required_facts: list[str]) -> dict[str, Any]:
    hits_in_one_unit = 0
    best = 0
    for unit in units:
        text = unit.content_markdown
        hit = sum(1 for f in required_facts if f and f in text)
        best = max(best, hit)
        if required_facts and hit == len(required_facts):
            hits_in_one_unit = 1
            break
    return {
        "best_unit_fact_hits": best,
        "all_facts_in_one_unit": bool(hits_in_one_unit),
        "required_count": len(required_facts),
    }
