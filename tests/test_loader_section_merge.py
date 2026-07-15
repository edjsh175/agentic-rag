"""FileLoader Round 0C wiring: DOCX merge metadata without Chroma writes."""
from __future__ import annotations

from langchain_core.documents import Document

from rag_knowledge.services.document_profiles import DocumentProfile
from rag_knowledge.services.loader import FileLoader
from rag_knowledge.services.section_chunk_merge import CHUNKING_METHOD, section_id_for


def test_docx_structured_path_applies_merge(monkeypatch, tmp_path):
    loader = FileLoader.__new__(FileLoader)
    loader._use_unstructured = True
    loader._extract_images = False

    element_docs = [
        Document(
            page_content="步骤一说明。" * 15,
            metadata={
                "source": "manual.docx",
                "section_path": "安装 > Rocky > 分区",
                "element_order": 1,
                "content_type": "text",
                "category": "text",
                "source_snapshot_hash": "abc123",
                "source_document_id": "abc123",
                "element_id": "el_1",
                "source_element_ids": ["el_1"],
                "source_raw_block_ids": ["rb_0001"],
            },
        ),
        Document(
            page_content="步骤二说明。" * 15,
            metadata={
                "source": "manual.docx",
                "section_path": "安装 > Rocky > 分区检查",
                "element_order": 2,
                "content_type": "text",
                "category": "text",
                "source_snapshot_hash": "abc123",
                "source_document_id": "abc123",
                "element_id": "el_2",
                "source_element_ids": ["el_2"],
                "source_raw_block_ids": ["rb_0002"],
            },
        ),
    ]

    class _FakeUnstructured:
        def load(self, _path):
            return element_docs

    loader._unstructured_loader = _FakeUnstructured()
    monkeypatch.setattr(loader, "_split_documents_preserving_blocks", lambda docs: docs)
    monkeypatch.setattr(loader, "_post_process_chunks", lambda docs: docs)

    docx = tmp_path / "manual.docx"
    docx.write_bytes(b"PK\x00\x00")

    chunks = loader._load_text(str(docx), document_profile=DocumentProfile.TECHNICAL_MANUAL)
    assert len(chunks) == 1
    meta = chunks[0].metadata
    assert meta["chunking_method"] == CHUNKING_METHOD
    assert meta["section_id"].startswith("sec_")
    assert meta["chunk_uid"].startswith("chk_")
    assert meta["merged_from"] == [1, 2]
    assert meta["source_element_ids"] == ["el_1", "el_2"]
    assert meta["source_raw_block_ids"] == ["rb_0001", "rb_0002"]
    assert meta["prev_chunk_id"] is None
    assert meta["next_chunk_id"] is None


def test_md_structured_path_skips_technical_manual_merge(monkeypatch, tmp_path):
    loader = FileLoader.__new__(FileLoader)
    loader._use_unstructured = True
    loader._extract_images = False

    element_docs = [
        Document(
            page_content="短A" * 20,
            metadata={
                "source": "note.md",
                "section_path": "A > B",
                "element_order": 1,
                "content_type": "text",
            },
        ),
        Document(
            page_content="短B" * 20,
            metadata={
                "source": "note.md",
                "section_path": "A > C",
                "element_order": 2,
                "content_type": "text",
            },
        ),
    ]

    class _FakeUnstructured:
        def load(self, _path):
            return element_docs

    loader._unstructured_loader = _FakeUnstructured()
    monkeypatch.setattr(loader, "_split_documents_preserving_blocks", lambda docs: docs)
    monkeypatch.setattr(loader, "_post_process_chunks", lambda docs: docs)

    md = tmp_path / "note.md"
    md.write_text("# hi\n", encoding="utf-8")

    chunks = loader._load_text(str(md))
    assert len(chunks) == 2
    assert chunks[0].metadata.get("chunking_method") != CHUNKING_METHOD


