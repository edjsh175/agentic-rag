"""Phase-2 format adapters that emit the canonical Document contract."""

from __future__ import annotations

import hashlib
import posixpath
import re
import statistics
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

import fitz  # PyMuPDF
from bs4 import BeautifulSoup
from langchain_core.documents import Document

from rag_knowledge.models.structured_document import build_searchable_text
from rag_knowledge.services.document_support import IngestionDecision, make_decision

ADAPTER_EXTENSIONS = {
    ".pdf", ".pptx", ".html", ".htm", ".sql",
    ".cnf", ".conf", ".cfg", ".ini", ".xml",
}
_CONFIG_EXTENSIONS = {".cnf", ".conf", ".cfg", ".ini"}
_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


@dataclass
class CanonicalAdapterResult:
    documents: list[Document] = field(default_factory=list)
    decisions: list[IngestionDecision] = field(default_factory=list)


def _snapshot(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _document(path: Path, snapshot: str, order: int, text: str, section_path: list[str], *,
              role: str = "ordinary_body", content_type: str = "text",
              page_number: int | None = None, raw_block_ids: list[str] | None = None,
              **metadata) -> Document:
    element_id = f"el_{hashlib.sha1(f'{snapshot}|{order}'.encode()).hexdigest()[:16]}"
    clean_path = [part for part in section_path if part]
    result = {
        "source": path.name,
        "section_title": clean_path[-1] if clean_path else "",
        "section_path": " > ".join(clean_path),
        "heading_level": len(clean_path),
        "element_order": order,
        "content_type": content_type,
        "content_role": role,
        "chunking_method": "canonical_adapter",
        "element_id": element_id,
        "source_element_ids": [element_id],
        "source_raw_block_ids": raw_block_ids or [f"rb_{order:04d}"],
        "related_element_ids": [],
        "source_document_id": snapshot[:32],
        "source_snapshot_hash": snapshot,
        "searchable_text": build_searchable_text(clean_path, text, content_type),
        **metadata,
    }
    if page_number is not None:
        result["page_number"] = page_number
    return Document(page_content=text, metadata=result)


def load_canonical_result(path: str | Path) -> CanonicalAdapterResult:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in ADAPTER_EXTENSIONS:
        raise ValueError(f"no canonical adapter for {suffix}")
    try:
        if suffix in {".html", ".htm"}:
            return _load_html(source)
        if suffix == ".pptx":
            return _load_pptx(source)
        if suffix == ".pdf":
            return _load_pdf(source)
        if suffix == ".sql":
            return _load_sql(source)
        if suffix == ".xml":
            return _load_xml(source)
        return _load_config(source)
    except Exception as exc:
        return CanonicalAdapterResult(decisions=[make_decision(
            source, status="queued", reason_code="FORMAT_PARSE_FAILED",
            message=f"FORMAT_PARSE_FAILED: {type(exc).__name__}",
        )])


def load_canonical_documents(path: str | Path) -> list[Document]:
    """Compatibility wrapper for callers that only need documents."""
    return load_canonical_result(path).documents


def _markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    return "\n".join([
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
        *("| " + " | ".join(row) + " |" for row in rows[1:]),
    ])


def _load_html(path: Path) -> CanonicalAdapterResult:
    snapshot = _snapshot(path)
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    for node in soup.select("script,style,nav,header,footer,aside,noscript"):
        node.decompose()
    container = soup.find("article") or soup.find("main") or soup.body or soup
    decisions = [make_decision(
        path, status="queued", reason_code="EMBEDDED_MEDIA_PROCESSING_DEFERRED",
        file_hash=snapshot, locator=f"html:media:{index}",
    ) for index, _ in enumerate(container.find_all(["img", "video", "audio"]), 1)]

    section_path: list[str] = []
    docs: list[Document] = []
    body: list[str] = []

    def emit(text: str, role: str = "ordinary_body", content_type: str = "text") -> None:
        if text.strip():
            docs.append(_document(path, snapshot, len(docs) + 1, text.strip(),
                                  section_path.copy() or [path.stem],
                                  role=role, content_type=content_type))

    def flush() -> None:
        if body:
            emit("\n\n".join(body))
            body.clear()

    for node in container.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "table"]):
        if node.find_parent(["table", "pre"]) and node.name not in {"table", "pre"}:
            continue
        if re.fullmatch(r"h[1-6]", node.name or ""):
            flush()
            level = int(node.name[1])
            section_path = section_path[:level - 1]
            section_path.extend([""] * max(0, level - 1 - len(section_path)))
            section_path.append(node.get_text(" ", strip=True))
        elif node.name == "table":
            flush()
            rows = [[cell.get_text(" ", strip=True).replace("|", "\\|")
                     for cell in row.find_all(["th", "td"])] for row in node.find_all("tr")]
            emit(_markdown_table([row for row in rows if row]), "table", "table")
        elif node.name == "pre":
            flush()
            emit(node.get_text("\n", strip=True), "code", "code")
        elif node.name == "li":
            flush()
            emit(node.get_text(" ", strip=True), "record")
        else:
            text = node.get_text(" ", strip=True)
            if text:
                body.append(text)
    flush()
    return CanonicalAdapterResult(docs, decisions)


