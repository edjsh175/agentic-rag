"""Document-profile policies, semantic builders, and common chunk finalization."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable

from langchain_core.documents import Document

from rag_knowledge.models.structured_document import build_searchable_text
from rag_knowledge.services.section_chunk_merge import (
    apply_technical_manual_merge,
    reassign_chunk_adjacency,
)


class DocumentProfile(str, Enum):
    SECTION_BASED = "section_based"
    TECHNICAL_MANUAL = "technical_manual"
    PROCEDURE = "procedure"
    API_DOC = "api_doc"
    TABLE_DOC = "table_doc"
    RECORD_LIST = "record_list"


@dataclass(frozen=True, slots=True)
class ChunkPolicy:
    """Validated profile-level policy. Per-document overrides are intentionally absent."""

    profile: DocumentProfile
    target_min: int = 0
    target_max: int = 800
    soft_max: int = 1200
    command_follow_max: int = 1500
    table_row_group_max: int = 500

    def __post_init__(self) -> None:
        if min(self.target_min, self.target_max, self.soft_max, self.command_follow_max) < 0:
            raise ValueError("chunk policy lengths must be non-negative")
        if self.target_min > self.target_max:
            raise ValueError("target_min must not exceed target_max")
        if self.target_max > self.soft_max:
            raise ValueError("target_max must not exceed soft_max")
        if self.command_follow_max < self.soft_max:
            raise ValueError("command_follow_max must not be below soft_max")
        if self.table_row_group_max <= 0:
            raise ValueError("table_row_group_max must be positive")

    @property
    def policy_id(self) -> str:
        payload = asdict(self)
        payload["profile"] = self.profile.value
        stable = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"cp_{hashlib.sha256(stable.encode('utf-8')).hexdigest()[:16]}"


_POLICIES = {
    DocumentProfile.SECTION_BASED: ChunkPolicy(DocumentProfile.SECTION_BASED),
    DocumentProfile.TECHNICAL_MANUAL: ChunkPolicy(
        DocumentProfile.TECHNICAL_MANUAL,
        target_min=300,
        target_max=800,
        soft_max=1200,
        command_follow_max=1500,
    ),
    DocumentProfile.PROCEDURE: ChunkPolicy(DocumentProfile.PROCEDURE),
    DocumentProfile.API_DOC: ChunkPolicy(DocumentProfile.API_DOC),
    DocumentProfile.TABLE_DOC: ChunkPolicy(DocumentProfile.TABLE_DOC, table_row_group_max=500),
    DocumentProfile.RECORD_LIST: ChunkPolicy(DocumentProfile.RECORD_LIST),
}

_ATOMIC_CONTENT_TYPES = {"table", "code", "embedded_image"}
_STEP_RE = re.compile(r"^\s*(?:步骤\s*)?(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+)[.、）)]?\s*\S+")
_COMMAND_RE = re.compile(
    r"^\s*(?:```|[$#>]\s*)?(?:sudo\s+)?(?:systemctl|docker|kubectl|curl|wget|pip|npm|yarn|"
    r"apt|yum|dnf|mount|umount|reboot|chmod|chown|mkdir|cd|cp|mv|rm|cat|echo|export|pm2)\b",
    re.I,
)
_ENDPOINT_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/\S+)", re.I)
# StampUtil / Factory 声明行：仅简单形参（params），排除示例调用 StampUtil.xxx(obj.field)
_SDK_SIGNATURE_RE = re.compile(
    r"(?m)^\s*((?:StampUtil\.|(?:earth\.)?Factory\.)(\w+))\s*"
    r"\(\s*(?:[A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_][\w]*)*)?\s*\)\s*;?\s*$"
)
_SDK_DECLARATION_FOLLOW_RE = re.compile(r"参数|params\s*[=：:]|请求|request", re.I)
_TABLE_TITLE_RE = re.compile(r"^\s*(?:表\s*[\d一二三四五六七八九十]+\s*[：:.、-]|table\s*\d+\s*[：:.、-])", re.I)
_RECORD_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.、）)]|[-*+]\s+)\s*\S+")
_SUBSTEP_RE = re.compile(r"^\s*\d+\.\d+(?:\.\d+)*[.、）)]?\s*\S+")


def normalize_document_profile(value: DocumentProfile | str | None) -> DocumentProfile:
    if value in (None, ""):
        return DocumentProfile.SECTION_BASED
    if isinstance(value, DocumentProfile):
        return value
    try:
        return DocumentProfile(str(value).strip())
    except ValueError as exc:
        supported = ", ".join(profile.value for profile in DocumentProfile)
        raise ValueError(f"unsupported document_profile {value!r}; expected one of: {supported}") from exc


def get_chunk_policy(profile: DocumentProfile | str, *, config=None) -> ChunkPolicy:
    selected = normalize_document_profile(profile)
    overrides = getattr(config, "document_profile_policies", {}).get(selected.value, {}) if config else {}
    if not overrides:
        return _POLICIES[selected]
    values = asdict(_POLICIES[selected])
    values.update(overrides)
    values["profile"] = selected
    return ChunkPolicy(**values)


def apply_document_profile(
    docs: list[Document],
    profile: DocumentProfile | str | None,
    *,
    policy: ChunkPolicy | None = None,
) -> list[Document]:
    """Build semantic chunks and apply the common identity/lineage finalizer."""
    selected = normalize_document_profile(profile)
    selected_policy = policy or get_chunk_policy(selected)
    if selected_policy.profile != selected:
        raise ValueError("chunk policy profile does not match document_profile")

    builders = {
        DocumentProfile.SECTION_BASED: _build_section_based,
        DocumentProfile.TECHNICAL_MANUAL: _build_technical_manual,
        DocumentProfile.PROCEDURE: _build_procedure,
        DocumentProfile.API_DOC: _build_api_doc,
        DocumentProfile.TABLE_DOC: _build_table_doc,
        DocumentProfile.RECORD_LIST: _build_record_list,
    }
    built = builders[selected](docs, selected_policy)
    return finalize_profile_chunks(built, selected, selected_policy)


def _copy_doc(doc: Document, **metadata) -> Document:
    return Document(page_content=doc.page_content, metadata={**(doc.metadata or {}), **metadata})


def _element_ids(doc: Document) -> list[str]:
    meta = doc.metadata or {}
    values = meta.get("source_element_ids") or ([meta.get("element_id")] if meta.get("element_id") else [])
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _raw_ids(doc: Document) -> list[str]:
    values = (doc.metadata or {}).get("source_raw_block_ids") or []
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _merge_documents(items: list[Document], role: str, method: str) -> Document:
    first = items[0]
    text = "\n\n".join(item.page_content.strip() for item in items if item.page_content.strip())
    source_ids = list(dict.fromkeys(value for item in items for value in _element_ids(item)))
    raw_ids = list(dict.fromkeys(value for item in items for value in _raw_ids(item)))
    return Document(
        page_content=text,
        metadata={
            **(first.metadata or {}),
            "content_role": role,
            "chunking_method": method,
            "source_element_ids": source_ids,
            "source_raw_block_ids": raw_ids,
            "related_element_ids": source_ids[1:],
            "merged_from": [int((item.metadata or {}).get("element_order") or 0) for item in items],
        },
    )


def _first_content_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _split_text_to_limit(text: str, limit: int) -> list[str]:
    """Split on line/paragraph boundaries, falling back to a hard character boundary."""
    pieces: list[str] = []
    current = ""
    for segment in (part.strip() for part in re.split(r"\n+", text) if part.strip()):
        while len(segment) > limit:
            if current:
                pieces.append(current)
                current = ""
            split_at = segment.rfind(" ", 0, limit + 1)
            if split_at < limit // 2:
                split_at = limit
            pieces.append(segment[:split_at].strip())
            segment = segment[split_at:].strip()
        if not segment:
            continue
        projected = f"{current}\n{segment}".strip() if current else segment
        if current and len(projected) > limit:
            pieces.append(current)
            current = segment
        else:
            current = projected
    if current:
        pieces.append(current)
    return pieces


def _split_profile_document(
    doc: Document,
    policy: ChunkPolicy,
    *,
    repeated_header: str = "",
) -> list[Document]:
    text = doc.page_content.strip()
    if len(text) <= policy.soft_max:
        return [doc]

    body = text
    header = repeated_header.strip()
    if header and body.startswith(header):
        body = body[len(header):].lstrip()
    overhead = len(header) + 2 if header else 0
    body_limit = max(1, min(policy.target_max, policy.soft_max) - overhead)
    pieces = _split_text_to_limit(body, body_limit)
    if not pieces:
        return [doc]
    return [
        Document(
            page_content=f"{header}\n\n{piece}" if header else piece,
            metadata=dict(doc.metadata or {}),
        )
        for piece in pieces
    ]


def _is_atomic(doc: Document) -> bool:
    meta = doc.metadata or {}
    return str(meta.get("content_type") or "") in _ATOMIC_CONTENT_TYPES or str(meta.get("content_role") or "") in {
        "command", "code", "table", "api_request", "api_response", "record"
    }


def _build_section_based(docs: list[Document], policy: ChunkPolicy) -> list[Document]:
    out: list[Document] = []
    bucket: list[Document] = []

    def flush() -> None:
        if bucket:
            merged = _merge_documents(list(bucket), "ordinary_body", "section_based")
            out.extend(_split_profile_document(merged, policy))
            bucket.clear()

    for doc in docs:
        if not doc.page_content.strip():
            continue
        if _is_atomic(doc):
            flush()
            meta = doc.metadata or {}
            role = str(meta.get("content_role") or meta.get("content_type") or "ordinary_body")
            out.append(
                _copy_doc(
                    doc,
                    content_role=role,
                    related_element_ids=list(meta.get("related_element_ids") or []),
                )
            )
            continue
        same_section = bool(bucket) and (bucket[-1].metadata or {}).get("section_path", "") == (doc.metadata or {}).get("section_path", "")
        projected = len("\n\n".join(item.page_content for item in [*bucket, doc]))
        if bucket and (not same_section or projected > policy.target_max):
            flush()
        bucket.append(doc)
    flush()
    return out


def _build_technical_manual(docs: list[Document], policy: ChunkPolicy) -> list[Document]:
    merged = apply_technical_manual_merge(docs, policy=policy)
    return [
        _copy_doc(
            doc,
            content_role=("ordinary_body" if (doc.metadata or {}).get("content_type") == "text" else (doc.metadata or {}).get("content_type", "ordinary_body")),
            related_element_ids=list((doc.metadata or {}).get("source_element_ids") or [])[1:],
        )
        for doc in merged
    ]


def _split_numbered_document(doc: Document, pattern: re.Pattern[str]) -> list[Document]:
    lines = doc.page_content.splitlines()
    groups: list[list[str]] = []
    for line in lines:
        if pattern.match(line) and (not groups or groups[-1]):
            groups.append([line])
        elif groups:
            groups[-1].append(line)
        elif line.strip():
            groups.append([line])
    if len(groups) <= 1:
        return [doc]
    return [
        Document(page_content="\n".join(group).strip(), metadata=dict(doc.metadata or {}))
        for group in groups if "\n".join(group).strip()
    ]


def _build_procedure(docs: list[Document], policy: ChunkPolicy) -> list[Document]:
    expanded = [part for doc in docs for part in _split_numbered_document(doc, _STEP_RE)]
    out: list[Document] = []
    bucket: list[Document] = []

    def flush() -> None:
        if bucket:
            role = "step" if _STEP_RE.match(bucket[0].page_content) else "ordinary_body"
            merged = _merge_documents(list(bucket), role, "procedure")
            header = _first_content_line(bucket[0].page_content) if role == "step" else ""
            out.extend(_split_profile_document(merged, policy, repeated_header=header))
            bucket.clear()

    for doc in expanded:
        text = doc.page_content.strip()
        canonical_role = str((doc.metadata or {}).get("content_role") or "")
        if not text:
            continue
        if canonical_role == "step" or _STEP_RE.match(text):
            if bucket and _SUBSTEP_RE.match(text):
                bucket.append(_copy_doc(doc, content_role="step"))
            else:
                flush()
                bucket.append(_copy_doc(doc, content_role="step"))
        elif canonical_role == "command" or _COMMAND_RE.match(text):
            if bucket:
                bucket.append(_copy_doc(doc, content_role="command"))
            else:
                flush()
                out.append(_copy_doc(doc, content_role="command", related_element_ids=[]))
        elif bucket:
            bucket.append(_copy_doc(doc, content_role="ordinary_body"))
        else:
            bucket.append(doc)
    flush()
    return out


def _sdk_declaration_matches(text: str) -> list[re.Match[str]]:
    """API 声明：签名行后紧跟参数说明；裸调用（如 showHighlightObj(guid)）不算新边界。"""
    content = text or ""
    out: list[re.Match[str]] = []
    for match in _SDK_SIGNATURE_RE.finditer(content):
        following = content[match.end() : match.end() + 160]
        if _SDK_DECLARATION_FOLLOW_RE.search(following):
            out.append(match)
    return out


def _sdk_signature_match(text: str) -> re.Match[str] | None:
    matches = _sdk_declaration_matches(text)
    if matches:
        return matches[0]
    # 单声明且无「参数」字样时仍当作 API 起点（避免漏检）
    loose = list(_SDK_SIGNATURE_RE.finditer(text or ""))
    return loose[0] if len(loose) == 1 else None


def _expand_sdk_api_documents(docs: list[Document]) -> list[Document]:
    """Split a section that declares multiple StampUtil/Factory APIs into atomic units."""
    out: list[Document] = []
    for doc in docs:
        text = doc.page_content or ""
        matches = _sdk_declaration_matches(text)
        if len(matches) <= 1:
            out.append(doc)
            continue
        for index, match in enumerate(matches):
            start = 0 if index == 0 else match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            piece = text[start:end].strip()
            if not piece:
                continue
            meta = {
                **(doc.metadata or {}),
                "content_role": "api_endpoint",
                "endpoint": match.group(1),
                "api_name": match.group(2),
            }
            out.append(Document(page_content=piece, metadata=meta))
    return out


def _api_endpoint_label(doc: Document) -> tuple[str, str]:
    """Return (endpoint, api_name) for HTTP or StampUtil/Factory signatures."""
    path = str((doc.metadata or {}).get("section_path") or "")
    text = doc.page_content or ""
    probe = f"{path}\n{text}"
    http = _ENDPOINT_RE.search(probe)
    if http:
        return f"{http.group(1).upper()} {http.group(2)}", http.group(2)
    explicit_endpoint = str((doc.metadata or {}).get("endpoint") or "").strip()
    explicit_name = str((doc.metadata or {}).get("api_name") or "").strip()
    if explicit_endpoint:
        return explicit_endpoint, explicit_name or explicit_endpoint.rsplit(".", 1)[-1]
    sdk = _sdk_signature_match(text) or _sdk_signature_match(path)
    if sdk:
        return sdk.group(1), sdk.group(2)
    first = text.strip().splitlines()[0] if text.strip() else path
    return first, ""


def _annotate_api_sample(doc: Document) -> Document:
    text = doc.page_content or ""
    meta = dict(doc.metadata or {})
    if re.search(r"代码示例|```|await\s+StampUtil\.|Factory\.Create", text, re.I):
        meta["content_type"] = "code"
    return Document(page_content=doc.page_content, metadata=meta)


def _build_api_doc(docs: list[Document], policy: ChunkPolicy) -> list[Document]:
    out: list[Document] = []
    bucket: list[Document] = []
    endpoint = ""
    api_name = ""

    def flush() -> None:
        nonlocal endpoint, api_name
        if bucket:
            merged = _merge_documents(list(bucket), "api_endpoint" if endpoint else "ordinary_body", "api_doc")
            merged.metadata["endpoint"] = endpoint
            if api_name:
                merged.metadata["api_name"] = api_name
            merged = _annotate_api_sample(merged)
            if not endpoint or len(merged.page_content) <= policy.soft_max:
                out.extend(_split_profile_document(merged, policy, repeated_header=endpoint))
            else:
                endpoint_doc = bucket[0]
                groups: list[list[Document]] = []
                for item in bucket[1:]:
                    role = str((item.metadata or {}).get("content_role") or "ordinary_body")
                    if groups and str((groups[-1][0].metadata or {}).get("content_role") or "ordinary_body") == role:
                        groups[-1].append(item)
                    else:
                        groups.append([item])
                if not groups:
                    groups = [[]]
                for group in groups:
                    role = str((group[0].metadata or {}).get("content_role") or "api_endpoint") if group else "api_endpoint"
                    part = _merge_documents([endpoint_doc, *group], role, "api_doc")
                    part.metadata["endpoint"] = endpoint
                    if api_name:
                        part.metadata["api_name"] = api_name
                    part = _annotate_api_sample(part)
                    out.extend(_split_profile_document(part, policy, repeated_header=endpoint))
            bucket.clear()
            endpoint = ""
            api_name = ""

    for doc in _expand_sdk_api_documents(docs):
        canonical_role = str((doc.metadata or {}).get("content_role") or "")
        path = str((doc.metadata or {}).get("section_path") or "")
        http = _ENDPOINT_RE.search(f"{path}\n{doc.page_content or ''}")
        sdk = _sdk_signature_match(doc.page_content or "") or (
            bool((doc.metadata or {}).get("endpoint")) and canonical_role == "api_endpoint"
        )
        if http or sdk or canonical_role == "api_endpoint":
            flush()
            endpoint, api_name = _api_endpoint_label(doc)
            bucket.append(_copy_doc(doc, content_role="api_endpoint", endpoint=endpoint, api_name=api_name))
            continue
        role = canonical_role if canonical_role in {"api_request", "api_response"} else ("api_request" if re.search(r"请求|request", path + doc.page_content[:30], re.I) else (
            "api_response" if re.search(r"响应|response|返回", path + doc.page_content[:30], re.I) else "ordinary_body"
        ))
        if bucket:
            bucket.append(_copy_doc(doc, content_role=role))
        else:
            bucket.append(_copy_doc(doc, content_role=role))
    flush()
    return out


def _split_table(doc: Document, limit: int) -> list[Document]:
    lines = [line for line in doc.page_content.splitlines() if line.strip()]
    if len(lines) < 3 or len(doc.page_content) <= limit:
        return [_copy_doc(doc, content_role="table")]
    header = lines[:2]
    rows = lines[2:]
    parts: list[Document] = []
    current: list[str] = []
    for row in rows:
        rendered = "\n".join([*header, *current, row])
        if current and len(rendered) > limit:
            part = _copy_doc(doc, content_role="table")
            part.page_content = "\n".join([*header, *current])
            parts.append(part)
            current = []
        current.append(row)
    if current:
        part = _copy_doc(doc, content_role="table")
        part.page_content = "\n".join([*header, *current])
        parts.append(part)
    return parts


def _build_table_doc(docs: list[Document], policy: ChunkPolicy) -> list[Document]:
    out: list[Document] = []
    table_positions = [i for i, doc in enumerate(docs) if (doc.metadata or {}).get("content_type") == "table"]
    for index, doc in enumerate(docs):
        element_id = (_element_ids(doc) or [""])[0]
        if (doc.metadata or {}).get("content_type") == "table":
            related = []
            if index > 0 and _TABLE_TITLE_RE.match(docs[index - 1].page_content or ""):
                related.extend(_element_ids(docs[index - 1]))
            if index + 1 < len(docs) and index + 1 not in table_positions and not _TABLE_TITLE_RE.match(docs[index + 1].page_content or ""):
                related.extend(_element_ids(docs[index + 1]))
            for part in _split_table(doc, policy.table_row_group_max):
                part.metadata.update(content_role="table", related_element_ids=related, chunking_method="table")
                out.append(part)
            continue
        next_table = index + 1 < len(docs) and (docs[index + 1].metadata or {}).get("content_type") == "table"
        prev_table = index > 0 and (docs[index - 1].metadata or {}).get("content_type") == "table"
        role = "table_title" if _TABLE_TITLE_RE.match(doc.page_content or "") and next_table else (
            "table_context" if prev_table else "ordinary_body"
        )
        related = _element_ids(docs[index + 1]) if role == "table_title" else (
            _element_ids(docs[index - 1]) if role == "table_context" else []
        )
        out.append(_copy_doc(doc, content_role=role, related_element_ids=related, chunking_method="table_doc"))
    return out


def _build_record_list(docs: list[Document], policy: ChunkPolicy) -> list[Document]:
    out: list[Document] = []
    for doc in docs:
        records = _split_numbered_document(doc, _RECORD_RE)
        for record in records:
            prepared = _copy_doc(
                record,
                content_role="record",
                related_element_ids=[],
                chunking_method="record_list",
            )
            out.extend(
                _split_profile_document(
                    prepared,
                    policy,
                    repeated_header=_first_content_line(record.page_content),
                )
            )
    return out


def finalize_profile_chunks(
    docs: Iterable[Document], profile: DocumentProfile, policy: ChunkPolicy
) -> list[Document]:
    """Common finalizer for profile metadata, explicit relations, IDs, and adjacency."""
    chunks = [doc for doc in docs if (doc.page_content or "").strip()]
    table_ids_by_element: dict[str, list[str]] = {}
    for doc in chunks:
        meta = doc.metadata or {}
        if meta.get("content_role") != "table":
            continue
        table_id = str(meta.get("table_id") or "")
        if not table_id:
            source_id = (_element_ids(doc) or [doc.page_content])[0]
            table_id = f"table_{hashlib.sha1(source_id.encode('utf-8')).hexdigest()[:12]}"
            meta["table_id"] = table_id
        for element_id in _element_ids(doc):
            table_ids_by_element.setdefault(element_id, []).append(table_id)
        doc.metadata = meta

    for doc in chunks:
        meta = dict(doc.metadata or {})
        content_type = str(meta.get("content_type") or "text")
        role = str(meta.get("content_role") or (content_type if content_type in _ATOMIC_CONTENT_TYPES else "ordinary_body"))
        related = list(dict.fromkeys(str(value) for value in (meta.get("related_element_ids") or []) if str(value).strip()))
        related_tables = list(dict.fromkeys(table_id for element_id in related for table_id in table_ids_by_element.get(element_id, [])))
        meta.update(
            document_profile=profile.value,
            chunk_policy_id=policy.policy_id,
            content_role=role,
            related_element_ids=related,
            related_table_ids=related_tables,
        )
        meta.setdefault("chunking_method", profile.value)
        meta["searchable_text"] = build_searchable_text(meta.get("section_path", "").split(" > "), doc.page_content, content_type)
        doc.metadata = meta

    return reassign_chunk_adjacency(chunks)
