"""Tests for Round 0C technical-manual section merge (production module)."""
from __future__ import annotations

from langchain_core.documents import Document

from rag_knowledge.services.section_chunk_merge import (
    CHUNKING_METHOD,
    apply_technical_manual_merge,
    documents_to_merge_units,
    reassign_chunk_adjacency,
    section_id_for,
)


def _doc(
    text: str,
    path: str,
    order: int,
    *,
    content_type: str = "text",
    source: str = "fixture.docx",
    snapshot: str = "snap_a",
    element_id: str | None = None,
    raw_ids: list[str] | None = None,
) -> Document:
    eid = element_id or f"el_{order}"
    return Document(
        page_content=text,
        metadata={
            "source": source,
            "section_path": path,
            "element_order": order,
            "content_type": content_type,
            "category": "text",
            "source_snapshot_hash": snapshot,
            "source_document_id": snapshot[:32],
            "element_id": eid,
            "source_element_ids": [eid],
            "source_raw_block_ids": raw_ids or [f"rb_{order:04d}"],
        },
    )


def test_does_not_merge_across_hard_l2_boundary():
    docs = [
        _doc("短正文A" * 20, "安装 > 分区", 1),
        _doc("短正文B" * 20, "部署 > 网络", 2),
    ]
    units = documents_to_merge_units(docs)
    assert len(units) == 2
    final = reassign_chunk_adjacency(apply_technical_manual_merge(docs))
    assert final[0].metadata["next_chunk_id"] == final[1].metadata["chunk_uid"]
    assert final[1].metadata["prev_chunk_id"] == final[0].metadata["chunk_uid"]


def test_merges_short_siblings_under_same_parent():
    docs = [
        _doc("步骤一说明。" * 15, "安装 > Rocky > 分区", 1),
        _doc("步骤二说明。" * 15, "安装 > Rocky > 分区检查", 2),
    ]
    units = documents_to_merge_units(docs)
    assert len(units) == 1
    assert units[0].merged_from_orders == [1, 2]
    assert units[0].source_element_ids == ["el_1", "el_2"]
    assert units[0].source_raw_block_ids == ["rb_0001", "rb_0002"]


def test_merges_bounded_short_l2_leaves_under_same_l1_with_section_lineage():
    docs = [
        _doc("运行环境说明。" * 10, "概述 > 运行环境", 1),
        _doc("密钥管理说明。" * 10, "概述 > 密钥管理 > 认证", 2),
    ]

    units = documents_to_merge_units(docs)

    assert len(units) == 1
    unit = units[0]
    assert unit.section_path == "概述"
    assert unit.source_section_paths == ["概述 > 运行环境", "概述 > 密钥管理 > 认证"]
    assert unit.source_section_ids == [
        section_id_for("snap_a", "概述 > 运行环境"),
        section_id_for("snap_a", "概述 > 密钥管理 > 认证"),
    ]
    assert "## 概述 > 运行环境" in unit.content_markdown
    assert "## 概述 > 密钥管理 > 认证" in unit.content_markdown


def test_does_not_merge_l2_leaf_when_next_body_is_not_short():
    docs = [
        _doc("图层管理说明。" * 10, "快捷菜单 > 图层管理", 1),
        _doc("飞行路径正文。" * 50, "快捷菜单 > 飞行路径", 2),
    ]

    assert len(documents_to_merge_units(docs)) == 2


def test_stops_l2_leaf_bucket_after_reaching_target_minimum():
    docs = [
        _doc("甲" * 140, "概述 > 运行环境", 1),
        _doc("乙" * 140, "概述 > 密钥管理", 2),
        _doc("丙" * 20, "概述 > 日志管理", 3),
    ]

    units = documents_to_merge_units(docs)

    assert len(units) == 2
    assert units[0].merged_from_orders == [1, 2]
    assert units[1].merged_from_orders == [3]


def test_does_not_merge_l2_leaf_when_rendered_content_exceeds_target_max():
    long_l2_a = "运行环境" * 120
    long_l2_b = "密钥管理" * 120
    docs = [
        _doc("甲" * 20, f"概述 > {long_l2_a}", 1),
        _doc("乙" * 20, f"概述 > {long_l2_b}", 2),
    ]

    assert len(documents_to_merge_units(docs)) == 2


def test_document_root_text_retains_root_source_section_lineage():
    unit = documents_to_merge_units([_doc("目录正文内容。" * 10, "", 1)])[0]

    assert unit.source_section_paths == [""]
    assert unit.source_section_ids == [section_id_for("snap_a", "")]


def test_command_lead_in_sticks_to_following_command():
    docs = [
        _doc("请执行以下命令完成修复。", "运维 > 修复", 1),
        _doc("umount -f /dev/mapper/rl-var", "运维 > 修复", 2),
        _doc("xfs_repair /dev/mapper/rl-var", "运维 > 修复", 3),
    ]
    units = documents_to_merge_units(docs)
    assert len(units) == 1
    assert "执行以下命令" in units[0].content_markdown
    assert "umount -f" in units[0].content_markdown


