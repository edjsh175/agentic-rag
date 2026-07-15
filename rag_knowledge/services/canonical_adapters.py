"""Phase-2 format adapters that emit the same canonical Document contract."""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup
from langchain_core.documents import Document

from rag_knowledge.models.structured_document import build_searchable_text


ADAPTER_EXTENSIONS = {".pdf", ".pptx", ".html", ".htm", ".sql", ".cnf", ".conf", ".cfg", ".ini", ".xml"}
_CONFIG_EXTENSIONS = {".cnf", ".conf", ".cfg", ".ini"}


def _snapshot(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _document(
    path: Path,
    snapshot: str,
    order: int,
    text: str,
    section_path: list[str],
    *,
    role: str = "ordinary_body",
    content_type: str = "text",
    page_number: int | None = None,
    **metadata,
) -> Document:
    element_id = f"el_{hashlib.sha1(f'{snapshot}|{order}'.encode('utf-8')).hexdigest()[:16]}"
    joined_path = " > ".join(section_path)
    result = {
        "source": path.name,
        "section_title": section_path[-1] if section_path else "",
        "section_path": joined_path,
        "heading_level": len(section_path),
        "element_order": order,
        "content_type": content_type,
        "content_role": role,
        "chunking_method": "canonical_adapter",
        "element_id": element_id,
        "source_element_ids": [element_id],
        "source_raw_block_ids": [f"rb_{order:04d}"],
        "related_element_ids": [],
        "source_document_id": snapshot[:32],
        "source_snapshot_hash": snapshot,
        "searchable_text": build_searchable_text(section_path, text, content_type),
        **metadata,
    }
    if page_number is not None:
        result["page_number"] = page_number
    return Document(page_content=text, metadata=result)


def load_canonical_documents(path: str | Path) -> list[Document]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in ADAPTER_EXTENSIONS:
        raise ValueError(f"no canonical adapter for {suffix}")
    if suffix in {".html", ".htm"}:
        return _load_html(source)
    if suffix == ".pptx":
        return _load_pptx(source)
    if suffix == ".pdf":
        return _load_pdf(source)
    if suffix == ".sql":
        return _load_sql(source)
    if suffix in _CONFIG_EXTENSIONS or suffix == ".xml":
        return _load_config(source)
    raise ValueError(f"no canonical adapter for {suffix}")


def _load_html(path: Path) -> list[Document]:
    snapshot = _snapshot(path)
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    for node in soup.select("script,style,nav,header,footer,aside,noscript"):
        node.decompose()

    section_path: list[str] = []
    body: list[str] = []
    docs: list[Document] = []

    def flush() -> None:
        text = "\n\n".join(part for part in body if part).strip()
        body.clear()
        if text:
            docs.append(_document(path, snapshot, len(docs) + 1, text, list(section_path)))

    for node in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "table"]):
        if re.fullmatch(r"h[1-6]", node.name or ""):
            flush()
            level = int(node.name[1])
            section_path = section_path[: level - 1]
            while len(section_path) < level - 1:
                section_path.append("")
            section_path.append(node.get_text(" ", strip=True))
        elif node.name == "table":
            rows = []
            for row in node.find_all("tr"):
                cells = [cell.get_text(" ", strip=True).replace("|", "\\|") for cell in row.find_all(["th", "td"])]
                if cells:
                    rows.append(cells)
            if rows:
                width = max(len(row) for row in rows)
                normalized = [row + [""] * (width - len(row)) for row in rows]
                body.append("| " + " | ".join(normalized[0]) + " |")
                body.append("| " + " | ".join("---" for _ in range(width)) + " |")
                body.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
        else:
            text = node.get_text("\n" if node.name == "pre" else " ", strip=True)
            if text:
                body.append(text)
    flush()
    return docs


def _xml_texts(payload: bytes) -> list[str]:
    root = ET.fromstring(payload)
    return [str(node.text).strip() for node in root.iter() if node.tag.endswith("}t") and str(node.text or "").strip()]


def _load_pptx(path: Path) -> list[Document]:
    snapshot = _snapshot(path)
    docs: list[Document] = []
    with zipfile.ZipFile(path) as archive:
        slide_names = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=lambda name: int(re.search(r"\d+", Path(name).stem).group()),
        )
        for page_number, slide_name in enumerate(slide_names, start=1):
            texts = _xml_texts(archive.read(slide_name))
            notes_name = f"ppt/notesSlides/notesSlide{page_number}.xml"
            notes = _xml_texts(archive.read(notes_name)) if notes_name in archive.namelist() else []
            title = texts[0] if texts else f"Slide {page_number}"
            body_parts = texts[1:]
            if notes:
                body_parts.append("备注：" + " ".join(notes))
            body = "\n\n".join(body_parts).strip() or title
            docs.append(_document(path, snapshot, page_number, body, [title], page_number=page_number, slide_number=page_number))
    return docs


def _load_pdf(path: Path) -> list[Document]:
    import fitz

    snapshot = _snapshot(path)
    docs: list[Document] = []
    pdf = fitz.open(path)
    try:
        for page_number, page in enumerate(pdf, start=1):
            text = page.get_text("text", sort=True).strip()
            if not text:
                docs.append(
                    _document(
                        path,
                        snapshot,
                        page_number,
                        f"[第 {page_number} 页需要 OCR]",
                        [f"第 {page_number} 页"],
                        role="ocr_required",
                        content_type="ocr_evidence",
                        page_number=page_number,
                        ocr_status="required",
                    )
                )
                continue
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            title = lines[0] if lines and len(lines[0]) <= 80 else f"第 {page_number} 页"
            docs.append(_document(path, snapshot, page_number, text, [title], page_number=page_number, ocr_status="not_needed"))
    finally:
        pdf.close()
    return docs


def _load_sql(path: Path) -> list[Document]:
    snapshot = _snapshot(path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        import sqlparse
        statements = [statement.strip() for statement in sqlparse.split(text) if statement.strip()]
    except ImportError:
        statements = [statement.strip() + ";" for statement in text.split(";") if statement.strip()]
    docs: list[Document] = []
    for order, statement in enumerate(statements, start=1):
        match = re.search(r"\b(?:CREATE|ALTER)\s+(?:OR\s+REPLACE\s+)?(?:TABLE|FUNCTION|PROCEDURE|VIEW)\s+([\w.\"`]+)", statement, re.I)
        title = match.group(1).strip('"`') if match else f"SQL {order}"
        docs.append(_document(path, snapshot, order, statement, [title], role="code", content_type="code"))
    return docs


def _load_config(path: Path) -> list[Document]:
    snapshot = _snapshot(path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() == ".xml":
        try:
            root_name = ET.fromstring(text).tag.split("}")[-1]
        except ET.ParseError:
            root_name = "XML"
        return [_document(path, snapshot, 1, text, [root_name], role="code", content_type="code")]

    sections: list[tuple[str, list[str]]] = []
    current_name = "global"
    current: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*\[([^]]+)]\s*$", line)
        if match:
            if current:
                sections.append((current_name, current))
            current_name = match.group(1).strip()
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append((current_name, current))
    return [
        _document(path, snapshot, order, "\n".join(lines).strip(), [name], role="code", content_type="code")
        for order, (name, lines) in enumerate(sections, start=1) if "\n".join(lines).strip()
    ]
