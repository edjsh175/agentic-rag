"""Structured chapter-aware loaders for DOCX / Markdown / TXT."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from docx import Document as DocxDocument
from docx.document import Document as DocxDocumentType
from docx.table import Table
from docx.text.paragraph import Paragraph
from langchain_core.documents import Document

from rag_knowledge.models.structured_document import (
    CanonicalDocumentElement,
    build_searchable_text,
    canonical_element_to_document,
)

logger = logging.getLogger(__name__)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _element_id_for(document_key: str, element_order: int) -> str:
    payload = f"{document_key}|el|{element_order}".encode("utf-8")
    return f"el_{hashlib.sha1(payload).hexdigest()[:16]}"

_FALLBACK_COMMAND_RE = re.compile(
    r"^(?:umount|mount|reboot|systemctl|vim|vi|nano|chmod|chown|mkdir|cd|cp|mv|rm|cat|echo|"
    r"export|pm2|docker|kubectl|curl|wget|pip|npm|yarn|apt|yum|dnf)(?:\s+|$)",
    re.I,
)
_FALLBACK_PATH_RE = re.compile(r"^(?:[A-Za-z]:)?(?:/|\\|~/)[\w./\\:-]+$")
_FALLBACK_CONFIG_RE = re.compile(r"^[A-Za-z_][\w.-]*\s*[=:]\s*\S+")
_FALLBACK_PORT_RE = re.compile(r"^\d{2,5}\s*[:：]")
_SEPARATOR_TITLE_RE = re.compile(r"^(?P<title>\d+\.\s*\S.*?)(?:={3,}|…{3,}|-{3,})\s*$")
_TOC_TITLE_RE = re.compile(r"^(?:目录|文章目录|contents?)$", re.I)
_NUMBERED_ROLE_RE = re.compile(r"^\s*(?:步骤\s*)?(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+)[.、）)]?\s*\S+")
_ENDPOINT_ROLE_RE = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/\S+", re.I)
_TABLE_TITLE_ROLE_RE = re.compile(r"^\s*(?:表\s*[\d一二三四五六七八九十]+|table\s*\d+)\s*[：:.、-]", re.I)

try:
    from unstructured.partition.md import partition_md
except ImportError:  # pragma: no cover - exercised via runtime fallback
    partition_md = None

try:
    from unstructured.partition.text import partition_text
except ImportError:  # pragma: no cover - exercised via runtime fallback
    partition_text = None


SUPPORTED_EXTS = {".txt", ".md", ".docx"}
_UNSTRUCTURED_PARTITIONERS = {
    ".txt": partition_text,
    ".md": partition_md,
}


def infer_content_role(text: str, *, numbered: bool = False) -> str:
    """Infer only local structural roles; never infer a document profile or use filenames."""
    value = (text or "").strip()
    if not value:
        return "ordinary_body"
    if _ENDPOINT_ROLE_RE.search(value):
        return "api_endpoint"
    if _TABLE_TITLE_ROLE_RE.match(value):
        return "table_title"
    if _FALLBACK_COMMAND_RE.match(value) or value.startswith("```"):
        return "command" if not value.startswith("```") else "code"
    if re.match(r"^(?:请求|request)\s*(?:参数|示例|头|体|body|headers?)?", value, re.I):
        return "api_request"
    if re.match(r"^(?:响应|返回|response)\s*(?:参数|示例|体|body)?", value, re.I):
        return "api_response"
    if numbered or _NUMBERED_ROLE_RE.match(value):
        return "step"
    return "ordinary_body"


class UnstructuredChapterLoader:
    """Parse structured documents into section-aware intermediate chunks."""

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 150, strategy: str = "fast"):
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._strategy = strategy

    def load(self, file_path: str) -> list[Document]:
        path = Path(file_path)
        suffix = path.suffix.lower()
        snapshot = _sha256_file(path) if path.is_file() else ""
        document_key = snapshot or path.name

        if suffix == ".docx":
            elements = self._parse_docx(path, document_key=document_key, snapshot=snapshot)
        else:
            partition_fn = _UNSTRUCTURED_PARTITIONERS.get(suffix)
            if partition_fn is None:
                raise ValueError(f"unstructured loader does not support file type: {suffix}")
            if partition_fn is None:
                raise ImportError(f"missing unstructured parser for {suffix}")
            elements = self._parse_unstructured(
                path, partition_fn, document_key=document_key, snapshot=snapshot
            )

        docs = [canonical_element_to_document(element) for element in elements if element.content_markdown.strip()]
        logger.info("structured parse finished: %s -> %d element docs", path.name, len(docs))
        return docs

    def _parse_docx(
        self,
        path: Path,
        document_key: str = "",
        snapshot: str = "",
    ) -> list[CanonicalDocumentElement]:
        document = DocxDocument(str(path))
        collector = _ElementCollector(
            path.name,
            document_key=document_key or path.name,
            source_snapshot_hash=snapshot,
        )
        has_explicit_heading = False
        raw_block_index = 0

        for block in self._iter_docx_blocks(document):
            if isinstance(block, Paragraph):
                text = block.text.strip()
                if not text:
                    continue
                if self._is_docx_toc_paragraph(block):
                    continue
                heading_level = self._extract_docx_heading_level(block)
                if heading_level is None:
                    fallback_level = self._is_conservative_heading_fallback(block)
                    if fallback_level is not None and has_explicit_heading:
                        heading_level = fallback_level
                        logger.info("Heuristic heading detected: %s (level=%d)", text, heading_level)
                    elif fallback_level is not None:
                        # A DOCX cover commonly uses large bold text for product names,
                        # version dates, and organizations. Do not let it create a
                        # section path before the document reaches a styled heading.
                        continue
                else:
                    has_explicit_heading = True
                if heading_level is not None:
                    collector.handle_heading(text, heading_level)
                else:
                    raw_block_index += 1
                    collector.handle_text(
                        text,
                        raw_block_id=f"rb_{raw_block_index:04d}",
                        content_role=infer_content_role(
                            text,
                            numbered=self._has_docx_numbering(block),
                        ),
                    )
            elif isinstance(block, Table):
                raw_block_index += 1
                collector.handle_table(
                    self._docx_table_to_markdown(block),
                    raw_block_id=f"rb_{raw_block_index:04d}",
                )

        return collector.finish()

    def _parse_unstructured(
        self,
        path: Path,
        partition_fn,
        document_key: str = "",
        snapshot: str = "",
    ) -> list[CanonicalDocumentElement]:
        if partition_fn is None:
            raise ImportError(f"missing unstructured parser for {path.suffix.lower()}")

        logger.info("unstructured parse: %s (strategy=%s)", path.name, self._strategy)
        elements = partition_fn(filename=str(path), strategy=self._strategy)
        collector = _ElementCollector(
            path.name,
            document_key=document_key or path.name,
            source_snapshot_hash=snapshot,
        )
        skipping_markdown_toc = False
        raw_block_index = 0

        for element in elements:
            category = str(getattr(element, "category", "") or "")
            text = str(element).strip()
            if not text:
                continue

            metadata = self._element_metadata_dict(element)
            separator_title = _SEPARATOR_TITLE_RE.match(text)
            is_title_candidate = category == "Title" or bool(separator_title)
            title = separator_title.group("title").strip() if separator_title else text
            if path.suffix.lower() == ".md" and is_title_candidate and _TOC_TITLE_RE.match(title):
                skipping_markdown_toc = True
                continue
            if skipping_markdown_toc:
                if not is_title_candidate:
                    continue
                skipping_markdown_toc = False

            # partition_text frequently marks ordinary sentences, table cells, and code
            # fragments as Title. For plain text, only an explicit separator title has
            # enough structure to become a section path; the remaining content stays
            # searchable as body text.
            is_plain_text_title = path.suffix.lower() == ".txt" and not separator_title
            if (category == "Title" and not is_plain_text_title) or separator_title:
                collector.handle_heading(title, self._extract_unstructured_heading_level(metadata))
                continue

            if category in {"Table", "TableChunk"}:
                raw_block_index += 1
                rb = f"rb_{raw_block_index:04d}"
                html = metadata.get("text_as_html") or metadata.get("html_text")
                if html:
                    table_markdown = self._html_table_to_markdown(html)
                    if table_markdown:
                        collector.handle_table(table_markdown, raw_block_id=rb)
                        continue
                collector.handle_table(text, raw_block_id=rb)
                continue

            raw_block_index += 1
            collector.handle_text(
                text,
                raw_block_id=f"rb_{raw_block_index:04d}",
                content_role=infer_content_role(text),
            )

        return collector.finish()

    @staticmethod
    def _iter_docx_blocks(document: DocxDocumentType):
        parent = document.element.body
        for child in parent.iterchildren():
            if child.tag.endswith("}p"):
                yield Paragraph(child, document)
            elif child.tag.endswith("}tbl"):
                yield Table(child, document)

    @staticmethod
    def _extract_docx_heading_level(paragraph: Paragraph) -> int | None:
        style = getattr(paragraph, "style", None)
        candidates = []
        if style is not None:
            candidates.extend(
                value for value in (
                    getattr(style, "style_id", None),
                    getattr(style, "name", None),
                ) if value
            )
        for candidate in candidates:
            match = re.search(r"(?:heading|标题)\s*([1-6])$", str(candidate).strip(), re.I)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _has_docx_numbering(paragraph: Paragraph) -> bool:
        paragraph_properties = getattr(paragraph._p, "pPr", None)
        if paragraph_properties is not None and paragraph_properties.numPr is not None:
            return True
        style_name = str(getattr(getattr(paragraph, "style", None), "name", "") or "")
        return "list" in style_name.lower() or "列表" in style_name

    @classmethod
    def _is_conservative_heading_fallback(cls, paragraph: Paragraph) -> int | None:
        text = paragraph.text.strip()
        if not text:
            return None

        # 1. 单行且长度限制
        if "\n" in text or len(text) > 50:
            return None

        # 2. 不能以特定标点符号结尾
        if text.endswith((".", "。", "?", "？", "!", "！", ";", "；", ":", "：")):
            return None

        if cls._is_non_heading_fallback_text(text):
            return None

        # 3. 匹配编号标题模式
        patterns = [
            r"^\d+(?:\.\d+)*\s+\S+",                      # e.g., 1.2.3 管线点表
            r"^第[一二三四五六七八九十百]+[章章节回]\s*\S*",      # e.g., 第一章 数据规范
            r"^[一二三四五六七八九十百]+[、\s]\s*\S+",           # e.g., 一、 发布流程
            r"^[（(][一二三四五六七八九十百]+[）)]\s*\S*",        # e.g., （一）数据规范 或 (一) 数据规范
        ]

        is_numbered = False
        for pattern in patterns:
            if re.match(pattern, text):
                is_numbered = True
                break

        if is_numbered:
            return cls._infer_heading_level(text)

        # 4. 无编号标题必须同时具有加粗和显著大字号，12pt 常见于中文正文。
        runs = getattr(paragraph, "runs", [])
        if runs and len(text) <= 25:
            has_text_runs = [r for r in runs if r.text.strip()]
            if has_text_runs:
                all_bold = all(r.bold for r in has_text_runs)
                has_large_font = False
                for r in has_text_runs:
                    if r.font and r.font.size and getattr(r.font.size, "pt", 0) >= 14:
                        has_large_font = True
                if all_bold and has_large_font:
                    return cls._infer_heading_level(text)

        return None

    @staticmethod
    def _is_non_heading_fallback_text(text: str) -> bool:
        return bool(
            _FALLBACK_COMMAND_RE.match(text)
            or _FALLBACK_PATH_RE.match(text)
            or _FALLBACK_CONFIG_RE.match(text)
            or _FALLBACK_PORT_RE.match(text)
        )

    @staticmethod
    def _infer_heading_level(text: str) -> int:
        text = text.strip()
        match = re.match(r"^(\d+(?:\.\d+)*)", text)
        if match:
            parts = match.group(1).split(".")
            return min(6, len(parts))

        if re.match(r"^第[一二三四五六七八九十百]+章", text):
            return 1
        if re.match(r"^第[一二三四五六七八九十百]+[章节]", text):
            return 2
        if re.match(r"^[一二三四五六七八九十百]+[、]", text):
            return 2
        if re.match(r"^[（(][一二三四五六七八九十百]+[）)]", text):
            return 3

        return 3

    @staticmethod
    def _is_docx_toc_paragraph(paragraph: Paragraph) -> bool:
        style = getattr(paragraph, "style", None)
        candidates = []
        if style is not None:
            candidates.extend(
                value for value in (
                    getattr(style, "style_id", None),
                    getattr(style, "name", None),
                ) if value
            )
        return any(str(candidate).strip().lower().startswith("toc") for candidate in candidates)

    @staticmethod
    def _extract_unstructured_heading_level(metadata: dict) -> int:
        depth = metadata.get("category_depth")
        try:
            return int(depth) + 1
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _element_metadata_dict(element) -> dict:
        metadata = getattr(element, "metadata", None)
        if metadata is None:
            return {}
        if hasattr(metadata, "to_dict"):
            return metadata.to_dict()
        if isinstance(metadata, dict):
            return metadata
        return {}

    @staticmethod
    def _docx_table_to_markdown(table: Table) -> str:
        rows = []
        max_cols = 0
        for row in table.rows:
            values = [cell.text.replace("|", "\\|").strip() for cell in row.cells]
            max_cols = max(max_cols, len(values))
            rows.append(values)

        if not rows or max_cols == 0:
            return ""

        normalized_rows = []
        for row in rows:
            padded = row + [""] * (max_cols - len(row))
            normalized_rows.append(padded)

        header = normalized_rows[0]
        lines = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join("---" for _ in range(max_cols)) + " |",
        ]
        for row in normalized_rows[1:]:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)

    @staticmethod
    def _html_table_to_markdown(html: str) -> str:
        try:
            from bs4 import BeautifulSoup
        except ImportError:  # pragma: no cover - dependency exists in project env
            return ""

        soup = BeautifulSoup(html, "html.parser")
        rows = []
        for tr in soup.find_all("tr"):
            cells = [cell.get_text(" ", strip=True).replace("|", "\\|") for cell in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        if not rows:
            return ""

        max_cols = max(len(row) for row in rows)
        padded_rows = [row + [""] * (max_cols - len(row)) for row in rows]
        header = padded_rows[0]
        lines = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join("---" for _ in range(max_cols)) + " |",
        ]
        for row in padded_rows[1:]:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)


class _ElementCollector:
    """Collect section-aware canonical elements while enforcing heading boundaries."""

    def __init__(
        self,
        source: str,
        document_key: str = "",
        source_snapshot_hash: str = "",
    ):
        self._source = source
        self._document_key = document_key or source
        self._source_snapshot_hash = source_snapshot_hash or ""
        self._source_document_id = (
            self._source_snapshot_hash[:32]
            if self._source_snapshot_hash
            else hashlib.sha1(self._document_key.encode("utf-8")).hexdigest()[:32]
        )
        self._section_path: list[str] = []
        self._buffer: list[str] = []
        self._buffer_raw_ids: list[str] = []
        self._buffer_role = "ordinary_body"
        self._elements: list[CanonicalDocumentElement] = []
        self._element_order = 0
        self._section_index = 0
        self._chunk_in_section = 0
        self._started = False
        self._pending_heading_titles: list[str] = []
        self._raw_fallback_seq = 0
        self._last_table_element_id = ""
        self._last_table_section_path: list[str] = []

    def _next_fallback_raw_id(self) -> str:
        self._raw_fallback_seq += 1
        return f"rb_{self._raw_fallback_seq:04d}"

    def handle_heading(self, title: str, level: int) -> None:
        self._flush_text_buffer()
        self._last_table_element_id = ""
        self._last_table_section_path = []
        level = max(1, int(level or 1))
        title = title.strip()
        if len(self._section_path) >= level:
            self._section_path = self._section_path[: level - 1]
        while len(self._section_path) < level - 1:
            self._section_path.append("")
        self._section_path.append(title)
        self._pending_heading_titles.append(title)
        if self._started:
            self._section_index += 1
        else:
            self._started = True
        self._chunk_in_section = 0

    def handle_text(
        self,
        text: str,
        raw_block_id: str | None = None,
        content_role: str = "ordinary_body",
    ) -> None:
        cleaned = text.strip()
        if cleaned:
            self._flush_orphan_headings_before_content()
            role = content_role or "ordinary_body"
            if (
                role == "ordinary_body"
                and self._last_table_element_id
                and self._last_table_section_path == self._section_path
            ):
                role = "table_context"
            if role in {"step", "record"}:
                if self._buffer and self._buffer_role != role:
                    self._flush_text_buffer()
                if not self._buffer:
                    self._buffer_role = role
            elif role in {"command", "table_title", "table_context", "api_endpoint"}:
                self._flush_text_buffer()
                self._buffer_role = role
            elif not self._buffer:
                self._buffer_role = role
            self._buffer.append(cleaned)
            self._buffer_raw_ids.append(raw_block_id or self._next_fallback_raw_id())
            if role == "command":
                self._flush_text_buffer()

    def handle_table(self, table_markdown: str, raw_block_id: str | None = None) -> None:
        table_text = table_markdown.strip()
        if not table_text:
            return
        self._flush_orphan_headings_before_content()
        self._flush_text_buffer()
        self._chunk_in_section += 1
        self._element_order += 1
        rb = raw_block_id or self._next_fallback_raw_id()
        element_id = _element_id_for(self._document_key, self._element_order)
        related: list[str] = []
        if self._elements:
            previous = self._elements[-1]
            if previous.content_role == "table_title" and previous.section_path == self._section_path:
                previous.related_element_ids = list(dict.fromkeys([*(previous.related_element_ids or []), element_id]))
                related.append(previous.element_id)
        self._elements.append(
            CanonicalDocumentElement(
                element_type="table",
                section_path=list(self._section_path),
                content_markdown=table_text,
                searchable_text=build_searchable_text(self._section_path, table_text, "table"),
                source=self._source,
                element_order=self._element_order,
                content_type="table",
                chunking_method="table",
                heading_level=len(self._section_path),
                section_index=self._section_index,
                chunk_in_section=self._chunk_in_section,
                element_id=element_id,
                source_raw_block_ids=[rb],
                source_document_id=self._source_document_id,
                source_snapshot_hash=self._source_snapshot_hash,
                content_role="table",
                related_element_ids=related,
            )
        )
        self._last_table_element_id = element_id
        self._last_table_section_path = list(self._section_path)

    def finish(self) -> list[CanonicalDocumentElement]:
        self._flush_text_buffer()
        self._flush_pending_heading_list()
        return self._elements

    def _flush_orphan_headings_before_content(self) -> None:
        if not self._pending_heading_titles:
            return
        path_ancestors = {title for title in self._section_path[:-1] if title}
        orphan_titles = [
            title
            for title in self._pending_heading_titles[:-1]
            if title and title not in path_ancestors
        ]
        self._append_heading_list(orphan_titles)
        self._pending_heading_titles.clear()

    def _flush_pending_heading_list(self) -> None:
        self._append_heading_list(self._pending_heading_titles)
        self._pending_heading_titles.clear()

    def _append_heading_list(self, titles: list[str]) -> None:
        if not titles:
            return
        body = "\n".join(f"- {title}" for title in titles)
        self._element_order += 1
        self._elements.append(
            CanonicalDocumentElement(
                element_type="heading_list",
                section_path=[],
                content_markdown=body,
                searchable_text=build_searchable_text([], body, "text"),
                source=self._source,
                element_order=self._element_order,
                content_type="heading",
                chunking_method="heading_list",
                element_id=_element_id_for(self._document_key, self._element_order),
                source_raw_block_ids=[],
                source_document_id=self._source_document_id,
                source_snapshot_hash=self._source_snapshot_hash,
                content_role="ordinary_body",
                related_element_ids=[],
            )
        )

    def _flush_text_buffer(self) -> None:
        if not self._buffer:
            return
        body = "\n\n".join(self._buffer).strip()
        raw_ids = list(self._buffer_raw_ids)
        role = self._buffer_role
        self._buffer.clear()
        self._buffer_raw_ids.clear()
        self._buffer_role = "ordinary_body"
        if not body:
            return
        self._chunk_in_section += 1
        self._element_order += 1
        element_id = _element_id_for(self._document_key, self._element_order)
        related: list[str] = []
        if (
            role == "table_context"
            and self._last_table_element_id
            and self._last_table_section_path == self._section_path
        ):
            related.append(self._last_table_element_id)
            for existing in reversed(self._elements):
                if existing.element_id == self._last_table_element_id:
                    existing.related_element_ids = list(dict.fromkeys([*(existing.related_element_ids or []), element_id]))
                    break
            self._last_table_element_id = ""
            self._last_table_section_path = []
        self._elements.append(
            CanonicalDocumentElement(
                element_type=role if role != "ordinary_body" else "paragraph",
                section_path=list(self._section_path),
                content_markdown=body,
                searchable_text=build_searchable_text(self._section_path, body, "text"),
                source=self._source,
                element_order=self._element_order,
                content_type="text",
                chunking_method="structured",
                heading_level=len(self._section_path),
                section_index=self._section_index,
                chunk_in_section=self._chunk_in_section,
                element_id=element_id,
                source_raw_block_ids=raw_ids,
                source_document_id=self._source_document_id,
                source_snapshot_hash=self._source_snapshot_hash,
                content_role=role,
                related_element_ids=related,
            )
        )
