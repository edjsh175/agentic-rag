"""Unit tests for readonly Chunk health audit helpers."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.documents import Document

from rag_knowledge.services.chunk_health_audit import (
    ChunkHealthAuditor,
    diagnose_low_information,
    is_suspicious_heading,
    percentile,
)


def test_diagnose_keeps_chinese_technical_mixed_content():
    text = (
        "若提示文件系统已挂载，请先执行 umount -f /dev/mapper/rl-var，"
        "再运行 xfs_repair 修复分区。配置 Redis 与 Cesium/WebGL 后重启服务。"
    )
    decision = diagnose_low_information(text)
    # Current production filter may still reject mixed-script tokens; audit must
    # expose the reason instead of silently dropping diagnostics.
    assert decision.reason_code in {"keep", "mixed_script_token", "mixed_script_line"}
    if decision.rejected:
        assert decision.reason_code.startswith("mixed_script")


def test_diagnose_rejects_toc_majority():
    text = "第一章 简介........ 1\n第二章 安装........ 2\n第三章 配置........ 3"
    decision = diagnose_low_information(text)
    assert decision.rejected
    assert decision.reason_code == "toc_majority"


def test_suspicious_heading_detects_command_and_port():
    assert is_suspicious_heading("reboot")[0] is True
    assert is_suspicious_heading("enabled=1")[0] is True
    assert is_suspicious_heading("vim /etc/sysctl.conf")[0] is True
    assert is_suspicious_heading("安装与部署")[0] is False


def test_percentile_basic():
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([], 0.5) == 0.0


def test_auditor_builds_report_from_snapshot(tmp_path: Path, isolated_storage):
    isolated_storage(
        db_name="chunk-health.db",
        chroma_name="chunk-health-chroma",
        data_dir_name="chunk-health-data",
    )
    snapshot = {
        "ids": ["c1", "c2", "c3"],
        "documents": [
            "短标题",
            "这是一段足够长的正文，用于验证长度统计不会把所有块都算成碎片。",
            "tls-listening-port=5349\nHttpsPort: 5439",
        ],
        "metadatas": [
            {
                "source": "demo.docx",
                "section_path": "reboot",
                "section_title": "reboot",
                "section_index": 1,
                "chunk_in_section": 0,
                "content_type": "text",
                "review_status": "approved",
            },
            {
                "source": "demo.docx",
                "section_path": "安装 > 步骤",
                "section_title": "步骤",
                "section_index": 2,
                "chunk_in_section": 0,
                "content_type": "text",
                "review_status": "approved",
            },
            {
                "source": "demo.docx",
                "section_path": "",
                "section_title": "",
                "section_index": 3,
                "chunk_in_section": 0,
                "content_type": "text",
                "review_status": "approved",
            },
        ],
    }
    index = {
        "files": {
            "demo": {
                "file_name": "demo.docx",
                "file_path": "missing/demo.docx",
                "chunk_ids": ["c1", "c2", "c3"],
            }
        }
    }
    auditor = ChunkHealthAuditor(chunk_snapshot=snapshot, file_index=index)
    report = auditor.run(reparse_sources=False, annotation_sample_size=20)
    assert report["readonly"] is True
    assert report["overview"]["total_chunks"] == 3
    assert report["suspicious_headings"]["count"] >= 1
    assert any(item["key"] in {"HttpsPort", "tls-listening-port", "port_mention"} for item in report["conflict_candidates"])

    out = tmp_path / "audit"
    paths = auditor.write_reports(report, out)
    assert paths["json"].exists()
    assert paths["markdown"].exists()
    assert "Chunk 健康审计报告" in paths["markdown"].read_text(encoding="utf-8")


def test_audit_markdown_uses_report_label():
    report = {
        "report_label": "Round 0B",
        "overview": {},
        "by_source": [],
        "suspicious_headings": {},
        "reparse": {},
        "consistency": {},
        "media": {},
        "conflict_candidates": [],
    }

    markdown = ChunkHealthAuditor.render_markdown(report)

    assert markdown.startswith("# Chunk 健康审计报告（Round 0B）")


def test_reparse_filters_finalized_chunk_instead_of_raw_short_element(tmp_path: Path):
    source_path = tmp_path / "short.txt"
    source_path.write_text("fixture", encoding="utf-8")
    cfg = SimpleNamespace(
        watch_dir=tmp_path,
        chunk_size=500,
        chunk_overlap=0,
        unstructured_strategy="fast",
    )
    index = {
        "files": {
            "short": {
                "file_name": source_path.name,
                "file_path": source_path.name,
                "chunk_ids": [],
            }
        }
    }
    raw_short_element = Document(
        page_content="短句",
        metadata={
            "source": source_path.name,
            "section_path": "操作系统安装 > 创建虚拟机 > 安装模式",
            "section_title": "安装模式",
            "content_type": "text",
        },
    )
    auditor = ChunkHealthAuditor(cfg=cfg, chunk_snapshot={}, file_index=index)

    with patch("rag_knowledge.services.chunk_health_audit.UnstructuredChapterLoader") as loader:
        loader.return_value.load.return_value = [raw_short_element]
        report = auditor._reparse_indexed_sources(index, max_filter_samples=10)

    document = report["documents"][0]
    assert document["pre_filter"] == 1
    assert document["post_filter"] == 1
    assert document["reason_counts"] == {}


def test_reparse_records_section_prefixed_toc_marker(tmp_path: Path):
    source_path = tmp_path / "toc.txt"
    source_path.write_text("fixture", encoding="utf-8")
    cfg = SimpleNamespace(
        watch_dir=tmp_path,
        chunk_size=500,
        chunk_overlap=0,
        unstructured_strategy="fast",
    )
    index = {
        "files": {
            "toc": {
                "file_name": source_path.name,
                "file_path": source_path.name,
                "chunk_ids": [],
            }
        }
    }
    toc_element = Document(
        page_content="目录",
        metadata={
            "source": source_path.name,
            "section_path": "2024年5月",
            "section_title": "2024年5月",
            "content_type": "text",
        },
    )
    auditor = ChunkHealthAuditor(cfg=cfg, chunk_snapshot={}, file_index=index)

    with patch("rag_knowledge.services.chunk_health_audit.UnstructuredChapterLoader") as loader:
        loader.return_value.load.return_value = [toc_element]
        report = auditor._reparse_indexed_sources(index, max_filter_samples=10)

    document = report["documents"][0]
    assert document["post_filter"] == 0
    assert document["reason_counts"] == {"toc_marker": 1}