def _xml_texts(node: ET.Element) -> list[str]:
    return [str(item.text).strip() for item in node.iter()
            if item.tag.endswith("}t") and str(item.text or "").strip()]


def _relationships(archive: zipfile.ZipFile, owner: str) -> list[tuple[str, str]]:
    rel_name = posixpath.join(posixpath.dirname(owner), "_rels", posixpath.basename(owner) + ".rels")
    if rel_name not in archive.namelist():
        return []
    root = ET.fromstring(archive.read(rel_name))
    return [(node.attrib.get("Type", ""), posixpath.normpath(posixpath.join(
        posixpath.dirname(owner), node.attrib.get("Target", "")))) for node in root]


def _load_pptx(path: Path) -> CanonicalAdapterResult:
    snapshot = _snapshot(path)
    docs: list[Document] = []
    decisions: list[IngestionDecision] = []
    with zipfile.ZipFile(path) as archive:
        slides = sorted((name for name in archive.namelist()
                         if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                        key=lambda name: int(re.search(r"\d+", Path(name).stem).group()))
        for slide_number, slide_name in enumerate(slides, 1):
            root = ET.fromstring(archive.read(slide_name))

            # Extract shapes with coordinates
            shapes = []
            for sp in root.findall(".//p:sp", _NS):
                off = sp.find("p:spPr/a:xfrm/a:off", _NS)
                x = int(off.attrib.get("x", 0)) if off is not None else 0
                y = int(off.attrib.get("y", 0)) if off is not None else 0

                ph = sp.find("p:nvSpPr/p:nvPr/p:ph", _NS)
                ph_type = ph.attrib.get("type", "") if ph is not None else ""
                is_title = ph_type in {"title", "ctrTitle"}

                paras_text = []
                for p in sp.findall("p:txBody/a:p", _NS):
                    p_text = "".join(t.text for t in p.findall("a:r/a:t", _NS) if t.text)
                    if p_text.strip():
                        lvl = p.find("a:pPr", _NS)
                        lvl_idx = int(lvl.attrib.get("lstLvl", 0)) if lvl is not None else 0
                        if lvl_idx > 0:
                            p_text = "  " * lvl_idx + "- " + p_text.strip()
                        paras_text.append(p_text)

                shape_text = "\n".join(paras_text).strip()
                if shape_text:
                    shapes.append({
                        "text": shape_text,
                        "x": x,
                        "y": y,
                        "is_title": is_title,
                    })

            shapes.sort(key=lambda s: (s["y"], s["x"]))

            title_shapes = [s for s in shapes if s["is_title"]]
            if title_shapes:
                title = "\n".join(s["text"] for s in title_shapes)
                body_shapes = [s for s in shapes if not s["is_title"]]
            else:
                if shapes:
                    title = shapes[0]["text"]
                    body_shapes = shapes[1:]
                else:
                    title = f"Slide {slide_number}"
                    body_shapes = []

            body_parts = [s["text"] for s in body_shapes]

            # Resolve notesSlide relationship with fallback
            notes_text = ""
            notes_resolved = False
            for kind, target in _relationships(archive, slide_name):
                if kind.endswith("/notesSlide") and target in archive.namelist():
                    notes_text = " ".join(_xml_texts(ET.fromstring(archive.read(target))))
                    notes_resolved = True
                    break
            if not notes_resolved:
                guessed_notes = f"ppt/notesSlides/notesSlide{slide_number}.xml"
                if guessed_notes in archive.namelist():
                    notes_text = " ".join(_xml_texts(ET.fromstring(archive.read(guessed_notes))))

            if notes_text.strip():
                body_parts.append(f"备注：{notes_text.strip()}")

            # Process slide relationships for media and charts
            for kind, target in _relationships(archive, slide_name):
                if kind.endswith(("/image", "/video", "/media", "/audio")):
                    decisions.append(make_decision(
                        path, status="queued", reason_code="EMBEDDED_MEDIA_PROCESSING_DEFERRED",
                        file_hash=snapshot, locator=f"slide:{slide_number}:{posixpath.basename(target)}",
                    ))
                elif kind.endswith("/chart") and target in archive.namelist():
                    values = [str(node.text).strip() for node in ET.fromstring(archive.read(target)).iter()
                              if node.tag.split("}")[-1] in {"t", "v"} and str(node.text or "").strip()]
                    if values:
                        docs.append(_document(
                            path, snapshot, len(docs) + 1, " ".join(dict.fromkeys(values)),
                            [title], role="chart", content_type="chart",
                            page_number=slide_number, slide_number=slide_number,
                            raw_block_ids=[f"slide_{slide_number}_chart"]
                        ))

            # Add slide body text chunk
            slide_content = "\n\n".join(body_parts).strip() or title
            docs.append(_document(
                path, snapshot, len(docs) + 1, slide_content, [title],
                page_number=slide_number, slide_number=slide_number,
                raw_block_ids=[f"slide_{slide_number}_body"]
            ))

            # Add slide tables
            for table_idx, table in enumerate(root.findall(".//a:tbl", _NS), 1):
                rows = []
                for row in table.findall("a:tr", _NS):
                    cells = []
                    for cell in row.findall("a:tc", _NS):
                        cell_text = "".join(t.text for t in cell.findall(".//a:t", _NS) if t.text)
                        cells.append(cell_text.strip().replace("|", "\\|"))
                    rows.append(cells)
                if rows:
                    docs.append(_document(
                        path, snapshot, len(docs) + 1, _markdown_table(rows), [title],
                        role="table", content_type="table", page_number=slide_number,
                        slide_number=slide_number,
                        raw_block_ids=[f"slide_{slide_number}_table_{table_idx}"]
                    ))

    return CanonicalAdapterResult(docs, decisions)


def _load_pdf(path: Path) -> CanonicalAdapterResult:
    snapshot = _snapshot(path)
    docs: list[Document] = []
    decisions: list[IngestionDecision] = []

    doc = fitz.open(path)
    try:
        toc = doc.get_toc()
        page_toc: dict[int, list[tuple[int, str]]] = {}
        for level, title, page in toc:
            if page > 0:
                page_toc.setdefault(page, []).append((level, title))

        # Collect font sizes to determine body baseline
        all_sizes = []
        for page in doc:
            blocks = page.get_text("dict", sort=True).get("blocks", [])
            for block in blocks:
                if block.get("type") == 0:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            all_sizes.append(span.get("size", 10.0))

        body_font_size = statistics.median(all_sizes) if all_sizes else 10.0

        section_path = [path.stem]
        active_section = section_path.copy()
        active_text_parts = []
        active_page_start = None
        active_page_end = None
        active_raw_block_ids = []

        def flush_active() -> None:
            if active_text_parts:
                merged_text = "\n\n".join(active_text_parts)
                docs.append(_document(
                    path, snapshot, len(docs) + 1, merged_text, active_section.copy(),
                    page_number=active_page_start, page_start=active_page_start, page_end=active_page_end,
                    raw_block_ids=active_raw_block_ids.copy(),
                ))
                active_text_parts.clear()
                active_raw_block_ids.clear()

        for page_idx, page in enumerate(doc, 1):
            page_has_text = False

            # Embedded images detection
            img_list = page.get_images(full=True)
            if img_list:
                decisions.append(make_decision(
                    path, status="queued", reason_code="EMBEDDED_MEDIA_PROCESSING_DEFERRED",
                    file_hash=snapshot, locator=f"page:{page_idx}",
                ))

            blocks = page.get_text("dict", sort=True).get("blocks", [])
            for block_idx, block in enumerate(blocks):
                if block.get("type") == 0:  # text block
                    block_text = ""
                    block_size_max = 0.0
                    block_is_bold = False
                    block_lines_count = len(block.get("lines", []))

                    lines = block.get("lines", [])
                    for line in lines:
                        line_text = ""
                        for span in line.get("spans", []):
                            span_text = span.get("text", "")
                            line_text += span_text
                            span_size = span.get("size", 10.0)
                            span_flags = span.get("flags", 0)
                            is_bold = bool(span_flags & 16)
                            if span_size > block_size_max:
                                block_size_max = span_size
                                block_is_bold = is_bold
                        if line_text.strip():
                            block_text += line_text + "\n"

                    block_text = block_text.strip()
                    if block_text:
                        page_has_text = True

                        is_heading = False
                        heading_title = ""
                        heading_level = 1

                        # Match Bookmarks
                        toc_matches = page_toc.get(page_idx, [])
                        for level, title in toc_matches:
                            t_clean = re.sub(r'[\s\d.、]+', '', title).lower()
                            b_clean = re.sub(r'[\s\d.、]+', '', block_text).lower()
                            if t_clean and b_clean and (b_clean in t_clean or t_clean in b_clean):
                                is_heading = True
                                heading_title = title
                                heading_level = level
                                break

                        if not is_heading:
                            if block_size_max > body_font_size * 1.25:
                                is_heading = True
                                heading_title = block_text
                                heading_level = 1 if block_size_max > body_font_size * 1.5 else 2
                            elif block_is_bold and len(block_text) < 80 and block_lines_count == 1 and block_size_max >= body_font_size:
                                is_heading = True
                                heading_title = block_text
                                heading_level = 3

                        if is_heading:
                            flush_active()
                            section_path = section_path[:heading_level]
                            section_path.extend([""] * max(0, heading_level - len(section_path)))
                            section_path.append(heading_title)
                            active_section = section_path.copy()
                        else:
                            if active_page_start is None:
                                active_page_start = page_idx
                            active_page_end = page_idx
                            active_text_parts.append(block_text)
                            active_raw_block_ids.append(f"page_{page_idx}_block_{block_idx}")

            if not page_has_text:
                decisions.append(make_decision(
                    path, status="queued", reason_code="PDF_PAGE_REQUIRES_OCR",
                    file_hash=snapshot, locator=f"page:{page_idx}",
                ))

        flush_active()
    finally:
        doc.close()

    return CanonicalAdapterResult(docs, decisions)


def _split_sql_statements(sql: str) -> list[str]:
    statements = []
    current = []
    i = 0
    n = len(sql)
    in_single_quote = False
    in_double_quote = False
    in_backtick = False
    in_line_comment = False
    in_block_comment = False
    dollar_tag = None

    while i < n:
        char = sql[i]

        if not (in_single_quote or in_double_quote or in_backtick or dollar_tag):
            if not in_block_comment and i + 1 < n and sql[i:i+2] == "--":
                in_line_comment = True
                current.append(sql[i:i+2])
                i += 2
                continue
            if not in_line_comment and i + 1 < n and sql[i:i+2] == "/*":
                in_block_comment = True
                current.append(sql[i:i+2])
                i += 2
                continue
            if in_block_comment and i + 1 < n and sql[i:i+2] == "*/":
                in_block_comment = False
                current.append(sql[i:i+2])
                i += 2
                continue

        if in_line_comment:
            current.append(char)
            if char == '\n':
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            current.append(char)
            i += 1
            continue

        if char == "\\" and in_single_quote and i + 1 < n:
            current.append(sql[i:i+2])
            i += 2
            continue

        if char == "'" and not (in_double_quote or in_backtick or dollar_tag):
            if in_single_quote:
                if i + 1 < n and sql[i+1] == "'":
                    current.append("''")
                    i += 2
                    continue
                else:
                    in_single_quote = False
            else:
                in_single_quote = True
            current.append(char)
            i += 1
            continue

        if char == '"' and not (in_single_quote or in_backtick or dollar_tag):
            in_double_quote = not in_double_quote
            current.append(char)
            i += 1
            continue

        if char == '`' and not (in_single_quote or in_double_quote or dollar_tag):
            in_backtick = not in_backtick
            current.append(char)
            i += 1
            continue

        if char == "$" and not (in_single_quote or in_double_quote or in_backtick):
            match = re.match(r"^\$[A-Za-z0-9_]*\$", sql[i:])
            if match:
                tag = match.group(0)
                if dollar_tag:
                    if dollar_tag == tag:
                        dollar_tag = None
                else:
                    dollar_tag = tag
                current.append(tag)
                i += len(tag)
                continue

        if char == ";" and not (in_single_quote or in_double_quote or in_backtick or dollar_tag):
            current.append(char)
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue

        current.append(char)
        i += 1

    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)
    return statements


