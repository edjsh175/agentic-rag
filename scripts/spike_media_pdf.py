#!/usr/bin/env python3
"""Offline DOCX media enumeration + PDF parse comparison (no Chroma writes)."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_knowledge.config import Config
from rag_knowledge.services.chunk_health_audit import count_docx_media

logger = logging.getLogger(__name__)

OUT_DIR = ROOT / "docs/3_待办清单/chunk-foundation-parallel-prep"
DEFAULT_DOCS = [
    "StampServer用户手册_Rocky9 .docx",
    "StampTools用户手册.docx",
    "StampWebRTC用户手册.docx",
]


def _find(watch_dir: Path, name: str) -> Path | None:
    direct = watch_dir / "word" / name
    if direct.exists():
        return direct
    matches = list(watch_dir.rglob(name))
    return matches[0] if matches else None


def enumerate_docx_media(path: Path, sample_hashes: int = 20) -> dict:
    media = []
    rels = []
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.startswith("word/media/") and not n.endswith("/")]
        for name in names:
            data = zf.read(name)
            media.append(
                {
                    "zip_path": name,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "suffix": Path(name).suffix.lower(),
                }
            )
        rel_name = "word/_rels/document.xml.rels"
        if rel_name in zf.namelist():
            root = ET.fromstring(zf.read(rel_name))
            for rel in root:
                target = rel.attrib.get("Target") or ""
                rid = rel.attrib.get("Id") or ""
                if "media/" in target.replace("\\", "/"):
                    rels.append({"rId": rid, "target": target})
    media.sort(key=lambda m: m["zip_path"])
    return {
        "source": path.name,
        "media_count": len(media),
        "audit_count_docx_media": count_docx_media(path),
        "relationship_media_count": len(rels),
        "sample": media[:sample_hashes],
        "note": "Spike enumerates ALL media; production loader still caps max_images=5.",
    }


def compare_pdf(path: Path, max_pages: int = 5) -> dict:
    from langchain_community.document_loaders import PyPDFLoader

    pypdf_pages = []
    try:
        docs = PyPDFLoader(str(path)).load()
        for doc in docs[:max_pages]:
            meta = doc.metadata or {}
            text = (doc.page_content or "").strip()
            pypdf_pages.append(
                {
                    "page": meta.get("page"),
                    "chars": len(text),
                    "preview": text[:160].replace("\n", " "),
                }
            )
    except Exception as exc:
        pypdf_pages = [{"error": str(exc)}]

    pdfminer_pages = []
    pdfminer_status = "skipped"
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextContainer

        pdfminer_status = "ok"
        for i, page_layout in enumerate(extract_pages(str(path))):
            if i >= max_pages:
                break
            texts = []
            for element in page_layout:
                if isinstance(element, LTTextContainer):
                    texts.append(element.get_text())
            joined = "".join(texts).strip()
            pdfminer_pages.append(
                {
                    "page": i,
                    "chars": len(joined),
                    "preview": joined[:160].replace("\n", " "),
                }
            )
    except Exception as exc:
        pdfminer_status = f"unavailable:{exc}"

    return {
        "source": path.name,
        "pypdf": pypdf_pages,
        "pdfminer": {"status": pdfminer_status, "pages": pdfminer_pages},
        "notes": (
            "Layout-aware title/table recovery is NOT implemented here. "
            "This spike only contrasts page text volume/previews for Round 0D planning."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--ocr-sample", action="store_true", help="Try optional OCR on first few images if pytesseract available")
    args = parser.parse_args(argv)

    cfg = Config()
    watch_dir = args.watch_dir or Path(cfg.watch_dir)
    media_reports = []
    for name in DEFAULT_DOCS:
        path = _find(watch_dir, name)
        if not path:
            logger.warning("missing %s", name)
            continue
        media_reports.append(enumerate_docx_media(path))

    ocr_note = "OCR not run (optional --ocr-sample)."
    ocr_samples = []
    if args.ocr_sample and media_reports:
        try:
            import io

            import pytesseract
            from PIL import Image

            path = _find(watch_dir, media_reports[0]["source"])
            if path:
                with zipfile.ZipFile(path) as zf:
                    names = [n for n in zf.namelist() if n.startswith("word/media/") and n.lower().endswith((".png", ".jpg", ".jpeg"))]
                    for name in names[:3]:
                        img = Image.open(io.BytesIO(zf.read(name)))
                        text = pytesseract.image_to_string(img, lang="chi_sim+eng")
                        ocr_samples.append({"zip_path": name, "ocr_preview": (text or "").strip()[:200]})
                ocr_note = "pytesseract sample OCR completed for up to 3 images."
        except Exception as exc:
            ocr_note = f"OCR unavailable: {exc}"

    pdf_reports = []
    pdf_candidates = list(watch_dir.rglob("*.pdf"))[:2]
    for pdf in pdf_candidates:
        pdf_reports.append(compare_pdf(pdf))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "watch_directory": str(watch_dir),
        "docx_media": media_reports,
        "ocr": {"note": ocr_note, "samples": ocr_samples},
        "pdf_compare": pdf_reports,
        "non_claims": [
            "Does not claim image coverage >=90%.",
            "Does not change loader max_images=5.",
            "Does not declare PDF section_path acceptance.",
        ],
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "round0d_media_pdf_spike.json"
    md_path = args.out_dir / "round0d_media_pdf_spike.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Round 0D Media / PDF Spike",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- OCR: {ocr_note}",
        "",
        "## DOCX media counts",
        "",
    ]
    for row in media_reports:
        lines.append(
            f"- **{row['source']}**: media={row['media_count']} "
            f"(audit helper={row['audit_count_docx_media']}, rels={row['relationship_media_count']})"
        )
    lines.extend(["", "## PDF compare", ""])
    if not pdf_reports:
        lines.append("_no pdf under watch_directory_")
    for row in pdf_reports:
        lines.append(f"### {row['source']}")
        lines.append(f"- pdfminer: {row['pdfminer']['status']}")
        for page in row.get("pypdf") or []:
            if "error" in page:
                lines.append(f"- pypdf error: {page['error']}")
            else:
                lines.append(f"- pypdf page={page.get('page')} chars={page.get('chars')}")
    lines.extend(["", "## Non-claims", ""])
    for n in report["non_claims"]:
        lines.append(f"- {n}")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    # also write design companion pointer
    design = args.out_dir / "round0d_media_pdf_spike.md"
    logger.info("wrote %s", design)
    print(json.dumps({"docx_media": [{"source": r["source"], "media_count": r["media_count"]} for r in media_reports], "pdfs": len(pdf_reports)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
