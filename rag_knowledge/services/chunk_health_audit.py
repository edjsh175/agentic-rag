"""Readonly Chunk health audit for Round 0A baseline governance.

Does not modify Chroma, file_index, or the relational DB.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag_knowledge.config import Config
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.knowledge_base_consistency import KnowledgeBaseConsistencyService
from rag_knowledge.services.loader import FileLoader
from rag_knowledge.services.unstructured_loader import SUPPORTED_EXTS, UnstructuredChapterLoader

# Suspicious section titles that look like commands/paths/config/ports.
COMMAND_LIKE_RE = re.compile(
    r"^(?:umount|mount|reboot|systemctl|vim|vi|nano|chmod|chown|mkdir|cd|cp|mv|rm|cat|echo|export|"
    r"pm2|docker|kubectl|curl|wget|pip|npm|yarn|apt|yum|dnf)(?:\s+|$)",
    re.I,
)
PATH_LIKE_RE = re.compile(r"^(?:[A-Za-z]:)?(?:/|\\)[\w./\\:-]+$")
CONFIG_LIKE_RE = re.compile(r'^[A-Za-z_][\w.-]*\s*[=:]\s*.+$')
PORT_LIKE_RE = re.compile(r"^(?:\d{2,5}\s*[:：]\s*)?\d{2,5}$|^\d{2,5}\s*[:：]")
KEY_VALUE_PORT_RE = re.compile(
    r"(?i)(?:port|端口|listening-port|HttpsPort|HttpPort)\s*[\"']?\s*[:=]\s*[\"']?(\d{2,5})"
)
BARE_PORT_RE = re.compile(r"(?i)\b(?:tls|ssl|udp|tcp)?-?listening-port\s*=\s*(\d{2,5})")
CONFIG_KEY_RE = re.compile(
    r"(?i)\b([A-Za-z_][\w.-]*(?:Port|Path|Dir|Url|Host)|tls-listening-port|listening-port|"
    r"HttpsPort|HttpPort)\s*[\"']?\s*[:=]\s*[\"']?([^\s,\"'}]+)"
)


@dataclass
class FilterDecision:
    rejected: bool
    reason_code: str
    detail: str = ""


def diagnose_low_information(text: str) -> FilterDecision:
    """Mirror FileLoader._is_low_information with audit-only reason codes."""
    stripped = text.strip()
    if not stripped:
        return FilterDecision(True, "empty")

    compact = re.sub(r"[\W_]+", "", stripped, flags=re.UNICODE)
    if len(compact) < 8:
        return FilterDecision(True, "compact_too_short", f"compact_len={len(compact)}")

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if not lines:
        return FilterDecision(True, "empty_lines")

    from rag_knowledge.services import loader as loader_mod

    toc_hits = sum(1 for line in lines if loader_mod.TOC_LINE_RE.match(line))
    if len(lines) >= 2 and toc_hits / len(lines) >= 0.6:
        return FilterDecision(True, "toc_majority", f"toc_ratio={toc_hits / len(lines):.2f}")

    url_hits = sum(1 for line in lines if loader_mod.URL_LINE_RE.match(line))
    if url_hits == len(lines):
        return FilterDecision(True, "url_only")

    if len(lines) == 1 and len(compact) < 16 and not loader_mod.VERSION_HINT_RE.search(stripped):
        return FilterDecision(True, "single_short_line", f"compact_len={len(compact)}")

    non_space_chars = [char for char in stripped if not char.isspace()]
    if len(non_space_chars) >= 24:
        unexpected_script_count = sum(
            1 for char in non_space_chars if FileLoader._is_unexpected_script_char(char)
        )
        if unexpected_script_count:
            return FilterDecision(
                True,
                "unexpected_script",
                f"count={unexpected_script_count}",
            )
        readable = sum(1 for char in non_space_chars if FileLoader._is_readable_text_char(char))
        if readable / len(non_space_chars) < 0.35:
            return FilterDecision(
                True,
                "low_readable_ratio",
                f"ratio={readable / len(non_space_chars):.2f}",
            )
        longest_unreadable_run = 0
        current_unreadable_run = 0
        for char in stripped:
            if char.isspace() or FileLoader._is_readable_text_char(char):
                current_unreadable_run = 0
                continue
            current_unreadable_run += 1
            longest_unreadable_run = max(longest_unreadable_run, current_unreadable_run)
        if longest_unreadable_run >= 12:
            return FilterDecision(
                True,
                "long_unreadable_run",
                f"run={longest_unreadable_run}",
            )

    return FilterDecision(False, "keep")


def percentile(sorted_values: list[int], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * p
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(sorted_values[low])
    weight = rank - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def is_suspicious_heading(text: str) -> tuple[bool, str]:
    title = (text or "").strip()
    if not title:
        return False, ""
    leaf = title.split(">")[-1].strip()
    if COMMAND_LIKE_RE.match(leaf):
        return True, "command_like"
    if PATH_LIKE_RE.match(leaf) or leaf.startswith("/etc/") or leaf.startswith("/boot"):
        return True, "path_like"
    if CONFIG_LIKE_RE.match(leaf) and ("=" in leaf or ":" in leaf):
        return True, "config_like"
    if PORT_LIKE_RE.match(leaf) or re.search(r"端口\s*[:：]?\s*\d+", leaf):
        return True, "port_like"
    if re.fullmatch(r"enabled=\d+", leaf, re.I):
        return True, "config_like"
    return False, ""


def count_docx_media(path: Path) -> int:
    if path.suffix.lower() != ".docx" or not path.exists():
        return 0
    try:
        with zipfile.ZipFile(path) as zf:
            return sum(
                1
                for name in zf.namelist()
                if name.startswith("word/media/") and not name.endswith("/")
            )
    except Exception:
        return 0


def sha256_file(path: Path, limit: int | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        remaining = limit
        while True:
            chunk_size = 1024 * 1024 if remaining is None else min(1024 * 1024, remaining)
            if chunk_size <= 0:
                break
            data = fh.read(chunk_size)
            if not data:
                break
            digest.update(data)
            if remaining is not None:
                remaining -= len(data)
    return digest.hexdigest()


def resolve_source_path(watch_dir: Path, relative_or_absolute: str) -> Path | None:
    raw = Path(relative_or_absolute)
    if raw.is_absolute() and raw.exists():
        return raw
    candidate = watch_dir / relative_or_absolute
    if candidate.exists():
        return candidate
    # Fall back to basename search under watch_dir
    name = Path(relative_or_absolute).name
    if not name:
        return None
    hits = list(watch_dir.rglob(name))
    return hits[0] if hits else None


class ChunkHealthAuditor:
    """Build a readonly Chunk health report from Chroma + optional source reparse."""

    def __init__(
        self,
        *,
        cfg: Config | None = None,
        store: VectorStore | None = None,
        chunk_snapshot: dict[str, Any] | None = None,
        file_index: dict[str, Any] | None = None,
    ):
        self._cfg = cfg or Config()
        self._store = store
        self._chunk_snapshot = chunk_snapshot
        self._file_index = file_index

    def run(
        self,
        *,
        reparse_sources: bool = True,
        max_filter_samples: int = 80,
        max_heading_samples: int = 80,
        annotation_sample_size: int = 160,
    ) -> dict[str, Any]:
        snapshot = self._load_snapshot()
        index = self._load_index()
        ids = [str(item) for item in (snapshot.get("ids") or [])]
        documents = list(snapshot.get("documents") or [])
        metadatas = [dict(meta or {}) for meta in (snapshot.get("metadatas") or [])]

        rows = []
        for chunk_id, content, meta in zip(ids, documents, metadatas):
            text = content or ""
            rows.append(
                {
                    "chunk_id": chunk_id,
                    "content": text,
                    "length": len(text),
                    "source": str(meta.get("source") or ""),
                    "section_path": str(meta.get("section_path") or ""),
                    "section_title": str(meta.get("section_title") or ""),
                    "section_index": meta.get("section_index"),
                    "chunk_in_section": meta.get("chunk_in_section"),
                    "element_order": meta.get("element_order"),
                    "content_type": str(meta.get("content_type") or "text"),
                    "chunking_method": str(meta.get("chunking_method") or ""),
                    "review_status": str(meta.get("review_status") or ""),
                    "kb_name": str(meta.get("kb_name") or ""),
                    "doc_category": str(meta.get("doc_category") or ""),
                }
            )

        overview = self._build_overview(rows)
        by_source = self._build_by_source(rows)
        heading_issues = self._find_suspicious_headings(rows, max_heading_samples)
        order_issues = self._find_order_issues(rows)
        conflict_candidates = self._find_conflict_candidates(rows)
        consistency = KnowledgeBaseConsistencyService(
            cfg=self._cfg,
            index_data=index,
            chunk_snapshot=snapshot,
        ).audit()

        reparse_report: dict[str, Any] = {"enabled": False, "documents": []}
        if reparse_sources:
            reparse_report = self._reparse_indexed_sources(
                index,
                max_filter_samples=max_filter_samples,
            )

        media_report = self._build_media_report(index)
        annotation_candidates = self._build_annotation_candidates(
            rows,
            heading_issues,
            reparse_report,
            sample_size=annotation_sample_size,
        )

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "readonly": True,
            "code_version": self._code_fingerprint(),
            "config_snapshot": {
                "chroma_dir": str(self._cfg.chroma_dir),
                "collection_name": self._cfg.collection_name,
                "data_dir": str(self._cfg.data_dir),
                "watch_dir": str(self._cfg.watch_dir),
                "chunk_size": self._cfg.chunk_size,
                "chunk_overlap": self._cfg.chunk_overlap,
                "semantic_chunking_enabled": self._cfg.semantic_chunking_enabled,
                "extract_embedded_images": self._cfg.extract_embedded_images,
                "use_unstructured": self._cfg.use_unstructured,
            },
            "overview": overview,
            "by_source": by_source,
            "suspicious_headings": heading_issues,
            "order_issues": order_issues,
            "conflict_candidates": conflict_candidates,
            "consistency": consistency.get("summary") or {},
            "media": media_report,
            "reparse": reparse_report,
            "annotation_candidates": annotation_candidates,
        }
        report["corpus_snapshot_hash"] = self._corpus_hash(report)
        return report

    def write_reports(self, report: dict[str, Any], output_dir: Path) -> dict[str, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "chunk_health_audit.json"
        md_path = output_dir / "chunk_health_audit.md"
        annotation_path = output_dir / "heading_body_garbage_candidates.json"
        filter_path = output_dir / "filter_reject_samples.json"

        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(self.render_markdown(report), encoding="utf-8")
        annotation_path.write_text(
            json.dumps(report.get("annotation_candidates") or [], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rejects = []
        for doc in (report.get("reparse") or {}).get("documents") or []:
            rejects.extend(doc.get("filter_samples") or [])
        filter_path.write_text(json.dumps(rejects, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "json": json_path,
            "markdown": md_path,
            "annotation_candidates": annotation_path,
            "filter_reject_samples": filter_path,
        }

    @staticmethod
    def render_markdown(report: dict[str, Any]) -> str:
        overview = report.get("overview") or {}
        report_label = str(report.get("report_label") or "Round 0A")
        lines = [
            f"# Chunk 健康审计报告（{report_label}）",
            "",
            f"- 生成时间：`{report.get('generated_at')}`",
            f"- 只读：`{report.get('readonly')}`",
            f"- 语料快照哈希：`{report.get('corpus_snapshot_hash')}`",
            f"- 代码指纹：`{report.get('code_version')}`",
            "",
            "## 总览",
            "",
            f"| 指标 | 值 |",
            f"|---|---:|",
            f"| Chunk 总量 | {overview.get('total_chunks', 0)} |",
            f"| 长度中位数 | {overview.get('length_p50', 0)} |",
            f"| `<100` 字符 | {overview.get('pct_lt_100', 0)}% |",
            f"| `<200` 字符 | {overview.get('pct_lt_200', 0)}% |",
            f"| `300-800` 字符 | {overview.get('pct_300_800', 0)}% |",
            f"| `>1200` 字符 | {overview.get('pct_gt_1200', 0)}% |",
            f"| 空 section_path | {overview.get('empty_section_path_pct', 0)}% |",
            f"| approved | {overview.get('approved_chunks', 0)} / {overview.get('total_chunks', 0)} |",
            "",
            "## 按文档分布",
            "",
            "| 文档 | Chunk | 中位数 | <100% | <200% | 空 section_path% |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for item in report.get("by_source") or []:
            lines.append(
                f"| `{item.get('source')}` | {item.get('chunk_count')} | {item.get('length_p50')} | "
                f"{item.get('pct_lt_100')} | {item.get('pct_lt_200')} | {item.get('empty_section_path_pct')} |"
            )

        lines.extend(["", "## 疑似错误标题（样本）", ""])
        heading_samples = (report.get("suspicious_headings") or {}).get("samples") or []
        if not heading_samples:
            lines.append("无。")
        else:
            for sample in heading_samples[:30]:
                lines.append(
                    f"- `{sample.get('reason')}` | `{sample.get('source')}` | "
                    f"`{sample.get('section_path') or sample.get('section_title')}`"
                )

        reparse = report.get("reparse") or {}
        lines.extend(["", "## 重解析过滤对比", ""])
        if not reparse.get("enabled"):
            lines.append("未启用源文件重解析。")
        else:
            lines.append(
                f"解析文档数：{reparse.get('document_count', 0)}；"
                f"过滤前块数：{reparse.get('pre_filter_chunks', 0)}；"
                f"过滤后块数：{reparse.get('post_filter_chunks', 0)}；"
                f"过滤比例：{reparse.get('filter_rate_pct', 0)}%"
            )
            lines.append("")
            lines.append("| 文档 | 过滤前 | 过滤后 | 过滤数 | 比例 | 主因 |")
            lines.append("|---|---:|---:|---:|---:|---|")
            for doc in reparse.get("documents") or []:
                top_reason = ""
                reasons = doc.get("reason_counts") or {}
                if reasons:
                    top_reason = sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                lines.append(
                    f"| `{doc.get('source')}` | {doc.get('pre_filter')} | {doc.get('post_filter')} | "
                    f"{doc.get('filtered')} | {doc.get('filter_rate_pct')}% | `{top_reason}` |"
                )

        consistency = report.get("consistency") or {}
        lines.extend(
            [
                "",
                "## 一致性",
                "",
                f"- consistent: `{consistency.get('consistent')}`",
                f"- index_chunks: `{consistency.get('index_chunk_total')}`",
                f"- chroma_chunks: `{consistency.get('chroma_chunk_total')}`",
                f"- missing_indexed: `{consistency.get('missing_indexed_chunk_total')}`",
                f"- unexpected_chroma: `{consistency.get('unexpected_chroma_chunk_total')}`",
                "",
                "## 媒体覆盖",
                "",
            ]
        )
        media = report.get("media") or {}
        lines.append(f"- DOCX 媒体合计：`{media.get('total_media_files', 0)}`")
        lines.append(f"- extract_embedded_images：`{media.get('extract_embedded_images')}`")
        for item in media.get("documents") or []:
            lines.append(
                f"- `{item.get('source')}`: media={item.get('media_count')} "
                f"path_exists={item.get('path_exists')}"
            )

        conflicts = report.get("conflict_candidates") or []
        lines.extend(["", "## 同键多值冲突候选", ""])
        if not conflicts:
            lines.append("无。")
        else:
            for item in conflicts[:20]:
                lines.append(
                    f"- `{item.get('key')}` values={item.get('values')} sources={item.get('sources')}"
                )

        return "\n".join(lines) + "\n"

    def _load_snapshot(self) -> dict[str, Any]:
        if self._chunk_snapshot is not None:
            return self._chunk_snapshot
        store = self._store or VectorStore()
        return store.get_chunk_stats_source()

    def _load_index(self) -> dict[str, Any]:
        if self._file_index is not None:
            return self._file_index
        path = Path(self._cfg.data_dir) / "file_index.json"
        if not path.exists():
            return {"files": {}}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"files": {}}

    def _build_overview(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        lengths = sorted(int(row["length"]) for row in rows)
        total = len(rows)
        empty_section = sum(1 for row in rows if not str(row.get("section_path") or "").strip())
        approved = sum(1 for row in rows if str(row.get("review_status") or "").lower() == "approved")
        content_types = Counter(str(row.get("content_type") or "text") for row in rows)

        def pct(count: int) -> float:
            return round(100.0 * count / total, 2) if total else 0.0

        return {
            "total_chunks": total,
            "approved_chunks": approved,
            "length_min": lengths[0] if lengths else 0,
            "length_max": lengths[-1] if lengths else 0,
            "length_p50": round(percentile(lengths, 0.5), 1),
            "length_p90": round(percentile(lengths, 0.9), 1),
            "pct_lt_100": pct(sum(1 for value in lengths if value < 100)),
            "pct_lt_200": pct(sum(1 for value in lengths if value < 200)),
            "pct_300_800": pct(sum(1 for value in lengths if 300 <= value <= 800)),
            "pct_gt_1200": pct(sum(1 for value in lengths if value > 1200)),
            "empty_section_path_count": empty_section,
            "empty_section_path_pct": pct(empty_section),
            "content_type_counts": dict(content_types),
        }

    def _build_by_source(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row.get("source") or "(unknown)")].append(row)
        result = []
        for source, items in grouped.items():
            lengths = sorted(int(item["length"]) for item in items)
            total = len(items)
            empty_section = sum(1 for item in items if not str(item.get("section_path") or "").strip())

            def pct(count: int) -> float:
                return round(100.0 * count / total, 2) if total else 0.0

            result.append(
                {
                    "source": source,
                    "chunk_count": total,
                    "length_p50": round(percentile(lengths, 0.5), 1),
                    "pct_lt_100": pct(sum(1 for value in lengths if value < 100)),
                    "pct_lt_200": pct(sum(1 for value in lengths if value < 200)),
                    "empty_section_path_pct": pct(empty_section),
                    "content_type_counts": dict(
                        Counter(str(item.get("content_type") or "text") for item in items)
                    ),
                }
            )
        return sorted(result, key=lambda item: (-item["chunk_count"], item["source"]))

    def _find_suspicious_headings(
        self,
        rows: list[dict[str, Any]],
        max_samples: int,
    ) -> dict[str, Any]:
        samples = []
        seen = set()
        reason_counts: Counter[str] = Counter()
        for row in rows:
            for field in ("section_title", "section_path"):
                value = str(row.get(field) or "").strip()
                if not value:
                    continue
                leaf = value.split(">")[-1].strip()
                suspicious, reason = is_suspicious_heading(leaf)
                if not suspicious:
                    continue
                key = (row.get("source"), leaf, reason)
                if key in seen:
                    continue
                seen.add(key)
                reason_counts[reason] += 1
                if len(samples) < max_samples:
                    samples.append(
                        {
                            "source": row.get("source"),
                            "section_path": row.get("section_path"),
                            "section_title": row.get("section_title"),
                            "leaf": leaf,
                            "reason": reason,
                            "chunk_id": row.get("chunk_id"),
                            "content_preview": (row.get("content") or "")[:160],
                        }
                    )
        return {
            "count": sum(reason_counts.values()),
            "reason_counts": dict(reason_counts),
            "samples": samples,
        }

    def _find_order_issues(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        missing_keys = 0
        duplicate_keys = 0
        broken_chains = 0
        samples: list[dict[str, Any]] = []
        by_source_section: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            section_index = row.get("section_index")
            chunk_in_section = row.get("chunk_in_section")
            if section_index in (None, "") or chunk_in_section in (None, ""):
                missing_keys += 1
                if len(samples) < 30:
                    samples.append(
                        {
                            "issue": "missing_order_key",
                            "source": row.get("source"),
                            "chunk_id": row.get("chunk_id"),
                            "section_path": row.get("section_path"),
                        }
                    )
            key = (str(row.get("source") or ""), str(row.get("section_path") or ""))
            by_source_section[key].append(row)

        for (source, section_path), items in by_source_section.items():
            key_counter = Counter(
                (item.get("section_index"), item.get("chunk_in_section")) for item in items
            )
            for key, count in key_counter.items():
                if key[0] in (None, "") or key[1] in (None, ""):
                    continue
                if count > 1:
                    duplicate_keys += 1
                    if len(samples) < 40:
                        samples.append(
                            {
                                "issue": "duplicate_order_key",
                                "source": source,
                                "section_path": section_path,
                                "section_index": key[0],
                                "chunk_in_section": key[1],
                                "count": count,
                            }
                        )
            orders = []
            for item in items:
                try:
                    orders.append(int(item.get("chunk_in_section")))
                except (TypeError, ValueError):
                    continue
            if orders:
                expected = list(range(min(orders), max(orders) + 1))
                if sorted(set(orders)) != expected:
                    broken_chains += 1
                    if len(samples) < 50:
                        samples.append(
                            {
                                "issue": "broken_chunk_in_section_chain",
                                "source": source,
                                "section_path": section_path,
                                "observed": sorted(set(orders))[:20],
                            }
                        )
        return {
            "missing_order_key_chunks": missing_keys,
            "duplicate_order_key_groups": duplicate_keys,
            "broken_section_chains": broken_chains,
            "samples": samples,
        }

    def _find_conflict_candidates(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        values_by_key: dict[str, set[str]] = defaultdict(set)
        sources_by_key: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            text = row.get("content") or ""
            for match in CONFIG_KEY_RE.finditer(text):
                key = match.group(1)
                value = match.group(2).strip().strip(",;'\"")
                values_by_key[key].add(value)
                sources_by_key[key].add(str(row.get("source") or ""))
            for match in KEY_VALUE_PORT_RE.finditer(text):
                key = "port_mention"
                value = match.group(1)
                values_by_key[key].add(value)
                sources_by_key[key].add(str(row.get("source") or ""))
            for match in BARE_PORT_RE.finditer(text):
                key = "tls-listening-port"
                value = match.group(1)
                values_by_key[key].add(value)
                sources_by_key[key].add(str(row.get("source") or ""))
        conflicts = []
        for key, values in values_by_key.items():
            if len(values) < 2:
                continue
            conflicts.append(
                {
                    "key": key,
                    "values": sorted(values)[:10],
                    "sources": sorted(sources_by_key[key])[:10],
                }
            )
        return sorted(conflicts, key=lambda item: (-len(item["values"]), item["key"]))[:50]

    def _build_media_report(self, index: dict[str, Any]) -> dict[str, Any]:
        documents = []
        total_media = 0
        for entry in (index.get("files") or {}).values():
            relative = str(entry.get("file_path") or "")
            source = str(entry.get("file_name") or Path(relative).name)
            path = resolve_source_path(Path(self._cfg.watch_dir), relative)
            media_count = count_docx_media(path) if path else 0
            total_media += media_count
            documents.append(
                {
                    "source": source,
                    "relative_path": relative,
                    "resolved_path": str(path) if path else "",
                    "path_exists": bool(path and path.exists()),
                    "media_count": media_count,
                    "indexed_chunks": len(entry.get("chunk_ids") or []),
                }
            )
        return {
            "extract_embedded_images": self._cfg.extract_embedded_images,
            "total_media_files": total_media,
            "documents": sorted(documents, key=lambda item: (-item["media_count"], item["source"])),
        }

    def _reparse_indexed_sources(
        self,
        index: dict[str, Any],
        *,
        max_filter_samples: int,
    ) -> dict[str, Any]:
        documents = []
        pre_total = 0
        post_total = 0
        reason_total: Counter[str] = Counter()
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self._cfg.chunk_size,
            chunk_overlap=self._cfg.chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        )
        chapter_loader = UnstructuredChapterLoader(
            chunk_size=self._cfg.chunk_size,
            chunk_overlap=self._cfg.chunk_overlap,
            strategy=self._cfg.unstructured_strategy,
        )
        pipeline_loader = object.__new__(FileLoader)
        pipeline_loader._chunk_size = self._cfg.chunk_size
        pipeline_loader._chunk_overlap = self._cfg.chunk_overlap
        pipeline_loader._semantic_chunking_enabled = False
        pipeline_loader._semantic_chunker = None
        pipeline_loader._splitter = splitter

        for entry in (index.get("files") or {}).values():
            relative = str(entry.get("file_path") or "")
            source_name = str(entry.get("file_name") or Path(relative).name)
            path = resolve_source_path(Path(self._cfg.watch_dir), relative)
            if path is None or not path.exists():
                documents.append(
                    {
                        "source": source_name,
                        "relative_path": relative,
                        "path_exists": False,
                        "skipped": True,
                        "skip_reason": "source_missing",
                    }
                )
                continue
            suffix = path.suffix.lower()
            if suffix not in SUPPORTED_EXTS and suffix != ".pdf":
                documents.append(
                    {
                        "source": source_name,
                        "relative_path": relative,
                        "path_exists": True,
                        "skipped": True,
                        "skip_reason": f"unsupported_suffix:{suffix}",
                    }
                )
                continue
            try:
                if suffix in SUPPORTED_EXTS:
                    raw_chunks = chapter_loader.load(str(path))
                else:
                    # PDF: approximate pre-filter units from current loader legacy path without writing.
                    from langchain_community.document_loaders import PyPDFLoader

                    raw_chunks = PyPDFLoader(str(path)).load()
                    for doc in raw_chunks:
                        doc.metadata["source"] = path.name
                        doc.metadata.setdefault("content_type", "text")
                expanded = pipeline_loader._split_documents_preserving_blocks(raw_chunks)
            except Exception as exc:
                documents.append(
                    {
                        "source": source_name,
                        "relative_path": relative,
                        "path_exists": True,
                        "skipped": True,
                        "skip_reason": f"parse_error:{exc}",
                    }
                )
                continue

            final_chunks = pipeline_loader._post_process_chunks(expanded)
            kept_chunk_ids = {id(chunk) for chunk in final_chunks}
            kept = len(final_chunks)
            filtered = len(expanded) - kept
            reason_counts: Counter[str] = Counter()
            filter_samples: list[dict[str, Any]] = []
            for doc in expanded:
                if id(doc) in kept_chunk_ids:
                    continue
                content_type = str(doc.metadata.get("content_type") or "text")
                if content_type in ("code", "table", "heading"):
                    reason_counts["empty"] += 1
                    reason_total["empty"] += 1
                    continue
                cleaned = FileLoader._sanitize_text(doc.page_content or "")
                if FileLoader._is_toc_marker_chunk(cleaned):
                    reason_code = "toc_marker"
                    detail = "section_prefixed_toc_marker"
                else:
                    decision = diagnose_low_information(cleaned)
                    reason_code = decision.reason_code if decision.rejected else "empty"
                    detail = decision.detail
                reason_counts[reason_code] += 1
                reason_total[reason_code] += 1
                if len(filter_samples) < max_filter_samples:
                    filter_samples.append(
                        {
                            "source": source_name,
                            "section_path": doc.metadata.get("section_path") or "",
                            "section_title": doc.metadata.get("section_title") or "",
                            "reason_code": reason_code,
                            "detail": detail,
                            "content_preview": cleaned[:300],
                            "content_length": len(cleaned),
                        }
                    )

            pre = len(expanded)
            post = kept
            pre_total += pre
            post_total += post
            filter_rate = round(100.0 * filtered / pre, 2) if pre else 0.0
            documents.append(
                {
                    "source": source_name,
                    "relative_path": relative,
                    "resolved_path": str(path),
                    "path_exists": True,
                    "skipped": False,
                    "pre_filter": pre,
                    "post_filter": post,
                    "filtered": filtered,
                    "filter_rate_pct": filter_rate,
                    "reason_counts": dict(reason_counts),
                    "filter_samples": filter_samples,
                    "indexed_chunks": len(entry.get("chunk_ids") or []),
                }
            )

        filter_rate_total = (
            round(100.0 * (pre_total - post_total) / pre_total, 2) if pre_total else 0.0
        )
        return {
            "enabled": True,
            "note": "Reparse uses production structure finalization and filtering with fixed-size split (no semantic embedding).",
            "document_count": len([d for d in documents if not d.get("skipped")]),
            "pre_filter_chunks": pre_total,
            "post_filter_chunks": post_total,
            "filter_rate_pct": filter_rate_total,
            "reason_counts": dict(reason_total),
            "documents": documents,
        }

    def _build_annotation_candidates(
        self,
        rows: list[dict[str, Any]],
        heading_issues: dict[str, Any],
        reparse_report: dict[str, Any],
        *,
        sample_size: int,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add_item(item: dict[str, Any]) -> None:
            key = f"{item.get('source')}|{item.get('kind')}|{item.get('text')[:120]}"
            if key in seen or len(candidates) >= sample_size:
                return
            seen.add(key)
            candidates.append(item)

        for sample in (heading_issues.get("samples") or [])[:60]:
            add_item(
                {
                    "id": f"heading-{len(candidates)+1:04d}",
                    "kind": "heading_candidate",
                    "suggested_label": "garbage_heading",
                    "label": None,
                    "source": sample.get("source"),
                    "section_path": sample.get("section_path"),
                    "text": sample.get("leaf") or sample.get("section_title"),
                    "reason_hint": sample.get("reason"),
                }
            )

        for doc in (reparse_report.get("documents") or []):
            for sample in doc.get("filter_samples") or []:
                add_item(
                    {
                        "id": f"filter-{len(candidates)+1:04d}",
                        "kind": "filter_reject",
                        "suggested_label": "false_positive"
                        if sample.get("reason_code") in {"mixed_script_token", "mixed_script_line"}
                        else "review",
                        "label": None,
                        "source": sample.get("source"),
                        "section_path": sample.get("section_path"),
                        "text": sample.get("content_preview"),
                        "reason_hint": sample.get("reason_code"),
                    }
                )

        # Fill remaining with short body chunks for heading/body/garbage labeling.
        short_rows = [row for row in rows if 20 <= int(row.get("length") or 0) <= 180]
        short_rows.sort(key=lambda row: (row.get("source") or "", row.get("length") or 0))
        for row in short_rows:
            add_item(
                {
                    "id": f"body-{len(candidates)+1:04d}",
                    "kind": "body_or_heading",
                    "suggested_label": None,
                    "label": None,
                    "source": row.get("source"),
                    "section_path": row.get("section_path"),
                    "text": (row.get("content") or "")[:240],
                    "reason_hint": "short_chunk",
                    "chunk_id": row.get("chunk_id"),
                }
            )
            if len(candidates) >= sample_size:
                break
        return candidates

    def _code_fingerprint(self) -> str:
        roots = [
            Path(__file__),
            Path(__file__).resolve().parents[1] / "services" / "loader.py",
            Path(__file__).resolve().parents[1] / "services" / "unstructured_loader.py",
        ]
        digest = hashlib.sha256()
        for path in roots:
            if path.exists():
                digest.update(path.name.encode("utf-8"))
                digest.update(path.read_bytes())
        return digest.hexdigest()[:16]

    def _corpus_hash(self, report: dict[str, Any]) -> str:
        payload = {
            "overview": report.get("overview"),
            "by_source": [
                {
                    "source": item.get("source"),
                    "chunk_count": item.get("chunk_count"),
                    "length_p50": item.get("length_p50"),
                }
                for item in (report.get("by_source") or [])
            ],
            "consistency": report.get("consistency"),
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
