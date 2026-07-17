import pytest

from rag_knowledge.services.knowledge_base_consistency import (
    KnowledgeBaseConsistencyError,
    KnowledgeBaseConsistencyService,
)


def _index(chunk_ids, *, profile="technical_manual", policy="cp_manual"):
    return {
        "files": {
            "hash-1": {
                "file_name": "manual.docx",
                "file_path": "word/manual.docx",
                "document_profile": profile,
                "document_profile_source": "profile_map",
                "chunk_policy_id": policy,
                "chunk_ids": chunk_ids,
            }
        }
    }


def _metadata(stable_id, **overrides):
    value = {
        "chunk_id": stable_id,
        "chunk_uid": stable_id,
        "source": "word/manual.docx",
        "source_document_id": "doc-1",
        "section_path": "Manual > Settings",
        "document_profile": "technical_manual",
        "chunk_policy_id": "cp_manual",
    }
    value.update(overrides)
    return value


def test_audit_detects_missing_indexed_chunks_and_source_sections():
    service = KnowledgeBaseConsistencyService(
        index_data=_index(["chunk-1", "chunk-2"]),
        chunk_snapshot={
            "ids": ["chunk-1"],
            "documents": ["settings"],
            "metadatas": [_metadata("chunk-1")],
        },
        profile_map={"word/manual.docx": "technical_manual"},
    )

    report = service.audit(source="word/manual.docx")

    assert report["summary"]["consistent"] is False
    assert report["summary"]["index_chunk_total"] == 2
    assert report["summary"]["chroma_chunk_total"] == 1
    assert report["summary"]["missing_indexed_chunk_total"] == 1
    assert report["source"]["chunk_total"] == 1
    assert report["source"]["section_paths"] == ["Manual > Settings"]
    assert report["files"][0]["missing_chunk_ids"] == ["chunk-2"]


def test_assert_consistent_raises_when_index_and_chroma_diverge():
    service = KnowledgeBaseConsistencyService(
        index_data=_index(["chunk-1"]),
        chunk_snapshot={"ids": [], "documents": [], "metadatas": []},
        profile_map={"word/manual.docx": "technical_manual"},
    )

    with pytest.raises(KnowledgeBaseConsistencyError) as caught:
        service.assert_consistent()

    assert caught.value.report["summary"]["consistent"] is False
    assert caught.value.report["summary"]["missing_indexed_chunk_total"] == 1


def test_assert_consistent_accepts_stable_reciprocal_adjacency():
    service = KnowledgeBaseConsistencyService(
        index_data=_index(["chk_1", "chk_2"]),
        chunk_snapshot={
            "ids": ["chk_1", "chk_2"],
            "documents": ["first", "second"],
            "metadatas": [
                _metadata("chk_1", next_chunk_id="chk_2"),
                _metadata("chk_2", prev_chunk_id="chk_1"),
            ],
        },
        profile_map={"word/manual.docx": "technical_manual"},
    )

    report = service.assert_consistent()

    assert report["summary"]["consistent"] is True
    assert report["summary"]["identity_error_total"] == 0
    assert report["summary"]["profile_error_total"] == 0
    assert report["summary"]["adjacency_error_total"] == 0


def test_audit_rejects_identity_profile_and_adjacency_mismatches():
    service = KnowledgeBaseConsistencyService(
        index_data=_index(["chk_1", "chk_2"]),
        chunk_snapshot={
            "ids": ["chk_1", "chk_2"],
            "documents": ["first", "second"],
            "metadatas": [
                _metadata(
                    "chk_1",
                    chunk_id="old_uuid",
                    next_chunk_id="missing",
                    document_profile="section_based",
                    chunk_policy_id="wrong_policy",
                ),
                _metadata(
                    "chk_2",
                    chunk_uid="wrong_uid",
                    prev_chunk_id="chk_1",
                    source_document_id="doc-2",
                ),
            ],
        },
        profile_map={"word/manual.docx": "technical_manual"},
    )

    report = service.audit()

    assert report["summary"]["consistent"] is False
    assert report["summary"]["identity_error_total"] == 2
    assert report["summary"]["profile_error_total"] == 2
    assert report["summary"]["adjacency_error_total"] >= 2