def _extract_sql_object_name(statement: str) -> str | None:
    clean = re.sub(r"--.*", "", statement)
    clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.S)
    clean = clean.strip()
    pattern = r"(?:CREATE|ALTER)\s+(?:OR\s+REPLACE\s+)?(?:TEMP|TEMPORARY\s+)?(TABLE|FUNCTION|PROCEDURE|VIEW|TRIGGER)\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_.\"'`]+)"
    match = re.search(pattern, clean, re.I)
    if match:
        obj_type = match.group(1).upper()
        obj_name = match.group(2).strip("`\"'")
        return f"{obj_type} {obj_name}"
    return None


def _load_sql(path: Path) -> CanonicalAdapterResult:
    snapshot = _snapshot(path)
    content = path.read_text(encoding="utf-8", errors="ignore")
    statements = _split_sql_statements(content)
    docs = []
    for idx, stmt in enumerate(statements, 1):
        obj_name = _extract_sql_object_name(stmt)
        title = obj_name if obj_name else f"SQL {idx}"
        docs.append(_document(
            path, snapshot, idx, stmt, [title],
            role="code", content_type="code"
        ))
    return CanonicalAdapterResult(docs, [])


def _load_config(path: Path) -> CanonicalAdapterResult:
    snapshot = _snapshot(path)
    content = path.read_text(encoding="utf-8", errors="ignore")
    lines = content.splitlines()
    sections = []
    current_section_name = "global"
    current_section_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if current_section_lines:
                sections.append((current_section_name, "\n".join(current_section_lines)))
                current_section_lines = []
            current_section_name = stripped[1:-1].strip()
        else:
            current_section_lines.append(line)

    if current_section_lines or current_section_name != "global":
        sections.append((current_section_name, "\n".join(current_section_lines)))

    docs = []
    for idx, (sec_name, sec_content) in enumerate(sections, 1):
        if sec_content.strip() or sec_name != "global":
            docs.append(_document(
                path, snapshot, idx, sec_content.strip(), [sec_name],
                role="code", content_type="code"
            ))
    return CanonicalAdapterResult(docs, [])


def _load_xml(path: Path) -> CanonicalAdapterResult:
    snapshot = _snapshot(path)
    docs = []
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception as exc:
        return CanonicalAdapterResult(decisions=[make_decision(
            path, status="queued", reason_code="FORMAT_PARSE_FAILED",
            file_hash=snapshot, message=f"FORMAT_PARSE_FAILED: {type(exc).__name__}",
        )])

    root_title = root.attrib.get("name") or root.attrib.get("id") or root.tag
    for idx, child in enumerate(root, 1):
        child_title = child.attrib.get("name") or child.attrib.get("id") or f"{child.tag}_{idx}"
        section_path = [root_title, child_title]
        child_xml = ET.tostring(child, encoding="utf-8").decode("utf-8")
        docs.append(_document(
            path, snapshot, idx, child_xml.strip(), section_path,
            role="code", content_type="code"
        ))
    return CanonicalAdapterResult(docs, [])
