"""Build a corpus-wide document-profile inventory without touching Chroma.

The command is dry-run by default: it writes an audit report and a candidate
mapping. A production mapping requires both --write-map and --controlled-0g.
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import Counter
from pathlib import Path


PHASE1_EXTENSIONS = {".docx", ".md", ".txt", ".xlsx"}
PHASE2_EXTENSIONS = {
    ".pdf", ".pptx", ".html", ".htm", ".sql", ".cnf", ".conf", ".cfg", ".ini", ".xml",
}
MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
    ".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv",
}
DEPENDENCY_EXTENSIONS = {".jar", ".css", ".js", ".map", ".dll", ".exe"}
ARCHIVE_EXTENSIONS = {".rar", ".zip", ".7z", ".tar", ".gz"}
HTTP_ENDPOINT_RE = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/\S+", re.I)
NUMBERED_RE = re.compile(r"(?m)^\s*(?:\d+(?:\.\d+)*[.、）)]|[-*+]\s+)\s*\S+")


def support_for_suffix(suffix: str) -> tuple[str, str]:
    value = suffix.lower()
    if value in PHASE1_EXTENSIONS:
        return "phase1_supported", ""
    if value in PHASE2_EXTENSIONS:
        return "phase2_supported", ""
    if value == ".doc":
        return "manual_queue", "LEGACY_DOC_REQUIRES_CONVERSION"
    if value == ".xls":
        return "manual_queue", "LEGACY_SPREADSHEET_REQUIRES_CONVERSION"
    if value in MEDIA_EXTENSIONS:
        return "manual_queue", "MEDIA_PROCESSING_DEFERRED"
    if value in DEPENDENCY_EXTENSIONS:
        return "excluded", "DEPENDENCY_ASSET"
    if value in ARCHIVE_EXTENSIONS:
        return "excluded", "ARCHIVE_ASSET"
    return "excluded", "UNSUPPORTED_EXTENSION"


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, OSError):
            continue
    return ""


def _docx_features(path: Path) -> tuple[str, dict]:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    except (OSError, KeyError, zipfile.BadZipFile):
        return "", {"parse_status": "invalid_or_unreadable"}
    text = " ".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, re.S))
    return text, {
        "heading_style_count": len(re.findall(r'w:pStyle[^>]+w:val="(?:Heading|标题)', xml, re.I)),
        "table_count": xml.count("<w:tbl>"),
        "numbering_count": xml.count("<w:numPr>"),
    }


def _container_features(path: Path) -> tuple[str, dict]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _docx_features(path)
    if suffix == ".xlsx":
        try:
            import openpyxl
            workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
            features = {"sheet_count": len(workbook.sheetnames)}
            workbook.close()
            return "", features
        except Exception:
            return "", {"parse_status": "invalid_or_unreadable"}
    if suffix == ".pdf":
        try:
            import fitz
            doc = fitz.open(path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            return text, {}
        except Exception:
            return "", {"parse_status": "invalid_or_unreadable"}
    if suffix in {".md", ".txt", ".html", ".htm", ".sql", ".cnf", ".conf", ".cfg", ".ini", ".xml"}:
        return _read_text(path), {}
    return "", {}


def _recommend_profile(path: Path, text: str, features: dict, status: str) -> str:
    if status not in {"phase1_supported", "phase2_supported"}:
        return ""
    suffix = path.suffix.lower()
    name = path.name.lower()
    evidence = f"{name}\n{text[:20000]}"
    if HTTP_ENDPOINT_RE.search(evidence) or re.search(r"(?:api|接口)", name, re.I):
        return "api_doc"
    if re.search(r"(?:问题(?:收集|清单)|功能清单|新功能|issue|bug)", name, re.I):
        return "record_list"
    if re.search(r"(?:部署|安装|流程|修改|sop|minio|https|vmware|麒麟)", name, re.I):
        return "procedure"
    if suffix == ".xlsx" or suffix in {".cnf", ".conf", ".cfg", ".ini"} or re.search(r"(?:表结构|数据字典|config\.ini|配置字典)", name, re.I):
        return "table_doc"
    if re.search(r"手册", name, re.I):
        return "technical_manual"
    return "section_based"


def build_inventory(root: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix().lower()):
        suffix = path.suffix.lower()
        status, reason = support_for_suffix(suffix)
        text, features = _container_features(path)
        features = {
            **features,
            "http_endpoint_count": len(HTTP_ENDPOINT_RE.findall(text)),
            "numbered_item_count": len(NUMBERED_RE.findall(text)),
            "text_chars_sampled": len(text),
        }
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "format": suffix or "[no_extension]",
            "structure_features": features,
            "recommended_profile": _recommend_profile(path, text, features, status),
            "support_status": status,
            "reason_code": reason,
        })
    return rows


def write_report(rows: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "document-profile-inventory.json").write_text(
        json.dumps({"files": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    mapping = {
        row["path"]: row["recommended_profile"]
        for row in rows if row["recommended_profile"]
    }
    (output_dir / "document_profile_map.candidate.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    statuses = Counter(row["support_status"] for row in rows)
    profiles = Counter(row["recommended_profile"] for row in rows if row["recommended_profile"])
    lines = [
        "# Document Profile 全目录覆盖报告",
        "",
        "本报告为 dry-run 候选，不会被生产 Scanner 自动消费。",
        "",
        f"- 文件总数：{len(rows)}",
        f"- 支持状态：{dict(statuses)}",
        f"- 建议 Profile：{dict(profiles)}",
        "",
        "| 路径 | 格式 | 建议 Profile | 支持状态 | 排除原因 |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        escaped_path = row["path"].replace("|", "\\|")
        lines.append(
            f"| {escaped_path} | {row['format']} | {row['recommended_profile']} | "
            f"{row['support_status']} | {row['reason_code']} |"
        )
    (output_dir / "document-profile-inventory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("watch_directory"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/3_待办清单/切块基石治理/已完成-第0C轮-切块合并与隔离验证/文档画像盘点"),
    )
    parser.add_argument("--write-map", action="store_true")
    parser.add_argument("--controlled-0g", action="store_true")
    args = parser.parse_args()

    rows = build_inventory(args.root)
    write_report(rows, args.output_dir)
    if args.write_map:
        if not args.controlled_0g:
            parser.error("--write-map requires --controlled-0g")
        mapping = {row["path"]: row["recommended_profile"] for row in rows if row["recommended_profile"]}
        Path("data/document_profile_map.json").write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
