"""Rule extractors for handbook chapter leaves: core procedures + format tables.

GraphRAG policy: graph stores navigational skeleton (Procedure / Format under Tool),
not GUI label ConfigItems. Detailed click-paths stay in evidence chunks.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Iterable

from rag_knowledge.models.graph_schema import DATA_SPEC_KEYWORDS
from rag_knowledge.services.domain_catalog import DomainCatalogLoader
from rag_knowledge.services.graph_extraction import (
    ChunkLinkCandidate,
    EntityCandidate,
    ExtractionResult,
    RelationCandidate,
)

# High-precision procedure titles common in Chinese tooling manuals.
_PROCEDURE_TITLES = frozenset({"新建工程", "继续工程"})

# e.g. TIFF（*.tif） / Las（*.las） / ArcInfo ASCII Grid(*.asc)
_FORMAT_CELL_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9_+./ -]{1,40}?)\s*[（(]\s*\*\.[A-Za-z0-9]+\s*[）)]"
)

_CREATED_BY = "rule:chapter_leaf"


def _parts(chunk: dict) -> tuple[str, str, str, str, str, dict]:
    metadata = chunk.get("metadata") or {}
    chunk_id = str(chunk.get("chunk_id") or metadata.get("chunk_id") or "")
    content = str(chunk.get("content") or "")
    source = str(metadata.get("source") or "")
    category = str(metadata.get("doc_category") or "")
    path = str(metadata.get("section_path") or "")
    return chunk_id, content, source, category, path, metadata


def resolve_path_owner(
    section_path: str,
    catalog: DomainCatalogLoader | None = None,
) -> str | None:
    """Most specific Tool/Service segment in section_path."""
    loader = catalog or DomainCatalogLoader()
    owner: str | None = None
    for part in [p.strip("：: \t\u3000") for p in section_path.split(">") if p.strip("：: \t\u3000")]:
        resolved = loader.resolve(part)
        if resolved and resolved[1] in {"Tool", "Service"}:
            owner = resolved[0]
    return owner


def _iter_markdown_tables(content: str) -> Iterable[list[list[str]]]:
    lines = [line.strip() for line in content.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        return
    rows = list(csv.reader(io.StringIO("\n".join(line.strip("|") for line in lines)), delimiter="|"))
    cleaned = [[cell.strip() for cell in row] for row in rows if any(cell.strip() for cell in row)]
    if len(cleaned) >= 3:
        yield cleaned


class ChapterLeafExtractor:
    """Extract Procedure + Format leaves under the path Tool owner (no ConfigItem)."""

    def __init__(self, catalog: DomainCatalogLoader | None = None):
        self.catalog = catalog or DomainCatalogLoader()

    def extract(self, chunk: dict, context: ExtractionResult | None = None) -> ExtractionResult:
        chunk_id, content, source, category, path, _metadata = _parts(chunk)
        result = ExtractionResult()
        owner = resolve_path_owner(path, self.catalog)
        if not owner:
            return result

        evidence_base = path or content[:240]
        props = {"created_by": _CREATED_BY}
        for title in _PROCEDURE_TITLES:
            if not self._has_standalone_title(content, title):
                continue
            result.entities.append(
                EntityCandidate(
                    title,
                    "Procedure",
                    category,
                    dict(props),
                    source_chunk_id=chunk_id,
                    evidence_text=title,
                )
            )
            result.relations.append(
                RelationCandidate(owner, "has_procedure", title, chunk_id, title)
            )
            result.links.append(
                ChunkLinkCandidate(title, chunk_id, section_path=path, source=source, evidence_text=title)
            )

        path_parts = [p.strip("：: \t\u3000") for p in path.split(">") if p.strip("：: \t\u3000")]
        if any(part in DATA_SPEC_KEYWORDS for part in path_parts):
            for fmt in self._formats_from_tables(content):
                result.entities.append(
                    EntityCandidate(
                        fmt,
                        "Format",
                        category,
                        dict(props),
                        source_chunk_id=chunk_id,
                        evidence_text=fmt,
                    )
                )
                result.relations.append(
                    RelationCandidate(owner, "supports_format", fmt, chunk_id, evidence_base)
                )
                result.links.append(
                    ChunkLinkCandidate(fmt, chunk_id, section_path=path, source=source, evidence_text=fmt)
                )
        return result

    @staticmethod
    def _has_standalone_title(content: str, title: str) -> bool:
        for raw in content.splitlines():
            line = raw.strip().lstrip("#").strip()
            if line == title:
                return True
        return False

    @staticmethod
    def _formats_from_tables(content: str) -> list[str]:
        formats: list[str] = []
        seen: set[str] = set()
        for table in _iter_markdown_tables(content):
            headers = [h.strip() for h in table[0]]
            fmt_idx = next((i for i, h in enumerate(headers) if h in {"数据格式", "格式", "Format"}), None)
            if fmt_idx is None:
                continue
            for row in table[2:]:
                if fmt_idx >= len(row):
                    continue
                cell = row[fmt_idx]
                for match in _FORMAT_CELL_RE.finditer(cell):
                    name = re.sub(r"\s+", " ", match.group(1)).strip()
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    formats.append(name)
        return formats