def test_docx_bounded_l2_merge_renders_anchor_and_source_sections(monkeypatch, tmp_path):
    loader = FileLoader.__new__(FileLoader)
    loader._use_unstructured = True
    loader._extract_images = False

    element_docs = [
        Document(
            page_content="运行环境说明。" * 10,
            metadata={
                "source": "manual.docx",
                "section_path": "概述 > 运行环境",
                "element_order": 1,
                "content_type": "text",
                "category": "text",
                "source_snapshot_hash": "abc123",
                "source_document_id": "abc123",
                "element_id": "el_1",
                "source_element_ids": ["el_1"],
                "source_raw_block_ids": ["rb_0001"],
            },
        ),
        Document(
            page_content="密钥管理说明。" * 10,
            metadata={
                "source": "manual.docx",
                "section_path": "概述 > 密钥管理 > 认证",
                "element_order": 2,
                "content_type": "text",
                "category": "text",
                "source_snapshot_hash": "abc123",
                "source_document_id": "abc123",
                "element_id": "el_2",
                "source_element_ids": ["el_2"],
                "source_raw_block_ids": ["rb_0002"],
            },
        ),
    ]

    class _FakeUnstructured:
        def load(self, _path):
            return element_docs

    loader._unstructured_loader = _FakeUnstructured()
    monkeypatch.setattr(loader, "_post_process_chunks", lambda docs: docs)

    docx = tmp_path / "manual.docx"
    docx.write_bytes(b"PK\x00\x00")

    chunks = loader._load_text(str(docx), document_profile=DocumentProfile.TECHNICAL_MANUAL)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.metadata["section_path"] == "概述"
    assert chunk.metadata["section_id"] == section_id_for("abc123", "概述")
    assert chunk.metadata["source_section_paths"] == [
        "概述 > 运行环境",
        "概述 > 密钥管理 > 认证",
    ]
    assert chunk.page_content.startswith("# 概述\n\n")
    assert "## 概述 > 运行环境" in chunk.page_content
    assert "## 概述 > 密钥管理 > 认证" in chunk.page_content
    assert "运行环境" in chunk.metadata["searchable_text"]
    assert "密钥管理" in chunk.metadata["searchable_text"]


def test_docx_defaults_to_section_based_instead_of_guessing_from_extension(monkeypatch, tmp_path):
    loader = FileLoader.__new__(FileLoader)
    loader._use_unstructured = True
    loader._extract_images = False
    element_docs = [
        Document(page_content="短A" * 20, metadata={"source": "用户手册.docx", "section_path": "A > B", "element_order": 1, "content_type": "text"}),
        Document(page_content="短B" * 20, metadata={"source": "用户手册.docx", "section_path": "A > C", "element_order": 2, "content_type": "text"}),
    ]

    class _FakeUnstructured:
        def load(self, _path):
            return element_docs

    loader._unstructured_loader = _FakeUnstructured()
    monkeypatch.setattr(loader, "_split_documents_preserving_blocks", lambda docs: docs)
    monkeypatch.setattr(loader, "_post_process_chunks", lambda docs: docs)
    docx = tmp_path / "用户手册.docx"
    docx.write_bytes(b"PK\x00\x00")

    chunks = loader._load_text(str(docx))

    assert len(chunks) == 2
    assert {chunk.metadata["document_profile"] for chunk in chunks} == {"section_based"}


def test_docx_reassigns_adjacency_after_post_process_removes_chunk(monkeypatch, tmp_path):
    loader = FileLoader.__new__(FileLoader)
    loader._use_unstructured = True
    loader._extract_images = False

    element_docs = [
        Document(
            page_content=f"第{order}章有效正文内容。" * 10,
            metadata={
                "source": "manual.docx",
                "section_path": f"第{order}章 > 说明",
                "element_order": order,
                "content_type": "text",
                "source_snapshot_hash": "abc123",
                "source_document_id": "abc123",
                "element_id": f"el_{order}",
                "source_element_ids": [f"el_{order}"],
                "source_raw_block_ids": [f"rb_{order:04d}"],
            },
        )
        for order in (1, 2, 3)
    ]

    class _FakeUnstructured:
        def load(self, _path):
            return element_docs

    loader._unstructured_loader = _FakeUnstructured()
    monkeypatch.setattr(loader, "_split_documents_preserving_blocks", lambda docs: docs)
    monkeypatch.setattr(loader, "_post_process_chunks", lambda docs: [docs[0], docs[2]])

    docx = tmp_path / "manual.docx"
    docx.write_bytes(b"PK\x00\x00")

    chunks = loader._load_text(str(docx), document_profile=DocumentProfile.TECHNICAL_MANUAL)

    assert len(chunks) == 2
    assert chunks[0].metadata["prev_chunk_id"] is None
    assert chunks[0].metadata["next_chunk_id"] == chunks[1].metadata["chunk_uid"]
    assert chunks[1].metadata["prev_chunk_id"] == chunks[0].metadata["chunk_uid"]
    assert chunks[1].metadata["next_chunk_id"] is None
