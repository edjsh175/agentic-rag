"""Round 0C isolation audit gate and section-lineage regressions."""
from __future__ import annotations

from langchain_core.documents import Document

from rag_knowledge.services.section_chunk_merge import section_id_for
from scripts import audit_round0c_isolation as audit


def test_length_gate_reasons_include_every_failed_prd_metric():
    reasons = audit._length_gate_reasons(
        {
            "after_lt_100_rate": 0.259,
            "after_lt_200_rate": 0.492,
            "after_gt_1200_rate": 0.0525,
        }
    )

    assert reasons == [
        "lt100 after=25.9% > 5% PRD gate",
        "lt200 after=49.2% > 15% PRD gate",
        "gt1200 after=5.2% > 5% PRD gate",
    ]


def test_chunk_gates_alone_do_not_authorize_round0g():
    result = audit._go_no_go_report([])

    assert result["chunk_foundation_gate_passed"] is True
    assert result["enter_0g"] is False
    assert "FR-10" in result["remaining_0g_requirements"][0]


def test_prd_text_length_stats_exclude_heading_table_and_code():
    docs = [
        Document(page_content="短" * 50, metadata={"content_type": "text"}),
        Document(page_content="中" * 150, metadata={"content_type": "text"}),
        Document(page_content="长" * 1300, metadata={"content_type": "text"}),
        Document(
            page_content="表格说明",
            metadata={"content_type": "text", "table_context": True},
        ),
        Document(page_content="标题", metadata={"content_type": "heading"}),
        Document(page_content="表" * 1500, metadata={"content_type": "table"}),
        Document(page_content="code", metadata={"content_type": "code"}),
    ]

    stats = audit._prd_text_lengths_from_documents(docs)

    assert stats["count"] == 3
    assert stats["lt_100_rate"] == 1 / 3
    assert stats["lt_200_rate"] == 2 / 3
    assert stats["gt_1200_rate"] == 1 / 3


def test_prepare_final_chunks_splits_filters_then_reassigns_adjacency():
    elements = [
        Document(
            page_content=f"第{order}章正文。" * 10,
            metadata={
                "source": "manual.docx",
                "section_path": f"第{order}章 > 说明",
                "element_order": order,
                "content_type": "text",
                "source_snapshot_hash": "snap_a",
                "source_document_id": "snap_a",
                "element_id": f"el_{order}",
                "source_element_ids": [f"el_{order}"],
                "source_raw_block_ids": [f"rb_{order}"],
            },
        )
        for order in (1, 2, 3)
    ]

    class _ChunkLoader:
        split_called = False
        mark_called = False
        filter_called = False

        def _split_documents_preserving_blocks(self, docs):
            self.split_called = True
            return docs

        def _mark_table_context_chunks(self, docs):
            self.mark_called = True
            return docs

        def _post_process_chunks(self, docs):
            self.filter_called = True
            return [docs[0], docs[2]]

    chunk_loader = _ChunkLoader()

    final = audit._prepare_final_chunks(elements, chunk_loader)

    assert chunk_loader.split_called
    assert chunk_loader.mark_called
    assert chunk_loader.filter_called
    assert len(final) == 2
    assert final[0].metadata["next_chunk_id"] == final[1].metadata["chunk_uid"]
    assert final[1].metadata["prev_chunk_id"] == final[0].metadata["chunk_uid"]


def test_source_section_lineage_accepts_ordered_document_scoped_pairs():
    document_key = "snap_a"
    paths = ["概述 > 运行环境", "概述 > 密钥管理"]
    doc = Document(
        page_content="## 概述 > 运行环境\n\n说明\n\n## 概述 > 密钥管理\n\n说明",
        metadata={
            "source_snapshot_hash": document_key,
            "source_section_paths": paths,
            "source_section_ids": [section_id_for(document_key, path) for path in paths],
            "source_element_ids": ["el_1", "el_2"],
            "source_raw_block_ids": ["rb_1", "rb_2"],
        },
    )

    assert audit._source_section_lineage_report([doc]) == {
        "missing_source_section_paths": 0,
        "missing_source_section_ids": 0,
        "mismatched_source_section_pairs": 0,
        "invalid_source_section_ids": 0,
        "missing_source_section_titles": 0,
    }


def test_source_section_lineage_rejects_wrong_id_and_missing_title():
    doc = Document(
        page_content="正文没有叶子标题",
        metadata={
            "source_snapshot_hash": "snap_a",
            "source_section_paths": ["概述 > 运行环境", "概述 > 密钥管理"],
            "source_section_ids": ["sec_wrong", "sec_wrong_again"],
        },
    )

    report = audit._source_section_lineage_report([doc])

    assert report["invalid_source_section_ids"] == 2
    assert report["missing_source_section_titles"] == 2


def test_source_section_lineage_allows_heading_list_without_section():
    doc = Document(
        page_content="- 孤立标题",
        metadata={"content_type": "heading"},
    )

    assert audit._source_section_lineage_report([doc]) == {
        "missing_source_section_paths": 0,
        "missing_source_section_ids": 0,
        "mismatched_source_section_pairs": 0,
        "invalid_source_section_ids": 0,
        "missing_source_section_titles": 0,
    }


def test_source_section_lineage_does_not_require_titles_for_same_l2_deep_siblings():
    document_key = "snap_a"
    paths = ["安装 > Rocky > 分区", "安装 > Rocky > 分区检查"]
    doc = Document(
        page_content="合并后的正文",
        metadata={
            "source_snapshot_hash": document_key,
            "source_section_paths": paths,
            "source_section_ids": [section_id_for(document_key, path) for path in paths],
        },
    )

    assert audit._source_section_lineage_report([doc])["missing_source_section_titles"] == 0


def test_source_section_lineage_does_not_require_titles_for_l1_to_child_merge():
    document_key = "snap_a"
    paths = ["授权设置", "授权设置 > 建立软链接"]
    doc = Document(
        page_content="合并后的正文",
        metadata={
            "source_snapshot_hash": document_key,
            "source_section_paths": paths,
            "source_section_ids": [section_id_for(document_key, path) for path in paths],
        },
    )

    assert audit._source_section_lineage_report([doc])["missing_source_section_titles"] == 0
