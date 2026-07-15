"""Fixture tests for offline short-section merge (compat re-export)."""
from __future__ import annotations

from langchain_core.documents import Document

from rag_knowledge.services.chunk_merge_spike import (
    apply_technical_manual_merge,
    documents_to_merge_units,
    reassign_chunk_adjacency,
)


def _doc(text: str, path: str, order: int, content_type: str = "text") -> Document:
    return Document(
        page_content=text,
        metadata={
            "source": "fixture.docx",
            "section_path": path,
            "element_order": order,
            "content_type": content_type,
            "source_snapshot_hash": "snap_fixture",
            "source_document_id": "snap_fixture",
            "element_id": f"el_{order}",
            "source_element_ids": [f"el_{order}"],
            "source_raw_block_ids": [f"rb_{order:04d}"],
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


def test_merges_short_siblings_under_same_parent():
    docs = [
        _doc("步骤一说明。" * 15, "安装 > Rocky > 分区", 1),
        _doc("步骤二说明。" * 15, "安装 > Rocky > 分区检查", 2),
    ]
    units = documents_to_merge_units(docs)
    assert len(units) == 1
    assert units[0].merged_from_orders == [1, 2]


def test_command_lead_in_sticks_to_following_command():
    docs = [
        _doc("请执行以下命令完成修复。", "运维 > 修复", 1),
        _doc("umount -f /dev/mapper/rl-var", "运维 > 修复", 2),
        _doc("xfs_repair /dev/mapper/rl-var", "运维 > 修复", 3),
    ]
    units = documents_to_merge_units(docs)
    assert len(units) == 1


def test_table_stays_atomic():
    docs = [
        _doc("说明文字" * 40, "部署 > 端口", 1),
        _doc("| 端口 | 说明 |\n| --- | --- |\n| 443 | HTTPS |", "部署 > 端口", 2, "table"),
    ]
    units = documents_to_merge_units(docs)
    assert len(units) == 2
