"""Structured chapter-aware loaders for DOCX / Markdown / TXT."""

from __future__ import annotations

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


class UnstructuredChapterLoader:
    """Parse structured documents into section-aware intermediate chunks."""

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 150, strategy: str = "fast"):
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._strategy = strategy

    def load(self, file_path: str) -> list[Document]:
        path = Path(file_path)
        suffix = path.suffix.lower()

        if suffix == ".docx":
            elements = self._parse_docx(path)
        else:
            partition_fn = _UNSTRUCTURED_PARTITIONERS.get(suffix)
            if partition_fn is None:
                raise ValueError(f"unstructured loader does not support file type: {suffix}")
            if partition_fn is None:
                raise ImportError(f"missing unstructured parser for {suffix}")
            elements = self._parse_unstructured(path, partition_fn)

        docs = [canonical_element_to_document(element) for element in elements if element.content_markdown.strip()]
        logger.info("structured parse finished: %s -> %d element docs", path.name, len(docs))
        return docs

    def _parse_docx(self, path: Path) -> list[CanonicalDocumentElement]:
        document = DocxDocument(str(path))
        collector = _ElementCollector(path.name)

        for block in self._iter_docx_blocks(document):
            if isinstance(block, Paragraph):
                text = block.text.strip()
                if not text:
                    continue
                if self._is_docx_toc_paragraph(block):
                    continue
                heading_level = self._extract_docx_heading_level(block)
                if heading_level is not None:
                    collector.handle_heading(text, heading_level)
                else:
                    collector.handle_text(text)
            elif isinstance(block, Table):
                collector.handle_table(self._docx_table_to_markdown(block))

        return collector.finish()

    def _parse_unstructured(self, path: Path, partition_fn) -> list[CanonicalDocumentElement]:
        if partition_fn is None:
            raise ImportError(f"missing unstructured parser for {path.suffix.lower()}")

        logger.info("unstructured parse: %s (strategy=%s)", path.name, self._strategy)
        elements = partition_fn(filename=str(path), strategy=self._strategy)
        collector = _ElementCollector(path.name)

        for element in elements:
            category = str(getattr(element, "category", "") or "")
            text = str(element).strip()
            if not text:
                continue

            metadata = self._element_metadata_dict(element)
            if category == "Title":
                collector.handle_heading(text, self._extract_unstructured_heading_level(metadata))
                continue

            if category in {"Table", "TableChunk"}:
                html = metadata.get("text_as_html") or metadata.get("html_text")
                if html:
                    table_markdown = self._html_table_to_markdown(html)
                    if table_markdown:
                        collector.handle_table(table_markdown)
                        continue
                collector.handle_table(text)
                continue

            collector.handle_text(text)

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

    def __init__(self, source: str):
        self._source = source
        self._section_path: list[str] = []
        self._buffer: list[str] = []
        self._elements: list[CanonicalDocumentElement] = []
        self._element_order = 0
        self._section_index = 0
        self._chunk_in_section = 0
        self._started = False

    def handle_heading(self, title: str, level: int) -> None:
        self._flush_text_buffer()
        level = max(1, int(level or 1))
        title = title.strip()
        if len(self._section_path) >= level:
            self._section_path = self._section_path[: level - 1]
        while len(self._section_path) < level - 1:
            self._section_path.append("")
        self._section_path.append(title)
        if self._started:
            self._section_index += 1
        else:
            self._started = True
        self._chunk_in_section = 0

    def handle_text(self, text: str) -> None:
        cleaned = text.strip()
        if cleaned:
            self._buffer.append(cleaned)

    def handle_table(self, table_markdown: str) -> None:
        table_text = table_markdown.strip()
        if not table_text:
            return
        self._flush_text_buffer()
        self._chunk_in_section += 1
        self._element_order += 1
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
            )
        )

    def finish(self) -> list[CanonicalDocumentElement]:
        self._flush_text_buffer()
        return self._elements

    def _flush_text_buffer(self) -> None:
        if not self._buffer:
            return
        body = "\n\n".join(self._buffer).strip()
        self._buffer.clear()
        if not body:
            return
        self._chunk_in_section += 1
        self._element_order += 1
        self._elements.append(
            CanonicalDocumentElement(
                element_type="paragraph",
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
            )
        )
