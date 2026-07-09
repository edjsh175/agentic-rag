from rag_knowledge.services.knowledge_base_consistency import (
    KnowledgeBaseConsistencyError,
    KnowledgeBaseConsistencyService,
)


def test_audit_detects_missing_indexed_chunks_and_source_sections():
    service = KnowledgeBaseConsistencyService(
        index_data={
            "files": {
                "hash-1": {
                    "file_name": "StampTools用户手册.docx",
                    "file_path": "word/StampTools用户手册.docx",
                    "chunk_ids": ["chunk-1", "chunk-2"],
                }
            }
        },
        chunk_snapshot={
            "ids": ["chunk-1"],
            "documents": ["工程设置内容"],
            "metadatas": [
                {
                    "chunk_id": "chunk-1",
                    "source": "word/StampTools用户手册.docx",
                    "section_path": "UEModelBuilder > UEModelBuilder > 工程设置",
                }
            ],
        },
    )

    report = service.audit(source="word/StampTools用户手册.docx")

    assert report["summary"]["consistent"] is False
    assert report["summary"]["index_chunk_total"] == 2
    assert report["summary"]["chroma_chunk_total"] == 1
    assert report["summary"]["missing_indexed_chunk_total"] == 1
    assert report["source"]["chunk_total"] == 1
    assert report["source"]["section_paths"] == ["UEModelBuilder > UEModelBuilder > 工程设置"]
    assert report["files"][0]["missing_chunk_ids"] == ["chunk-2"]


def test_assert_consistent_raises_when_index_and_chroma_diverge():
    service = KnowledgeBaseConsistencyService(
        index_data={
            "files": {
                "hash-1": {
                    "file_name": "manual.docx",
                    "file_path": "manual.docx",
                    "chunk_ids": ["chunk-1"],
                }
            }
        },
        chunk_snapshot={
            "ids": [],
            "documents": [],
            "metadatas": [],
        },
    )

    try:
        service.assert_consistent()
        assert False, "consistency mismatch should raise"
    except KnowledgeBaseConsistencyError as exc:
        assert exc.report["summary"]["consistent"] is False
        assert exc.report["summary"]["missing_indexed_chunk_total"] == 1
