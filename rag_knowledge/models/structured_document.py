"""Canonical structured document elements and rendering helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re

from langchain_core.documents import Document

from rag_knowledge.models.document import FileCategory


@dataclass(slots=True)
class CanonicalDocumentElement:
    """Normalized intermediate representation for structured documents."""

    element_type: str
    section_path: list[str]
    content_markdown: str
    searchable_text: str
    source: str
    page_label: str = ""
    element_order: int = 0
    content_type: str = "text"
    chunking_method: str = "structured"
    heading_level: int = 0
    section_index: int = 0
    chunk_in_section: int = 0
    element_id: str = ""
    source_raw_block_ids: list[str] | None = None
    source_document_id: str = ""
    source_snapshot_hash: str = ""
    content_role: str = "ordinary_body"
    related_element_ids: list[str] | None = None


def join_section_path(section_path: list[str] | tuple[str, ...] | None) -> str:
    return " > ".join(part.strip() for part in (section_path or []) if str(part).strip())


def render_section_prefix(section_path: list[str] | tuple[str, ...] | None) -> str:
    joined = join_section_path(section_path)
    if not joined:
        return ""
    return f"# {joined}"


def markdown_table_to_searchable_text(table_text: str) -> str:
    lines = [line.strip() for line in (table_text or "").splitlines() if line.strip()]
    rows: list[str] = []
    for line in lines:
        if "|" not in line:
            rows.append(_collapse_whitespace(line))
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells:
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells if cell):
            continue
        joined = " ".join(cell for cell in cells if cell)
        if joined:
            rows.append(_collapse_whitespace(joined))
    return "\n".join(rows)


def markdown_to_searchable_text(markdown_text: str) -> str:
    text = markdown_text or ""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"[*_~>#]", " ", text)
    return _collapse_whitespace(text)


def build_searchable_text(
    section_path: list[str] | tuple[str, ...] | None,
    body_markdown: str,
    content_type: str | None = None,
) -> str:
    body = (
        markdown_table_to_searchable_text(body_markdown)
        if content_type == "table"
        else markdown_to_searchable_text(body_markdown)
    )
    section_text = join_section_path(section_path)
    combined = "\n".join(part for part in (section_text, body) if part)
    return _collapse_whitespace(combined, preserve_newlines=True)


def canonical_element_to_document(element: CanonicalDocumentElement) -> Document:
    section_path = join_section_path(element.section_path)
    metadata = {
        "source": element.source,
        "category": FileCategory.TEXT,
        "section_title": element.section_path[-1] if element.section_path else "",
        "section_path": section_path,
        "heading_level": int(element.heading_level or 0),
        "section_index": int(element.section_index or 0),
        "chunk_in_section": int(element.chunk_in_section or 0),
        "element_order": int(element.element_order or 0),
        "content_type": element.content_type,
        "chunking_method": element.chunking_method,
        "searchable_text": element.searchable_text,
        "element_id": element.element_id,
        "source_element_ids": [element.element_id] if element.element_id else [],
        "source_raw_block_ids": list(element.source_raw_block_ids or []),
        "source_document_id": element.source_document_id or "",
        "source_snapshot_hash": element.source_snapshot_hash or "",
        "content_role": element.content_role or "ordinary_body",
        "related_element_ids": list(element.related_element_ids or []),
    }
    if element.page_label not in ("", None):
        metadata["page_number"] = element.page_label
    return Document(page_content=element.content_markdown, metadata=metadata)


def _collapse_whitespace(text: str, preserve_newlines: bool = False) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return ""
    if preserve_newlines:
        lines = [re.sub(r"\s+", " ", line).strip() for line in stripped.splitlines()]
        return "\n".join(line for line in lines if line)
    return re.sub(r"\s+", " ", stripped).strip()