def test_table_stays_atomic():
    docs = [
        _doc("说明文字" * 40, "部署 > 端口", 1),
        _doc(
            "| 端口 | 说明 |\n| --- | --- |\n| 443 | HTTPS |",
            "部署 > 端口",
            2,
            content_type="table",
        ),
    ]
    units = documents_to_merge_units(docs)
    assert any(u.content_type == "table" for u in units)
    assert len(units) == 2


def test_section_id_is_document_scoped():
    same_path = "部署 > 网络"
    a = section_id_for("snap_a", same_path)
    b = section_id_for("snap_b", same_path)
    assert a != b
    assert a == section_id_for("snap_a", same_path)


def test_chunk_uid_unique_across_documents_same_local_index():
    docs_a = [
        _doc("步骤一说明。" * 15, "安装 > Rocky > 分区", 1, snapshot="snap_a", source="a.docx"),
        _doc("步骤二说明。" * 15, "安装 > Rocky > 分区检查", 2, snapshot="snap_a", source="a.docx"),
    ]
    docs_b = [
        _doc("步骤一说明。" * 15, "安装 > Rocky > 分区", 1, snapshot="snap_b", source="b.docx"),
        _doc("步骤二说明。" * 15, "安装 > Rocky > 分区检查", 2, snapshot="snap_b", source="b.docx"),
    ]
    final_a = reassign_chunk_adjacency(apply_technical_manual_merge(docs_a))
    final_b = reassign_chunk_adjacency(apply_technical_manual_merge(docs_b))
    assert final_a[0].metadata["chunk_uid"] != final_b[0].metadata["chunk_uid"]
    assert final_a[0].metadata["section_id"] != final_b[0].metadata["section_id"]
    assert final_a[0].metadata["next_chunk_id"] is None  # single merged unit each
    assert final_a[0].metadata["source_raw_block_ids"]


def test_apply_sets_lineage_and_leaves_prev_next_until_reassign():
    docs = [
        _doc("步骤一说明。" * 15, "安装 > Rocky > 分区", 1),
        _doc("步骤二说明。" * 15, "安装 > Rocky > 分区检查", 2),
        _doc("另章说明。" * 20, "部署 > 服务", 3),
    ]
    merged = apply_technical_manual_merge(docs)
    assert all(m.metadata.get("chunking_method") == CHUNKING_METHOD for m in merged)
    assert all(m.metadata.get("chunk_uid", "").startswith("chk_") for m in merged)
    assert all(m.metadata.get("source_element_ids") for m in merged)
    assert all(m.metadata.get("source_section_paths") for m in merged)
    assert all(m.metadata.get("source_section_ids") for m in merged)
    final = reassign_chunk_adjacency(merged)
    assert final[0].metadata["next_chunk_id"] == final[1].metadata["chunk_uid"]
    assert final[1].metadata["prev_chunk_id"] == final[0].metadata["chunk_uid"]
    assert final[0].metadata["next_chunk_id"].startswith("chk_")


def test_spike_module_reexports_production_api():
    from rag_knowledge.services import chunk_merge_spike as spike
    from rag_knowledge.services import section_chunk_merge as prod

    assert spike.documents_to_merge_units is prod.documents_to_merge_units
    assert spike.apply_technical_manual_merge is prod.apply_technical_manual_merge


def test_classify_hard_boundary_and_parent():
    from rag_knowledge.services.section_chunk_merge import classify_adjacent_merge_decision

    assert (
        classify_adjacent_merge_decision(
            "安装 > 分区",
            "部署 > 网络",
            "短" * 20,
            "短" * 20,
        )
        == "different_l1_hard_boundary"
    )
    assert (
        classify_adjacent_merge_decision(
            "安装 > Rocky > A",
            "安装 > Rocky > B",
            "短" * 20,
            "短" * 20,
        )
        == "merge_under_target_min"
    )


def test_explain_merge_flush_reasons_records_hard_boundary():
    from rag_knowledge.services.section_chunk_merge import explain_merge_flush_reasons

    docs = [
        _doc("短正文A" * 20, "安装 > 分区", 1),
        _doc("短正文B" * 20, "部署 > 网络", 2),
    ]
    events = explain_merge_flush_reasons(docs)
    reasons = [e["reason"] for e in events]
    assert "different_l1_hard_boundary" in reasons


def test_explain_merge_flush_reasons_records_same_l1_short_leaf_merge():
    from rag_knowledge.services.section_chunk_merge import explain_merge_flush_reasons

    docs = [
        _doc("运行环境说明。" * 10, "概述 > 运行环境", 1),
        _doc("密钥管理说明。" * 10, "概述 > 密钥管理", 2),
    ]

    reasons = [event["reason"] for event in explain_merge_flush_reasons(docs)]

    assert "merge_same_l1_short_leaf" in reasons
